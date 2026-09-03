#!/usr/bin/env python3
"""Read-only audit of a CUMCM contract project.

This program verifies structure, identifiers, file hashes, dependency freshness
and evidence reachability.  It intentionally does *not* execute experiments or
claim that a mathematical model is correct.  Human reviews remain mandatory
for scientific suitability and interpretation when a project is released.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
import sys
import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    from _contract_support import (
        DuplicateKeyError,
        SHA256_RE,
        TYPED_ID_RE,
        UniqueKeyLoader,
        VALIDATION_STATUSES,
        _validate_yaml_tree,
        aggregate_status,
        load_json_strict,
        load_yaml,
        safe_project_path,
        sha256_file,
        write_text_exclusive,
    )
except ImportError as exc:  # pragma: no cover - used for a clean ENV_BLOCK failure
    print(
        json.dumps(
            {"status": "ENV_BLOCK", "message": f"missing script dependency: {exc}"},
            ensure_ascii=False,
        )
    )
    raise SystemExit(11)


SCHEMA_BY_KIND = {
    "manifest": "manifest.schema.json",
    "problem_spec": "problem_spec.schema.json",
    "model_spec": "model_spec.schema.json",
    "model_promotion": "model_promotion.schema.json",
    "experiment": "experiment.schema.json",
    "results": "results.schema.json",
    "claims": "claims.schema.json",
    "figures": "figures.schema.json",
    "paper_build": "paper_build.schema.json",
    "gate_review": "gate_review.schema.json",
}

GATE_BY_KIND = {
    "manifest": "G7",
    "problem_spec": "G1",
    "model_spec": "G2",
    "model_promotion": "G2",
    "experiment": "G3",
    "results": "G4",
    "claims": "G5",
    "figures": "G5",
    "paper_build": "G6",
    "gate_review": "G7",
}

GATES = tuple(f"G{number}" for number in range(8))
ZERO_HASH = "0" * 64
HARD_STATUSES = {"BLOCK", "ENV_BLOCK", "STALE"}
TEAM_ROLES = {"modeling", "computation", "writing"}
DECISION_TIMING_VALUES = {"here_and_now", "wait_and_see", "recourse"}
RELEASE_GATE_REQUIRED_ROLES: dict[str, set[str]] = {
    "G0": set(),
    "G1": set(TEAM_ROLES),
    "G2": set(TEAM_ROLES),
    "G3": {"modeling", "computation"},
    "G4": {"modeling", "computation"},
    "G5": set(TEAM_ROLES),
    "G6": set(TEAM_ROLES),
    "G7": set(TEAM_ROLES),
}
GATE_ROLLBACK_TARGET = {
    "G0": "INTAKE",
    "G1": "FRAMING",
    "G2": "MODELING",
    "G3": "EXPERIMENT_DESIGN",
    "G4": "VALIDATING",
    "G5": "CLAIMING",
    "G6": "WRITING",
    "G7": "RELEASE_QA",
}

# Each family lists checks that must be considered before G2.  A check may be
# explicitly marked not_applicable with a rationale; this preserves legitimate
# exceptions while preventing an entire failure mode from being forgotten.
VALIDATION_COVERAGE_BY_FAMILY: dict[str, set[str]] = {
    "descriptive": {"input_integrity"},
    "statistical": {"residual_diagnostics", "uncertainty"},
    "prediction": {"baseline_comparison", "holdout_leakage", "predictive_error", "uncertainty"},
    "optimization": {
        "constraint_feasibility",
        "solver_optimality",
        "objective_reconciliation",
        "baseline_comparison",
        "holdout_leakage",
        "sensitivity",
    },
    "simulation": {"boundary_case", "convergence", "conservation_balance", "numerical_stability"},
    "evaluation": {"baseline_comparison", "sensitivity", "rank_stability"},
    "causal": {"identifiability", "falsification", "uncertainty"},
}
VALIDATION_COVERAGE_BY_TASK: dict[str, set[str]] = {
    "description": {"input_integrity"},
    "prediction": {"baseline_comparison", "holdout_leakage", "predictive_error", "uncertainty"},
    "evaluation": {"baseline_comparison", "sensitivity", "rank_stability"},
    "optimization": {"constraint_feasibility", "solver_optimality", "baseline_comparison", "sensitivity"},
    "mechanism": {"boundary_case", "sensitivity", "dimensional_consistency"},
    "decision": {"baseline_comparison", "sensitivity"},
}
FORMULA_VALIDATION_CHECKS = {"dimensional_consistency", "domain_validity", "formula_back_substitution"}
OBJECTIVE_RECONCILIATION_INTRODUCED_VERSION = (2, 2, 0)
SCENARIO_SETS_INTRODUCED_VERSION = (2, 3, 0)
MODEL_EVIDENCE_CONSISTENCY_INTRODUCED_VERSION = (2, 4, 0)
MAX_CLOCK_SKEW = timedelta(minutes=5)
PAPER_COMPILERS_BY_ENGINE = {
    "latex": {"latexmk", "xelatex", "lualatex", "pdflatex", "tectonic"},
    "typst": {"typst"},
}
DANGEROUS_BUILD_FLAGS = {
    "-shell-escape",
    "--shell-escape",
    "--enable-write18",
    "-enable-write18",
}
OUTPUT_REDIRECT_FLAG_PREFIXES = (
    "-outdir",
    "--outdir",
    "-output-directory",
    "--output-directory",
    "-auxdir",
    "--auxdir",
    "-jobname",
    "--jobname",
)
BUILD_LOG_FAILURE_RE = re.compile(
    r"(?im)^\s*(?:"
    r"!\s+\S.*|"
    r"error(?:\[[^\]]+\])?\s*:|"
    r"fatal(?:\s+error)?\s*:|"
    r"emergency\s+stop\b|"
    r"undefined\s+control\s+sequence\b|"
    r"no\s+pages\s+of\s+output\b|"
    r"compilation\s+failed\b|"
    r"build\s+failed\b|"
    r"failed\s+to\s+compile\b|"
    r"thread\s+.+\s+panicked\b|"
    r"latexmk:\s+errors?,\s+so\s+i\s+did\s+not\s+complete"
    r")"
)
BUILD_LOG_SUCCESS_RE = re.compile(
    r"(?im)(?:"
    r"output\s+written\s+on\s+.+\.pdf|"
    r"build\s+(?:completed|succeeded|successful)|"
    r"compil(?:ation\s+(?:completed|succeeded|successful)|ed\s+successfully)|"
    r"latexmk:\s+all\s+targets\s+.+\s+are\s+up-to-date"
    r")"
)

NUMERIC_OPERATIONS = {
    "==": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
}


def effective_validation_facets(model: dict[str, Any]) -> set[str]:
    """Return additive family facets without allowing a mapped family to be dropped."""

    declared = model.get("validation_facets", [])
    facets = (
        {
            facet
            for facet in declared
            if isinstance(facet, str) and facet in VALIDATION_COVERAGE_BY_FAMILY
        }
        if isinstance(declared, list)
        else set()
    )
    model_family = model.get("model_family")
    if model_family in VALIDATION_COVERAGE_BY_FAMILY:
        facets.add(str(model_family))
    return facets


def scenario_holdout_is_actionable(model: dict[str, Any]) -> bool:
    """Return whether the model predeclares a scenario holdout check to run."""

    return any(
        check.get("check_type") == "holdout_leakage"
        and check.get("applicability") in {"required", "conditional"}
        for check in model.get("validation_plan", {}).get("checks", [])
        if isinstance(check, dict)
    )


def metric_signature(metric: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """Return the declared comparison semantics of one experiment metric."""

    return (
        metric.get("name"),
        metric.get("direction"),
        metric.get("unit"),
        metric.get("aggregation"),
    )


def decimal_number(value: Any) -> Decimal:
    """Convert a finite schema number without IEEE-754 integer collapse."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"not a numeric value: {value!r}")
    try:
        converted = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value: {value!r}") from exc
    if not converted.is_finite():
        raise ValueError(f"non-finite numeric value: {value!r}")
    return converted


def contract_version_tuple(document: dict[str, Any]) -> tuple[int, int, int]:
    """Return a schema-validated 2.x contract version for compatibility gates."""

    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)", str(document.get("schema_version", ""))
    )
    if match is None:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def model_evidence_consistency_code(model: dict[str, Any], current_code: str) -> str:
    """Keep pre-2.4 contracts readable while naming their required migration."""

    if contract_version_tuple(model) < MODEL_EVIDENCE_CONSISTENCY_INTRODUCED_VERSION:
        return f"{current_code}_MIGRATION_REQUIRED"
    return current_code


def experiments_are_comparable(primary: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """Require one fair experimental frame and all decision metrics.

    Code entrypoints intentionally differ between methods.  The data, question
    scope, split, stochastic budget, timeout and environment must not.  Every
    metric used by a primary acceptance rule must have an exact semantic match
    in the baseline; exploratory runs without rules compare all declared
    metrics instead.
    """

    fair_frame = (
        set(primary.get("question_refs", [])) == set(baseline.get("question_refs", []))
        and set(primary.get("data_refs", [])) == set(baseline.get("data_refs", []))
        and primary.get("split_strategy") == baseline.get("split_strategy")
        and primary.get("decision_timing") in DECISION_TIMING_VALUES
        and primary.get("decision_timing") == baseline.get("decision_timing")
        and primary.get("seeds") == baseline.get("seeds")
        and primary.get("repetitions") == baseline.get("repetitions")
        and primary.get("timeout_seconds") == baseline.get("timeout_seconds")
        and primary.get("environment", {}).get("sha256") == baseline.get("environment", {}).get("sha256")
    )
    if not fair_frame:
        return False
    primary_metrics = {metric.get("id"): metric for metric in primary.get("metrics", [])}
    decision_metric_refs = {
        rule.get("metric_ref") for rule in primary.get("acceptance_rules", [])
    } or set(primary_metrics)
    required_signatures = {
        metric_signature(primary_metrics[metric_ref])
        for metric_ref in decision_metric_refs
        if metric_ref in primary_metrics
    }
    baseline_signatures = {metric_signature(metric) for metric in baseline.get("metrics", [])}
    return bool(required_signatures) and required_signatures.issubset(baseline_signatures)


def experiment_implementation_signature(experiment: dict[str, Any]) -> tuple[Any, ...]:
    """Identify implementation bytes and parameters, not entrypoint spelling.

    A copied script is not an independent baseline merely because its path in
    ``argv`` changed.  The fair-test frame is checked separately by
    :func:`experiments_are_comparable`; this signature therefore binds the
    actual code hashes and method parameters only.
    """

    return (
        json.dumps(experiment.get("parameters", {}), sort_keys=True, ensure_ascii=False),
        tuple(sorted(item.get("sha256") for item in experiment.get("code_files", []) if item.get("sha256"))),
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of silently accepting the last one."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> None:
    """Reject non-standard NaN/Infinity tokens accepted by Python's decoder."""

    raise ValueError(f"non-finite JSON constant: {token}")


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    """Recognize POSIX links and Windows reparse-point indirection."""

    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_attribute)


def read_stable_bytes(path: Path) -> bytes:
    """Capture one regular file through a single descriptor.

    Hashing a pathname and reopening it for semantic parsing creates a
    time-of-check/time-of-use gap: another process can replace the pathname
    between those operations.  A descriptor-bound capture makes the digest
    and parser consume exactly the same bytes.  Metadata checks reject a file
    that was modified while the capture itself was in progress.
    """

    logical_metadata = path.lstat()
    if _is_link_or_reparse(logical_metadata):
        raise ValueError(f"audited contract path must not be a symlink or reparse point: {path}")
    if not stat.S_ISREG(logical_metadata.st_mode):
        raise ValueError(f"audited contract path must be a regular file: {path}")
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not os.path.samestat(logical_metadata, before):
            raise OSError(f"file changed between path lookup and capture: {path}")
        data = handle.read()
        after = os.fstat(handle.fileno())
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(before, field, None) != getattr(after, field, None) for field in stable_fields):
        raise OSError(f"file changed while being captured: {path}")
    if len(data) != after.st_size:
        raise OSError(f"captured byte count differs from file size: {path}")
    current_metadata = path.lstat()
    if not os.path.samestat(after, current_metadata):
        raise OSError(f"file pathname changed while being captured: {path}")
    return data


def sha256_stable_file(path: Path) -> str:
    """Stream a link-free file hash while checking descriptor stability.

    This variant is used for excluded historical artifacts: their bytes have
    no semantic role and may be large, so retaining the complete file merely
    to report an informational hash would create avoidable memory growth.
    """

    logical_metadata = path.lstat()
    if _is_link_or_reparse(logical_metadata):
        raise ValueError(f"audited path must not be a symlink or reparse point: {path}")
    if not stat.S_ISREG(logical_metadata.st_mode):
        raise ValueError(f"audited path must be a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not os.path.samestat(logical_metadata, before):
            raise OSError(f"file changed between path lookup and hashing: {path}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(before, field, None) != getattr(after, field, None) for field in stable_fields):
        raise OSError(f"file changed while being hashed: {path}")
    current_metadata = path.lstat()
    if not os.path.samestat(after, current_metadata):
        raise OSError(f"file pathname changed while being hashed: {path}")
    return digest.hexdigest()


def load_yaml_bytes(data: bytes) -> Any:
    """Parse strict UTF-8 YAML from already captured bytes."""

    document = yaml.load(data.decode("utf-8"), Loader=UniqueKeyLoader)
    _validate_yaml_tree(document, ancestors=set())
    return document


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Resolve a minimal RFC 6901 JSON pointer for JSON/YAML metric extraction."""

    if pointer == "":
        return document
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def extract_metric_value(path: Path, extractor: dict[str, Any]) -> Any:
    """Read one declared scalar from a hashed experiment output."""

    extractor_type = extractor.get("type")
    if extractor_type == "json_pointer":
        return resolve_pointer(
            json.loads(
                path.read_text(encoding="utf-8"),
                parse_float=Decimal,
                parse_int=Decimal,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            ),
            extractor.get("pointer", ""),
        )
    if extractor_type == "yaml_pointer":
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        _validate_yaml_tree(document, ancestors=set())
        return resolve_pointer(document, extractor.get("pointer", ""))
    if extractor_type == "csv_cell":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            if not fieldnames:
                raise ValueError("CSV has no header")
            if len(fieldnames) != len(set(fieldnames)):
                raise ValueError("CSV has duplicate header names")
            if any(not name for name in fieldnames):
                raise ValueError("CSV has an empty header name")
            rows = list(reader)
            if any(None in row for row in rows):
                raise ValueError("CSV row contains more fields than its header")
        return rows[extractor["row_index"]][extractor["column"]]
    raise ValueError(f"unsupported metric extractor: {extractor_type!r}")


def extracted_decimal(value: Any) -> Decimal:
    """Convert a scalar extracted from JSON/YAML/CSV to a finite Decimal."""

    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"extracted metric is not scalar numeric: {value!r}")
    try:
        converted = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"extracted metric is not numeric: {value!r}") from exc
    if not converted.is_finite():
        raise ValueError(f"extracted metric is non-finite: {value!r}")
    return converted


def _decimal_text(value: Any) -> Decimal | None:
    """Parse a finite decimal-looking scalar, returning None for text."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return decimal_number(value) if not isinstance(value, Decimal) else value if value.is_finite() else None
        except ValueError:
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _numbers_close(actual: Decimal, expected: Decimal, absolute: Decimal, relative: Decimal) -> bool:
    return abs(actual - expected) <= absolute + relative * abs(expected)


def _compare_json_tree(actual: Any, expected: Any, absolute: Decimal, relative: Decimal, location: str = "$") -> tuple[bool, str]:
    """Recursively compare JSON structure with declared numeric tolerances."""

    actual_number = _decimal_text(actual)
    expected_number = _decimal_text(expected)
    if actual_number is not None or expected_number is not None:
        if actual_number is None or expected_number is None:
            return False, f"numeric type mismatch at {location}"
        if not _numbers_close(actual_number, expected_number, absolute, relative):
            return False, f"numeric mismatch at {location}: {actual_number} versus {expected_number}"
        return True, "numeric values match"
    if type(actual) is not type(expected):
        return False, f"type mismatch at {location}"
    if isinstance(actual, dict):
        if set(actual) != set(expected):
            return False, f"object keys differ at {location}"
        for key in sorted(actual):
            matched, note = _compare_json_tree(actual[key], expected[key], absolute, relative, f"{location}/{key}")
            if not matched:
                return matched, note
        return True, "JSON structures match"
    if isinstance(actual, list):
        if len(actual) != len(expected):
            return False, f"array length differs at {location}"
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            matched, note = _compare_json_tree(left, right, absolute, relative, f"{location}/{index}")
            if not matched:
                return matched, note
        return True, "JSON structures match"
    return (True, "scalar values match") if actual == expected else (False, f"value mismatch at {location}")


def _load_strict_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_float=Decimal,
        parse_int=Decimal,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_unique_json_object,
    )


def _load_strict_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        if not fieldnames or any(not field for field in fieldnames):
            raise ValueError("CSV requires non-empty header names")
        if len(fieldnames) != len(set(fieldnames)):
            raise ValueError("CSV has duplicate header names")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("CSV row width differs from its header")
    return fieldnames, rows


def evaluate_output_comparator(root: Path, output_path: Path, comparator: dict[str, Any]) -> tuple[bool, str]:
    """Recompute one predeclared output comparison from hashed files."""

    comparator_type = comparator.get("type")
    if comparator_type == "existence":
        return output_path.is_file(), "output file exists" if output_path.is_file() else "output file is missing"
    if comparator_type == "exact_sha256":
        expected = comparator.get("expected_sha256")
        actual = sha256_file(output_path)
        return actual == expected, f"output sha256 is {actual}; expected {expected}"
    reference = comparator.get("reference_file")
    if not isinstance(reference, dict):
        return False, "numeric comparator has no hashed reference file"
    reference_path = safe_project_path(root, reference.get("path"), must_exist=True)
    if sha256_file(reference_path) != reference.get("sha256"):
        return False, "numeric comparator reference hash is stale"
    absolute = decimal_number(comparator.get("absolute_tolerance"))
    relative = decimal_number(comparator.get("relative_tolerance"))
    if absolute < 0 or relative < 0:
        return False, "numeric comparator tolerances must be non-negative"
    if comparator_type == "json_numeric":
        return _compare_json_tree(_load_strict_json(output_path), _load_strict_json(reference_path), absolute, relative)
    if comparator_type == "csv_numeric":
        actual_header, actual_rows = _load_strict_csv(output_path)
        expected_header, expected_rows = _load_strict_csv(reference_path)
        if actual_header != expected_header:
            return False, "CSV headers differ"
        key_columns = comparator.get("key_columns", [])
        if any(column not in actual_header for column in key_columns):
            return False, "CSV key_columns are absent from the header"
        if key_columns:
            def keyed(rows: list[dict[str, str]]) -> dict[tuple[str, ...], dict[str, str]]:
                result: dict[tuple[str, ...], dict[str, str]] = {}
                for row in rows:
                    key = tuple(row[column] for column in key_columns)
                    if key in result:
                        raise ValueError(f"duplicate CSV key {key!r}")
                    result[key] = row
                return result
            actual_map = keyed(actual_rows)
            expected_map = keyed(expected_rows)
            if set(actual_map) != set(expected_map):
                return False, "CSV key sets differ"
            paired_rows = [(actual_map[key], expected_map[key]) for key in sorted(actual_map)]
        else:
            if len(actual_rows) != len(expected_rows):
                return False, "CSV row counts differ"
            paired_rows = list(zip(actual_rows, expected_rows, strict=True))
        for row_index, (actual_row, expected_row) in enumerate(paired_rows):
            for column in actual_header:
                if column in key_columns:
                    continue
                left = _decimal_text(actual_row[column])
                right = _decimal_text(expected_row[column])
                if left is None and right is None:
                    if actual_row[column] != expected_row[column]:
                        return False, f"CSV text mismatch at row {row_index}, column {column}"
                elif left is None or right is None or not _numbers_close(left, right, absolute, relative):
                    return False, f"CSV numeric mismatch at row {row_index}, column {column}"
        return True, "CSV structures and values match"
    return False, f"unsupported output comparator {comparator_type!r}"


def validate_visual_file(path: Path) -> None:
    """Parse a registered visual instead of trusting its filename extension."""

    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(f"Pillow is required to validate raster figures: {exc}") from exc
        expected = "PNG" if suffix == ".png" else "JPEG"
        with Image.open(path) as image:
            if image.format != expected:
                raise ValueError(f"content format {image.format!r} does not match {suffix}")
            image.verify()
        return
    if suffix == ".svg":
        data = path.read_bytes()
        upper = data.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise ValueError("SVG must not contain DTD or entity declarations")
        root = ET.fromstring(data)
        if root.tag.rsplit("}", 1)[-1].lower() != "svg":
            raise ValueError("XML root element is not svg")
        return
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(f"pypdf is required to validate PDF figures: {exc}") from exc
        reader = PdfReader(str(path))
        if reader.is_encrypted or len(reader.pages) < 1:
            raise ValueError("PDF figure is encrypted or contains no pages")
        return
    raise ValueError(f"unsupported figure extension: {suffix or '<none>'}")


def parse_latex_recorder(root: Path, cwd: Any, recorder_path: Path) -> set[Path]:
    """Return project-local ``INPUT`` files from a TeX ``-recorder`` log.

    TeX-distribution files remain external compiler-environment inputs. Every
    input resolving inside the project must exist and becomes part of the
    byte-level paper-build receipt.
    """

    root_resolved = root.resolve()
    working = safe_project_path(root_resolved, cwd, must_exist=True)
    if not working.is_dir():
        raise ValueError("recorder cwd is not a directory")
    declared_working = working
    text = recorder_path.read_text(encoding="utf-8")
    if not text.strip() or "\x00" in text:
        raise ValueError("LaTeX recorder log is empty or contains NUL bytes")
    local_inputs: set[Path] = set()
    input_rows = 0
    for raw_line in text.splitlines():
        if raw_line.startswith("PWD "):
            raw = raw_line[4:].strip()
            if raw:
                candidate = Path(raw)
                recorded_working = (
                    candidate if candidate.is_absolute() else declared_working / candidate
                ).resolve(strict=False)
                if recorded_working != declared_working:
                    raise ValueError("LaTeX recorder PWD differs from the declared compiler cwd")
                working = recorded_working
            continue
        if not raw_line.startswith("INPUT "):
            continue
        input_rows += 1
        raw = raw_line[6:].strip()
        if not raw:
            raise ValueError("LaTeX recorder contains an empty INPUT row")
        candidate = Path(raw)
        resolved = (candidate if candidate.is_absolute() else working / candidate).resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            continue
        if not resolved.is_file():
            raise ValueError("LaTeX recorder names a missing project-local input")
        local_inputs.add(resolved)
    if input_rows == 0:
        raise ValueError("LaTeX recorder contains no INPUT rows")
    return local_inputs


PROOF_STRUCTURE_RE = re.compile(
    r"(?im)(?:^|\n)\s*(?:#{1,6}\s*)?(?:proof|derivation|argument|证明|推导|论证)\s*[:：]?"
)
PROOF_REASONING_RE = re.compile(
    r"(?i)\b(?:assume|because|since|let|by|step)\b|因为|由于|设|令|根据|由|(?:<=|>=|=|\\le|\\ge)"
)
PROOF_CONCLUSION_RE = re.compile(
    r"(?i)\b(?:therefore|hence|thus|conclusion|qed|proved)\b|因此|所以|故|结论|证毕"
)


def _proof_text(path: Path) -> str:
    """Extract text that can bind a proof to one registered claim."""

    if path.stat().st_size <= 0:
        raise ValueError("proof artifact is empty")
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".tex", ".typ"}:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("proof artifact contains no non-whitespace text")
        return text
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(f"pypdf is required to validate proof PDFs: {exc}") from exc
        reader = PdfReader(str(path))
        if reader.is_encrypted or len(reader.pages) < 1:
            raise ValueError("proof PDF is encrypted or has no pages")
        extracted: list[str] = []
        for page in reader.pages:
            try:
                extracted.append(page.extract_text() or "")
            except Exception:
                extracted.append("")
        text = "\n".join(extracted)
        if not text.strip():
            raise ValueError("proof PDF has no extractable text for claim and argument verification")
        return text
    raise ValueError("proof artifact must be UTF-8 .txt/.md/.tex/.typ or a readable PDF")


def validate_proof_file(path: Path, *, claim_id: str, statement: str) -> None:
    """Require explicit claim binding and a minimal inspectable argument shape.

    This is deliberately a structural check, not a mathematical proof checker.
    It prevents an arbitrary non-empty token such as ``hello`` from becoming a
    proof-only release while leaving correctness to evidence-bound reviewers.
    """

    text = _proof_text(path)
    normalized_text = re.sub(r"\s+", " ", text).strip().casefold()
    normalized_statement = re.sub(r"\s+", " ", statement).strip().casefold()
    if claim_id.casefold() not in normalized_text:
        raise ValueError(f"proof text does not bind registered claim ID {claim_id}")
    if not normalized_statement or normalized_statement not in normalized_text:
        raise ValueError("proof text does not restate the registered proposition/conclusion")
    if len(normalized_text) < 80:
        raise ValueError("proof text is too short to contain an inspectable derivation")
    if PROOF_STRUCTURE_RE.search(text) is None:
        raise ValueError("proof text lacks a Proof/Derivation/Argument structure marker")
    if PROOF_REASONING_RE.search(text) is None:
        raise ValueError("proof text lacks an inspectable reasoning or derivation step")
    if PROOF_CONCLUSION_RE.search(text) is None:
        raise ValueError("proof text lacks an explicit conclusion/QED marker")


SCRIPT_RUNNERS_BY_SUFFIX: dict[str, set[str]] = {
    ".py": {"python", "python3", "py", "pypy", "pypy3"},
    ".r": {"rscript"},
    ".jl": {"julia"},
    ".js": {"node"},
    ".m": {"octave", "octave-cli"},
    ".jar": {"java"},
    ".sh": {"bash", "sh"},
    ".ps1": {"pwsh", "powershell"},
}


def normalized_executable_name(token: Any) -> str:
    """Return a case-insensitive executable basename without one file suffix."""

    if not isinstance(token, str) or not token:
        return ""
    return Path(token).stem.casefold()


def resolved_command_token(
    root: Path,
    cwd: Any,
    token: Any,
) -> Path | None:
    """Resolve an argv path token using the command's declared working directory.

    Options and opaque expressions are not treated as paths.  Invalid or
    escaping tokens simply do not resolve; the caller emits the contextual
    BLOCK finding.
    """

    if not isinstance(cwd, str) or not isinstance(token, str) or not token or token.startswith("-"):
        return None
    candidate = token if cwd == "." else f"{cwd}/{token}"
    try:
        return safe_project_path(root, candidate)
    except (TypeError, ValueError):
        return None


def command_executes_project_path(
    root: Path,
    *,
    argv: Any,
    cwd: Any,
    expected_relative: Any,
) -> tuple[bool, str]:
    """Conservatively verify that a supported command executes a hashed file.

    Merely mentioning ``code/main.py`` after ``echo`` is not execution.  A
    command passes only when the expected project path resolves under ``cwd``
    and is either argv[0] (a directly executed registered file) or is consumed
    by a runner appropriate for its suffix.  Unsupported tools can be invoked
    through a small hashed wrapper in one of the supported languages.
    """

    if not isinstance(argv, list) or not argv or not isinstance(expected_relative, str):
        return False, "command, cwd or expected entrypoint is missing"
    try:
        expected = safe_project_path(root, expected_relative)
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    runner = normalized_executable_name(argv[0])
    if expected.suffix.casefold() == ".m" and runner == "matlab":
        matlab_call = re.compile(r"^\s*(?:run|runtests)\(\s*(['\"])([^'\"]+\.m)\1\s*\)\s*;?\s*$", re.IGNORECASE)
        for token in argv[1:]:
            if not isinstance(token, str):
                continue
            match = matlab_call.fullmatch(token)
            if match and resolved_command_token(root, cwd, match.group(2)) == expected:
                return True, "registered MATLAB entrypoint is consumed by an exact -batch run/runtests expression"

    matched_indices = [
        index
        for index, token in enumerate(argv)
        if resolved_command_token(root, cwd, token) == expected
    ]
    if not matched_indices:
        return False, "no argv token resolves to the registered entrypoint under command.cwd"
    if 0 in matched_indices:
        return True, "registered entrypoint is executed directly"
    allowed = SCRIPT_RUNNERS_BY_SUFFIX.get(expected.suffix.casefold(), set())
    if runner not in allowed:
        return False, f"{runner or '<missing>'} is not a supported runner for {expected.suffix or '<no suffix>'}"
    if expected.suffix.casefold() == ".jar" and not any(
        index > 0 and argv[index - 1] == "-jar" for index in matched_indices
    ):
        return False, "Java archives must be passed through the exact -jar argument"
    return True, f"registered entrypoint is consumed by {runner}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit CUMCM schemas, cross-file references, hashes and stage gates without modifying the project."
    )
    parser.add_argument("project_root", type=Path, help="Directory containing manifest.yaml")
    parser.add_argument(
        "--schema-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "references" / "schemas",
        help="Directory containing the bundled JSON schemas",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optionally create a new report file; an existing path is never overwritten",
    )
    return parser.parse_args()


class Audit:
    """Collect findings and the parsed artifact graph for one project."""

    def __init__(self, root: Path, schema_root: Path) -> None:
        self.root = root.resolve()
        self.schema_root = schema_root.resolve()
        self.bundled_schema_root = (Path(__file__).resolve().parent.parent / "references" / "schemas").resolve()
        self.findings: list[dict[str, Any]] = []
        self.documents: dict[str, dict[str, Any]] = {}
        self.document_paths: dict[str, Path] = {}
        self.manifest: dict[str, Any] | None = None
        self.manifest_artifacts: dict[str, dict[str, Any]] = {}
        self.current_hashes: dict[str, str] = {}
        self.id_definitions: dict[str, tuple[str, str, str]] = {}
        self.result_eligibility: dict[str, bool] = {}
        self.effective_primary_model_ids: set[str] = set()
        self.promotion_trigger_diagnostics: dict[str, tuple[str, str]] = {}
        self.promoted_model_events: dict[str, tuple[str, datetime]] = {}
        # Promotion validation is intentionally two-phase.  The first phase
        # records structurally valid route candidates so their partial trigger
        # and post-promotion runs can be audited.  Only the second phase may
        # certify those routes after the trigger result has passed every other
        # result-contract check.
        self.promotion_candidates: list[dict[str, Any]] = []
        self.valid_promotion_trigger_result_ids: set[str] = set()
        self.valid_final_proof_claim_ids: set[str] = set()
        self.stale_roots: set[str] = set()
        self.current_approval_sets: dict[str, str] = {}
        self.valid_gate_approvals: set[str] = set()
        self.release_snapshot_digest: str | None = None
        self.schemas: dict[str, dict[str, Any]] = {}
        self.schema_registry = Registry()
        # Contract files are captured once.  Their digest and parsed meaning
        # therefore come from identical bytes, and an end-of-audit check
        # detects replacement of the pathname after capture.
        self.file_snapshots: dict[Path, tuple[str, str, str | None]] = {}
        self.changed_snapshot_paths: set[Path] = set()

    def _validate_contract_path_components(self, path: Path) -> None:
        """Reject indirection in every project-relative contract component."""

        logical = path.absolute()
        try:
            relative = logical.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"audited contract path escapes the project root: {path}") from exc
        current = self.root
        for component in relative.parts:
            current = current / component
            metadata = current.lstat()
            if _is_link_or_reparse(metadata):
                raise ValueError(
                    f"audited contract path must not traverse a symlink or reparse point: {current}"
                )

    def capture_file(
        self,
        path: Path,
        *,
        gate: str,
        artifact_id: str | None = None,
    ) -> bytes:
        """Return one immutable in-memory view of a semantically read file."""

        # ``absolute`` preserves the declared logical pathname.  ``resolve``
        # would erase symlink identity and let a later link retarget escape the
        # end-of-audit comparison.
        logical = path.absolute()
        self._validate_contract_path_components(logical)
        data = read_stable_bytes(logical)
        digest = hashlib.sha256(data).hexdigest()
        self.file_snapshots.setdefault(logical, (digest, gate, artifact_id))
        return data

    def verify_captured_files_unchanged(self) -> bool:
        """Reject a report if a captured pathname changed before completion.

        The parser still reasons over its immutable snapshot.  This final
        comparison additionally guarantees that the project left on disk is
        the project described by the report.
        """

        changed_now = False
        for path, (expected, gate, artifact_id) in sorted(
            self.file_snapshots.items(), key=lambda item: str(item[0])
        ):
            if path in self.changed_snapshot_paths:
                continue
            relative = self._display_path(path)
            try:
                self._validate_contract_path_components(path)
                actual = hashlib.sha256(read_stable_bytes(path)).hexdigest()
            except (OSError, ValueError) as exc:
                self.add(
                    gate,
                    "STALE",
                    "FILE_CHANGED_DURING_AUDIT",
                    f"captured file became unavailable before audit completion: {exc}",
                    path=relative,
                    artifact_id=artifact_id,
                )
                if artifact_id:
                    self.stale_roots.add(artifact_id)
                self.changed_snapshot_paths.add(path)
                changed_now = True
                continue
            if actual != expected:
                self.add(
                    gate,
                    "STALE",
                    "FILE_CHANGED_DURING_AUDIT",
                    f"captured sha256 {expected} changed to {actual} before audit completion",
                    path=relative,
                    artifact_id=artifact_id,
                )
                if artifact_id:
                    self.stale_roots.add(artifact_id)
                self.changed_snapshot_paths.add(path)
                changed_now = True
        return changed_now

    def add(
        self,
        gate: str,
        status: str,
        code: str,
        message: str,
        *,
        path: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        if gate not in GATES:
            raise ValueError(f"unknown gate: {gate}")
        if status not in VALIDATION_STATUSES:
            raise ValueError(f"unknown validation status: {status}")
        finding: dict[str, Any] = {
            "gate": gate,
            "status": status,
            "code": code,
            "message": message,
        }
        if path is not None:
            finding["path"] = path
        if artifact_id is not None:
            finding["artifact_id"] = artifact_id
        self.findings.append(finding)

    def load_schemas(self) -> bool:
        """Load every schema once and pre-check each schema itself."""

        if not self.schema_root.is_dir():
            self.add("G0", "ENV_BLOCK", "SCHEMA_DIR_MISSING", f"schema directory not found: {self.schema_root}")
            return False
        try:
            for path in sorted(self.schema_root.glob("*.schema.json")):
                schema = load_json_strict(path)
                Draft202012Validator.check_schema(schema)
                # The distributed schemas use portable relative $id values.
                # At runtime we give each schema an absolute local retrieval
                # URI so relative refs such as common.schema.json cannot fall
                # through to jsonschema's remote retriever.
                schema["$id"] = path.resolve().as_uri()
                self.schemas[path.name] = schema
                self.schema_registry = self.schema_registry.with_resource(
                    schema["$id"], Resource.from_contents(schema)
                )
        except Exception as exc:
            self.add("G0", "ENV_BLOCK", "SCHEMA_LOAD_FAILED", str(exc))
            return False

        required = {"common.schema.json", *SCHEMA_BY_KIND.values()}
        missing = sorted(required.difference(self.schemas))
        if missing:
            self.add("G0", "ENV_BLOCK", "SCHEMA_MISSING", f"missing schemas: {', '.join(missing)}")
            return False
        self.add("G0", "PASS", "SCHEMAS_READY", "all required schemas loaded and passed schema self-check")
        return True

    def validate_schema(self, document: dict[str, Any], path: Path, gate: str) -> bool:
        kind = document.get("kind")
        schema_name = SCHEMA_BY_KIND.get(kind)
        relative = self._display_path(path)
        if schema_name is None:
            self.add(gate, "BLOCK", "UNKNOWN_KIND", f"unsupported contract kind: {kind!r}", path=relative)
            return False

        schema = self.schemas[schema_name]
        # The local store makes relative $ref values deterministic and avoids
        # network retrieval.  This does not restrict the modeling workflow's
        # use of online sources; it only keeps schema validation self-contained.
        validator = Draft202012Validator(
            schema,
            registry=self.schema_registry,
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
        for error in errors:
            location = "/".join(str(part) for part in error.absolute_path) or "<root>"
            self.add(
                gate,
                "BLOCK",
                "SCHEMA_INVALID",
                f"{location}: {error.message}",
                path=relative,
                artifact_id=document.get("id"),
            )
        validation_facets = document.get("validation_facets")
        if kind == "model_spec" and document.get("model_family") in {"hybrid", "other"} and (
            "validation_facets" not in document
            or isinstance(validation_facets, list) and not validation_facets
        ):
            self.add(
                "G2",
                "BLOCK",
                "VALIDATION_FACETS_REQUIRED",
                f"{document.get('id')} model_family={document.get('model_family')!r} requires at least one validation facet",
                path=relative,
                artifact_id=document.get("id"),
            )
        if kind == "experiment" and "decision_timing" not in document:
            self.add(
                "G3",
                "BLOCK",
                "DECISION_TIMING_REQUIRED",
                (
                    f"{document.get('id')} schema_version={document.get('schema_version')!r} omits "
                    "decision_timing; explicitly declare here_and_now, wait_and_see, or recourse; "
                    "the auditor does not infer a default"
                ),
                path=relative,
                artifact_id=document.get("id"),
            )
        if (
            kind == "experiment"
            and "scenario_sets" not in document
            and (2, 0, 0)
            <= contract_version_tuple(document)
            < SCENARIO_SETS_INTRODUCED_VERSION
        ):
            self.add(
                "G3",
                "BLOCK",
                "SCENARIO_SETS_LEGACY_MIGRATION_REQUIRED",
                (
                    f"{document.get('id')} schema_version={document.get('schema_version')!r} predates "
                    "the scenario_sets contract; add an explicit empty list for non-scenario work or "
                    "register disjoint selection/holdout sets for scenario-based experiments"
                ),
                path=relative,
                artifact_id=document.get("id"),
            )
        if not errors:
            self.add(gate, "PASS", "SCHEMA_VALID", "contract matches its schema", path=relative, artifact_id=document.get("id"))
        return not errors

    def _display_path(self, path: Path) -> str:
        try:
            return path.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def release_mode(self) -> bool:
        """Return whether the manifest requests a final release audit."""

        return bool(self.manifest and self.manifest.get("manifest_type") == "release")

    def artifact_is_release_active(self, artifact_id: str) -> bool:
        """Identify artifacts allowed to affect release scientific gates.

        Optional/superseded artifacts may remain as an audit trail, but they
        cannot support a final claim or poison a later release.  Required
        release artifacts are active only after review/freeze; a separate
        validator emits an explicit BLOCK when a required artifact is still
        draft, stale or superseded.
        """

        if not self.release_mode():
            return True
        entry = self.manifest_artifacts.get(artifact_id, {})
        document = self.documents.get(artifact_id, {})
        return entry.get("required") is True and document.get("lifecycle_status") in {"reviewed", "frozen"}

    def _latest_review_set_ids(self, gate: str) -> set[str]:
        """Return the latest immutable approval-set ID for a gate.

        A set begins when its first member signs.  Later co-signatures extend
        that same set without letting an older set become current again.  If
        two distinct sets have the same start instant, both are returned so
        the dedicated review validator can reject the ambiguity instead of
        letting document or array order choose one.
        """

        grouped_times: dict[str, list[datetime]] = defaultdict(list)
        for artifact_id, document in self.documents.items():
            if document.get("kind") != "gate_review" or not self.artifact_is_release_active(artifact_id):
                continue
            for review in document.get("reviews", []):
                if not isinstance(review, dict) or review.get("gate") != gate:
                    continue
                approval_set_id = review.get("approval_set_id")
                if not isinstance(approval_set_id, str):
                    continue
                try:
                    grouped_times[approval_set_id].append(parse_rfc3339(review.get("reviewed_at")))
                except ValueError:
                    continue
        starts = {
            approval_set_id: min(times)
            for approval_set_id, times in grouped_times.items()
            if times
        }
        if not starts:
            return set()
        latest_start = max(starts.values())
        return {
            approval_set_id
            for approval_set_id, started_at in starts.items()
            if started_at == latest_start
        }

    def release_location_is_active(self, document: dict[str, Any], location: str) -> bool:
        """Exclude draft claim/figure rows from release reference and file checks."""

        if not self.release_mode():
            return True
        kind = document.get("kind")
        collection = "claims" if kind == "claims" else "figures" if kind == "figures" else None
        if kind == "gate_review":
            match = re.search(r"(?:^|/)reviews/(\d+)(?:/|$)", location)
            if match is None:
                return True
            index = int(match.group(1))
            reviews = document.get("reviews", [])
            if index >= len(reviews) or not isinstance(reviews[index], dict):
                return False
            candidate = reviews[index]
            gate = candidate.get("gate")
            approval_set_id = candidate.get("approval_set_id")
            if gate not in GATES or not isinstance(approval_set_id, str):
                # Keep malformed candidates visible to schema/review checks.
                return True
            latest_set_ids = self._latest_review_set_ids(gate)
            return not latest_set_ids or approval_set_id in latest_set_ids
        if collection is None:
            return True
        match = re.search(rf"(?:^|/){collection}/(\d+)(?:/|$)", location)
        if match is None:
            return True
        index = int(match.group(1))
        rows = document.get(collection, [])
        if index >= len(rows) or not isinstance(rows[index], dict):
            return False
        return rows[index].get("publication_status") == "final"

    def release_active_dependency_refs(self, document: dict[str, Any]) -> set[str]:
        """Map release-row semantic references to their owning artifacts."""

        references: set[str] = set()
        kind = document.get("kind")
        if kind == "claims":
            for claim in document.get("claims", []):
                if claim.get("publication_status") != "final":
                    continue
                references.update(
                    item.get("ref")
                    for item in [*claim.get("evidence_refs", []), *claim.get("counterevidence", [])]
                    if isinstance(item, dict) and isinstance(item.get("ref"), str)
                )
                references.update(claim.get("assumption_refs", []))
                references.update(claim.get("deliverable_refs", []))
        elif kind == "figures":
            for figure in document.get("figures", []):
                if figure.get("publication_status") != "final":
                    continue
                references.update(figure.get("source_result_refs", []))
                references.update(figure.get("claim_refs", []))
        elif kind == "gate_review":
            for index, review in enumerate(document.get("reviews", [])):
                if not self.release_location_is_active(document, f"<root>/reviews/{index}"):
                    continue
                references.update(review.get("evidence_refs", []))
                references.update(review.get("artifact_fingerprints", {}).keys())
        owners = {
            self.id_definitions[reference][0]
            for reference in references
            if reference in self.id_definitions
        }
        return {owner for owner in owners if owner in self.manifest_artifacts}

    def release_reference_is_active(
        self,
        document: dict[str, Any],
        location: str,
        reference: str,
    ) -> bool:
        """Return whether one reference participates in the current release."""

        if not self.release_mode() or self.release_location_is_active(document, location):
            pass
        else:
            return False
        if document.get("kind") in {"claims", "figures", "gate_review"} and "/depends_on/" in location:
            return reference in self.release_active_dependency_refs(document)
        return True

    def load_manifest(self) -> bool:
        path = self.root / "manifest.yaml"
        if not path.is_file():
            self.add("G7", "BLOCK", "MANIFEST_MISSING", "manifest.yaml is required")
            return False
        try:
            captured = self.capture_file(path, gate="G7", artifact_id="manifest:project")
        except (OSError, ValueError) as exc:
            self.add("G7", "BLOCK", "MANIFEST_CAPTURE_FAILED", str(exc), path="manifest.yaml")
            return False
        try:
            document = load_yaml_bytes(captured)
        except Exception as exc:
            self.add("G7", "BLOCK", "YAML_INVALID", str(exc), path="manifest.yaml")
            return False
        if not isinstance(document, dict):
            self.add("G7", "BLOCK", "DOCUMENT_NOT_OBJECT", "manifest must be a YAML mapping", path="manifest.yaml")
            return False
        if document.get("manifest_type") == "release" and self.schema_root != self.bundled_schema_root:
            self.add(
                "G0",
                "BLOCK",
                "RELEASE_SCHEMA_ROOT_UNTRUSTED",
                "release audits must use this Skill version's bundled schema set",
                path="manifest.yaml",
            )
            return False
        if not self.validate_schema(document, path, "G7"):
            # Semantic traversal assumes a structurally valid manifest.  Do
            # not continue through missing or wrongly typed collections and
            # risk turning a useful schema finding into a traceback.
            return False
        self.manifest = document
        return True

    def load_artifacts(self) -> None:
        """Verify manifest rows and load each declared contract document."""

        assert self.manifest is not None
        seen_paths: dict[str, str] = {}
        seen_casefold: dict[str, str] = {}
        for entry in self.manifest.get("artifacts", []):
            if not isinstance(entry, dict):
                continue
            artifact_id = entry.get("id")
            relative = entry.get("path")
            if not isinstance(artifact_id, str) or not isinstance(relative, str):
                continue
            if artifact_id in self.manifest_artifacts:
                self.add("G7", "BLOCK", "DUPLICATE_MANIFEST_ID", f"duplicate manifest artifact ID: {artifact_id}")
                continue
            self.manifest_artifacts[artifact_id] = entry

            if relative in seen_paths:
                self.add("G7", "BLOCK", "DUPLICATE_PATH", f"path used by {seen_paths[relative]} and {artifact_id}", path=relative)
            else:
                seen_paths[relative] = artifact_id
            folded = relative.casefold()
            if folded in seen_casefold and seen_casefold[folded] != relative:
                self.add("G7", "BLOCK", "CASE_COLLISION", f"paths collide on case-insensitive filesystems: {seen_casefold[folded]} and {relative}")
            else:
                seen_casefold[folded] = relative

            try:
                # Validate the resolved destination for root escape, then keep
                # the lexical project path so capture can reject any symlink
                # or reparse-point component instead of losing its identity.
                safe_project_path(self.root, relative)
                path = self.root.joinpath(*relative.split("/"))
            except ValueError as exc:
                self.add("G7", "BLOCK", "PATH_UNSAFE", str(exc), path=relative, artifact_id=artifact_id)
                continue
            if not path.is_file():
                optional_history = self.release_mode() and entry.get("required") is not True
                status = "NOT_APPLICABLE" if optional_history else "BLOCK" if entry.get("required", True) else "WARN"
                code = "HISTORICAL_ARTIFACT_MISSING" if optional_history else "ARTIFACT_MISSING"
                self.add("G7", status, code, "declared artifact file does not exist", path=relative, artifact_id=artifact_id)
                continue

            expected_hash = entry.get("sha256")
            optional_release_history = self.release_mode() and entry.get("required") is not True
            if optional_release_history:
                # Excluded history has no semantic role, so stream its hash
                # without retaining its complete bytes in memory.
                try:
                    self._validate_contract_path_components(path)
                    actual_hash = sha256_stable_file(path)
                except (OSError, ValueError) as exc:
                    self.add(
                        "G7",
                        "NOT_APPLICABLE",
                        "HISTORICAL_ARTIFACT_UNAVAILABLE",
                        f"optional historical artifact is excluded and could not be safely fingerprinted: {exc}",
                        path=relative,
                        artifact_id=artifact_id,
                    )
                    continue
                self.current_hashes[artifact_id] = actual_hash
                relationship = "matches" if expected_hash == actual_hash else "differs from"
                self.add(
                    "G7",
                    "NOT_APPLICABLE",
                    "HISTORICAL_ARTIFACT_EXCLUDED",
                    f"optional historical artifact is outside release evidence; recorded hash {relationship} current bytes",
                    path=relative,
                    artifact_id=artifact_id,
                )
                continue

            gate = GATE_BY_KIND.get(entry.get("kind"), "G7")
            try:
                captured = self.capture_file(path, gate=gate, artifact_id=artifact_id)
            except (OSError, ValueError) as exc:
                self.add(
                    gate,
                    "BLOCK",
                    "ARTIFACT_CAPTURE_FAILED",
                    str(exc),
                    path=relative,
                    artifact_id=artifact_id,
                )
                continue
            actual_hash = hashlib.sha256(captured).hexdigest()
            self.current_hashes[artifact_id] = actual_hash
            if expected_hash == ZERO_HASH:
                self.add("G7", "STALE", "HASH_PLACEHOLDER", "manifest still contains a placeholder hash", path=relative, artifact_id=artifact_id)
                self.stale_roots.add(artifact_id)
            elif expected_hash != actual_hash:
                self.add("G7", "STALE", "ARTIFACT_HASH_MISMATCH", f"expected {expected_hash}, got {actual_hash}", path=relative, artifact_id=artifact_id)
                self.stale_roots.add(artifact_id)
            else:
                self.add("G7", "PASS", "ARTIFACT_HASH_MATCH", "manifest hash matches file bytes", path=relative, artifact_id=artifact_id)

            try:
                document = load_yaml_bytes(captured)
            except Exception as exc:
                self.add(GATE_BY_KIND.get(entry.get("kind"), "G7"), "BLOCK", "YAML_INVALID", str(exc), path=relative, artifact_id=artifact_id)
                continue
            if not isinstance(document, dict):
                self.add(GATE_BY_KIND.get(entry.get("kind"), "G7"), "BLOCK", "DOCUMENT_NOT_OBJECT", "contract must be a YAML mapping", path=relative, artifact_id=artifact_id)
                continue

            schema_valid = self.validate_schema(document, path, gate)
            if document.get("id") != artifact_id:
                self.add(gate, "BLOCK", "ID_MISMATCH", f"manifest ID {artifact_id!r} differs from document ID {document.get('id')!r}", path=relative, artifact_id=artifact_id)
                schema_valid = False
            if document.get("kind") != entry.get("kind"):
                self.add(gate, "BLOCK", "KIND_MISMATCH", f"manifest kind {entry.get('kind')!r} differs from document kind {document.get('kind')!r}", path=relative, artifact_id=artifact_id)
                schema_valid = False
            if not schema_valid:
                # Keep manifest/hash findings, but exclude the malformed
                # document from all semantic checks.
                continue
            manifest_dependencies = set(entry.get("depends_on", []))
            document_dependencies = set(document.get("depends_on", []))
            if manifest_dependencies != document_dependencies:
                self.add(
                    "G7",
                    "BLOCK",
                    "DEPENDENCY_DECLARATION_MISMATCH",
                    (
                        f"manifest dependencies {sorted(manifest_dependencies)} differ from "
                        f"{artifact_id} document dependencies {sorted(document_dependencies)}"
                    ),
                    path=relative,
                    artifact_id=artifact_id,
                )
            self.documents[artifact_id] = document
            self.document_paths[artifact_id] = path

    def validate_manifest_files(self) -> None:
        """Validate environment, entrypoint and deliverable files in manifest.

        Unlike artifact files, these rows are not YAML contracts.  They still
        receive stable typed IDs and current hashes so fingerprint-bound G6/G7
        reviews become stale whenever the actual paper or release bytes change.
        Environment drift marks every experiment root stale; the artifact DAG
        then propagates that state to results, claims, figures and reviews.
        """

        if not self.manifest:
            return

        experiment_ids = {
            artifact_id
            for artifact_id, entry in self.manifest_artifacts.items()
            if entry.get("kind") == "experiment"
        }
        paper_build_ids = {
            artifact_id
            for artifact_id, entry in self.manifest_artifacts.items()
            if entry.get("kind") == "paper_build"
        }
        profile = self.manifest.get("competition_profile", {})
        if isinstance(profile, dict) and profile.get("enabled") is True:
            self._verify_manifest_file_ref(
                profile,
                role="competition_profile",
                missing_status="BLOCK",
                stale_targets=paper_build_ids,
                gate="G6",
            )
        for item in self.manifest.get("environment_files", []):
            self._verify_manifest_file_ref(
                item,
                role="environment",
                missing_status="BLOCK",
                stale_targets=experiment_ids,
            )

        for name, relative in self.manifest.get("entrypoints", {}).items():
            entrypoint_id = f"entrypoint:{name}"
            try:
                path = safe_project_path(self.root, relative)
            except (TypeError, ValueError) as exc:
                self.add("G7", "BLOCK", "ENTRYPOINT_PATH_UNSAFE", str(exc), path=relative, artifact_id=entrypoint_id)
                continue
            if not path.is_file():
                self.add("G7", "BLOCK", "ENTRYPOINT_MISSING", "declared entrypoint does not exist", path=relative, artifact_id=entrypoint_id)
                continue
            self.current_hashes[entrypoint_id] = sha256_file(path)
            self.add("G7", "PASS", "ENTRYPOINT_PRESENT", "declared entrypoint exists and was fingerprinted", path=relative, artifact_id=entrypoint_id)

        for item in self.manifest.get("deliverables", []):
            self._verify_manifest_file_ref(
                item,
                role="deliverable",
                missing_status="BLOCK" if item.get("required") else "WARN",
                stale_targets=set(),
            )
        deliverable_paths: dict[str, list[str]] = defaultdict(list)
        for item in self.manifest.get("deliverables", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                deliverable_paths[item["path"]].append(str(item.get("id")))
        for relative, owners in deliverable_paths.items():
            if len(owners) > 1:
                self.add("G7", "BLOCK", "DUPLICATE_DELIVERABLE_PATH", f"deliverable path {relative!r} is reused by {owners}", path=relative)

    def register_release_snapshot(self) -> None:
        """Register a cycle-free digest of the final release selection.

        The manifest records the gate-review log hash, while G7 reviews must in
        turn bind the manifest selection.  To avoid a cryptographic
        self-reference, the canonical snapshot preserves the review artifact's
        ID/path/dependencies but replaces only its ``sha256`` value with a
        fixed marker.  Every other manifest field (including notes,
        required/optional choices, environment files and deliverables) and the
        actual entrypoint bytes remains covered.
        """

        if not self.release_mode() or not self.manifest:
            return
        canonical_manifest = json.loads(json.dumps(self.manifest, ensure_ascii=False))
        for row in canonical_manifest.get("artifacts", []):
            if isinstance(row, dict) and row.get("kind") == "gate_review":
                row["sha256"] = "<gate-review-self-reference-elided>"
        entrypoint_hashes = {
            f"entrypoint:{name}": self.current_hashes.get(f"entrypoint:{name}")
            for name in sorted(self.manifest.get("entrypoints", {}))
        }
        payload = {
            "snapshot_version": "1",
            "manifest": canonical_manifest,
            "entrypoint_sha256": entrypoint_hashes,
        }
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(rendered).hexdigest()
        self.release_snapshot_digest = digest
        self.current_hashes["snapshot:release"] = digest
        self.add(
            "G7",
            "PASS",
            "RELEASE_SNAPSHOT_REGISTERED",
            "canonical release selection and actual entrypoint bytes were registered as snapshot:release",
            artifact_id="snapshot:release",
        )

    def validate_release_activity(self) -> None:
        """Separate current release evidence from retained historical artifacts."""

        if not self.release_mode():
            return
        manifest_lifecycle = (self.manifest or {}).get("lifecycle_status")
        if manifest_lifecycle not in {"reviewed", "frozen"}:
            self.add(
                "G7",
                "BLOCK",
                "RELEASE_MANIFEST_INACTIVE",
                f"release manifest lifecycle_status={manifest_lifecycle!r}; it must be reviewed or frozen",
                artifact_id=(self.manifest or {}).get("id"),
            )
        active_problem_ids = {
            artifact_id
            for artifact_id, entry in self.manifest_artifacts.items()
            if entry.get("kind") == "problem_spec" and self.artifact_is_release_active(artifact_id)
        }
        if not active_problem_ids:
            self.add("G1", "BLOCK", "RELEASE_WITHOUT_ACTIVE_PROBLEM", "release requires at least one reviewed or frozen required problem specification")
        for artifact_id, entry in self.manifest_artifacts.items():
            document = self.documents.get(artifact_id)
            if document is None:
                continue
            lifecycle = document.get("lifecycle_status")
            if entry.get("required") is True:
                if lifecycle not in {"reviewed", "frozen"}:
                    self.add(
                        GATE_BY_KIND.get(entry.get("kind"), "G7"),
                        "BLOCK",
                        "RELEASE_REQUIRED_ARTIFACT_INACTIVE",
                        f"{artifact_id} is required but lifecycle_status={lifecycle!r}; release evidence must be reviewed or frozen",
                        artifact_id=artifact_id,
                    )
            else:
                self.add(
                    GATE_BY_KIND.get(entry.get("kind"), "G7"),
                    "NOT_APPLICABLE",
                    "HISTORICAL_ARTIFACT_EXCLUDED",
                    f"{artifact_id} is optional and excluded from release scientific evidence (lifecycle_status={lifecycle!r})",
                    artifact_id=artifact_id,
                )

        for artifact_id, entry in self.manifest_artifacts.items():
            if not self.artifact_is_release_active(artifact_id):
                continue
            dependencies = {
                *entry.get("depends_on", []),
                *self.documents.get(artifact_id, {}).get("depends_on", []),
            }
            inactive = sorted(
                dependency
                for dependency in dependencies
                if dependency in self.manifest_artifacts and not self.artifact_is_release_active(dependency)
            )
            if inactive:
                self.add(
                    GATE_BY_KIND.get(entry.get("kind"), "G7"),
                    "BLOCK",
                    "ACTIVE_ARTIFACT_DEPENDS_ON_INACTIVE",
                    f"{artifact_id} depends on release-inactive artifacts {inactive}",
                    artifact_id=artifact_id,
                )

    def _verify_manifest_file_ref(
        self,
        item: Any,
        *,
        role: str,
        missing_status: str,
        stale_targets: set[str],
        gate: str = "G7",
    ) -> None:
        if not isinstance(item, dict):
            self.add(gate, "BLOCK", f"{role.upper()}_ROW_INVALID", f"manifest {role} row is not a mapping")
            return
        item_id = item.get("id")
        relative = item.get("path")
        expected = item.get("sha256")
        if not isinstance(item_id, str) or not TYPED_ID_RE.fullmatch(item_id):
            self.add(gate, "BLOCK", f"{role.upper()}_ID_INVALID", f"manifest {role} requires a typed id")
            return
        optional_release_file = (
            role == "deliverable"
            and self.release_mode()
            and item.get("required") is not True
        )
        try:
            path = safe_project_path(self.root, relative)
        except (TypeError, ValueError) as exc:
            self.add(gate, "BLOCK", f"{role.upper()}_PATH_UNSAFE", str(exc), path=relative, artifact_id=item_id)
            self.stale_roots.update(stale_targets)
            return
        if not path.is_file():
            status = "NOT_APPLICABLE" if optional_release_file else missing_status
            code = f"OPTIONAL_{role.upper()}_MISSING" if optional_release_file else f"{role.upper()}_MISSING"
            self.add(gate, status, code, f"declared {role} does not exist", path=relative, artifact_id=item_id)
            if not optional_release_file:
                self.stale_roots.update(stale_targets)
            return
        actual = sha256_file(path)
        self.current_hashes[item_id] = actual
        if optional_release_file:
            self.add(gate, "NOT_APPLICABLE", "OPTIONAL_DELIVERABLE_EXCLUDED", "optional deliverable is outside release gate bindings", path=relative, artifact_id=item_id)
            return
        if expected == ZERO_HASH:
            self.add(gate, "STALE", f"{role.upper()}_HASH_PLACEHOLDER", f"{role} hash is still a placeholder", path=relative, artifact_id=item_id)
            self.stale_roots.update(stale_targets)
        elif expected != actual:
            self.add(gate, "STALE", f"{role.upper()}_HASH_MISMATCH", f"expected {expected}, got {actual}", path=relative, artifact_id=item_id)
            self.stale_roots.update(stale_targets)
        else:
            self.add(gate, "PASS", f"{role.upper()}_HASH_MATCH", f"{role} hash matches file bytes", path=relative, artifact_id=item_id)

    def verify_embedded_files(self) -> None:
        """Check every nested object shaped like a {path, sha256} file ref."""

        for artifact_id, document in self.documents.items():
            if not self.artifact_is_release_active(artifact_id):
                continue
            kind = document.get("kind")
            gate = "G0" if kind == "problem_spec" else GATE_BY_KIND.get(kind, "G7")
            for location, file_ref in iter_file_refs(document):
                if not self.release_location_is_active(document, location):
                    continue
                relative = file_ref.get("path")
                expected = file_ref.get("sha256")
                if not isinstance(relative, str) or not isinstance(expected, str):
                    continue
                try:
                    path = safe_project_path(self.root, relative)
                except ValueError as exc:
                    self.add(gate, "BLOCK", "FILE_PATH_UNSAFE", str(exc), path=relative, artifact_id=artifact_id)
                    continue
                if not path.is_file():
                    self.add(gate, "BLOCK", "REFERENCED_FILE_MISSING", f"referenced at {location}", path=relative, artifact_id=artifact_id)
                    self.stale_roots.add(artifact_id)
                    continue
                actual = sha256_file(path)
                if expected == ZERO_HASH:
                    self.add(gate, "STALE", "FILE_HASH_PLACEHOLDER", f"placeholder hash at {location}", path=relative, artifact_id=artifact_id)
                    self.stale_roots.add(artifact_id)
                elif expected != actual:
                    self.add(gate, "STALE", "FILE_HASH_MISMATCH", f"expected {expected}, got {actual}", path=relative, artifact_id=artifact_id)
                    self.stale_roots.add(artifact_id)
                else:
                    self.add(gate, "PASS", "FILE_HASH_MATCH", f"referenced file matches at {location}", path=relative, artifact_id=artifact_id)

    def validate_ids_and_refs(self) -> None:
        """Build a nested-ID registry and reject dangling cross-file refs."""

        definitions: dict[str, tuple[str, str, str]] = {}
        manifest_id = self.manifest.get("id") if self.manifest else None
        if isinstance(manifest_id, str):
            definitions[manifest_id] = (manifest_id, "<root>/id", "manifest")

        for artifact_id, document in self.documents.items():
            for location, value in iter_defined_ids(document):
                if value in definitions:
                    first_artifact, first_location, _first_kind = definitions[value]
                    self.add(
                        GATE_BY_KIND.get(document.get("kind"), "G7"),
                        "BLOCK",
                        "DUPLICATE_ID",
                        f"{value} already defined in {first_artifact} at {first_location}; repeated at {location}",
                        artifact_id=artifact_id,
                    )
                else:
                    definitions[value] = (
                        artifact_id,
                        location,
                        definition_semantic_kind(document.get("kind"), location),
                    )

        # Manifest-owned files are first-class review evidence even though
        # they are not contract artifacts.  Their typed IDs participate in
        # reference resolution and their current bytes live in current_hashes.
        if self.manifest:
            virtual_definitions: list[tuple[str, str]] = []
            virtual_definitions.extend(
                (item.get("id"), item.get("kind", "manifest_artifact"))
                for item in self.manifest.get("artifacts", [])
                if item.get("id") not in definitions
            )
            virtual_definitions.extend((item.get("id"), "environment_file") for item in self.manifest.get("environment_files", []))
            virtual_definitions.extend((item.get("id"), "deliverable") for item in self.manifest.get("deliverables", []))
            virtual_definitions.extend((f"entrypoint:{name}", "entrypoint") for name in self.manifest.get("entrypoints", {}))
            if self.release_snapshot_digest is not None:
                virtual_definitions.append(("snapshot:release", "release_snapshot"))
            profile = self.manifest.get("competition_profile", {})
            if isinstance(profile, dict) and profile.get("enabled") is True:
                virtual_definitions.append((profile.get("id"), "competition_profile"))
            for virtual_id, virtual_kind in virtual_definitions:
                if not isinstance(virtual_id, str):
                    continue
                if virtual_id in definitions:
                    first_artifact, first_location, _first_kind = definitions[virtual_id]
                    self.add("G7", "BLOCK", "DUPLICATE_ID", f"{virtual_id} already defined in {first_artifact} at {first_location}")
                else:
                    owner = virtual_id if virtual_id in self.manifest_artifacts else "manifest:virtual"
                    definitions[virtual_id] = (owner, f"manifest/{virtual_kind}", virtual_kind)

        self.id_definitions = definitions

        for artifact_id, document in self.documents.items():
            if not self.artifact_is_release_active(artifact_id):
                continue
            gate = GATE_BY_KIND.get(document.get("kind"), "G7")
            # A structured reference such as evidence_refs[*].ref is found by
            # both its container rule and the generic ``ref`` rule during the
            # recursive walk.  Report each concrete location only once so a
            # valid contract does not produce duplicated PASS findings.
            seen_references: set[tuple[str, str]] = set()
            for location, reference in iter_references(document):
                if not self.release_reference_is_active(document, location, reference):
                    continue
                reference_key = (location, reference)
                if reference_key in seen_references:
                    continue
                seen_references.add(reference_key)
                if reference not in definitions:
                    self.add(gate, "BLOCK", "DANGLING_REFERENCE", f"{reference} at {location} has no local definition", artifact_id=artifact_id)
                else:
                    target_owner = definitions[reference][0]
                    target_kind = definitions[reference][2]
                    allowed_kinds = expected_reference_kinds(document.get("kind"), location)
                    if allowed_kinds is not None and target_kind not in allowed_kinds:
                        self.add(
                            gate,
                            "BLOCK",
                            "REFERENCE_KIND_MISMATCH",
                            f"{reference} at {location} resolves to {target_kind}, expected one of {sorted(allowed_kinds)}",
                            artifact_id=artifact_id,
                        )
                    elif target_owner in self.manifest_artifacts and not self.artifact_is_release_active(target_owner):
                        self.add(
                            gate,
                            "BLOCK",
                            "ACTIVE_REFERENCE_TO_INACTIVE_ARTIFACT",
                            f"{reference} at {location} is owned by release-inactive {target_owner}",
                            artifact_id=artifact_id,
                        )
                    else:
                        self.add(gate, "PASS", "REFERENCE_RESOLVED", f"{reference} at {location} resolves to {target_kind}", artifact_id=artifact_id)

        # The manifest root is a real contract document.  Its own depends_on
        # and provenance.source_refs must not bypass the same local reference
        # checks applied to artifact contracts.  Artifact-row dependencies are
        # intentionally included here as a second, typed check in addition to
        # the explicit declaration check below.
        if self.manifest:
            seen_manifest_refs: set[tuple[str, str]] = set()
            for location, reference in iter_references(self.manifest):
                artifact_match = re.search(r"(?:^|/)artifacts/(\d+)(?:/|$)", location)
                if artifact_match is not None:
                    index = int(artifact_match.group(1))
                    rows = self.manifest.get("artifacts", [])
                    if self.release_mode() and index < len(rows) and rows[index].get("required") is not True:
                        continue
                key = (location, reference)
                if key in seen_manifest_refs:
                    continue
                seen_manifest_refs.add(key)
                if reference not in definitions:
                    self.add("G7", "BLOCK", "DANGLING_REFERENCE", f"{reference} at {location} has no local definition", artifact_id=manifest_id)
                    continue
                target_kind = definitions[reference][2]
                allowed_kinds = expected_reference_kinds("manifest", location)
                if allowed_kinds is not None and target_kind not in allowed_kinds:
                    self.add(
                        "G7",
                        "BLOCK",
                        "REFERENCE_KIND_MISMATCH",
                        f"{reference} at {location} resolves to {target_kind}, expected one of {sorted(allowed_kinds)}",
                        artifact_id=manifest_id,
                    )
                else:
                    self.add("G7", "PASS", "REFERENCE_RESOLVED", f"{reference} at {location} resolves to {target_kind}", artifact_id=manifest_id)

        # Manifest dependency refs are checked even if an artifact file failed
        # to parse, because these refs define stale propagation and release order.
        declared = set(self.manifest_artifacts)
        for artifact_id, entry in self.manifest_artifacts.items():
            if self.release_mode() and not self.artifact_is_release_active(artifact_id):
                continue
            for dependency in entry.get("depends_on", []):
                if dependency not in declared:
                    self.add("G7", "BLOCK", "DANGLING_MANIFEST_DEPENDENCY", f"{artifact_id} depends on undeclared {dependency}")

    def validate_dag_and_propagate_stale(self) -> None:
        graph_nodes = {
            artifact_id
            for artifact_id in self.manifest_artifacts
            if not self.release_mode() or self.artifact_is_release_active(artifact_id)
        }
        graph = {
            artifact_id: sorted(
                {
                    *entry.get("depends_on", []),
                    *self.documents.get(artifact_id, {}).get("depends_on", []),
                }.intersection(graph_nodes)
            )
            for artifact_id, entry in self.manifest_artifacts.items()
            if artifact_id in graph_nodes
        }
        state: dict[str, int] = {node: 0 for node in graph}
        stack: list[str] = []

        def visit(node: str) -> None:
            state[node] = 1
            stack.append(node)
            for dependency in graph[node]:
                if state[dependency] == 0:
                    visit(dependency)
                elif state[dependency] == 1:
                    cycle_start = stack.index(dependency)
                    cycle = stack[cycle_start:] + [dependency]
                    message = " -> ".join(cycle)
                    if not any(
                        finding["code"] == "DEPENDENCY_CYCLE"
                        and finding["message"] == message
                        for finding in self.findings
                    ):
                        self.add("G7", "BLOCK", "DEPENDENCY_CYCLE", message)
            stack.pop()
            state[node] = 2

        for node in graph:
            if state[node] == 0:
                visit(node)

        reverse: dict[str, set[str]] = defaultdict(set)
        for child, parents in graph.items():
            for parent in parents:
                reverse[parent].add(child)
        active_stale_roots = self.stale_roots.intersection(graph_nodes)
        queue: deque[str] = deque(active_stale_roots)
        affected = set(active_stale_roots)
        while queue:
            changed = queue.popleft()
            for dependent in reverse.get(changed, set()):
                if dependent not in affected:
                    affected.add(dependent)
                    queue.append(dependent)
                    if not any(
                        finding["code"] == "UPSTREAM_STALE"
                        and finding.get("artifact_id") == dependent
                        for finding in self.findings
                    ):
                        self.add(
                            GATE_BY_KIND.get(self.manifest_artifacts[dependent].get("kind"), "G7"),
                            "STALE",
                            "UPSTREAM_STALE",
                            f"{dependent} depends transitively on changed or missing {changed}",
                            artifact_id=dependent,
                        )
        if not any(finding["code"] == "DEPENDENCY_CYCLE" for finding in self.findings):
            if not any(finding["code"] == "DEPENDENCY_DAG" for finding in self.findings):
                self.add("G7", "PASS", "DEPENDENCY_DAG", "manifest dependency graph is acyclic")

    def validate_scientific_invariants(self) -> None:
        """Validate executable evidence invariants without proving mathematics.

        The checks here deliberately distinguish a historical failed run from
        an invalid evidentiary result: failed history may remain in a release,
        but only a successful, current, internally consistent and
        acceptance-passing result may support a final claim.
        """

        active_documents = [
            document
            for artifact_id, document in self.documents.items()
            if self.artifact_is_release_active(artifact_id)
        ]
        problems = [doc for doc in active_documents if doc.get("kind") == "problem_spec"]
        models = [doc for doc in active_documents if doc.get("kind") == "model_spec"]
        promotions = [doc for doc in active_documents if doc.get("kind") == "model_promotion"]
        experiments = [doc for doc in active_documents if doc.get("kind") == "experiment"]
        results = [doc for doc in active_documents if doc.get("kind") == "results"]
        claims_docs = [doc for doc in active_documents if doc.get("kind") == "claims"]
        figures_docs = [doc for doc in active_documents if doc.get("kind") == "figures"]

        problems_by_id = {document.get("id"): document for document in problems}
        models_by_id = {document.get("id"): document for document in models}
        experiments_by_id = {document.get("id"): document for document in experiments}
        results_by_id = {document.get("id"): document for document in results}
        quantitative_final_claims_by_model: dict[str, set[str]] = defaultdict(set)
        for registry in claims_docs:
            for claim in registry.get("claims", []):
                claim_id = claim.get("id")
                if (
                    claim.get("publication_status") != "final"
                    or not claim.get("numeric_assertions")
                    or not isinstance(claim_id, str)
                ):
                    continue
                for evidence in claim.get("evidence_refs", []):
                    if not isinstance(evidence, dict):
                        continue
                    evidence_ref = evidence.get("ref")
                    if evidence_ref in models_by_id:
                        quantitative_final_claims_by_model[str(evidence_ref)].add(claim_id)
                        continue
                    result = results_by_id.get(evidence_ref)
                    if not isinstance(result, dict):
                        continue
                    experiment = experiments_by_id.get(result.get("experiment_ref"))
                    model_ref = experiment.get("model_ref") if isinstance(experiment, dict) else None
                    if model_ref in models_by_id:
                        quantitative_final_claims_by_model[str(model_ref)].add(claim_id)
        self.validate_model_promotions(promotions, models_by_id, experiments_by_id, results_by_id)
        question_owner_by_id: dict[str, str] = {}
        questions_by_id: dict[str, dict[str, Any]] = {}
        assumption_owner_by_id: dict[str, str] = {}
        constraints_by_id: dict[str, dict[str, Any]] = {}
        constraint_owner_by_id: dict[str, str] = {}
        deliverables_by_id: dict[str, dict[str, Any]] = {}
        deliverable_owner_by_id: dict[str, str] = {}
        for problem in problems:
            problem_id = str(problem.get("id"))
            for question in problem.get("questions", []):
                question_id = question.get("id")
                if isinstance(question_id, str):
                    question_owner_by_id[question_id] = problem_id
                    questions_by_id[question_id] = question
            for assumption in problem.get("assumptions", []):
                assumption_id = assumption.get("id")
                if isinstance(assumption_id, str):
                    assumption_owner_by_id[assumption_id] = problem_id
            for constraint in problem.get("constraints", []):
                constraint_id = constraint.get("id")
                if isinstance(constraint_id, str):
                    constraints_by_id[constraint_id] = constraint
                    constraint_owner_by_id[constraint_id] = problem_id
            for deliverable in problem.get("deliverables", []):
                deliverable_id = deliverable.get("id")
                if isinstance(deliverable_id, str):
                    deliverables_by_id[deliverable_id] = deliverable
                    deliverable_owner_by_id[deliverable_id] = problem_id
        # Input roles are semantic declarations, not filename guesses.  The
        # schema records the declaration; these checks enforce which roles may
        # flow into a model and preserve one canonical registration per byte
        # sequence so old outputs cannot masquerade as observations.
        data_assets_by_id: dict[str, dict[str, Any]] = {}
        data_asset_owner_by_id: dict[str, str] = {}
        registered_asset_hashes: dict[str, list[str]] = defaultdict(list)
        for problem in problems:
            problem_id = str(problem.get("id"))
            for constraint in problem.get("constraints", []):
                if any(question_owner_by_id.get(str(ref)) != problem_id for ref in constraint.get("question_refs", [])):
                    self.add(
                        "G1",
                        "BLOCK",
                        "CONSTRAINT_QUESTION_SCOPE_INVALID",
                        f"{constraint.get('id')} references a question outside {problem_id}",
                        artifact_id=problem_id,
                    )
            for deliverable in problem.get("deliverables", []):
                if any(question_owner_by_id.get(str(ref)) != problem_id for ref in deliverable.get("question_refs", [])):
                    self.add(
                        "G1",
                        "BLOCK",
                        "DELIVERABLE_QUESTION_SCOPE_INVALID",
                        f"{deliverable.get('id')} references a question outside {problem_id}",
                        artifact_id=problem_id,
                    )
        for problem in problems:
            for asset in problem.get("data_assets", []):
                asset_id = asset.get("id")
                if isinstance(asset_id, str):
                    if asset_id in data_assets_by_id:
                        self.add(
                            "G0",
                            "BLOCK",
                            "DATA_ASSET_ID_DUPLICATE",
                            f"{asset_id} is registered by more than one problem specification",
                            artifact_id=problem.get("id"),
                        )
                    else:
                        data_assets_by_id[asset_id] = asset
                        data_asset_owner_by_id[asset_id] = str(problem.get("id"))
                role = asset.get("role")
                usable = asset.get("usable_for_modeling") is True
                if role != "generated_intermediate" and asset.get("producer_ref") is not None:
                    self.add(
                        "G0",
                        "BLOCK",
                        "NONGENERATED_INPUT_HAS_PRODUCER",
                        f"{asset_id} is not a generated intermediate, so producer_ref must be null or absent",
                        artifact_id=problem.get("id"),
                    )
                if any(
                    question_owner_by_id.get(str(ref)) != str(problem.get("id"))
                    for ref in asset.get("question_refs", [])
                ):
                    self.add(
                        "G0",
                        "BLOCK",
                        "DATA_ASSET_QUESTION_SCOPE_INVALID",
                        f"{asset_id} references a question outside {problem.get('id')}",
                        artifact_id=problem.get("id"),
                    )
                if usable and role not in {"raw_data", "generated_intermediate"}:
                    self.add(
                        "G0",
                        "BLOCK",
                        "INPUT_ROLE_NOT_MODEL_DATA",
                        f"{asset_id} is role={role!r} and cannot be a modeling input",
                        artifact_id=problem.get("id"),
                    )
                if usable and asset.get("classification_basis") == "filename_heuristic":
                    self.add(
                        "G0",
                        "BLOCK",
                        "HEURISTIC_INPUT_UNCONFIRMED",
                        f"{asset_id} is usable only because of a filename heuristic; inspect content and reclassify it",
                        artifact_id=problem.get("id"),
                    )
                if usable and role == "raw_data" and asset.get("immutable_raw") is not True:
                    self.add(
                        "G0",
                        "BLOCK",
                        "RAW_INPUT_NOT_IMMUTABLE",
                        f"{asset_id} is accepted raw data but immutable_raw is not true",
                        artifact_id=problem.get("id"),
                    )
                file_ref = asset.get("file")
                if usable and not isinstance(file_ref, dict):
                    self.add(
                        "G0",
                        "BLOCK",
                        "USABLE_INPUT_NOT_MATERIALIZED",
                        f"{asset_id} is approved for modeling but has no hashed local materialization",
                        artifact_id=problem.get("id"),
                    )
                if usable and role == "generated_intermediate":
                    producer_ref = asset.get("producer_ref")
                    producer = self.documents.get(producer_ref, {})
                    if producer.get("kind") != "results":
                        self.add(
                            "G0",
                            "BLOCK",
                            "GENERATED_INPUT_PRODUCER_INVALID",
                            f"{asset_id} must identify the producing results artifact; got {producer_ref!r}",
                            artifact_id=problem.get("id"),
                        )
                    else:
                        producer_experiment = self.documents.get(producer.get("experiment_ref"), {})
                        producer_model = self.documents.get(producer_experiment.get("model_ref"), {})
                        if producer_model.get("problem_ref") != problem.get("id"):
                            self.add(
                                "G0",
                                "BLOCK",
                                "GENERATED_INPUT_CROSS_PROBLEM",
                                f"{asset_id} producer {producer_ref} belongs to {producer_model.get('problem_ref')}, not {problem.get('id')}",
                                artifact_id=problem.get("id"),
                            )
                    if producer.get("kind") == "results" and isinstance(file_ref, dict):
                        matching_outputs = [
                            output
                            for output in producer.get("outputs", [])
                            if isinstance(output.get("file"), dict)
                            and output.get("file", {}).get("path") == file_ref.get("path")
                            and output.get("file", {}).get("sha256") == file_ref.get("sha256")
                        ]
                        if len(matching_outputs) != 1:
                            self.add(
                                "G0",
                                "BLOCK",
                                "GENERATED_INPUT_OUTPUT_MISMATCH",
                                f"{asset_id} local bytes must match exactly one output of producer {producer_ref}",
                                artifact_id=problem.get("id"),
                            )
                if isinstance(file_ref, dict):
                    digest = file_ref.get("sha256")
                    if isinstance(digest, str) and digest != ZERO_HASH:
                        registered_asset_hashes[digest].append(str(asset_id))

        for digest, asset_ids in registered_asset_hashes.items():
            if len(asset_ids) > 1:
                self.add(
                    "G0",
                    "BLOCK",
                    "DUPLICATE_INPUT_BYTES",
                    f"the same bytes are registered as multiple data assets: {sorted(asset_ids)} ({digest})",
                )

        self.validate_proof_artifacts(claims_docs)
        proof_backed_deliverables = {
            deliverable_ref
            for registry in claims_docs
            for claim in registry.get("claims", [])
            if claim.get("id") in self.valid_final_proof_claim_ids
            for deliverable_ref in claim.get("deliverable_refs", [])
        }
        addressed = {
            reference
            for model in models
            if model.get("id") in self.effective_primary_model_ids
            for reference in model.get("addresses", [])
        }
        for problem in problems:
            for ambiguity in problem.get("ambiguities", []):
                if ambiguity.get("severity") == "high" and ambiguity.get("status") == "open":
                    self.add("G1", "BLOCK", "HIGH_AMBIGUITY_OPEN", ambiguity.get("text", "high ambiguity remains open"), artifact_id=problem.get("id"))
            for question in problem.get("questions", []):
                question_id = question.get("id")
                for deliverable_ref in question.get("required_outputs", []):
                    deliverable = deliverables_by_id.get(deliverable_ref)
                    if (
                        deliverable is None
                        or deliverable_owner_by_id.get(str(deliverable_ref)) != problem.get("id")
                        or question_id not in deliverable.get("question_refs", [])
                    ):
                        self.add(
                            "G1",
                            "BLOCK",
                            "QUESTION_DELIVERABLE_INVALID",
                            f"{question_id} required output {deliverable_ref} is missing, cross-problem, or not scoped to the question",
                            artifact_id=problem.get("id"),
                        )
                if question_id not in addressed and not proof_backed_deliverables.intersection(question.get("required_outputs", [])):
                    self.add("G2", "BLOCK", "QUESTION_NOT_MODELED", f"no selected primary model addresses {question_id}", artifact_id=problem.get("id"))
                elif question_id not in addressed:
                    self.add("G2", "PASS", "QUESTION_COVERED_BY_PROOF", f"{question_id} is covered by a final hashed theoretical proof", artifact_id=problem.get("id"))
                else:
                    self.add("G2", "PASS", "QUESTION_MODELED", f"at least one selected primary model addresses {question_id}", artifact_id=problem.get("id"))

        selected_baseline_ids = {
            baseline_ref
            for primary in models
            if primary.get("id") in self.effective_primary_model_ids
            and primary.get("method_selection", {}).get("baseline_policy", {}).get("status") == "required"
            for baseline_ref in primary.get("method_selection", {}).get("baseline_policy", {}).get("model_refs", [])
        }
        for model in models:
            symbol_ids = {symbol.get("id") for symbol in model.get("symbols", [])}
            model_id = model.get("id")
            model_problem_ref = model.get("problem_ref")
            model_addresses = set(model.get("addresses", []))
            if model_problem_ref not in model.get("depends_on", []):
                self.add(
                    "G2",
                    "BLOCK",
                    "MODEL_PROBLEM_DEPENDENCY_MISSING",
                    f"{model_id} must include problem_ref {model_problem_ref} in depends_on",
                    artifact_id=model_id,
                )
            if model_problem_ref not in problems_by_id:
                self.add(
                    "G2",
                    "BLOCK",
                    "MODEL_PROBLEM_MISSING",
                    f"{model_id} problem_ref does not resolve to a problem specification",
                    artifact_id=model_id,
                )
            for question_ref in model_addresses:
                if question_owner_by_id.get(str(question_ref)) != model_problem_ref:
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_QUESTION_WRONG_PROBLEM",
                        f"{model_id} addresses {question_ref}, which is not owned by {model_problem_ref}",
                        artifact_id=model_id,
                    )
            for assumption_ref in model.get("assumption_refs", []):
                if assumption_owner_by_id.get(str(assumption_ref)) != model_problem_ref:
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_ASSUMPTION_WRONG_PROBLEM",
                        f"{model_id} uses {assumption_ref}, which is not owned by {model_problem_ref}",
                        artifact_id=model_id,
                    )
            for constraint_ref in model.get("constraint_refs", []):
                if constraint_owner_by_id.get(str(constraint_ref)) != model_problem_ref:
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_CONSTRAINT_WRONG_PROBLEM",
                        f"{model_id} uses {constraint_ref}, which is not owned by {model_problem_ref}",
                        artifact_id=model_id,
                    )
            relevant_hard_constraints = {
                constraint_id
                for constraint_id, constraint in constraints_by_id.items()
                if constraint_owner_by_id.get(constraint_id) == model_problem_ref
                and constraint.get("hard") is True
                and set(constraint.get("question_refs", [])).intersection(model_addresses)
            }
            constraint_coverage_required = (
                model_id in self.effective_primary_model_ids
            ) or (
                model_id in selected_baseline_ids
                and model.get("role") == "baseline"
                and model.get("method_selection", {}).get("decision") == "selected"
            )
            if constraint_coverage_required:
                missing_hard_constraints = relevant_hard_constraints.difference(model.get("constraint_refs", []))
                if missing_hard_constraints:
                    self.add(
                        "G2",
                        "BLOCK",
                        "HARD_CONSTRAINT_NOT_MODELED",
                        f"{model_id} omits hard problem constraints {sorted(missing_hard_constraints)}",
                        artifact_id=model_id,
                    )
                if relevant_hard_constraints and not model.get("formulation", {}).get("constraints"):
                    self.add(
                        "G2",
                        "BLOCK",
                        "HARD_CONSTRAINT_FORMULATION_MISSING",
                        f"{model_id} cites hard constraints but has no mathematical constraint formulation",
                        artifact_id=model_id,
                    )
                formulated_hard_constraints = {
                    constraint_ref
                    for formula in model.get("formulation", {}).get("constraints", [])
                    for constraint_ref in formula.get("source_constraint_refs", [])
                }
                missing_formulations = relevant_hard_constraints.difference(formulated_hard_constraints)
                if missing_formulations:
                    self.add(
                        "G2",
                        "BLOCK",
                        "HARD_CONSTRAINT_FORMULA_BINDING_MISSING",
                        f"{model_id} lists but does not bind formulas to hard constraints {sorted(missing_formulations)}",
                        artifact_id=model_id,
                    )
            quantitative_claim_ids = quantitative_final_claims_by_model.get(str(model_id), set())
            formulation = model.get("formulation", {})
            if quantitative_claim_ids and not any(
                formulation.get(section) for section in ("equations", "objectives", "constraints")
            ):
                code = model_evidence_consistency_code(
                    model, "EMPTY_FORMULATION_SUPPORTS_CLAIM"
                )
                migration_note = (
                    f" schema_version={model.get('schema_version')!r} predates the 2.4.0 "
                    "model/claim consistency contract and must be migrated;"
                    if code.endswith("_MIGRATION_REQUIRED")
                    else ""
                )
                self.add(
                    "G2",
                    "BLOCK",
                    code,
                    (
                        f"{model_id}{migration_note} equations, objectives, and constraints are all "
                        f"empty while supporting final quantitative claims {sorted(quantitative_claim_ids)}; "
                        "declare at least one formulation item"
                    ),
                    artifact_id=model_id,
                )
            for section in ("equations", "objectives", "constraints"):
                for formula in model.get("formulation", {}).get(section, []):
                    for symbol_ref in [*formula.get("defines", []), *formula.get("uses", [])]:
                        if symbol_ref not in symbol_ids:
                            self.add("G2", "BLOCK", "FORMULA_SYMBOL_UNDECLARED", f"{formula.get('id')} refers to undeclared {symbol_ref}", artifact_id=model.get("id"))
                    source_constraint_refs = set(formula.get("source_constraint_refs", []))
                    if section != "constraints" and source_constraint_refs:
                        self.add("G2", "BLOCK", "NONCONSTRAINT_FORMULA_HAS_CONSTRAINT_SOURCE", f"{formula.get('id')} is not a constraint formula but cites source constraints", artifact_id=model_id)
                    invalid_source_refs = {
                        reference
                        for reference in source_constraint_refs
                        if reference not in model.get("constraint_refs", [])
                        or constraint_owner_by_id.get(str(reference)) != model_problem_ref
                    }
                    if invalid_source_refs:
                        self.add("G2", "BLOCK", "FORMULA_CONSTRAINT_SOURCE_INVALID", f"{formula.get('id')} cites unbound or cross-problem constraints {sorted(invalid_source_refs)}", artifact_id=model_id)
            entrypoint = model.get("algorithm", {}).get("entrypoint")
            if isinstance(entrypoint, str):
                try:
                    code_path = safe_project_path(self.root, entrypoint)
                    if not code_path.is_file():
                        self.add("G2", "BLOCK", "MODEL_ENTRYPOINT_MISSING", "algorithm entrypoint does not exist", path=entrypoint, artifact_id=model.get("id"))
                except ValueError as exc:
                    self.add("G2", "BLOCK", "MODEL_ENTRYPOINT_UNSAFE", str(exc), path=entrypoint, artifact_id=model.get("id"))

            for binding in model.get("data_bindings", []):
                data_ref = binding.get("data_ref")
                asset = data_assets_by_id.get(data_ref)
                binding_questions = set(binding.get("question_refs", []))
                if binding.get("symbol_ref") not in symbol_ids:
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_BINDING_SYMBOL_UNDECLARED",
                        f"{model_id} binds data to undeclared symbol {binding.get('symbol_ref')}",
                        artifact_id=model_id,
                    )
                if asset is None:
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_DATA_ASSET_MISSING",
                        f"{model_id} data_ref {data_ref!r} is not a registered data asset",
                        artifact_id=model_id,
                    )
                    continue
                if data_asset_owner_by_id.get(str(data_ref)) != model_problem_ref:
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_DATA_ASSET_WRONG_PROBLEM",
                        f"{model_id} binds {data_ref}, which belongs to another problem specification",
                        artifact_id=model_id,
                    )
                if asset.get("usable_for_modeling") is not True:
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_BINDS_EXCLUDED_INPUT",
                        f"{model.get('id')} binds {data_ref}, which is not approved for modeling",
                        artifact_id=model.get("id"),
                    )
                if not binding_questions or not binding_questions.issubset(model_addresses):
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_BINDING_QUESTION_SCOPE_INVALID",
                        f"{model_id}/{data_ref} binding questions {sorted(binding_questions)} must be a non-empty subset of model addresses",
                        artifact_id=model_id,
                    )
                if not binding_questions.issubset(set(asset.get("question_refs", []))):
                    self.add(
                        "G2",
                        "BLOCK",
                        "MODEL_DATA_SCOPE_MISMATCH",
                        f"{model_id}/{data_ref} binding covers {sorted(binding_questions)} but the asset is approved only for {sorted(asset.get('question_refs', []))}",
                        artifact_id=model_id,
                    )
                if asset.get("role") == "generated_intermediate" and asset.get("producer_ref") not in model.get("depends_on", []):
                    self.add(
                        "G2",
                        "BLOCK",
                        "GENERATED_INPUT_DEPENDENCY_MISSING",
                        f"{model_id} must depend on producer {asset.get('producer_ref')} for generated input {data_ref}",
                        artifact_id=model_id,
                    )

            selection = model.get("method_selection", {})
            if model.get("role") == "primary" and selection.get("decision") != "selected":
                self.add(
                    "G2",
                    "BLOCK",
                    "PRIMARY_MODEL_NOT_SELECTED",
                    f"{model.get('id')} is primary but its method decision is {selection.get('decision')!r}",
                    artifact_id=model.get("id"),
                )
            baseline_policy = selection.get("baseline_policy", {})
            baseline_refs = baseline_policy.get("model_refs", [])
            if baseline_policy.get("status") == "waived":
                self.add(
                    "G2",
                    "PASS",
                    "BASELINE_WAIVER_DECLARED",
                    f"{model.get('id')} records an explicit baseline waiver for G2 human review",
                    artifact_id=model.get("id"),
                )
            for baseline_ref in baseline_refs:
                baseline = models_by_id.get(baseline_ref)
                # The dependency edge is essential: without it a modified
                # baseline model would leave the primary result looking fresh.
                if baseline_ref not in model.get("depends_on", []):
                    self.add(
                        "G2",
                        "BLOCK",
                        "BASELINE_DEPENDENCY_MISSING",
                        f"{model.get('id')} must include selected baseline {baseline_ref} in depends_on so freshness propagates",
                        artifact_id=model.get("id"),
                    )
                if baseline_ref == model.get("id"):
                    self.add("G2", "BLOCK", "BASELINE_SELF_REFERENCE", f"{model.get('id')} cannot be its own baseline", artifact_id=model.get("id"))
                elif baseline is None:
                    self.add("G2", "BLOCK", "BASELINE_MODEL_MISSING", f"baseline {baseline_ref} does not resolve", artifact_id=model.get("id"))
                elif baseline.get("role") != "baseline":
                    self.add("G2", "BLOCK", "BASELINE_ROLE_INVALID", f"{baseline_ref} is not role=baseline", artifact_id=model.get("id"))
                elif baseline.get("method_selection", {}).get("decision") != "selected":
                    self.add("G2", "BLOCK", "BASELINE_METHOD_NOT_SELECTED", f"{baseline_ref} is not an actively selected method", artifact_id=model.get("id"))
                elif not set(model.get("addresses", [])).issubset(baseline.get("addresses", [])):
                    self.add("G2", "BLOCK", "BASELINE_TASK_MISMATCH", f"{baseline_ref} does not cover every question addressed by {model.get('id')}", artifact_id=model.get("id"))

            for fallback_ref in model.get("fallback_models", []):
                fallback = models_by_id.get(fallback_ref)
                if fallback_ref not in model.get("depends_on", []):
                    self.add(
                        "G2",
                        "BLOCK",
                        "FALLBACK_DEPENDENCY_MISSING",
                        f"{model_id} must include fallback {fallback_ref} in depends_on",
                        artifact_id=model_id,
                    )
                if fallback is None:
                    self.add("G2", "BLOCK", "FALLBACK_MODEL_MISSING", f"fallback {fallback_ref} does not resolve", artifact_id=model_id)
                elif fallback.get("role") != "fallback":
                    self.add("G2", "BLOCK", "FALLBACK_ROLE_INVALID", f"{fallback_ref} is not role=fallback", artifact_id=model_id)
                elif fallback.get("method_selection", {}).get("decision") != "conditional":
                    self.add("G2", "BLOCK", "FALLBACK_METHOD_NOT_CONDITIONAL", f"{fallback_ref} must remain conditional until activated", artifact_id=model_id)
                elif fallback.get("problem_ref") != model_problem_ref:
                    self.add("G2", "BLOCK", "FALLBACK_PROBLEM_MISMATCH", f"{fallback_ref} belongs to another problem", artifact_id=model_id)
                elif not model_addresses.issubset(set(fallback.get("addresses", []))):
                    self.add("G2", "BLOCK", "FALLBACK_TASK_MISMATCH", f"{fallback_ref} does not cover every question addressed by {model_id}", artifact_id=model_id)

            checks = model.get("validation_plan", {}).get("checks", [])
            declared_check_types = {check.get("check_type") for check in checks}
            validation_facets = effective_validation_facets(model)
            required_check_types: set[str] = set()
            for validation_facet in validation_facets:
                required_check_types.update(VALIDATION_COVERAGE_BY_FAMILY[validation_facet])
            for question_ref in model_addresses:
                required_check_types.update(
                    VALIDATION_COVERAGE_BY_TASK.get(questions_by_id.get(str(question_ref), {}).get("task_type"), set())
                )
            if any(model.get("formulation", {}).get(section, []) for section in ("equations", "objectives", "constraints")):
                required_check_types.update(FORMULA_VALIDATION_CHECKS)
            missing_check_types = required_check_types.difference(declared_check_types)
            if (
                "objective_reconciliation" in missing_check_types
                and "optimization" in validation_facets
                and contract_version_tuple(model) < OBJECTIVE_RECONCILIATION_INTRODUCED_VERSION
            ):
                # Contracts before 2.2.0 remain schema-readable.  Surface the
                # newly required optimization check as a precise semantic
                # migration finding instead of hiding it in the generic
                # coverage message or rejecting the old document at parse time.
                missing_check_types.remove("objective_reconciliation")
                self.add(
                    "G2",
                    "BLOCK",
                    "OBJECTIVE_RECONCILIATION_REQUIRED",
                    (
                        f"{model_id} schema_version={model.get('schema_version')!r} has optimization "
                        "validation scope but predates the objective_reconciliation requirement; "
                        "declare the check and its fixed-decision best-response procedure"
                    ),
                    artifact_id=model_id,
                )
            if (
                "holdout_leakage" in missing_check_types
                and "optimization" in validation_facets
                and contract_version_tuple(model) < SCENARIO_SETS_INTRODUCED_VERSION
            ):
                missing_check_types.remove("holdout_leakage")
                self.add(
                    "G2",
                    "BLOCK",
                    "SCENARIO_HOLDOUT_CHECK_REQUIRED",
                    (
                        f"{model_id} schema_version={model.get('schema_version')!r} has optimization "
                        "validation scope but predates the scenario selection/holdout requirement; "
                        "declare holdout_leakage as actionable for scenario-based optimization or "
                        "not_applicable with a deterministic rationale"
                    ),
                    artifact_id=model_id,
                )
            if missing_check_types:
                self.add(
                    "G2",
                    "BLOCK",
                    "MODEL_VALIDATION_COVERAGE_UNDECLARED",
                    f"{model.get('id')} has not considered required family checks: {sorted(missing_check_types)}",
                    artifact_id=model.get("id"),
                )
            if model_id in self.effective_primary_model_ids and not any(
                check.get("applicability") != "not_applicable" for check in checks
            ):
                self.add(
                    "G2",
                    "BLOCK",
                    "PRIMARY_MODEL_WITHOUT_ACTIONABLE_CHECK",
                    f"{model.get('id')} has no applicable validation check",
                    artifact_id=model.get("id"),
                )
            for check in checks:
                if check.get("applicability") == "conditional" and not isinstance(check.get("activation_condition"), str):
                    self.add("G2", "BLOCK", "CONDITIONAL_CHECK_WITHOUT_ACTIVATION", f"{check.get('id')} lacks an explicit activation condition", artifact_id=model.get("id"))
                if check.get("applicability") != "conditional" and check.get("activation_condition") is not None:
                    self.add("G2", "BLOCK", "NONCONDITIONAL_CHECK_HAS_ACTIVATION", f"{check.get('id')} declares an activation condition but is not conditional", artifact_id=model.get("id"))
                if check.get("applicability") == "not_applicable" and check.get("threshold") is not None:
                    self.add("G2", "BLOCK", "NOT_APPLICABLE_CHECK_HAS_THRESHOLD", f"{check.get('id')} is not applicable but declares a threshold", artifact_id=model.get("id"))
                if check.get("criticality") == "blocking" and check.get("failure_response") == "report_only":
                    self.add("G2", "BLOCK", "BLOCKING_CHECK_REPORT_ONLY", f"{check.get('id')} is blocking but its failure response is report_only", artifact_id=model.get("id"))
            fallback_rules = model.get("fallback_rules", [])
            fallback_rule_refs = [rule.get("model_ref") for rule in fallback_rules]
            if set(fallback_rule_refs) != set(model.get("fallback_models", [])) or len(fallback_rule_refs) != len(set(fallback_rule_refs)):
                self.add(
                    "G2",
                    "BLOCK",
                    "FALLBACK_RULE_COVERAGE_MISMATCH",
                    f"{model_id} must declare exactly one fallback rule for each fallback model",
                    artifact_id=model_id,
                )
            checks_by_id = {check.get("id"): check for check in checks}
            for rule in fallback_rules:
                trigger = checks_by_id.get(rule.get("trigger_check_ref"))
                if trigger is None:
                    self.add(
                        "G2",
                        "BLOCK",
                        "FALLBACK_TRIGGER_CHECK_MISSING",
                        f"{model_id} fallback {rule.get('model_ref')} trigger check does not resolve in the source model",
                        artifact_id=model_id,
                    )
                elif trigger.get("criticality") != "blocking" or trigger.get("failure_response") not in {"block_result", "return_to_modeling"}:
                    self.add(
                        "G2",
                        "BLOCK",
                        "FALLBACK_TRIGGER_NOT_BLOCKING",
                        f"{model_id}/{rule.get('trigger_check_ref')} cannot trigger fallback because it is not a blocking failure",
                        artifact_id=model_id,
                    )
            if "optimization" in validation_facets and not any(
                check.get("check_type") == "solver_optimality"
                and check.get("applicability") == "required"
                and check.get("criticality") == "blocking"
                and isinstance(check.get("threshold"), dict)
                and is_finite_number(check.get("threshold", {}).get("value"))
                for check in checks
            ):
                self.add(
                    "G2",
                    "BLOCK",
                    "SOLVER_OPTIMALITY_NOT_BLOCKING",
                    f"{model_id} has optimization validation scope but no required blocking solver_optimality check with a numeric threshold",
                    artifact_id=model_id,
                )
            selected_baselines = set(model.get("method_selection", {}).get("baseline_policy", {}).get("model_refs", []))
            if selected_baselines and not any(
                check.get("check_type") == "baseline_comparison"
                and check.get("applicability") == "required"
                and check.get("criticality") == "blocking"
                for check in checks
            ):
                self.add(
                    "G2",
                    "BLOCK",
                    "BASELINE_COMPARISON_NOT_BLOCKING",
                    f"{model_id} selects baselines but has no required blocking baseline_comparison check",
                    artifact_id=model_id,
                )

        experiments_by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for experiment in experiments:
            experiments_by_model[experiment.get("model_ref")].append(experiment)
        results_by_experiment: dict[str, list[dict[str, Any]]] = defaultdict(list)
        comparison_bindings_by_result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for result in results:
            results_by_experiment[result.get("experiment_ref")].append(result)
        for experiment in experiments:
            model = models_by_id.get(experiment.get("model_ref"))
            experiment_id = experiment.get("id")
            experiment_questions = set(experiment.get("question_refs", []))
            experiment_data_refs = set(experiment.get("data_refs", []))
            output_ids = [item.get("id") for item in experiment.get("outputs", [])]
            scenario_sets = [
                row for row in experiment.get("scenario_sets", []) if isinstance(row, dict)
            ]
            scenario_ids = [row.get("id") for row in scenario_sets]
            for metric in experiment.get("metrics", []):
                matches = output_ids.count(metric.get("source_output_ref"))
                if matches != 1:
                    self.add(
                        "G3",
                        "BLOCK",
                        "METRIC_SOURCE_OUTPUT_NOT_LOCAL",
                        f"{experiment_id}/{metric.get('id')} source_output_ref must resolve exactly once in this experiment; found {matches}",
                        artifact_id=experiment_id,
                    )
                scenario_set_ref = metric.get("scenario_set_ref")
                if scenario_set_ref is not None and scenario_ids.count(scenario_set_ref) != 1:
                    self.add(
                        "G3",
                        "BLOCK",
                        "METRIC_SCENARIO_SET_NOT_LOCAL",
                        (
                            f"{experiment_id}/{metric.get('id')} scenario_set_ref must resolve "
                            f"exactly once in this experiment; found {scenario_ids.count(scenario_set_ref)}"
                        ),
                        artifact_id=experiment_id,
                    )
            optimization_metric_signals = [
                f"{metric.get('id')} direction={metric.get('direction')}"
                for metric in experiment.get("metrics", [])
                if metric.get("direction") in {"minimize", "maximize"}
            ]
            if (
                model is not None
                and optimization_metric_signals
                and "optimization" not in effective_validation_facets(model)
            ):
                code = model_evidence_consistency_code(model, "FAMILY_EVIDENCE_MISMATCH")
                migration_note = (
                    f" schema_version={model.get('schema_version')!r} predates the 2.4.0 "
                    "family/evidence consistency contract and must be migrated;"
                    if code.endswith("_MIGRATION_REQUIRED")
                    else ""
                )
                self.add(
                    "G3",
                    "BLOCK",
                    code,
                    (
                        f"{model.get('id')}{migration_note} experiment {experiment_id} triggers the "
                        f"optimization-metric signal {optimization_metric_signals}; "
                        f"model_family={model.get('model_family')!r}, "
                        f"validation_facets={model.get('validation_facets', [])!r}; add the "
                        "'optimization' validation facet"
                    ),
                    artifact_id=experiment_id,
                )
            optimization_scope = bool(
                model is not None and "optimization" in effective_validation_facets(model)
            )
            scenario_holdout_required = bool(
                optimization_scope
                and model is not None
                and scenario_holdout_is_actionable(model)
            )
            if optimization_scope and scenario_sets and not scenario_holdout_required:
                self.add(
                    "G3",
                    "BLOCK",
                    "SCENARIO_HOLDOUT_CHECK_INAPPLICABLE",
                    (
                        f"{experiment_id} registers scenario_sets but its optimization model does not "
                        "declare holdout_leakage as required or conditional"
                    ),
                    artifact_id=experiment_id,
                )
            if scenario_holdout_required:
                if not scenario_sets:
                    self.add(
                        "G3",
                        "BLOCK",
                        "SCENARIO_SETS_REQUIRED",
                        (
                            f"{experiment_id} is scenario-based optimization but does not register "
                            "selection and holdout scenario sets"
                        ),
                        artifact_id=experiment_id,
                    )
                else:
                    roles = {row.get("role") for row in scenario_sets}
                    missing_roles = {"selection", "holdout"}.difference(roles)
                    if missing_roles:
                        self.add(
                            "G3",
                            "BLOCK",
                            "SCENARIO_SET_ROLE_COVERAGE_MISSING",
                            f"{experiment_id} lacks scenario roles {sorted(missing_roles)}",
                            artifact_id=experiment_id,
                        )
                    selection_hashes = {
                        row.get("scenario_sha256")
                        for row in scenario_sets
                        if row.get("role") == "selection"
                    }
                    holdout_hashes = {
                        row.get("scenario_sha256")
                        for row in scenario_sets
                        if row.get("role") == "holdout"
                    }
                    overlap = sorted(
                        value
                        for value in selection_hashes.intersection(holdout_hashes)
                        if isinstance(value, str)
                    )
                    if overlap:
                        self.add(
                            "G3",
                            "BLOCK",
                            "SCENARIO_SET_HASH_OVERLAP",
                            (
                                f"{experiment_id} reuses scenario_sha256 across selection and holdout: "
                                f"{overlap}"
                            ),
                            artifact_id=experiment_id,
                        )
            if experiment.get("model_ref") not in experiment.get("depends_on", []):
                self.add(
                    "G3",
                    "BLOCK",
                    "EXPERIMENT_MODEL_DEPENDENCY_MISSING",
                    f"{experiment_id} must include model_ref {experiment.get('model_ref')} in depends_on",
                    artifact_id=experiment_id,
                )
            if model is None:
                self.add(
                    "G3",
                    "BLOCK",
                    "EXPERIMENT_MODEL_MISSING",
                    f"{experiment_id} model_ref does not resolve",
                    artifact_id=experiment_id,
                )
            elif not experiment_questions.issubset(set(model.get("addresses", []))):
                self.add(
                    "G3",
                    "BLOCK",
                    "EXPERIMENT_QUESTION_SCOPE_MISMATCH",
                    f"{experiment_id} includes questions outside {model.get('id')} addresses",
                    artifact_id=experiment_id,
                )
            for data_ref in experiment.get("data_refs", []):
                asset = data_assets_by_id.get(data_ref)
                if asset is None:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_DATA_ASSET_MISSING",
                        f"{experiment_id} data_ref {data_ref!r} is not a registered data asset",
                        artifact_id=experiment_id,
                    )
                    continue
                if model is not None and data_asset_owner_by_id.get(str(data_ref)) != model.get("problem_ref"):
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_DATA_ASSET_WRONG_PROBLEM",
                        f"{experiment_id} uses {data_ref}, which belongs to another problem specification",
                        artifact_id=experiment_id,
                    )
                if asset.get("usable_for_modeling") is not True:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_USES_EXCLUDED_INPUT",
                        f"{experiment.get('id')} uses {data_ref}, which is not approved for modeling",
                        artifact_id=experiment.get("id"),
                    )
            if model is not None:
                model_entrypoint = model.get("algorithm", {}).get("entrypoint")
                entrypoint_rows = [
                    item for item in experiment.get("code_files", []) if item.get("path") == model_entrypoint
                ]
                if len(entrypoint_rows) != 1:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_ENTRYPOINT_NOT_CAPTURED",
                        f"{experiment_id} must capture model entrypoint {model_entrypoint!r} exactly once in code_files",
                        artifact_id=experiment_id,
                    )
                command = experiment.get("command", {})
                executes_entrypoint, execution_reason = command_executes_project_path(
                    self.root,
                    argv=command.get("argv"),
                    cwd=command.get("cwd"),
                    expected_relative=model_entrypoint,
                )
                if not executes_entrypoint:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_COMMAND_ENTRYPOINT_MISMATCH",
                        f"{experiment_id} command does not execute model entrypoint {model_entrypoint!r}: {execution_reason}",
                        artifact_id=experiment_id,
                    )
                relevant_bindings = [
                    binding
                    for binding in model.get("data_bindings", [])
                    if set(binding.get("question_refs", [])).intersection(experiment_questions)
                ]
                relevant_data_refs = {binding.get("data_ref") for binding in relevant_bindings}
                all_bound_data_refs = {binding.get("data_ref") for binding in model.get("data_bindings", [])}
                missing_run_inputs = relevant_data_refs.difference(experiment_data_refs)
                unbound_run_inputs = experiment_data_refs.difference(all_bound_data_refs)
                out_of_scope_run_inputs = experiment_data_refs.difference(relevant_data_refs)
                if missing_run_inputs:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_MODEL_INPUT_MISSING",
                        f"{experiment_id} omits model-bound inputs {sorted(missing_run_inputs)}",
                        artifact_id=experiment_id,
                    )
                if unbound_run_inputs:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_INPUT_NOT_BOUND",
                        f"{experiment_id} uses inputs not bound by {model.get('id')}: {sorted(unbound_run_inputs)}",
                        artifact_id=experiment_id,
                    )
                if out_of_scope_run_inputs:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_DATA_SCOPE_MISMATCH",
                        f"{experiment_id} uses model inputs outside its question scope: {sorted(out_of_scope_run_inputs)}",
                        artifact_id=experiment_id,
                    )
                baseline_policy = model.get("method_selection", {}).get("baseline_policy", {})
                planned_baselines = set(baseline_policy.get("model_refs", []))
                experiment_baselines = set(experiment.get("baseline_refs", []))
                if planned_baselines != experiment_baselines:
                    self.add(
                        "G3",
                        "BLOCK",
                        "EXPERIMENT_BASELINE_PLAN_MISMATCH",
                        f"{experiment.get('id')} baseline_refs differ from {model.get('id')} method selection",
                        artifact_id=experiment.get("id"),
                    )
                comparison_rules = experiment.get("baseline_comparison_rules", [])
                metric_specs_for_experiment = {
                    metric.get("id"): metric for metric in experiment.get("metrics", [])
                }
                decision_metric_refs = {
                    rule.get("metric_ref") for rule in experiment.get("acceptance_rules", [])
                } or set(metric_specs_for_experiment)
                expected_comparison_pairs = {
                    (baseline_ref, metric_ref)
                    for baseline_ref in experiment_baselines
                    for metric_ref in decision_metric_refs
                }
                actual_comparison_pairs = [
                    (rule.get("baseline_model_ref"), rule.get("primary_metric_ref"))
                    for rule in comparison_rules
                ]
                if set(actual_comparison_pairs) != expected_comparison_pairs or len(actual_comparison_pairs) != len(set(actual_comparison_pairs)):
                    self.add(
                        "G3",
                        "BLOCK",
                        "BASELINE_COMPARISON_RULE_COVERAGE_MISMATCH",
                        f"{experiment_id} must predeclare exactly one comparison rule for every selected baseline and decision metric",
                        artifact_id=experiment_id,
                    )
                if experiment_baselines:
                    comparison_checks = {
                        check.get("id")
                        for check in model.get("validation_plan", {}).get("checks", [])
                        if check.get("check_type") == "baseline_comparison"
                        and check.get("applicability") == "required"
                        and check.get("criticality") == "blocking"
                    }
                    if not comparison_checks:
                        self.add(
                            "G3",
                            "BLOCK",
                            "BASELINE_COMPARISON_CHECK_MISSING",
                            f"{experiment.get('id')} declares baselines without a required blocking baseline_comparison check",
                            artifact_id=experiment.get("id"),
                        )
                    for rule in comparison_rules:
                        metric_spec = metric_specs_for_experiment.get(rule.get("primary_metric_ref"))
                        if rule.get("check_ref") not in comparison_checks:
                            self.add(
                                "G3",
                                "BLOCK",
                                "BASELINE_COMPARISON_RULE_CHECK_INVALID",
                                f"{rule.get('id')} must bind a required blocking baseline_comparison check",
                                artifact_id=experiment_id,
                            )
                        if rule.get("baseline_model_ref") not in experiment_baselines:
                            self.add(
                                "G3",
                                "BLOCK",
                                "BASELINE_COMPARISON_RULE_MODEL_INVALID",
                                f"{rule.get('id')} targets an unselected baseline",
                                artifact_id=experiment_id,
                            )
                        if metric_spec is None or rule.get("unit") != metric_spec.get("unit"):
                            self.add(
                                "G3",
                                "BLOCK",
                                "BASELINE_COMPARISON_RULE_METRIC_INVALID",
                                f"{rule.get('id')} metric or unit does not match the primary experiment",
                                artifact_id=experiment_id,
                            )
                    for baseline_ref in experiment_baselines:
                        comparable_experiments = [
                            candidate
                            for candidate in experiments_by_model.get(baseline_ref, [])
                            if experiments_are_comparable(experiment, candidate)
                        ]
                        if not comparable_experiments:
                            self.add(
                                "G3",
                                "BLOCK",
                                "BASELINE_EXPERIMENT_NOT_COMPARABLE",
                                f"{baseline_ref} has no experiment with the same questions, data, split, seeds, repetitions, timeout and environment as {experiment.get('id')}",
                                artifact_id=experiment.get("id"),
                            )
            if not results_by_experiment.get(experiment.get("id")):
                self.add("G4", "BLOCK", "EXPERIMENT_WITHOUT_RESULT", f"no result artifact refers to {experiment.get('id')}")
            if experiment.get("mode") != "exploratory" and not experiment.get("acceptance_rules"):
                self.add("G4", "BLOCK", "ACCEPTANCE_RULES_REQUIRED", f"{experiment.get('id')} is {experiment.get('mode')} but has no acceptance rule")
            for rule in experiment.get("acceptance_rules", []):
                if rule.get("registration_timing") != "post_result":
                    continue
                if experiment.get("mode") == "exploratory":
                    self.add(
                        "G4",
                        "WARN",
                        "POST_HOC_ACCEPTANCE_RULE",
                        f"{experiment.get('id')}/{rule.get('metric_ref')} was registered after results and is exploratory only",
                        artifact_id=experiment.get("id"),
                    )
                else:
                    self.add(
                        "G4",
                        "BLOCK",
                        "CONFIRMATORY_RULE_POST_HOC",
                        f"{experiment.get('id')}/{rule.get('metric_ref')} cannot be confirmatory because it was registered after results",
                        artifact_id=experiment.get("id"),
                    )

        for result in results:
            result_id = result.get("id")
            experiment = experiments_by_id.get(result.get("experiment_ref"))
            eligible = True
            if experiment is None:
                self.add("G4", "BLOCK", "RESULT_EXPERIMENT_MISSING", f"{result_id} does not resolve to an experiment", artifact_id=result_id)
                self.result_eligibility[result_id] = False
                continue
            successful = result.get("run_status") == "success"
            trigger_binding = self.promotion_trigger_diagnostics.get(str(result_id))
            trigger_partial = result.get("run_status") == "partial" and trigger_binding is not None
            execution_complete = successful or trigger_partial
            run = result.get("run", {})
            started: datetime | None = None
            finished: datetime | None = None
            time_valid = True
            started_value = run.get("started_at")
            finished_value = run.get("finished_at")
            requires_timestamps = result.get("run_status") in {"success", "failed"} or trigger_partial

            # Placeholder partial results may use a null/null pair.  Any actual
            # success, failure or promotion-trigger execution must bind a real
            # interval, while a half-populated interval is invalid for every
            # status because it cannot be interpreted deterministically.
            try:
                if (started_value is None) != (finished_value is None):
                    raise ValueError("started_at and finished_at must both be null or both be timestamps")
                if started_value is None:
                    if requires_timestamps:
                        raise ValueError(f"{result.get('run_status')} run requires started_at and finished_at")
                else:
                    started = parse_rfc3339(started_value)
                    finished = parse_rfc3339(finished_value)
                    if finished < started:
                        raise ValueError("finished_at precedes started_at")
                    if finished > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                        raise ValueError("finished_at is implausibly in the future")
            except ValueError as exc:
                self.add("G4", "BLOCK", "RUN_TIME_INVALID", f"{result_id}: {exc}", artifact_id=result_id)
                eligible = False
                time_valid = False

            model = models_by_id.get(experiment.get("model_ref"))
            if model is None:
                self.add("G4", "BLOCK", "RESULT_MODEL_MISSING", f"{result_id} experiment model does not resolve", artifact_id=result_id)
                eligible = False
            else:
                result_optimization_signals: list[str] = []
                for diagnostic in result.get("diagnostics", []):
                    if not isinstance(diagnostic, dict):
                        continue
                    diagnostic_id = diagnostic.get("id")
                    if (
                        "objective_incumbent" in diagnostic
                        and "objective_bound" in diagnostic
                    ):
                        result_optimization_signals.append(
                            f"objective_incumbent/objective_bound in {diagnostic_id}"
                        )
                    if "objective_reconciliation" in diagnostic:
                        result_optimization_signals.append(
                            f"objective_reconciliation in {diagnostic_id}"
                        )
                if (
                    result_optimization_signals
                    and "optimization" not in effective_validation_facets(model)
                ):
                    code = model_evidence_consistency_code(
                        model, "FAMILY_EVIDENCE_MISMATCH"
                    )
                    migration_note = (
                        f" schema_version={model.get('schema_version')!r} predates the 2.4.0 "
                        "family/evidence consistency contract and must be migrated;"
                        if code.endswith("_MIGRATION_REQUIRED")
                        else ""
                    )
                    self.add(
                        "G4",
                        "BLOCK",
                        code,
                        (
                            f"{model.get('id')}{migration_note} result {result_id} from experiment "
                            f"{experiment.get('id')} triggers optimization evidence "
                            f"{result_optimization_signals}; model_family={model.get('model_family')!r}, "
                            f"validation_facets={model.get('validation_facets', [])!r}; add the "
                            "'optimization' validation facet"
                        ),
                        artifact_id=result_id,
                    )
                    eligible = False
            if model is not None and successful and (
                model.get("method_selection", {}).get("decision") != "selected"
                and str(model.get("id")) not in self.effective_primary_model_ids
            ):
                self.add(
                    "G4",
                    "BLOCK",
                    "RESULT_MODEL_NOT_SELECTED",
                    f"{result_id} was produced by method decision={model.get('method_selection', {}).get('decision')!r}",
                    artifact_id=result_id,
                )
                eligible = False
            if result.get("experiment_ref") not in result.get("depends_on", []):
                self.add(
                    "G4",
                    "BLOCK",
                    "RESULT_EXPERIMENT_DEPENDENCY_MISSING",
                    f"{result_id} must include experiment_ref {result.get('experiment_ref')} in depends_on",
                    artifact_id=result_id,
                )
                eligible = False

            if not execution_complete:
                self.add(
                    "G4",
                    "NOT_APPLICABLE",
                    "HISTORICAL_RESULT_NOT_SUCCESSFUL",
                    f"{result_id} is retained as {result.get('run_status')} history; run/output deviations are not release evidence",
                    artifact_id=result_id,
                )
                self.result_eligibility[result_id] = False
                continue

            if run.get("argv") != experiment.get("command", {}).get("argv"):
                self.add("G4", "BLOCK", "RUN_ARGV_MISMATCH", f"{result_id} argv differs from its experiment", artifact_id=result_id)
                eligible = False
            if run.get("cwd") != experiment.get("command", {}).get("cwd"):
                self.add("G4", "BLOCK", "RUN_CWD_MISMATCH", f"{result_id} cwd differs from its experiment", artifact_id=result_id)
                eligible = False
            if run.get("seeds") != experiment.get("seeds"):
                self.add("G4", "BLOCK", "RUN_SEEDS_MISMATCH", f"{result_id} seeds differ from its experiment", artifact_id=result_id)
                eligible = False
            repetitions_completed = run.get("repetitions_completed")
            repetitions_planned = experiment.get("repetitions")
            if execution_complete and repetitions_completed != repetitions_planned:
                self.add(
                    "G4",
                    "BLOCK",
                    "RUN_REPETITIONS_MISMATCH",
                    f"{result_id} completed {run.get('repetitions_completed')} repetitions; experiment requires {experiment.get('repetitions')}",
                    artifact_id=result_id,
                )
                eligible = False
            elif (
                not execution_complete
                and isinstance(repetitions_completed, int)
                and isinstance(repetitions_planned, int)
                and repetitions_completed > repetitions_planned
            ):
                self.add(
                    "G4",
                    "BLOCK",
                    "RUN_REPETITIONS_EXCEED_PLAN",
                    f"{result_id} records more completed repetitions than planned",
                    artifact_id=result_id,
                )
                eligible = False
            if time_valid and started is not None and finished is not None:
                try:
                    # Promotion and timeout checks are meaningful only after a
                    # complete, valid execution interval has been established.
                    promotion_event = self.promoted_model_events.get(str(model.get("id"))) if isinstance(model, dict) else None
                    if promotion_event is not None:
                        event_id, promoted_at = promotion_event
                        if event_id not in experiment.get("depends_on", []):
                            raise ValueError("promoted-route experiment does not depend on its promotion event")
                        if started < promoted_at:
                            raise ValueError("run started before its fallback route was promoted")
                    timeout_seconds = experiment.get("timeout_seconds")
                    if isinstance(timeout_seconds, int) and (finished - started).total_seconds() > timeout_seconds:
                        self.add(
                            "G4",
                            "BLOCK",
                            "RUN_TIMEOUT_EXCEEDED",
                            f"{result_id} elapsed time exceeds the predeclared {timeout_seconds}s budget",
                            artifact_id=result_id,
                        )
                        eligible = False
                except ValueError as exc:
                    self.add("G4", "BLOCK", "RUN_TIME_INVALID", f"{result_id}: {exc}", artifact_id=result_id)
                    eligible = False

            if execution_complete:
                if run.get("exit_code") != 0:
                    code = "SUCCESS_EXIT_CODE_NONZERO" if successful else "PROMOTION_TRIGGER_EXIT_CODE_NONZERO"
                    self.add("G4", "BLOCK", code, f"{result_id} completed execution but exit_code={run.get('exit_code')}", artifact_id=result_id)
                    eligible = False
                elif trigger_partial:
                    self.add("G4", "PASS", "PROMOTION_TRIGGER_EXECUTION_COMPLETED", f"{result_id} completed its predeclared run frame before scientific rejection", artifact_id=result_id)
                else:
                    self.add("G4", "PASS", "RUN_SUCCESSFUL", f"{result_id} records success with exit_code 0", artifact_id=result_id)

            recorded_inputs = {
                (item.get("path"), item.get("sha256"))
                for item in result.get("inputs", [])
                if isinstance(item, dict)
            }
            expected_inputs: set[tuple[Any, Any]] = set()
            for data_ref in experiment.get("data_refs", []):
                asset = data_assets_by_id.get(data_ref, {})
                file_ref = asset.get("file")
                if execution_complete and asset.get("usable_for_modeling") is True and isinstance(file_ref, dict):
                    expected_input = (file_ref.get("path"), file_ref.get("sha256"))
                    expected_inputs.add(expected_input)
                    if expected_input not in recorded_inputs:
                        self.add(
                            "G4",
                            "BLOCK",
                            "RESULT_INPUT_NOT_CAPTURED",
                            f"{result_id} does not record hashed experiment input {data_ref}",
                            artifact_id=result_id,
                        )
                        eligible = False
            if execution_complete:
                undeclared_inputs = recorded_inputs.difference(expected_inputs)
                if undeclared_inputs:
                    self.add(
                        "G4",
                        "BLOCK",
                        "UNDECLARED_RESULT_INPUT",
                        f"{result_id} records inputs not approved by its experiment: {sorted(undeclared_inputs)}",
                        artifact_id=result_id,
                    )
                    eligible = False

            output_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            output_specs = {item.get("id"): item for item in experiment.get("outputs", [])}
            for output in result.get("outputs", []):
                output_ref = output.get("output_ref")
                output_rows[output_ref].append(output)
                output_spec = output_specs.get(output_ref)
                if output_spec is None:
                    self.add(
                        "G4",
                        "BLOCK",
                        "RESULT_OUTPUT_UNDECLARED",
                        f"{result_id}/{output_ref} is not declared by its experiment",
                        artifact_id=result_id,
                    )
                    eligible = False
                elif output.get("file", {}).get("path") != output_spec.get("path"):
                    self.add(
                        "G4",
                        "BLOCK",
                        "RESULT_OUTPUT_PATH_MISMATCH",
                        f"{result_id}/{output_ref} file path differs from the experiment declaration",
                        artifact_id=result_id,
                    )
                    eligible = False
                if execution_complete and output_spec is not None:
                    try:
                        output_path = safe_project_path(self.root, output.get("file", {}).get("path"), must_exist=True)
                        comparison_passed, comparison_note = evaluate_output_comparator(
                            self.root,
                            output_path,
                            output_spec.get("comparator", {}),
                        )
                    except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                        comparison_passed = False
                        comparison_note = f"comparison could not be recomputed: {exc}"
                    expected_status = "PASS" if comparison_passed else "BLOCK"
                    if output.get("comparison_status") != expected_status:
                        self.add(
                            "G4",
                            "BLOCK",
                            "OUTPUT_COMPARISON_STATUS_MISMATCH",
                            f"{result_id}/{output_ref} declares {output.get('comparison_status')}; recomputation requires {expected_status}",
                            artifact_id=result_id,
                        )
                        eligible = False
                    if comparison_passed:
                        self.add("G4", "PASS", "OUTPUT_COMPARISON_RECOMPUTED", f"{result_id}/{output_ref}: {comparison_note}", artifact_id=result_id)
                    else:
                        self.add("G4", "BLOCK", "OUTPUT_COMPARISON_FAILED", f"{result_id}/{output_ref}: {comparison_note}", artifact_id=result_id)
                        eligible = False
            for declared in experiment.get("outputs", []):
                matches = output_rows.get(declared.get("id"), [])
                if execution_complete and declared.get("required") and len(matches) != 1:
                    self.add("G4", "BLOCK", "REQUIRED_OUTPUT_AMBIGUOUS", f"{result_id} has {len(matches)} rows for required {declared.get('id')}", artifact_id=result_id)
                    eligible = False
                if len(matches) > 1:
                    self.add(
                        "G4",
                        "BLOCK",
                        "RESULT_OUTPUT_AMBIGUOUS",
                        f"{result_id} has {len(matches)} rows for {declared.get('id')}",
                        artifact_id=result_id,
                    )
                    eligible = False

            registered_evidence_files = {
                (item.get("path"), item.get("sha256"))
                for item in [
                    *result.get("inputs", []),
                    *[output.get("file", {}) for output in result.get("outputs", [])],
                    *result.get("logs", []),
                ]
                if isinstance(item, dict)
            }

            metric_specs = {metric.get("id"): metric for metric in experiment.get("metrics", [])}
            metric_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for metric in result.get("metrics", []):
                metric_ref = metric.get("metric_ref")
                metric_rows[metric_ref].append(metric)
                measurement = metric.get("measurement", {})
                value = measurement.get("value")
                if value is not None and not is_finite_number(value):
                    self.add("G4", "BLOCK", "RESULT_METRIC_NONFINITE", f"{result_id}/{metric_ref} is non-finite", artifact_id=result_id)
                    eligible = False
                uncertainty = metric.get("uncertainty")
                if isinstance(uncertainty, dict):
                    numeric_uncertainty = {
                        key: uncertainty.get(key)
                        for key in ("level", "lower", "upper")
                        if key in uncertainty
                    }
                    if any(not is_finite_number(item) for item in numeric_uncertainty.values()):
                        self.add("G4", "BLOCK", "RESULT_UNCERTAINTY_NONFINITE", f"{result_id}/{metric_ref} uncertainty contains a non-finite value", artifact_id=result_id)
                        eligible = False
                    has_lower = "lower" in uncertainty
                    has_upper = "upper" in uncertainty
                    if has_lower != has_upper:
                        self.add("G4", "BLOCK", "RESULT_UNCERTAINTY_BOUNDS_INCOMPLETE", f"{result_id}/{metric_ref} must provide both lower and upper bounds", artifact_id=result_id)
                        eligible = False
                    elif has_lower and has_upper and all(
                        is_finite_number(uncertainty.get(key)) for key in ("lower", "upper")
                    ):
                        lower = decimal_number(uncertainty["lower"])
                        upper = decimal_number(uncertainty["upper"])
                        if lower > upper:
                            self.add("G4", "BLOCK", "RESULT_UNCERTAINTY_BOUNDS_REVERSED", f"{result_id}/{metric_ref} lower bound exceeds upper bound", artifact_id=result_id)
                            eligible = False
                        elif value is not None and is_finite_number(value) and not (lower <= decimal_number(value) <= upper):
                            self.add("G4", "BLOCK", "RESULT_VALUE_OUTSIDE_UNCERTAINTY", f"{result_id}/{metric_ref} point value lies outside its reported interval", artifact_id=result_id)
                            eligible = False
                spec = metric_specs.get(metric_ref)
                if spec is None:
                    self.add("G4", "BLOCK", "RESULT_METRIC_UNDECLARED", f"{result_id}/{metric_ref} is not declared by the experiment", artifact_id=result_id)
                    eligible = False
                elif measurement.get("unit") != spec.get("unit"):
                    self.add("G4", "BLOCK", "RESULT_METRIC_UNIT_MISMATCH", f"{result_id}/{metric_ref} unit differs from the experiment", artifact_id=result_id)
                    eligible = False
                if execution_complete and spec is not None:
                    source_output_ref = spec.get("source_output_ref")
                    source_output_rows = output_rows.get(source_output_ref, [])
                    if len(source_output_rows) != 1:
                        self.add(
                            "G4",
                            "BLOCK",
                            "METRIC_SOURCE_OUTPUT_AMBIGUOUS",
                            f"{result_id}/{metric_ref} cannot resolve exactly one source output {source_output_ref}",
                            artifact_id=result_id,
                        )
                        eligible = False
                    else:
                        source_path = source_output_rows[0].get("file", {}).get("path")
                        try:
                            resolved_source = safe_project_path(self.root, source_path, must_exist=True)
                            extracted = extracted_decimal(extract_metric_value(resolved_source, spec.get("extractor", {})))
                            recorded = decimal_number(value)
                        except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                            self.add(
                                "G4",
                                "BLOCK",
                                "METRIC_EXTRACTION_FAILED",
                                f"{result_id}/{metric_ref} could not be reproduced from {source_output_ref}: {exc}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        else:
                            if extracted != recorded:
                                self.add(
                                    "G4",
                                    "BLOCK",
                                    "METRIC_OUTPUT_VALUE_MISMATCH",
                                    f"{result_id}/{metric_ref} records {recorded}; output bytes contain {extracted}",
                                    artifact_id=result_id,
                                )
                                eligible = False
                            else:
                                self.add(
                                    "G4",
                                    "PASS",
                                    "METRIC_OUTPUT_VALUE_MATCH",
                                    f"{result_id}/{metric_ref} matches its hashed output extractor",
                                    artifact_id=result_id,
                                )
            for metric_ref, rows in metric_rows.items():
                if len(rows) != 1:
                    self.add("G4", "BLOCK", "RESULT_METRIC_AMBIGUOUS", f"{result_id} has {len(rows)} rows for {metric_ref}", artifact_id=result_id)
                    eligible = False
            if execution_complete:
                for metric_ref in metric_specs:
                    if len(metric_rows.get(metric_ref, [])) != 1:
                        self.add(
                            "G4",
                            "BLOCK",
                            "DECLARED_METRIC_MISSING",
                            f"{result_id} does not record exactly one value for declared metric {metric_ref}",
                            artifact_id=result_id,
                        )
                        eligible = False

            planned_checks = {
                check.get("id"): check
                for check in (model or {}).get("validation_plan", {}).get("checks", [])
            }
            baseline_rules_by_id = {
                rule.get("id"): rule for rule in experiment.get("baseline_comparison_rules", [])
            }
            diagnostic_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for diagnostic in result.get("diagnostics", []):
                check_ref = diagnostic.get("check_ref")
                diagnostic_rows[check_ref].append(diagnostic)
                check = planned_checks.get(check_ref)
                if check is None:
                    self.add(
                        "G4",
                        "BLOCK",
                        "DIAGNOSTIC_CHECK_UNDECLARED",
                        f"{result_id}/{diagnostic.get('id')} refers to a check outside its selected model",
                        artifact_id=result_id,
                    )
                    eligible = False
                    continue
                if diagnostic.get("check_type") != check.get("check_type"):
                    self.add(
                        "G4",
                        "BLOCK",
                        "DIAGNOSTIC_TYPE_MISMATCH",
                        f"{result_id}/{diagnostic.get('id')} type differs from {check_ref}",
                        artifact_id=result_id,
                    )
                    eligible = False

                status = diagnostic.get("status")
                is_expected_trigger = bool(
                    trigger_partial
                    and trigger_binding is not None
                    and diagnostic.get("id") == trigger_binding[1]
                )
                applicability = check.get("applicability")
                condition_met = diagnostic.get("condition_met")
                condition_evidence = diagnostic.get("condition_evidence")
                if applicability == "conditional":
                    if not isinstance(condition_met, bool) or not isinstance(condition_evidence, str):
                        self.add(
                            "G4",
                            "BLOCK",
                            "CONDITIONAL_DIAGNOSTIC_DECISION_MISSING",
                            f"{result_id}/{check_ref} must record whether the predeclared condition activated and why",
                            artifact_id=result_id,
                        )
                        eligible = False
                    elif condition_met and status == "NOT_APPLICABLE":
                        self.add(
                            "G4",
                            "BLOCK",
                            "ACTIVATED_DIAGNOSTIC_NOT_EXECUTED",
                            f"{result_id}/{check_ref} activated but is marked NOT_APPLICABLE",
                            artifact_id=result_id,
                        )
                        eligible = False
                    elif not condition_met and status != "NOT_APPLICABLE":
                        self.add(
                            "G4",
                            "BLOCK",
                            "INACTIVE_DIAGNOSTIC_EXECUTED",
                            f"{result_id}/{check_ref} did not activate but records {status}",
                            artifact_id=result_id,
                        )
                        eligible = False
                elif condition_met is not None or condition_evidence is not None:
                    self.add(
                        "G4",
                        "BLOCK",
                        "NONCONDITIONAL_DIAGNOSTIC_HAS_CONDITION",
                        f"{result_id}/{check_ref} is not conditional; condition fields must be null",
                        artifact_id=result_id,
                    )
                    eligible = False
                if applicability == "not_applicable" and status != "NOT_APPLICABLE":
                    self.add(
                        "G4",
                        "BLOCK",
                        "NOT_APPLICABLE_CHECK_EXECUTED",
                        f"{result_id}/{check_ref} was predeclared not_applicable but records {status}",
                        artifact_id=result_id,
                    )
                    eligible = False

                comparison_bindings = diagnostic.get("comparison_bindings", [])
                diagnostic_evidence = {
                    (item.get("path"), item.get("sha256"))
                    for item in diagnostic.get("evidence_files", [])
                    if isinstance(item, dict)
                }
                if not diagnostic_evidence.issubset(registered_evidence_files):
                    self.add(
                        "G4",
                        "BLOCK",
                        "DIAGNOSTIC_EVIDENCE_UNREGISTERED",
                        f"{result_id}/{check_ref} cites evidence outside this run's inputs, outputs or logs",
                        artifact_id=result_id,
                    )
                    eligible = False
                if diagnostic.get("check_type") != "baseline_comparison" and comparison_bindings:
                    self.add(
                        "G4",
                        "BLOCK",
                        "NONBASELINE_DIAGNOSTIC_HAS_BINDINGS",
                        f"{result_id}/{check_ref} is not a baseline comparison but declares comparison bindings",
                        artifact_id=result_id,
                    )
                    eligible = False
                if execution_complete and diagnostic.get("check_type") == "baseline_comparison" and status != "NOT_APPLICABLE":
                    if not comparison_bindings:
                        self.add(
                            "G4",
                            "BLOCK",
                            "BASELINE_COMPARISON_BINDING_MISSING",
                            f"{result_id}/{check_ref} has no concrete baseline result and metric binding",
                            artifact_id=result_id,
                        )
                        eligible = False
                    seen_binding_keys: set[tuple[Any, Any, Any, Any]] = set()
                    computed_binding_statuses: list[str] = []
                    for binding in comparison_bindings:
                        binding_key = (
                            binding.get("baseline_model_ref"),
                            binding.get("baseline_result_ref"),
                            binding.get("primary_metric_ref"),
                            binding.get("baseline_metric_ref"),
                        )
                        if binding_key in seen_binding_keys:
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_COMPARISON_BINDING_DUPLICATE",
                                f"{result_id}/{check_ref} repeats comparison binding {binding_key}",
                                artifact_id=result_id,
                            )
                            eligible = False
                            continue
                        seen_binding_keys.add(binding_key)
                        comparison_bindings_by_result[str(result_id)].append(binding)
                        baseline_model_ref = binding.get("baseline_model_ref")
                        baseline_result_ref = binding.get("baseline_result_ref")
                        primary_metric_ref = binding.get("primary_metric_ref")
                        baseline_metric_ref = binding.get("baseline_metric_ref")
                        comparison_rule_ref = binding.get("comparison_rule_ref")
                        comparison_rule = baseline_rules_by_id.get(comparison_rule_ref)
                        if (
                            comparison_rule is None
                            or comparison_rule.get("check_ref") != check_ref
                            or comparison_rule.get("baseline_model_ref") != baseline_model_ref
                            or comparison_rule.get("primary_metric_ref") != primary_metric_ref
                        ):
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_COMPARISON_RULE_BINDING_INVALID",
                                f"{result_id} binding does not match predeclared rule {comparison_rule_ref}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        if baseline_model_ref not in experiment.get("baseline_refs", []):
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_BINDING_MODEL_NOT_SELECTED",
                                f"{result_id} binds unselected baseline model {baseline_model_ref}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        if baseline_result_ref not in result.get("depends_on", []):
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_RESULT_DEPENDENCY_MISSING",
                                f"{result_id} must directly depend on bound baseline result {baseline_result_ref}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        baseline_result = results_by_id.get(baseline_result_ref)
                        baseline_experiment = experiments_by_id.get(
                            baseline_result.get("experiment_ref") if isinstance(baseline_result, dict) else None
                        )
                        if baseline_result is None or baseline_experiment is None:
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_BINDING_RESULT_INVALID",
                                f"{result_id} baseline result {baseline_result_ref} or its experiment does not resolve",
                                artifact_id=result_id,
                            )
                            eligible = False
                            continue
                        if baseline_experiment.get("model_ref") != baseline_model_ref:
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_BINDING_MODEL_MISMATCH",
                                f"{baseline_result_ref} was not produced by {baseline_model_ref}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        if not experiments_are_comparable(experiment, baseline_experiment):
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_BINDING_EXPERIMENT_NOT_COMPARABLE",
                                f"{baseline_result_ref} was produced outside the primary experiment's fair comparison frame",
                                artifact_id=result_id,
                            )
                            eligible = False
                        if experiment_implementation_signature(experiment) == experiment_implementation_signature(baseline_experiment):
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_EXECUTION_NOT_DISTINCT",
                                f"{result_id} and {baseline_result_ref} use the same executable method signature",
                                artifact_id=result_id,
                            )
                            eligible = False
                        primary_output_paths = {
                            item.get("file", {}).get("path") for item in result.get("outputs", [])
                        }
                        baseline_output_paths = {
                            item.get("file", {}).get("path") for item in baseline_result.get("outputs", [])
                        }
                        overlapping_output_paths = sorted(
                            path for path in primary_output_paths.intersection(baseline_output_paths) if path
                        )
                        if overlapping_output_paths:
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_OUTPUT_PATH_OVERLAP",
                                f"{result_id} and {baseline_result_ref} share output paths {overlapping_output_paths}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        primary_rows = metric_rows.get(primary_metric_ref, [])
                        baseline_rows = [
                            row
                            for row in baseline_result.get("metrics", [])
                            if row.get("metric_ref") == baseline_metric_ref
                        ]
                        baseline_metric_specs = {
                            item.get("id"): item for item in baseline_experiment.get("metrics", [])
                        }
                        primary_spec = metric_specs.get(primary_metric_ref)
                        baseline_spec = baseline_metric_specs.get(baseline_metric_ref)
                        if len(primary_rows) != 1 or len(baseline_rows) != 1:
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_BINDING_METRIC_AMBIGUOUS",
                                f"{result_id} comparison must resolve one primary and one baseline metric row",
                                artifact_id=result_id,
                            )
                            eligible = False
                        if primary_spec is None or baseline_spec is None or metric_signature(primary_spec) != metric_signature(baseline_spec):
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_BINDING_METRIC_MISMATCH",
                                f"{result_id} comparison metrics do not share name, direction, unit and aggregation",
                                artifact_id=result_id,
                            )
                            eligible = False
                        if (
                            comparison_rule is not None
                            and len(primary_rows) == 1
                            and len(baseline_rows) == 1
                            and primary_spec is not None
                            and baseline_spec is not None
                            and metric_signature(primary_spec) == metric_signature(baseline_spec)
                        ):
                            primary_value = primary_rows[0].get("measurement", {}).get("value")
                            baseline_value = baseline_rows[0].get("measurement", {}).get("value")
                            observed_delta = binding.get("observed_delta", {})
                            observed_value = observed_delta.get("value") if isinstance(observed_delta, dict) else None
                            if (
                                not is_finite_number(primary_value)
                                or not is_finite_number(baseline_value)
                                or not is_finite_number(observed_value)
                                or observed_delta.get("unit") != comparison_rule.get("unit")
                            ):
                                self.add(
                                    "G4",
                                    "BLOCK",
                                    "BASELINE_COMPARISON_VALUE_INVALID",
                                    f"{result_id}/{comparison_rule_ref} lacks finite unit-consistent metric values",
                                    artifact_id=result_id,
                                )
                                eligible = False
                            else:
                                computed_delta = decimal_number(primary_value) - decimal_number(baseline_value)
                                recorded_delta = decimal_number(observed_value)
                                if recorded_delta != computed_delta:
                                    self.add(
                                        "G4",
                                        "BLOCK",
                                        "BASELINE_COMPARISON_DELTA_MISMATCH",
                                        f"{result_id}/{comparison_rule_ref} records delta {recorded_delta}; metrics require {computed_delta}",
                                        artifact_id=result_id,
                                    )
                                    eligible = False
                                operation = NUMERIC_OPERATIONS.get(comparison_rule.get("operator"))
                                threshold_value = comparison_rule.get("threshold")
                                if operation is None or not is_finite_number(threshold_value):
                                    self.add(
                                        "G4",
                                        "BLOCK",
                                        "BASELINE_COMPARISON_RULE_NUMERIC_INVALID",
                                        f"{result_id}/{comparison_rule_ref} has an invalid comparator",
                                        artifact_id=result_id,
                                    )
                                    eligible = False
                                else:
                                    passed = operation(computed_delta, decimal_number(threshold_value))
                                    expected_binding_status = "PASS" if passed else "BLOCK"
                                    computed_binding_statuses.append(expected_binding_status)
                                    if binding.get("status") != expected_binding_status:
                                        self.add(
                                            "G4",
                                            "BLOCK",
                                            "BASELINE_COMPARISON_STATUS_MISMATCH",
                                            f"{result_id}/{comparison_rule_ref} declares {binding.get('status')}; recomputation requires {expected_binding_status}",
                                            artifact_id=result_id,
                                        )
                                        eligible = False
                    if computed_binding_statuses:
                        expected_diagnostic_status = (
                            "PASS" if all(item == "PASS" for item in computed_binding_statuses) else "BLOCK"
                        )
                        if status != expected_diagnostic_status:
                            self.add(
                                "G4",
                                "BLOCK",
                                "BASELINE_DIAGNOSTIC_STATUS_MISMATCH",
                                f"{result_id}/{check_ref} declares {status}; its bound comparisons require {expected_diagnostic_status}",
                                artifact_id=result_id,
                            )
                            eligible = False

                if (
                    execution_complete
                    and diagnostic.get("check_type") == "objective_reconciliation"
                    and status != "NOT_APPLICABLE"
                    and not self.validate_objective_reconciliation(
                        result=result,
                        experiment=experiment,
                        model=model or {},
                        diagnostic=diagnostic,
                        metric_rows=metric_rows,
                        metric_specs=metric_specs,
                    )
                ):
                    eligible = False

                threshold = check.get("threshold")
                observed_measurement = diagnostic.get("observed")
                if (
                    isinstance(observed_measurement, dict)
                    and observed_measurement.get("value") is not None
                    and not is_finite_number(observed_measurement.get("value"))
                ):
                    self.add("G4", "BLOCK", "DIAGNOSTIC_OBSERVED_NONFINITE", f"{result_id}/{check_ref} records a non-finite observation", artifact_id=result_id)
                    eligible = False
                if execution_complete and status == "PASS" and threshold is None and not diagnostic_evidence:
                    self.add(
                        "G4",
                        "BLOCK",
                        "QUALITATIVE_DIAGNOSTIC_EVIDENCE_MISSING",
                        f"{result_id}/{check_ref} is a qualitative PASS without a hashed run input, output or log",
                        artifact_id=result_id,
                    )
                    eligible = False
                if execution_complete and isinstance(threshold, dict) and status != "NOT_APPLICABLE":
                    # Recompute numeric verdicts from the frozen plan.  A PASS
                    # copied into YAML is not trusted when its value, unit or
                    # operator contradicts the predeclared threshold.
                    observed = diagnostic.get("observed")
                    source_file = diagnostic.get("source_file")
                    extractor = diagnostic.get("extractor")
                    source_signature = (
                        source_file.get("path"),
                        source_file.get("sha256"),
                    ) if isinstance(source_file, dict) else None
                    if source_signature not in registered_evidence_files or not isinstance(extractor, dict):
                        self.add(
                            "G4",
                            "BLOCK",
                            "DIAGNOSTIC_SOURCE_EXTRACTOR_MISSING",
                            f"{result_id}/{check_ref} numeric observation must bind a hashed run file and scalar extractor",
                            artifact_id=result_id,
                        )
                        eligible = False
                    else:
                        try:
                            diagnostic_source = safe_project_path(self.root, source_file.get("path"), must_exist=True)
                            extracted_observation = extracted_decimal(extract_metric_value(diagnostic_source, extractor))
                            recorded_observation = decimal_number(observed.get("value") if isinstance(observed, dict) else None)
                        except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                            self.add(
                                "G4",
                                "BLOCK",
                                "DIAGNOSTIC_EXTRACTION_FAILED",
                                f"{result_id}/{check_ref} observation could not be reproduced: {exc}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        else:
                            if extracted_observation != recorded_observation:
                                self.add(
                                    "G4",
                                    "BLOCK",
                                    "DIAGNOSTIC_OBSERVED_VALUE_MISMATCH",
                                    f"{result_id}/{check_ref} records {recorded_observation}; source bytes contain {extracted_observation}",
                                    artifact_id=result_id,
                                )
                                eligible = False
                            else:
                                self.add(
                                    "G4",
                                    "PASS",
                                    "DIAGNOSTIC_OBSERVED_VALUE_MATCH",
                                    f"{result_id}/{check_ref} matches its hashed source extractor",
                                    artifact_id=result_id,
                                )
                    operator = threshold.get("operator")
                    operation = NUMERIC_OPERATIONS.get(operator)
                    observed_value = observed.get("value") if isinstance(observed, dict) else None
                    threshold_value = threshold.get("value")
                    if (
                        operation is None
                        or not is_finite_number(observed_value)
                        or not is_finite_number(threshold_value)
                        or not isinstance(observed, dict)
                        or observed.get("unit") != threshold.get("unit")
                    ):
                        self.add(
                            "G4",
                            "BLOCK",
                            "DIAGNOSTIC_NUMERIC_CHECK_INVALID",
                            f"{result_id}/{check_ref} lacks a finite, unit-consistent observation for its threshold",
                            artifact_id=result_id,
                        )
                        eligible = False
                    else:
                        passed = operation(decimal_number(observed_value), decimal_number(threshold_value))
                        expected_status = "PASS" if passed else "BLOCK"
                        if status != expected_status:
                            self.add(
                                "G4",
                                "BLOCK",
                                "DIAGNOSTIC_STATUS_MISMATCH",
                                f"{result_id}/{check_ref} declares {status}; threshold evaluation requires {expected_status}",
                                artifact_id=result_id,
                            )
                            eligible = False
                        else:
                            if not passed and is_expected_trigger:
                                self.add(
                                    "G4",
                                    "PASS",
                                    "PROMOTION_TRIGGER_THRESHOLD_CONFIRMED",
                                    f"{result_id}/{check_ref} reproducibly failed its predeclared blocking threshold",
                                    artifact_id=result_id,
                                )
                            else:
                                self.add(
                                    "G4",
                                    "PASS" if passed else "BLOCK",
                                    "DIAGNOSTIC_THRESHOLD_PASS" if passed else "DIAGNOSTIC_THRESHOLD_FAILED",
                                    f"{result_id}/{check_ref}: {observed_value} {operator} {threshold_value}",
                                    artifact_id=result_id,
                                )
                            if not passed and not is_expected_trigger:
                                eligible = False

                if execution_complete and applicability == "required" and status != "PASS" and not is_expected_trigger:
                    self.add(
                        "G4",
                        "BLOCK",
                        "REQUIRED_DIAGNOSTIC_NOT_PASS",
                        f"{result_id}/{check_ref} is required but status is {status}",
                        artifact_id=result_id,
                    )
                    eligible = False
                condition_is_active = applicability == "required" or (
                    applicability == "conditional" and condition_met is True
                )
                if execution_complete and check.get("criticality") == "blocking" and condition_is_active and status != "PASS" and not is_expected_trigger:
                    self.add(
                        "G4",
                        "BLOCK",
                        "BLOCKING_DIAGNOSTIC_NOT_PASS",
                        f"{result_id}/{check_ref} is blocking but status is {status}",
                        artifact_id=result_id,
                    )
                    eligible = False

            if execution_complete:
                for check_ref, check in planned_checks.items():
                    rows = diagnostic_rows.get(check_ref, [])
                    applicability = check.get("applicability")
                    if applicability in {"required", "conditional"} and len(rows) != 1:
                        self.add(
                            "G4",
                            "BLOCK",
                            "VALIDATION_CHECK_EVIDENCE_AMBIGUOUS",
                            f"{result_id} has {len(rows)} diagnostics for planned {check_ref}",
                            artifact_id=result_id,
                        )
                        eligible = False
                    if applicability == "not_applicable" and len(rows) > 1:
                        self.add(
                            "G4",
                            "BLOCK",
                            "NOT_APPLICABLE_DIAGNOSTIC_AMBIGUOUS",
                            f"{result_id} has multiple N/A diagnostics for {check_ref}",
                            artifact_id=result_id,
                        )
                        eligible = False
                actionable_rows = [
                    row
                    for check_ref, check in planned_checks.items()
                    if check.get("applicability") in {"required", "conditional"}
                    for row in diagnostic_rows.get(check_ref, [])
                ]
                expected_trigger_rows = [
                    row
                    for row in actionable_rows
                    if trigger_partial
                    and trigger_binding is not None
                    and row.get("id") == trigger_binding[1]
                    and row.get("status") == "BLOCK"
                ]
                unexpected_nonpass = [
                    row
                    for row in actionable_rows
                    if row not in expected_trigger_rows and row.get("status") not in {"PASS", "NOT_APPLICABLE"}
                ]
                if trigger_partial and (len(expected_trigger_rows) != 1 or unexpected_nonpass):
                    self.add(
                        "G4",
                        "BLOCK",
                        "PROMOTION_TRIGGER_DIAGNOSTIC_SET_INVALID",
                        f"{result_id} must contain exactly one expected trigger BLOCK and no other active failure",
                        artifact_id=result_id,
                    )
                    eligible = False
                elif not trigger_partial and (not actionable_rows or not any(row.get("status") == "PASS" for row in actionable_rows)):
                    self.add(
                        "G4",
                        "BLOCK",
                        "ALL_ACTIONABLE_DIAGNOSTICS_NOT_PASS",
                        f"{result_id} has no PASS among its required or conditional diagnostics",
                        artifact_id=result_id,
                    )
                    eligible = False
                selected_baselines = set(experiment.get("baseline_refs", []))
                if selected_baselines:
                    decision_metric_refs = {
                        rule.get("metric_ref") for rule in experiment.get("acceptance_rules", [])
                    } or set(metric_specs)
                    binding_pairs = {
                        (binding.get("baseline_model_ref"), binding.get("primary_metric_ref"))
                        for binding in comparison_bindings_by_result.get(str(result_id), [])
                    }
                    binding_pair_counts: dict[tuple[Any, Any], int] = defaultdict(int)
                    for binding in comparison_bindings_by_result.get(str(result_id), []):
                        binding_pair_counts[
                            (binding.get("baseline_model_ref"), binding.get("primary_metric_ref"))
                        ] += 1
                    missing_pairs = {
                        (baseline_ref, metric_ref)
                        for baseline_ref in selected_baselines
                        for metric_ref in decision_metric_refs
                        if (baseline_ref, metric_ref) not in binding_pairs
                    }
                    if missing_pairs:
                        self.add(
                            "G4",
                            "BLOCK",
                            "BASELINE_COMPARISON_COVERAGE_MISSING",
                            f"{result_id} lacks concrete comparison bindings for {sorted(missing_pairs)}",
                            artifact_id=result_id,
                        )
                        eligible = False
                    ambiguous_pairs = sorted(
                        pair
                        for pair, count in binding_pair_counts.items()
                        if pair[0] in selected_baselines and pair[1] in decision_metric_refs and count != 1
                    )
                    if ambiguous_pairs:
                        self.add(
                            "G4",
                            "BLOCK",
                            "BASELINE_COMPARISON_BINDING_AMBIGUOUS",
                            f"{result_id} has multiple bindings for {ambiguous_pairs}",
                            artifact_id=result_id,
                        )
                        eligible = False

            # Bind the result's entire dependency closure, not only the main
            # experiment closure.  Baseline-result dependencies are attached
            # directly to a comparison result and must therefore be hashed too.
            required_fingerprints = {
                experiment.get("id"),
                *self.artifact_dependency_closure(result_id),
                *self.artifact_dependency_closure(str(experiment.get("id"))),
            }
            recorded_fingerprints = result.get("fingerprints", {})
            missing_fingerprints = required_fingerprints.difference(recorded_fingerprints)
            if execution_complete and missing_fingerprints:
                self.add("G4", "BLOCK", "RESULT_FINGERPRINT_CLOSURE_MISSING", f"{result_id} lacks fingerprints for {sorted(missing_fingerprints)}", artifact_id=result_id)
                eligible = False
            for dependency_id, recorded_hash in recorded_fingerprints.items():
                current = self.current_hashes.get(dependency_id)
                if current is None:
                    self.add(
                        "G4",
                        "BLOCK" if execution_complete else "WARN",
                        "FINGERPRINT_TARGET_MISSING" if execution_complete else "HISTORICAL_FINGERPRINT_TARGET_MISSING",
                        f"no current hash for {dependency_id}",
                        artifact_id=result_id,
                    )
                    if execution_complete:
                        eligible = False
                elif recorded_hash != current:
                    self.add(
                        "G4",
                        "STALE" if execution_complete else "NOT_APPLICABLE",
                        "RESULT_FINGERPRINT_STALE" if execution_complete else "HISTORICAL_FINGERPRINT_STALE",
                        f"{dependency_id} changed since {result_id}",
                        artifact_id=result_id,
                    )
                    if execution_complete:
                        self.stale_roots.add(result_id)
                        eligible = False
                else:
                    self.add("G4", "PASS", "RESULT_FINGERPRINT_CURRENT", f"{dependency_id} fingerprint is current", artifact_id=result_id)
            if execution_complete and (result_id in self.stale_roots or required_fingerprints.intersection(self.stale_roots)):
                eligible = False

            if execution_complete:
                for rule in experiment.get("acceptance_rules", []):
                    if not self.validate_acceptance_rule(result, experiment, rule, metric_rows, metric_specs):
                        eligible = False

            if trigger_partial and eligible:
                # A promotion trigger is historical routing evidence rather
                # than a successful scientific result.  It is certified only
                # after its run frame, inputs, outputs, metrics, every planned
                # diagnostic, fingerprints and acceptance rules have all been
                # checked.  The one predeclared trigger diagnostic is the sole
                # permitted scientific BLOCK.
                self.valid_promotion_trigger_result_ids.add(str(result_id))
                self.add(
                    "G4",
                    "PASS",
                    "PROMOTION_TRIGGER_RESULT_CONTRACT_VERIFIED",
                    f"{result_id} passed its full result contract apart from the exact routing trigger",
                    artifact_id=result_id,
                )
            self.result_eligibility[result_id] = eligible and successful

        # Recompute the effective route using only fully certified trigger
        # results.  Structural prevalidation alone must never make a fallback
        # eligible to support final claims.
        self.finalize_model_promotions(models_by_id, experiments_by_id, results)

        # A declared usable baseline is evidence, not a label.  Requirements
        # are solved to a fixed point because baselines may themselves depend
        # on another comparison; a single list-order pass could otherwise keep
        # a primary result eligible after its baseline later becomes invalid.
        baseline_requirements: dict[str, dict[str, set[str]]] = defaultdict(dict)
        producer_requirements: dict[str, set[str]] = defaultdict(set)
        upstream_result_requirements: dict[str, set[str]] = {}
        for result in results:
            result_id = str(result.get("id"))
            closure = self.artifact_dependency_closure(result_id)
            upstream_results = {
                dependency_id
                for dependency_id in closure
                if self.documents.get(dependency_id, {}).get("kind") == "results"
            }

            # A partial trigger controls routing but is never scientific
            # evidence. Exempt it from upstream eligibility propagation only
            # when this result is, or transitively depends on, a result from
            # the exact fallback model activated by the bound event. Merely
            # adding the event ID to an unrelated dependency closure is not
            # sufficient to launder an ineligible partial result.
            route_candidates = {result_id, *upstream_results}
            certified_trigger_history: set[str] = set()
            for trigger_result_id, (event_id, _diagnostic_id) in self.promotion_trigger_diagnostics.items():
                if trigger_result_id not in self.valid_promotion_trigger_result_ids:
                    continue
                if event_id not in closure:
                    continue
                for candidate_id in route_candidates:
                    candidate = results_by_id.get(candidate_id, {})
                    experiment = experiments_by_id.get(candidate.get("experiment_ref"), {})
                    promotion = self.promoted_model_events.get(str(experiment.get("model_ref")))
                    if (
                        promotion is not None
                        and promotion[0] == event_id
                        and event_id in experiment.get("depends_on", [])
                    ):
                        certified_trigger_history.add(trigger_result_id)
                        break
            upstream_result_requirements[result_id] = upstream_results.difference(certified_trigger_history)
        eligibility_before_baselines = dict(self.result_eligibility)
        for experiment in experiments:
            baseline_refs = set(experiment.get("baseline_refs", []))
            for result in results_by_experiment.get(experiment.get("id"), []):
                result_id = str(result.get("id"))
                bindings = comparison_bindings_by_result.get(result_id, [])
                for baseline_ref in baseline_refs:
                    bound_result_ids = {
                        str(binding.get("baseline_result_ref"))
                        for binding in bindings
                        if binding.get("baseline_model_ref") == baseline_ref
                    }
                    baseline_requirements[result_id][str(baseline_ref)] = bound_result_ids
                    if not bound_result_ids:
                        self.add(
                            "G4",
                            "BLOCK",
                            "BASELINE_RESULT_INELIGIBLE",
                            f"{result_id} has no bound result for baseline {baseline_ref}",
                            artifact_id=result_id,
                        )
                for data_ref in experiment.get("data_refs", []):
                    asset = data_assets_by_id.get(data_ref, {})
                    if asset.get("role") == "generated_intermediate" and isinstance(asset.get("producer_ref"), str):
                        producer_requirements[result_id].add(asset.get("producer_ref"))

        changed = True
        while changed:
            changed = False
            for result_id, requirements in baseline_requirements.items():
                if not self.result_eligibility.get(result_id):
                    continue
                dependencies = set(self.documents.get(result_id, {}).get("depends_on", []))
                if any(
                    not {
                        candidate_id
                        for candidate_id in candidate_ids.intersection(dependencies)
                        if self.result_eligibility.get(candidate_id)
                    }
                    for candidate_ids in requirements.values()
                ):
                    self.result_eligibility[result_id] = False
                    changed = True
            for result_id, producer_ids in producer_requirements.items():
                if self.result_eligibility.get(result_id) and any(
                    not self.result_eligibility.get(producer_id) for producer_id in producer_ids
                ):
                    self.result_eligibility[result_id] = False
                    changed = True
            for result_id, upstream_result_ids in upstream_result_requirements.items():
                if self.result_eligibility.get(result_id) and any(
                    not self.result_eligibility.get(upstream_id) for upstream_id in upstream_result_ids
                ):
                    self.result_eligibility[result_id] = False
                    changed = True

        for result_id, requirements in baseline_requirements.items():
            if not eligibility_before_baselines.get(result_id):
                continue
            dependencies = set(self.documents.get(result_id, {}).get("depends_on", []))
            for baseline_ref, candidate_result_ids in requirements.items():
                bound_eligible = {
                    candidate_id
                    for candidate_id in candidate_result_ids.intersection(dependencies)
                    if self.result_eligibility.get(candidate_id)
                }
                if bound_eligible:
                    self.add(
                        "G4",
                        "PASS",
                        "BASELINE_RESULT_ELIGIBLE",
                        f"{result_id} binds eligible baseline evidence {sorted(bound_eligible)}",
                        artifact_id=result_id,
                    )
                elif any(self.result_eligibility.get(candidate_id) for candidate_id in candidate_result_ids):
                    self.add(
                        "G4",
                        "BLOCK",
                        "BASELINE_RESULT_DEPENDENCY_MISSING",
                        f"{result_id} does not depend on an eligible result for baseline {baseline_ref}",
                        artifact_id=result_id,
                    )
                else:
                    self.add(
                        "G4",
                        "BLOCK",
                        "BASELINE_RESULT_INELIGIBLE",
                        f"{result_id} has no eligible bound result for baseline {baseline_ref}",
                        artifact_id=result_id,
                    )

        for result_id, producer_ids in producer_requirements.items():
            if not eligibility_before_baselines.get(result_id):
                continue
            for producer_id in producer_ids:
                if self.result_eligibility.get(producer_id):
                    self.add(
                        "G4",
                        "PASS",
                        "GENERATED_INPUT_PRODUCER_ELIGIBLE",
                        f"{result_id} consumes generated input from eligible {producer_id}",
                        artifact_id=result_id,
                    )
                else:
                    self.add(
                        "G4",
                        "BLOCK",
                        "GENERATED_INPUT_PRODUCER_INELIGIBLE",
                        f"{result_id} consumes generated input from ineligible {producer_id}",
                        artifact_id=result_id,
                    )

        for result_id, upstream_result_ids in upstream_result_requirements.items():
            if not eligibility_before_baselines.get(result_id):
                continue
            ineligible_upstream = sorted(
                upstream_id for upstream_id in upstream_result_ids if not self.result_eligibility.get(upstream_id)
            )
            if ineligible_upstream:
                self.add(
                    "G4",
                    "BLOCK",
                    "UPSTREAM_RESULT_INELIGIBLE",
                    f"{result_id} depends on ineligible upstream results {ineligible_upstream}",
                    artifact_id=result_id,
                )

        for model_id, (event_id, _promoted_at) in self.promoted_model_events.items():
            eligible_post_promotion = [
                str(result.get("id"))
                for result in results
                if self.result_eligibility.get(result.get("id"))
                and experiments_by_id.get(result.get("experiment_ref"), {}).get("model_ref") == model_id
                and event_id in experiments_by_id.get(result.get("experiment_ref"), {}).get("depends_on", [])
            ]
            if eligible_post_promotion:
                self.add(
                    "G4",
                    "PASS",
                    "PROMOTED_ROUTE_RESULT_ELIGIBLE",
                    f"{event_id} produced eligible post-promotion evidence {sorted(eligible_post_promotion)}",
                    artifact_id=event_id,
                )
                self.add(
                    "G2",
                    "PASS",
                    "FALLBACK_PROMOTION_EVENT_VERIFIED",
                    f"{event_id} completed the full immutable trigger→fallback→eligible-result transaction",
                    artifact_id=event_id,
                )
            else:
                self.add(
                    "G4",
                    "BLOCK",
                    "PROMOTED_ROUTE_WITHOUT_ELIGIBLE_RESULT",
                    f"{event_id} has no eligible result from the activated fallback route",
                    artifact_id=event_id,
                )

        self.validate_claims(claims_docs, results)
        self.validate_question_evidence_paths(problems, models, experiments, results, claims_docs)

        claims_by_id = {
            claim.get("id"): claim
            for registry in claims_docs
            for claim in registry.get("claims", [])
        }
        release_mode = bool(self.manifest and self.manifest.get("manifest_type") == "release")
        for registry in figures_docs:
            registry_dependencies = set(registry.get("depends_on", []))
            for figure in registry.get("figures", []):
                if release_mode and figure.get("publication_status") != "final":
                    self.add(
                        "G5",
                        "NOT_APPLICABLE",
                        "HISTORICAL_FIGURE_NOT_RELEASED",
                        f"{figure.get('id')} is retained as {figure.get('publication_status')} and is outside the release paper",
                        artifact_id=registry.get("id"),
                    )
                    continue
                semantic_dependencies = set(figure.get("source_result_refs", []))
                semantic_dependencies.update(
                    self.id_definitions[reference][0]
                    for reference in figure.get("claim_refs", [])
                    if reference in self.id_definitions
                )
                missing_dependency_edges = semantic_dependencies.difference(registry_dependencies)
                if missing_dependency_edges:
                    self.add(
                        "G5",
                        "BLOCK",
                        "FIGURE_DEPENDENCY_MISSING",
                        f"{figure.get('id')} semantic refs are missing from registry depends_on: {sorted(missing_dependency_edges)}",
                        artifact_id=registry.get("id"),
                    )
                output_path = figure.get("output", {}).get("path")
                if not isinstance(output_path, str) or Path(output_path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
                    self.add(
                        "G5",
                        "BLOCK",
                        "FIGURE_OUTPUT_TYPE_INVALID",
                        f"{figure.get('id')} output must be PNG, JPEG, SVG or PDF",
                        artifact_id=registry.get("id"),
                    )
                else:
                    try:
                        resolved_output = safe_project_path(self.root, output_path, must_exist=True)
                        validate_visual_file(resolved_output)
                    except RuntimeError as exc:
                        self.add(
                            "G5",
                            "ENV_BLOCK",
                            "FIGURE_VALIDATOR_UNAVAILABLE",
                            f"{figure.get('id')}: {exc}",
                            path=output_path,
                            artifact_id=registry.get("id"),
                        )
                    except (OSError, ValueError, ET.ParseError) as exc:
                        self.add(
                            "G5",
                            "BLOCK",
                            "FIGURE_CONTENT_INVALID",
                            f"{figure.get('id')} is not a valid {Path(output_path).suffix.lower()} visual: {exc}",
                            path=output_path,
                            artifact_id=registry.get("id"),
                        )
                    else:
                        self.add(
                            "G5",
                            "PASS",
                            "FIGURE_CONTENT_VALID",
                            f"{figure.get('id')} content parses as {Path(output_path).suffix.lower()}",
                            path=output_path,
                            artifact_id=registry.get("id"),
                        )
                if figure.get("provenance_type") == "derived" and not figure.get("source_result_refs"):
                    self.add("G5", "BLOCK", "DERIVED_FIGURE_WITHOUT_RESULT", f"{figure.get('id')} has no source result", artifact_id=registry.get("id"))
                if figure.get("provenance_type") == "derived":
                    source_files = {
                        (item.get("path"), item.get("sha256"))
                        for item in figure.get("source_files", [])
                        if isinstance(item, dict)
                    }
                    allowed_source_files = {
                        (item.get("path"), item.get("sha256"))
                        for result_ref in figure.get("source_result_refs", [])
                        for item in [
                            *[
                                output.get("file", {})
                                for output in self.documents.get(result_ref, {}).get("outputs", [])
                            ],
                            *self.documents.get(result_ref, {}).get("logs", []),
                            *[
                                evidence
                                for diagnostic in self.documents.get(result_ref, {}).get("diagnostics", [])
                                for evidence in diagnostic.get("evidence_files", [])
                            ],
                        ]
                        if isinstance(item, dict)
                    }
                    if not source_files or not source_files.issubset(allowed_source_files):
                        self.add(
                            "G5",
                            "BLOCK",
                            "FIGURE_SOURCE_FILE_UNTRACED",
                            f"{figure.get('id')} source_files must be hashed outputs, logs or diagnostic evidence of its cited results",
                            artifact_id=registry.get("id"),
                        )
                    generator_paths = {
                        item.get("path") for item in figure.get("generator_files", []) if isinstance(item, dict)
                    }
                    generator_argv = figure.get("generator_argv", [])
                    executable_generators = {
                        path
                        for path in generator_paths
                        if command_executes_project_path(
                            self.root,
                            argv=generator_argv,
                            cwd=".",
                            expected_relative=path,
                        )[0]
                    }
                    if not executable_generators:
                        self.add(
                            "G5",
                            "BLOCK",
                            "FIGURE_GENERATOR_ENTRYPOINT_MISMATCH",
                            f"{figure.get('id')} generator_argv does not execute a registered generator file through a supported runner",
                            artifact_id=registry.get("id"),
                        )
                    for result_ref in figure.get("source_result_refs", []):
                        if not self.result_eligibility.get(result_ref):
                            self.add(
                                "G5",
                                "BLOCK",
                                "FIGURE_SOURCE_RESULT_INELIGIBLE",
                                f"{figure.get('id')} derives from ineligible {result_ref}",
                                artifact_id=registry.get("id"),
                            )
                if release_mode:
                    if figure.get("provenance_type") == "derived" and not figure.get("claim_refs"):
                        self.add(
                            "G5",
                            "BLOCK",
                            "RELEASE_DERIVED_FIGURE_WITHOUT_CLAIM",
                            f"{figure.get('id')} is a release figure but supports no final claim",
                            artifact_id=registry.get("id"),
                        )
                    for claim_ref in figure.get("claim_refs", []):
                        if claims_by_id.get(claim_ref, {}).get("publication_status") != "final":
                            self.add(
                                "G5",
                                "BLOCK",
                                "FIGURE_CLAIM_NOT_FINAL",
                                f"{figure.get('id')} cites non-final {claim_ref}",
                                artifact_id=registry.get("id"),
                            )

    def artifact_dependency_closure(self, artifact_id: str) -> set[str]:
        closure: set[str] = set()
        queue: deque[str] = deque([artifact_id])
        while queue:
            current = queue.popleft()
            entry = self.manifest_artifacts.get(current, {})
            dependencies = {
                *entry.get("depends_on", []),
                *self.documents.get(current, {}).get("depends_on", []),
            }
            for dependency in dependencies:
                if dependency in self.manifest_artifacts and dependency not in closure:
                    closure.add(dependency)
                    queue.append(dependency)
        closure.discard(artifact_id)
        return closure

    def validate_model_promotions(
        self,
        promotions: list[dict[str, Any]],
        models_by_id: dict[Any, dict[str, Any]],
        experiments_by_id: dict[Any, dict[str, Any]],
        results_by_id: dict[Any, dict[str, Any]],
    ) -> None:
        """Validate immutable promotion events and compute the effective route.

        Model specifications are never rewritten during activation.  The old
        selected primary, the predeclared conditional fallback and the partial
        trigger result remain byte-identical; a separate fingerprinted event
        changes only which existing model is effective for later evidence.
        """

        effective = {
            str(model_id)
            for model_id, model in models_by_id.items()
            if model.get("role") == "primary"
            and model.get("method_selection", {}).get("decision") == "selected"
        }
        ordered: list[tuple[datetime, dict[str, Any]]] = []
        for event in promotions:
            try:
                ordered.append((parse_rfc3339(event.get("promoted_at")), event))
            except ValueError as exc:
                self.add("G2", "BLOCK", "PROMOTION_TIME_INVALID", f"{event.get('id')}: {exc}", artifact_id=event.get("id"))
        ordered.sort(key=lambda item: (item[0], str(item[1].get("id"))))
        fallback_activation_owner: dict[str, str] = {}

        for promoted_at, event in ordered:
            finding_start = len(self.findings)
            event_id = str(event.get("id"))
            fallback_ref = event.get("source_fallback_ref")
            primary_ref = event.get("replaces_primary_ref")
            trigger_result_ref = event.get("trigger_result_ref")
            trigger_diagnostic_ref = event.get("trigger_diagnostic_ref")
            fallback = models_by_id.get(fallback_ref)
            replaced = models_by_id.get(primary_ref)
            trigger_result = results_by_id.get(trigger_result_ref)
            required_dependencies = {fallback_ref, primary_ref, trigger_result_ref}

            if isinstance(fallback_ref, str):
                previous_owner = fallback_activation_owner.get(fallback_ref)
                if previous_owner is not None:
                    self.add(
                        "G2",
                        "BLOCK",
                        "PROMOTION_FALLBACK_ALREADY_ACTIVATED",
                        f"{event_id} reuses fallback {fallback_ref}, already owned by immutable event {previous_owner}",
                        artifact_id=event_id,
                    )
                else:
                    fallback_activation_owner[fallback_ref] = event_id

            if set(event.get("depends_on", [])) != required_dependencies:
                self.add("G2", "BLOCK", "PROMOTION_DEPENDENCIES_INVALID", f"{event_id} dependencies must exactly bind fallback, replaced route and trigger result", artifact_id=event_id)
            fingerprints = event.get("fingerprints", {})
            if set(fingerprints) != required_dependencies:
                self.add("G2", "STALE", "PROMOTION_FINGERPRINT_CLOSURE_MISMATCH", f"{event_id} fingerprints must exactly bind its direct immutable evidence", artifact_id=event_id)
            for dependency in required_dependencies:
                if fingerprints.get(dependency) != self.current_hashes.get(dependency):
                    self.add("G2", "STALE", "PROMOTION_FINGERPRINT_STALE", f"{event_id} no longer matches {dependency}", artifact_id=event_id)

            if fallback is None or fallback.get("role") != "fallback" or fallback.get("method_selection", {}).get("decision") != "conditional":
                self.add("G2", "BLOCK", "PROMOTION_SOURCE_FALLBACK_INVALID", f"{event_id} source fallback is missing or was not preserved as conditional", artifact_id=event_id)
            if replaced is None:
                self.add("G2", "BLOCK", "PROMOTION_REPLACED_PRIMARY_MISSING", f"{event_id} replaced model does not resolve", artifact_id=event_id)
            elif str(primary_ref) not in effective:
                self.add("G2", "BLOCK", "PROMOTION_REPLACED_ROUTE_NOT_EFFECTIVE", f"{event_id} does not replace the effective primary route at that time", artifact_id=event_id)

            rules = [
                rule
                for rule in (replaced or {}).get("fallback_rules", [])
                if rule.get("model_ref") == fallback_ref and rule.get("action") == "promote_to_primary"
            ]
            if len(rules) != 1:
                self.add("G2", "BLOCK", "PROMOTION_RULE_AMBIGUOUS", f"{event_id} must resolve exactly one predeclared fallback rule", artifact_id=event_id)

            trigger_experiment = None
            if trigger_result is None or trigger_result.get("run_status") != "partial":
                self.add("G2", "BLOCK", "PROMOTION_TRIGGER_RESULT_INVALID", f"{event_id} requires one retained partial execution with a blocking scientific trigger", artifact_id=event_id)
            else:
                trigger_experiment = experiments_by_id.get(trigger_result.get("experiment_ref"))
                if trigger_experiment is None or trigger_experiment.get("model_ref") != primary_ref:
                    self.add("G2", "BLOCK", "PROMOTION_TRIGGER_PRIMARY_MISMATCH", f"{trigger_result_ref} was not produced by the replaced route {primary_ref}", artifact_id=event_id)

            diagnostics = [
                item
                for item in (trigger_result or {}).get("diagnostics", [])
                if item.get("id") == trigger_diagnostic_ref
            ]
            source_check = None
            if len(diagnostics) != 1 or len(rules) != 1:
                self.add("G2", "BLOCK", "PROMOTION_TRIGGER_BINDING_AMBIGUOUS", f"{event_id} must bind exactly one trigger diagnostic and fallback rule", artifact_id=event_id)
            else:
                diagnostic = diagnostics[0]
                rule = rules[0]
                source_check = next(
                    (
                        check
                        for check in (replaced or {}).get("validation_plan", {}).get("checks", [])
                        if check.get("id") == rule.get("trigger_check_ref")
                    ),
                    None,
                )
                condition_valid = source_check is not None and (
                    source_check.get("applicability") == "required"
                    or (
                        source_check.get("applicability") == "conditional"
                        and diagnostic.get("condition_met") is True
                        and isinstance(diagnostic.get("condition_evidence"), str)
                        and bool(diagnostic.get("condition_evidence").strip())
                    )
                )
                if (
                    diagnostic.get("check_ref") != rule.get("trigger_check_ref")
                    or diagnostic.get("status") != "BLOCK"
                    or not condition_valid
                    or source_check.get("criticality") != "blocking"
                    or source_check.get("failure_response") not in {"block_result", "return_to_modeling"}
                ):
                    self.add("G2", "BLOCK", "PROMOTION_TRIGGER_NOT_BLOCKING", f"{event_id} trigger is not an active predeclared blocking failure", artifact_id=event_id)
                else:
                    run = trigger_result.get("run", {})
                    if (
                        trigger_experiment is None
                        or run.get("exit_code") != 0
                        or run.get("argv") != trigger_experiment.get("command", {}).get("argv")
                        or run.get("cwd") != trigger_experiment.get("command", {}).get("cwd")
                        or run.get("seeds") != trigger_experiment.get("seeds")
                        or run.get("repetitions_completed") != trigger_experiment.get("repetitions")
                    ):
                        self.add("G2", "BLOCK", "PROMOTION_TRIGGER_RUN_INVALID", f"{event_id} trigger execution did not complete its predeclared frame", artifact_id=event_id)
                    threshold = source_check.get("threshold")
                    source_file = diagnostic.get("source_file")
                    extractor = diagnostic.get("extractor")
                    run_file_refs = [
                        *trigger_result.get("inputs", []),
                        *[output.get("file", {}) for output in trigger_result.get("outputs", [])],
                        *trigger_result.get("logs", []),
                    ]
                    signatures = {
                        (item.get("path"), item.get("sha256"))
                        for item in run_file_refs
                        if isinstance(item, dict)
                    }
                    source_signature = (source_file.get("path"), source_file.get("sha256")) if isinstance(source_file, dict) else None
                    if not isinstance(threshold, dict) or source_signature not in signatures or not isinstance(extractor, dict):
                        self.add("G2", "BLOCK", "PROMOTION_TRIGGER_EVIDENCE_INVALID", f"{event_id} trigger must be recomputable from a hashed run file", artifact_id=event_id)
                    else:
                        try:
                            observed = extracted_decimal(extract_metric_value(safe_project_path(self.root, source_file.get("path"), must_exist=True), extractor))
                            recorded = decimal_number(diagnostic.get("observed", {}).get("value"))
                            threshold_value = decimal_number(threshold.get("value"))
                            operation = NUMERIC_OPERATIONS[threshold.get("operator")]
                            if diagnostic.get("observed", {}).get("unit") != threshold.get("unit"):
                                raise ValueError("trigger units differ")
                        except (OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                            self.add("G2", "BLOCK", "PROMOTION_TRIGGER_RECOMPUTE_FAILED", f"{event_id}: {exc}", artifact_id=event_id)
                        else:
                            if observed != recorded or operation(observed, threshold_value):
                                self.add("G2", "BLOCK", "PROMOTION_TRIGGER_RECOMPUTE_MISMATCH", f"{event_id} trigger bytes do not reproduce the declared failed threshold", artifact_id=event_id)

            if trigger_result is not None:
                expected_closure = self.artifact_dependency_closure(str(trigger_result_ref))
                trigger_fingerprints = trigger_result.get("fingerprints", {})
                if set(trigger_fingerprints) != expected_closure or any(
                    trigger_fingerprints.get(dependency) != self.current_hashes.get(dependency)
                    for dependency in expected_closure
                ):
                    self.add("G2", "STALE", "PROMOTION_TRIGGER_FINGERPRINT_STALE", f"{event_id} trigger result does not bind its complete current dependency closure", artifact_id=event_id)
                try:
                    trigger_finished = parse_rfc3339(trigger_result.get("run", {}).get("finished_at"))
                    if promoted_at < trigger_finished:
                        raise ValueError("promoted_at precedes the trigger result")
                except ValueError as exc:
                    self.add("G2", "BLOCK", "PROMOTION_TIME_INVALID", f"{event_id}: {exc}", artifact_id=event_id)
            if promoted_at > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                self.add("G2", "BLOCK", "PROMOTION_TIME_INVALID", f"{event_id}: promoted_at is implausibly in the future", artifact_id=event_id)

            new_experiments = [
                experiment
                for experiment in experiments_by_id.values()
                if experiment.get("model_ref") == fallback_ref
                and event_id in experiment.get("depends_on", [])
                and fallback_ref in experiment.get("depends_on", [])
            ]
            if not new_experiments:
                self.add("G3", "BLOCK", "PROMOTED_MODEL_WITHOUT_NEW_EXPERIMENT", f"{event_id} has no post-promotion experiment depending on the event and fallback model", artifact_id=event_id)

            new_failures = [
                finding
                for finding in self.findings[finding_start:]
                if finding.get("status") in {"BLOCK", "STALE", "ENV_BLOCK"}
            ]
            if not new_failures:
                effective.discard(str(primary_ref))
                effective.add(str(fallback_ref))
                self.promotion_trigger_diagnostics[str(trigger_result_ref)] = (event_id, str(trigger_diagnostic_ref))
                self.promoted_model_events[str(fallback_ref)] = (event_id, promoted_at)
                self.promotion_candidates.append(
                    {
                        "event_id": event_id,
                        "primary_ref": str(primary_ref),
                        "fallback_ref": str(fallback_ref),
                        "trigger_result_ref": str(trigger_result_ref),
                        "trigger_diagnostic_ref": str(trigger_diagnostic_ref),
                        "promoted_at": promoted_at,
                    }
                )

        self.effective_primary_model_ids = effective

    def finalize_model_promotions(
        self,
        models_by_id: dict[Any, dict[str, Any]],
        experiments_by_id: dict[Any, dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> None:
        """Certify candidate promotions after full trigger-result validation.

        The prevalidation pass is needed so the auditor can recognize the one
        expected partial trigger and can enforce post-promotion timestamps.
        This pass removes any candidate whose trigger failed another result
        invariant, then recomputes the route from the immutable event order.
        """

        effective = {
            str(model_id)
            for model_id, model in models_by_id.items()
            if model.get("role") == "primary"
            and model.get("method_selection", {}).get("decision") == "selected"
        }
        certified_events: dict[str, tuple[str, datetime]] = {}
        certified_triggers: dict[str, tuple[str, str]] = {}
        certified_event_ids: set[str] = set()

        for candidate in self.promotion_candidates:
            event_id = candidate["event_id"]
            primary_ref = candidate["primary_ref"]
            fallback_ref = candidate["fallback_ref"]
            trigger_result_ref = candidate["trigger_result_ref"]
            if trigger_result_ref not in self.valid_promotion_trigger_result_ids:
                self.add(
                    "G2",
                    "BLOCK",
                    "PROMOTION_TRIGGER_RESULT_CONTRACT_INVALID",
                    f"{event_id} trigger {trigger_result_ref} failed at least one non-trigger result invariant",
                    artifact_id=event_id,
                )
                continue
            if primary_ref not in effective:
                self.add(
                    "G2",
                    "BLOCK",
                    "PROMOTION_CERTIFIED_ROUTE_NOT_EFFECTIVE",
                    f"{event_id} cannot replace {primary_ref} because an earlier promotion in its route was not certified",
                    artifact_id=event_id,
                )
                continue
            effective.discard(primary_ref)
            effective.add(fallback_ref)
            certified_events[fallback_ref] = (event_id, candidate["promoted_at"])
            certified_triggers[trigger_result_ref] = (
                event_id,
                candidate["trigger_diagnostic_ref"],
            )
            certified_event_ids.add(event_id)

        self.effective_primary_model_ids = effective
        self.promoted_model_events = certified_events
        self.promotion_trigger_diagnostics = certified_triggers

        # A successful run that depends on an uncertified candidate event may
        # not remain eligible merely because it was checked while the route was
        # provisionally active.  Bind each fallback run to its exact certified
        # event, which also protects against event-ID laundering.
        candidate_events_by_fallback: dict[str, set[str]] = defaultdict(set)
        for candidate in self.promotion_candidates:
            candidate_events_by_fallback[candidate["fallback_ref"]].add(candidate["event_id"])
        for result in results:
            result_id = str(result.get("id"))
            experiment = experiments_by_id.get(result.get("experiment_ref"), {})
            fallback_ref = str(experiment.get("model_ref"))
            candidate_events = candidate_events_by_fallback.get(fallback_ref, set())
            bound_candidates = candidate_events.intersection(experiment.get("depends_on", []))
            if bound_candidates and not bound_candidates.intersection(certified_event_ids):
                self.result_eligibility[result_id] = False
                self.add(
                    "G4",
                    "BLOCK",
                    "RESULT_PROMOTION_EVENT_UNCERTIFIED",
                    f"{result_id} depends on uncertified fallback promotion event(s) {sorted(bound_candidates)}",
                    artifact_id=result_id,
                )

    def validate_objective_reconciliation(
        self,
        *,
        result: dict[str, Any],
        experiment: dict[str, Any],
        model: dict[str, Any],
        diagnostic: dict[str, Any],
        metric_rows: dict[str, list[dict[str, Any]]],
        metric_specs: dict[str, dict[str, Any]],
    ) -> bool:
        """Recompute fixed-decision best-response gain from structured evidence.

        The auditor cannot prove that the independent script actually solves
        the claimed auxiliary optimization.  It can, however, make a same-
        assignment re-sum structurally visible by requiring disjoint variable
        scopes, a separately hashed code file and an explicit solver/method.
        """

        result_id = result.get("id")
        check_ref = diagnostic.get("check_ref")
        reconciliation = diagnostic.get("objective_reconciliation")
        required_fields = {
            "objective_metric_ref",
            "fixed_primary_decisions",
            "reoptimized_auxiliary_variables",
            "solver_objective",
            "best_response_objective",
            "repair_gain",
            "registration_timing",
            "reconciliation_code_file",
            "reconciliation_method",
        }
        if not isinstance(reconciliation, dict):
            missing_fields = sorted(required_fields)
        else:
            missing_fields = sorted(required_fields.difference(reconciliation))
        absolute_tolerance = (
            reconciliation.get("absolute_tolerance")
            if isinstance(reconciliation, dict)
            else None
        )
        relative_tolerance = (
            reconciliation.get("relative_tolerance")
            if isinstance(reconciliation, dict)
            else None
        )
        if missing_fields or not (
            is_finite_number(absolute_tolerance) or is_finite_number(relative_tolerance)
        ):
            tolerance_note = (
                ""
                if is_finite_number(absolute_tolerance)
                or is_finite_number(relative_tolerance)
                else "; at least one finite tolerance is required"
            )
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_INCOMPLETE",
                f"{result_id}/{check_ref} lacks required reconciliation fields {missing_fields}{tolerance_note}",
                artifact_id=result_id,
            )
            return False

        fixed_primary = reconciliation.get("fixed_primary_decisions")
        reoptimized_auxiliary = reconciliation.get("reoptimized_auxiliary_variables")
        fixed_set = set(fixed_primary) if isinstance(fixed_primary, list) else set()
        auxiliary_set = (
            set(reoptimized_auxiliary)
            if isinstance(reoptimized_auxiliary, list)
            else set()
        )
        model_symbol_ids = {
            symbol.get("id")
            for symbol in model.get("symbols", [])
            if isinstance(symbol, dict) and isinstance(symbol.get("id"), str)
        }
        unknown_variables = (fixed_set | auxiliary_set).difference(model_symbol_ids)
        if (
            not fixed_set
            or not auxiliary_set
            or fixed_set.intersection(auxiliary_set)
            or unknown_variables
        ):
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_SCOPE_INVALID",
                (
                    f"{result_id}/{check_ref} requires non-empty disjoint primary/auxiliary symbol sets; "
                    f"overlap={sorted(fixed_set.intersection(auxiliary_set))}, "
                    f"unknown={sorted(unknown_variables)}"
                ),
                artifact_id=result_id,
            )
            return False

        reconciliation_code = reconciliation.get("reconciliation_code_file")
        code_signature = (
            reconciliation_code.get("path"),
            reconciliation_code.get("sha256"),
        ) if isinstance(reconciliation_code, dict) else None
        experiment_code_signatures = {
            (item.get("path"), item.get("sha256"))
            for item in experiment.get("code_files", [])
            if isinstance(item, dict)
        }
        if code_signature is None or code_signature not in experiment_code_signatures:
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_INCOMPLETE",
                f"{result_id}/{check_ref} reconciliation code and SHA-256 are not bound in experiment.code_files",
                artifact_id=result_id,
            )
            return False
        try:
            reconciliation_path = safe_project_path(
                self.root, reconciliation_code.get("path"), must_exist=True
            )
            entrypoint_path = safe_project_path(
                self.root, model.get("algorithm", {}).get("entrypoint"), must_exist=True
            )
            same_as_entrypoint = reconciliation_path == entrypoint_path or os.path.samefile(
                reconciliation_path, entrypoint_path
            )
        except (OSError, TypeError, ValueError) as exc:
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_INCOMPLETE",
                f"{result_id}/{check_ref} cannot resolve its independent code binding: {exc}",
                artifact_id=result_id,
            )
            return False
        if same_as_entrypoint:
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_NOT_INDEPENDENT",
                f"{result_id}/{check_ref} reuses the experiment's main model entrypoint",
                artifact_id=result_id,
            )
            return False

        objective_metric_ref = reconciliation.get("objective_metric_ref")
        objective_spec = metric_specs.get(objective_metric_ref)
        objective_rows = metric_rows.get(objective_metric_ref, [])
        solver_objective = reconciliation.get("solver_objective")
        best_response_objective = reconciliation.get("best_response_objective")
        recorded_gain = reconciliation.get("repair_gain")
        measurements = (solver_objective, best_response_objective, recorded_gain)
        objective_unit = objective_spec.get("unit") if isinstance(objective_spec, dict) else None
        direction = objective_spec.get("direction") if isinstance(objective_spec, dict) else None
        if (
            len(objective_rows) != 1
            or direction not in {"minimize", "maximize"}
            or not isinstance(objective_unit, str)
            or any(
                not isinstance(measurement, dict)
                or not is_finite_number(measurement.get("value"))
                or measurement.get("unit") != objective_unit
                for measurement in measurements
            )
        ):
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_INCOMPLETE",
                f"{result_id}/{check_ref} must bind one finite minimize/maximize metric with consistent units",
                artifact_id=result_id,
            )
            return False

        result_objective = objective_rows[0].get("measurement", {})
        solver_value = decimal_number(solver_objective["value"])
        best_response_value = decimal_number(best_response_objective["value"])
        result_objective_value = (
            result_objective.get("value")
            if isinstance(result_objective, dict)
            else None
        )
        if (
            not is_finite_number(result_objective_value)
            or result_objective.get("unit") != objective_unit
            or decimal_number(result_objective_value) != solver_value
        ):
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_MISMATCH",
                f"{result_id}/{check_ref} solver_objective does not equal the registered result metric",
                artifact_id=result_id,
            )
            return False

        computed_gain = (
            best_response_value - solver_value
            if direction == "maximize"
            else solver_value - best_response_value
        )
        registered_gain = decimal_number(recorded_gain["value"])
        if registered_gain != computed_gain:
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_RECONCILIATION_MISMATCH",
                f"{result_id}/{check_ref} records repair_gain {registered_gain}; objectives require {computed_gain}",
                artifact_id=result_id,
            )
            return False

        timing_valid = True
        if reconciliation.get("registration_timing") == "post_result":
            if experiment.get("mode") == "exploratory":
                self.add(
                    "G4",
                    "WARN",
                    "POST_HOC_OBJECTIVE_RECONCILIATION_TOLERANCE",
                    f"{result_id}/{check_ref} tolerance was registered after results and is exploratory only",
                    artifact_id=result_id,
                )
            else:
                self.add(
                    "G4",
                    "BLOCK",
                    "CONFIRMATORY_OBJECTIVE_RECONCILIATION_TOLERANCE_POST_HOC",
                    f"{result_id}/{check_ref} cannot use a post-result tolerance as confirmatory evidence",
                    artifact_id=result_id,
                )
                timing_valid = False

        allowed_tolerances: list[Decimal] = []
        if is_finite_number(absolute_tolerance):
            allowed_tolerances.append(decimal_number(absolute_tolerance))
        if is_finite_number(relative_tolerance):
            scale = max(abs(solver_value), abs(best_response_value))
            allowed_tolerances.append(decimal_number(relative_tolerance) * scale)
        allowed_gain = max(allowed_tolerances)
        if abs(computed_gain) > allowed_gain:
            self.add(
                "G4",
                "BLOCK",
                "OBJECTIVE_REPAIR_GAIN_EXCEEDED",
                (
                    f"{result_id}/{check_ref} fixed-decision best response changes the {direction} "
                    f"objective by {computed_gain} {objective_unit}; allowed magnitude is {allowed_gain}"
                ),
                artifact_id=result_id,
            )
            return False

        self.add(
            "G4",
            "PASS",
            "OBJECTIVE_RECONCILIATION_PASS",
            f"{result_id}/{check_ref} repair_gain {computed_gain} is within tolerance {allowed_gain}",
            artifact_id=result_id,
        )
        return timing_valid

    def validate_acceptance_rule(
        self,
        result: dict[str, Any],
        experiment: dict[str, Any],
        rule: dict[str, Any],
        metric_rows: dict[str, list[dict[str, Any]]],
        metric_specs: dict[str, dict[str, Any]],
    ) -> bool:
        result_id = result.get("id")
        metric_ref = rule.get("metric_ref")
        rows = metric_rows.get(metric_ref, [])
        if len(rows) != 1:
            self.add("G4", "BLOCK", "ACCEPTANCE_METRIC_AMBIGUOUS", f"{result_id} has {len(rows)} values for acceptance metric {metric_ref}", artifact_id=result_id)
            return False
        spec = metric_specs.get(metric_ref)
        measurement = rows[0].get("measurement", {})
        value = measurement.get("value")
        threshold = rule.get("threshold")
        if not is_finite_number(value) or not is_finite_number(threshold):
            self.add("G4", "BLOCK", "ACCEPTANCE_VALUE_NONFINITE", f"{result_id}/{metric_ref} or its threshold is non-finite", artifact_id=result_id)
            return False
        if spec is None or measurement.get("unit") != rule.get("unit") or spec.get("unit") != rule.get("unit"):
            self.add("G4", "BLOCK", "ACCEPTANCE_UNIT_MISMATCH", f"{result_id}/{metric_ref} acceptance units do not agree", artifact_id=result_id)
            return False
        operator = rule.get("operator")
        operations = {
            "==": lambda left, right: left == right,
            "!=": lambda left, right: left != right,
            "<": lambda left, right: left < right,
            "<=": lambda left, right: left <= right,
            ">": lambda left, right: left > right,
            ">=": lambda left, right: left >= right,
        }
        operation = operations.get(operator)
        if operation is None:
            self.add("G4", "BLOCK", "ACCEPTANCE_OPERATOR_INVALID", f"unsupported acceptance operator {operator!r}", artifact_id=result_id)
            return False
        if not operation(decimal_number(value), decimal_number(threshold)):
            self.add("G4", "BLOCK", "ACCEPTANCE_RULE_FAILED", f"{result_id}: {value} {operator} {threshold} is false", artifact_id=result_id)
            return False
        self.add("G4", "PASS", "ACCEPTANCE_RULE_PASS", f"{result_id}: {metric_ref} satisfies {operator} {threshold}", artifact_id=result_id)
        return True

    def validate_proof_artifacts(self, claims_docs: list[dict[str, Any]]) -> None:
        """Validate final theoretical proof bytes before they cover a question."""

        for registry in claims_docs:
            for claim in registry.get("claims", []):
                if claim.get("publication_status") != "final" or claim.get("claim_type") != "theoretical":
                    continue
                claim_id = str(claim.get("id"))
                proof = claim.get("proof_artifact")
                if claim.get("epistemic_status") not in {"analytically_derived", "formally_proved"}:
                    self.add("G5", "BLOCK", "THEORETICAL_CLAIM_STATUS_INVALID", f"{claim_id} must be analytically derived or formally proved", artifact_id=registry.get("id"))
                    continue
                if not isinstance(proof, dict):
                    self.add("G5", "BLOCK", "PROOF_ARTIFACT_MISSING", f"{claim_id} has no proof artifact", artifact_id=registry.get("id"))
                    continue
                try:
                    path = safe_project_path(self.root, proof.get("path"), must_exist=True)
                    if sha256_file(path) != proof.get("sha256"):
                        raise ValueError("proof artifact hash is stale")
                    validate_proof_file(
                        path,
                        claim_id=claim_id,
                        statement=str(claim.get("statement", "")),
                    )
                except RuntimeError as exc:
                    self.add("G5", "ENV_BLOCK", "PROOF_VALIDATOR_UNAVAILABLE", f"{claim_id}: {exc}", artifact_id=registry.get("id"))
                    continue
                except (OSError, UnicodeError, ValueError, FileNotFoundError) as exc:
                    self.add("G5", "BLOCK", "PROOF_ARTIFACT_INVALID", f"{claim_id}: {exc}", artifact_id=registry.get("id"))
                    continue
                if claim.get("human_review", {}).get("status") != "PASS":
                    self.add("G5", "BLOCK", "PROOF_NOT_HUMAN_REVIEWED", f"{claim_id} proof lacks PASS human review", artifact_id=registry.get("id"))
                    continue
                if claim.get("epistemic_status") == "formally_proved":
                    review = claim.get("human_review", {})
                    rationale = str(review.get("rationale", ""))
                    proof_hash = str(proof.get("sha256", ""))
                    if claim_id not in rationale or proof_hash not in rationale:
                        self.add(
                            "G5",
                            "BLOCK",
                            "FORMAL_PROOF_RECEIPT_UNBOUND",
                            f"{claim_id} formal-proof review must cite both the claim ID and exact proof SHA-256",
                            artifact_id=registry.get("id"),
                        )
                        continue
                self.valid_final_proof_claim_ids.add(claim_id)
                self.add(
                    "G5",
                    "PASS",
                    "PROOF_ARTIFACT_VERIFIED",
                    f"{claim_id} binds an inspectable proposition, argument structure and evidence-bound review",
                    artifact_id=registry.get("id"),
                )

    def validate_claims(self, claims_docs: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
        release_mode = self.release_mode()
        result_by_id = {result.get("id"): result for result in results}
        experiment_by_id = {
            document.get("id"): document
            for artifact_id, document in self.documents.items()
            if self.artifact_is_release_active(artifact_id)
            and document.get("kind") == "experiment"
        }

        def solver_objective_interval(result: dict[str, Any]) -> tuple[Decimal, Decimal, str] | None:
            intervals: list[tuple[Decimal, Decimal, str]] = []
            for diagnostic in result.get("diagnostics", []):
                if diagnostic.get("check_type") != "solver_optimality":
                    continue
                incumbent = diagnostic.get("objective_incumbent")
                bound = diagnostic.get("objective_bound")
                if not isinstance(incumbent, dict) or not isinstance(bound, dict):
                    continue
                incumbent_value = incumbent.get("value")
                bound_value = bound.get("value")
                unit = incumbent.get("unit")
                if (
                    not is_finite_number(incumbent_value)
                    or not is_finite_number(bound_value)
                    or not isinstance(unit, str)
                    or bound.get("unit") != unit
                ):
                    continue
                incumbent_decimal = decimal_number(incumbent_value)
                bound_decimal = decimal_number(bound_value)
                intervals.append(
                    (
                        min(incumbent_decimal, bound_decimal),
                        max(incumbent_decimal, bound_decimal),
                        unit,
                    )
                )
            return intervals[0] if len(intervals) == 1 else None

        valid_assumption_ids = {
            assumption.get("id")
            for artifact_id, problem in self.documents.items()
            if self.artifact_is_release_active(artifact_id)
            if problem.get("kind") == "problem_spec"
            for assumption in problem.get("assumptions", [])
        }
        valid_deliverable_ids = {
            deliverable.get("id")
            for artifact_id, problem in self.documents.items()
            if self.artifact_is_release_active(artifact_id)
            if problem.get("kind") == "problem_spec"
            for deliverable in problem.get("deliverables", [])
        }
        marker_owners: dict[str, list[str]] = defaultdict(list)
        for registry in claims_docs:
            for claim in registry.get("claims", []):
                if claim.get("publication_status") == "final":
                    for marker in claim.get("paper_markers", []):
                        marker_owners[str(marker)].append(str(claim.get("id")))
        for marker, owners in marker_owners.items():
            if len(owners) > 1:
                self.add("G5", "BLOCK", "GLOBAL_CLAIM_MARKER_REUSED", f"paper marker {marker!r} is assigned to multiple final claims: {owners}")
        for registry in claims_docs:
            registry_dependencies = set(registry.get("depends_on", []))
            direct_semantic_refs = {
                item.get("ref")
                for claim in registry.get("claims", [])
                if claim.get("publication_status") == "final"
                for item in [*claim.get("evidence_refs", []), *claim.get("counterevidence", [])]
                if isinstance(item, dict)
            }
            nested_problem_refs = {
                reference
                for claim in registry.get("claims", [])
                if claim.get("publication_status") == "final"
                for reference in [*claim.get("assumption_refs", []), *claim.get("deliverable_refs", [])]
            }
            semantic_dependencies = set(direct_semantic_refs)
            semantic_dependencies.update(
                self.id_definitions[reference][0]
                for reference in nested_problem_refs
                if reference in self.id_definitions
            )
            missing_dependency_edges = semantic_dependencies.difference(registry_dependencies)
            if missing_dependency_edges:
                self.add(
                    "G5",
                    "BLOCK",
                    "CLAIM_EVIDENCE_DEPENDENCY_MISSING",
                    f"{registry.get('id')} omits semantic evidence dependencies {sorted(missing_dependency_edges)}",
                    artifact_id=registry.get("id"),
                )
            for claim in registry.get("claims", []):
                if release_mode and claim.get("publication_status") != "final":
                    self.add("G5", "NOT_APPLICABLE", "HISTORICAL_CLAIM_NOT_RELEASED", f"{claim.get('id')} is retained as {claim.get('publication_status')} and is outside release evidence", artifact_id=registry.get("id"))
                    continue
                invalid_assumptions = set(claim.get("assumption_refs", [])).difference(valid_assumption_ids)
                if invalid_assumptions:
                    self.add("G5", "BLOCK", "CLAIM_ASSUMPTION_REF_INVALID", f"{claim.get('id')} cites non-assumption IDs {sorted(invalid_assumptions)}", artifact_id=registry.get("id"))
                invalid_deliverables = set(claim.get("deliverable_refs", [])).difference(valid_deliverable_ids)
                if invalid_deliverables:
                    self.add("G5", "BLOCK", "CLAIM_DELIVERABLE_REF_INVALID", f"{claim.get('id')} cites non-deliverable IDs {sorted(invalid_deliverables)}", artifact_id=registry.get("id"))
                if claim.get("publication_status") != "final":
                    self.add("G5", "NOT_APPLICABLE", "HISTORICAL_CLAIM_NOT_RELEASED", f"{claim.get('id')} is retained as {claim.get('publication_status')} and is outside release evidence", artifact_id=registry.get("id"))
                    continue
                evidence_result_ids = [
                    item.get("ref")
                    for item in claim.get("evidence_refs", [])
                    if self.id_definitions.get(item.get("ref"), (None, None, None))[2] == "results"
                ]
                if claim.get("claim_type") == "comparative":
                    comparison_result_ids = {
                        result_id
                        for result_id in evidence_result_ids
                        if isinstance(result_id, str) and result_id in result_by_id
                    }
                    for result_id in tuple(comparison_result_ids):
                        for diagnostic in result_by_id[result_id].get("diagnostics", []):
                            if diagnostic.get("check_type") != "baseline_comparison":
                                continue
                            comparison_result_ids.update(
                                baseline_result_ref
                                for binding in diagnostic.get("comparison_bindings", [])
                                if isinstance(binding, dict)
                                for baseline_result_ref in [binding.get("baseline_result_ref")]
                                if isinstance(baseline_result_ref, str) and baseline_result_ref in result_by_id
                            )

                    if len(comparison_result_ids) >= 2:
                        timing_by_result: dict[str, str] = {}
                        interval_by_result: dict[str, tuple[Decimal, Decimal, str]] = {}
                        for result_id in sorted(comparison_result_ids):
                            comparison_result = result_by_id[result_id]
                            experiment = experiment_by_id.get(comparison_result.get("experiment_ref"))
                            if isinstance(experiment, dict) and isinstance(experiment.get("decision_timing"), str):
                                timing_by_result[result_id] = experiment["decision_timing"]
                            objective_interval = solver_objective_interval(comparison_result)
                            if objective_interval is not None:
                                interval_by_result[result_id] = objective_interval

                        if len(set(timing_by_result.values())) > 1:
                            timing_summary = ", ".join(
                                f"{result_id}={timing_by_result[result_id]}"
                                for result_id in sorted(timing_by_result)
                            )
                            self.add(
                                "G5",
                                "BLOCK",
                                "DECISION_TIMING_MISMATCH",
                                f"{claim.get('id')} compares results produced under different decision timing: {timing_summary}",
                                artifact_id=registry.get("id"),
                            )

                        overlapping_pairs: list[tuple[str, str]] = []
                        interval_result_ids = sorted(interval_by_result)
                        for left_index, left_result_id in enumerate(interval_result_ids):
                            left_lower, left_upper, left_unit = interval_by_result[left_result_id]
                            for right_result_id in interval_result_ids[left_index + 1:]:
                                right_lower, right_upper, right_unit = interval_by_result[right_result_id]
                                if left_unit != right_unit:
                                    continue
                                if max(left_lower, right_lower) <= min(left_upper, right_upper):
                                    overlapping_pairs.append((left_result_id, right_result_id))
                        if overlapping_pairs:
                            self.add(
                                "G5",
                                "BLOCK",
                                "RANKING_WITHIN_SOLVER_GAP",
                                f"{claim.get('id')} ranks or compares results whose solver objective intervals overlap: {overlapping_pairs}",
                                artifact_id=registry.get("id"),
                            )
                for assertion in claim.get("numeric_assertions", []):
                    metric_ref = assertion.get("metric_ref")
                    source_token = assertion.get("source_token", "")
                    rendered_token = assertion.get("rendered_token", "")
                    token_pattern = r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?(?![A-Za-z0-9_.])"
                    reported_value = assertion.get("reported_value")
                    try:
                        source_values = {Decimal(token) for token in re.findall(token_pattern, source_token)}
                        rendered_values = {Decimal(token) for token in re.findall(token_pattern, rendered_token)}
                        reported_decimal = decimal_number(reported_value)
                    except (InvalidOperation, ValueError):
                        source_values = set()
                        rendered_values = set()
                        reported_decimal = None
                    if reported_decimal not in source_values:
                        self.add("G5", "BLOCK", "SOURCE_TOKEN_VALUE_MISMATCH", f"{claim.get('id')} source_token does not encode reported_value {reported_value}", artifact_id=registry.get("id"))
                    if reported_decimal not in rendered_values:
                        self.add("G5", "BLOCK", "RENDERED_TOKEN_VALUE_MISMATCH", f"{claim.get('id')} rendered_token does not encode reported_value {reported_value}", artifact_id=registry.get("id"))
                    assertion_unit = assertion.get("unit")
                    if assertion_unit != "1" and assertion_unit not in rendered_token:
                        self.add("G5", "BLOCK", "RENDERED_TOKEN_UNIT_MISSING", f"{claim.get('id')} rendered_token omits unit {assertion_unit!r}", artifact_id=registry.get("id"))
                    matches: list[tuple[str, dict[str, Any]]] = []
                    for result_id in evidence_result_ids:
                        for metric in result_by_id.get(result_id, {}).get("metrics", []):
                            if metric.get("metric_ref") == metric_ref:
                                matches.append((result_id, metric))
                    if len(matches) != 1:
                        code = "NUMERIC_ASSERTION_UNSUPPORTED" if not matches else "NUMERIC_ASSERTION_AMBIGUOUS"
                        self.add("G5", "BLOCK", code, f"{claim.get('id')} has {len(matches)} directly evidenced values for {metric_ref}", artifact_id=registry.get("id"))
                        continue
                    result_id, metric = matches[0]
                    assertion_result = result_by_id.get(result_id, {})
                    assertion_experiment = experiment_by_id.get(
                        assertion_result.get("experiment_ref"), {}
                    )
                    assertion_model = self.documents.get(
                        assertion_experiment.get("model_ref"), {}
                    )
                    if (
                        "optimization" in effective_validation_facets(assertion_model)
                        and scenario_holdout_is_actionable(assertion_model)
                    ):
                        metric_spec = next(
                            (
                                row
                                for row in assertion_experiment.get("metrics", [])
                                if row.get("id") == metric_ref
                            ),
                            None,
                        )
                        scenario_set_ref = (
                            metric_spec.get("scenario_set_ref")
                            if isinstance(metric_spec, dict)
                            else None
                        )
                        scenario_set = next(
                            (
                                row
                                for row in assertion_experiment.get("scenario_sets", [])
                                if isinstance(row, dict)
                                and row.get("id") == scenario_set_ref
                            ),
                            None,
                        )
                        if scenario_set is None:
                            self.add(
                                "G5",
                                "BLOCK",
                                "FINAL_CLAIM_METRIC_SCENARIO_UNBOUND",
                                (
                                    f"{claim.get('id')}/{metric_ref} does not bind a local holdout "
                                    "scenario set"
                                ),
                                artifact_id=registry.get("id"),
                            )
                        elif scenario_set.get("role") != "holdout":
                            self.add(
                                "G5",
                                "BLOCK",
                                "FINAL_CLAIM_SELECTION_SCENARIO_METRIC",
                                (
                                    f"{claim.get('id')}/{metric_ref} is sourced from "
                                    f"{scenario_set_ref} role={scenario_set.get('role')!r}; final claims "
                                    "must use holdout metrics"
                                ),
                                artifact_id=registry.get("id"),
                            )
                    measurement = metric.get("measurement", {})
                    values = (
                        measurement.get("value"),
                        assertion.get("reported_value"),
                        assertion.get("absolute_tolerance"),
                        assertion.get("relative_tolerance"),
                    )
                    if not all(is_finite_number(value) for value in values):
                        self.add("G5", "BLOCK", "NUMERIC_ASSERTION_NONFINITE", f"{claim.get('id')} has a non-finite numeric assertion", artifact_id=registry.get("id"))
                        continue
                    if measurement.get("unit") != assertion.get("unit"):
                        self.add("G5", "BLOCK", "NUMERIC_ASSERTION_UNIT_MISMATCH", f"{claim.get('id')} unit differs from {result_id}", artifact_id=registry.get("id"))
                        continue
                    actual, reported, absolute_tolerance, relative_tolerance = map(decimal_number, values)
                    allowed = absolute_tolerance + relative_tolerance * abs(actual)
                    if abs(reported - actual) > allowed:
                        self.add("G5", "BLOCK", "NUMERIC_ASSERTION_OUT_OF_TOLERANCE", f"{claim.get('id')} exceeds its declared tolerance", artifact_id=registry.get("id"))
                    else:
                        self.add("G5", "PASS", "NUMERIC_ASSERTION_MATCH", f"{claim.get('id')} matches {result_id}/{metric_ref}", artifact_id=registry.get("id"))

                eligible_evidence = [result_id for result_id in evidence_result_ids if self.result_eligibility.get(result_id)]
                eligible_primary_evidence = []
                inherited_assumptions: set[str] = set()
                for result_id in eligible_evidence:
                    evidence_result = result_by_id.get(result_id, {})
                    evidence_experiment = self.documents.get(evidence_result.get("experiment_ref"), {})
                    evidence_model = self.documents.get(evidence_experiment.get("model_ref"), {})
                    if evidence_model.get("id") in self.effective_primary_model_ids:
                        eligible_primary_evidence.append(result_id)
                    inherited_assumptions.update(evidence_model.get("assumption_refs", []))
                has_proof = str(claim.get("id")) in self.valid_final_proof_claim_ids
                if not eligible_evidence and not (claim.get("claim_type") == "theoretical" and has_proof):
                    self.add("G5", "BLOCK", "FINAL_CLAIM_WITHOUT_ELIGIBLE_RESULT", f"{claim.get('id')} lacks a successful, current, acceptance-passing result", artifact_id=registry.get("id"))
                if not eligible_primary_evidence and not (claim.get("claim_type") == "theoretical" and has_proof):
                    self.add(
                        "G5",
                        "BLOCK",
                        "FINAL_CLAIM_WITHOUT_PRIMARY_RESULT",
                        f"{claim.get('id')} lacks evidence from a selected primary model",
                        artifact_id=registry.get("id"),
                    )
                missing_assumptions = inherited_assumptions.difference(claim.get("assumption_refs", []))
                if missing_assumptions:
                    self.add(
                        "G5",
                        "BLOCK",
                        "CLAIM_ASSUMPTION_INHERITANCE_MISSING",
                        f"{claim.get('id')} omits assumptions inherited from its evidence models: {sorted(missing_assumptions)}",
                        artifact_id=registry.get("id"),
                    )
                for result_id in evidence_result_ids:
                    if not self.result_eligibility.get(result_id):
                        self.add("G5", "BLOCK", "FINAL_CLAIM_INELIGIBLE_RESULT", f"{claim.get('id')} cites ineligible {result_id}", artifact_id=registry.get("id"))
                if claim.get("human_review", {}).get("status") != "PASS":
                    self.add("G5", "BLOCK", "FINAL_CLAIM_NOT_HUMAN_REVIEWED", f"{claim.get('id')} lacks a PASS human review", artifact_id=registry.get("id"))
                if claim.get("epistemic_status") == "formally_proved" and not has_proof:
                    self.add("G5", "BLOCK", "PROOF_ARTIFACT_MISSING", f"{claim.get('id')} declares formal proof without a proof file", artifact_id=registry.get("id"))

    def validate_question_evidence_paths(
        self,
        problems: list[dict[str, Any]],
        models: list[dict[str, Any]],
        experiments: list[dict[str, Any]],
        results: list[dict[str, Any]],
        claims_docs: list[dict[str, Any]],
    ) -> None:
        if not self.manifest or self.manifest.get("manifest_type") != "release":
            return
        final_claims: list[dict[str, set[str]]] = []
        for registry in claims_docs:
            for claim in registry.get("claims", []):
                if claim.get("publication_status") == "final":
                    final_claims.append(
                        {
                            "result_refs": {
                            item.get("ref")
                            for item in claim.get("evidence_refs", [])
                            if self.id_definitions.get(item.get("ref"), (None, None, None))[2] == "results"
                            },
                            "deliverable_refs": set(claim.get("deliverable_refs", [])),
                            "proof_backed": str(claim.get("id")) in self.valid_final_proof_claim_ids,
                        }
                    )
        eligible_primary_results_by_question: dict[str, set[str]] = {}
        for problem in problems:
            for question in problem.get("questions", []):
                question_id = question.get("id")
                model_ids = {
                    model.get("id")
                    for model in models
                    if question_id in model.get("addresses", [])
                    and model.get("id") in self.effective_primary_model_ids
                }
                experiment_ids = {
                    experiment.get("id")
                    for experiment in experiments
                    if experiment.get("model_ref") in model_ids and question_id in experiment.get("question_refs", [])
                }
                eligible_results = {
                    result.get("id")
                    for result in results
                    if result.get("experiment_ref") in experiment_ids and self.result_eligibility.get(result.get("id"))
                }
                eligible_primary_results_by_question[str(question_id)] = eligible_results
                result_path_complete = eligible_results and any(
                    eligible_results.intersection(claim["result_refs"]) for claim in final_claims
                )
                proof_path_complete = any(
                    claim["proof_backed"]
                    and set(question.get("required_outputs", [])).intersection(claim["deliverable_refs"])
                    for claim in final_claims
                )
                if result_path_complete or proof_path_complete:
                    route = "selected-primary experiment evidence" if result_path_complete else "a hashed, reviewed theoretical proof"
                    self.add("G5", "PASS", "QUESTION_EVIDENCE_PATH_COMPLETE", f"{question_id} reaches a final claim through {route}", artifact_id=problem.get("id"))
                else:
                    self.add("G5", "BLOCK", "QUESTION_EVIDENCE_PATH_MISSING", f"{question_id} lacks a selected-primary question→model→experiment→eligible result→final claim path", artifact_id=problem.get("id"))

            for deliverable in problem.get("deliverables", []):
                deliverable_id = deliverable.get("id")
                deliverable_questions = set(deliverable.get("question_refs", []))
                completed = any(
                    deliverable_id in claim["deliverable_refs"]
                    and (
                        claim["proof_backed"]
                        or all(
                            eligible_primary_results_by_question.get(str(question_ref), set()).intersection(
                                claim["result_refs"]
                            )
                            for question_ref in deliverable_questions
                        )
                    )
                    for claim in final_claims
                )
                if completed:
                    self.add(
                        "G5",
                        "PASS",
                        "DELIVERABLE_EVIDENCE_PATH_COMPLETE",
                        f"{deliverable_id} reaches a final claim through eligible selected-primary evidence",
                        artifact_id=problem.get("id"),
                    )
                else:
                    self.add(
                        "G5",
                        "BLOCK",
                        "DELIVERABLE_EVIDENCE_PATH_MISSING",
                        f"{deliverable_id} lacks a complete eligible selected-primary result→final claim path",
                        artifact_id=problem.get("id"),
                    )

    def validate_reviews_and_profile(self) -> None:
        self.current_approval_sets.clear()
        self.valid_gate_approvals.clear()
        release_mode = self.release_mode()
        review_docs = [
            (artifact_id, doc)
            for artifact_id, doc in self.documents.items()
            if doc.get("kind") == "gate_review" and self.artifact_is_release_active(artifact_id)
        ]
        if release_mode and len(review_docs) != 1:
            self.add(
                "G7",
                "BLOCK",
                "RELEASE_REVIEW_LOG_COUNT_INVALID",
                f"release requires exactly one active gate_review artifact, found {len(review_docs)}",
            )

        team_by_id: dict[str, dict[str, Any]] = {}
        role_owners: dict[str, list[str]] = defaultdict(list)
        for review_artifact_id, document in review_docs:
            for member in document.get("team_members", []):
                member_id = member.get("id")
                if not isinstance(member_id, str):
                    continue
                if member_id in team_by_id:
                    self.add(
                        "G7",
                        "BLOCK",
                        "DUPLICATE_TEAM_MEMBER",
                        f"team member ID appears more than once: {member_id}",
                        artifact_id=review_artifact_id,
                    )
                    continue
                team_by_id[member_id] = member
                role = member.get("primary_role")
                if isinstance(role, str):
                    role_owners[role].append(member_id)
                display_name = str(member.get("display_name", ""))
                if release_mode and display_name.startswith("待填写-"):
                    self.add(
                        "G7",
                        "BLOCK",
                        "TEAM_PLACEHOLDER_MEMBER",
                        f"replace the placeholder display name for {member_id} before release",
                        artifact_id=review_artifact_id,
                    )

        if release_mode:
            if len(team_by_id) != 3:
                self.add(
                    "G7",
                    "BLOCK",
                    "RELEASE_TEAM_SIZE_INVALID",
                    f"release multi-signature requires exactly three declared members, found {len(team_by_id)}",
                )
            missing_roles = TEAM_ROLES.difference(role_owners)
            duplicate_roles = sorted(role for role, owners in role_owners.items() if len(owners) != 1)
            if missing_roles or duplicate_roles:
                self.add(
                    "G7",
                    "BLOCK",
                    "RELEASE_TEAM_ROLE_COVERAGE_INVALID",
                    f"one distinct owner is required for each role; missing={sorted(missing_roles)}, non_unique={duplicate_roles}",
                )

        grouped: dict[str, dict[str, list[tuple[dict[str, Any], str, datetime]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        approval_set_gates: dict[str, set[str]] = defaultdict(set)
        now = datetime.now(timezone.utc)
        for review_artifact_id, document in review_docs:
            for review in document.get("reviews", []):
                gate = review.get("gate")
                if gate not in GATES:
                    continue
                try:
                    reviewed_at = parse_rfc3339(review.get("reviewed_at"))
                    if reviewed_at > now + MAX_CLOCK_SKEW:
                        raise ValueError("reviewed_at is implausibly in the future")
                except ValueError as exc:
                    self.add(gate, "BLOCK", "REVIEW_TIME_INVALID", str(exc), artifact_id=review_artifact_id)
                    continue
                approval_set_id = review.get("approval_set_id")
                if not isinstance(approval_set_id, str):
                    continue
                approval_set_gates[approval_set_id].add(gate)
                grouped[gate][approval_set_id].append((review, review_artifact_id, reviewed_at))

                member_id = review.get("member_id")
                member = team_by_id.get(member_id)
                if member is None:
                    self.add(
                        gate,
                        "BLOCK",
                        "REVIEW_MEMBER_UNDECLARED",
                        f"review cites undeclared team member {member_id!r}",
                        artifact_id=review_artifact_id,
                    )
                elif review.get("reviewer") != member.get("display_name"):
                    self.add(
                        gate,
                        "BLOCK",
                        "REVIEWER_NAME_MISMATCH",
                        f"reviewer label does not match team declaration for {member_id}",
                        artifact_id=review_artifact_id,
                    )

        for approval_set_id, gates in approval_set_gates.items():
            if len(gates) > 1:
                self.add(
                    "G7",
                    "BLOCK",
                    "APPROVAL_SET_GATE_REUSED",
                    f"{approval_set_id} is reused across gates {sorted(gates)}",
                )

        latest: dict[str, tuple[str, list[tuple[dict[str, Any], str, datetime]]]] = {}
        for gate, sets in grouped.items():
            starts = {
                approval_set_id: min(item[2] for item in entries)
                for approval_set_id, entries in sets.items()
            }
            latest_start = max(starts.values())
            candidates = [
                approval_set_id
                for approval_set_id, started_at in starts.items()
                if started_at == latest_start
            ]
            if len(candidates) != 1:
                self.add(
                    gate,
                    "BLOCK",
                    "AMBIGUOUS_LATEST_APPROVAL_SET",
                    f"approval sets share the latest start timestamp {latest_start.isoformat()}: {sorted(candidates)}",
                )
                continue
            approval_set_id = candidates[0]
            entries = sets[approval_set_id]
            member_ids = [entry[0].get("member_id") for entry in entries]
            duplicates = sorted(
                member_id
                for member_id in set(member_ids)
                if member_ids.count(member_id) > 1
            )
            if duplicates:
                self.add(
                    gate,
                    "BLOCK",
                    "APPROVAL_SET_DUPLICATE_MEMBER",
                    f"members signed the same immutable approval set more than once: {duplicates}",
                )
                continue
            latest[gate] = (approval_set_id, entries)
            self.current_approval_sets[gate] = approval_set_id

        promotion_times: list[datetime] = []
        for artifact_id, document in self.documents.items():
            if not self.artifact_is_release_active(artifact_id) or document.get("kind") != "model_promotion":
                continue
            try:
                promotion_times.append(parse_rfc3339(document["promoted_at"]))
            except ValueError:
                continue
        latest_promotion = max(promotion_times) if promotion_times else None

        for gate in GATES:
            if gate not in latest:
                self.add(
                    gate,
                    "BLOCK" if release_mode else "WARN",
                    "HUMAN_REVIEW_MISSING",
                    "release requires a current complete approval set" if release_mode else "human approval is pending before this gate can advance",
                )
                continue

            approval_set_id, entries = latest[gate]
            group_valid = True
            if latest_promotion is not None and gate in {"G2", "G3", "G4", "G5", "G6", "G7"}:
                if any(reviewed_at < latest_promotion for _review, _artifact_id, reviewed_at in entries):
                    self.add(
                        gate,
                        "BLOCK",
                        "PROMOTION_REVIEW_PREDATES_ACTIVATION",
                        f"every signature in {approval_set_id} must be recorded after fallback promotion",
                    )
                    group_valid = False

            required_bindings = self.required_review_bindings(gate) if release_mode else set()
            member_ids: set[str] = set()
            signer_roles: set[str] = set()
            for review, review_artifact_id, _reviewed_at in entries:
                member_id = review.get("member_id")
                if isinstance(member_id, str):
                    member_ids.add(member_id)
                    member = team_by_id.get(member_id, {})
                    role = member.get("primary_role")
                    if isinstance(role, str):
                        signer_roles.add(role)
                decision = review.get("decision")
                if decision in VALIDATION_STATUSES:
                    self.add(
                        gate,
                        decision,
                        "HUMAN_REVIEW",
                        f"{member_id} signed {approval_set_id}: {review.get('rationale', 'human review recorded')}",
                    )
                if decision != "PASS":
                    group_valid = False
                    if release_mode:
                        self.add(
                            gate,
                            "BLOCK",
                            "CRITICAL_REVIEW_NOT_PASS",
                            f"every required signer in {approval_set_id} must record PASS",
                        )

                evidence_refs = set(review.get("evidence_refs", []))
                fingerprints = review.get("artifact_fingerprints", {})
                fingerprint_ids = set(fingerprints)
                if not evidence_refs or not fingerprint_ids:
                    self.add(
                        gate,
                        "BLOCK" if release_mode else "WARN",
                        "PASS_REVIEW_UNBOUND",
                        f"{member_id} must cite evidence and bind fingerprints",
                    )
                    group_valid = False
                if not fingerprint_ids.issubset(evidence_refs):
                    self.add(
                        gate,
                        "BLOCK",
                        "REVIEW_FINGERPRINT_NOT_EVIDENCE",
                        f"{member_id} fingerprinted IDs not cited as evidence: {sorted(fingerprint_ids.difference(evidence_refs))}",
                    )
                    group_valid = False
                for artifact_id, fingerprint in fingerprints.items():
                    current = self.current_hashes.get(artifact_id)
                    if current is None or current != fingerprint:
                        self.add(
                            gate,
                            "STALE",
                            "REVIEW_FINGERPRINT_STALE",
                            f"{member_id} review no longer matches {artifact_id}",
                        )
                        group_valid = False
                if release_mode and decision == "PASS":
                    missing = required_bindings.difference(fingerprint_ids.intersection(evidence_refs))
                    if missing:
                        self.add(
                            gate,
                            "BLOCK",
                            "GATE_REVIEW_REQUIRED_BINDING_MISSING",
                            f"{member_id} signature lacks gate-specific bindings: {sorted(missing)}",
                        )
                        group_valid = False

            if release_mode:
                required_roles = RELEASE_GATE_REQUIRED_ROLES[gate]
                missing_roles = required_roles.difference(signer_roles)
                if missing_roles:
                    self.add(
                        gate,
                        "BLOCK",
                        "APPROVAL_ROLE_COVERAGE_MISSING",
                        f"{approval_set_id} lacks independent signers for roles {sorted(missing_roles)}",
                    )
                    group_valid = False
                if gate in {"G1", "G2", "G5", "G6", "G7"} and member_ids != set(team_by_id):
                    self.add(
                        gate,
                        "BLOCK",
                        "FULL_TEAM_APPROVAL_MISSING",
                        f"{approval_set_id} requires all three declared members; signed={sorted(member_ids)}",
                    )
                    group_valid = False
                if gate == "G0" and not member_ids:
                    self.add(gate, "BLOCK", "APPROVAL_SIGNER_MISSING", f"{approval_set_id} has no signer")
                    group_valid = False

            if group_valid:
                self.valid_gate_approvals.add(gate)
                self.add(
                    gate,
                    "PASS",
                    "APPROVAL_SET_COMPLETE",
                    f"{approval_set_id} satisfies the current signer and evidence contract",
                )

        profile = self.manifest.get("competition_profile", {}) if self.manifest else {}
        if not profile.get("enabled"):
            self.add("G6", "NOT_APPLICABLE", "FORMAT_PROFILE_DISABLED", "paper format profile is optional and not enabled")
        else:
            profile_id = profile.get("id")
            if self.current_hashes.get(profile_id) == profile.get("sha256"):
                self.add("G6", "PASS", "FORMAT_PROFILE_BOUND", "configured competition profile is hash-bound")
            else:
                self.add("G6", "STALE", "FORMAT_PROFILE_STALE", "configured competition profile is missing or its hash is stale")

    def required_review_bindings(self, gate: str) -> set[str]:
        """Return release evidence IDs that a PASS review must bind."""

        artifacts_of_kind = lambda *kinds: {
            artifact_id
            for artifact_id, entry in self.manifest_artifacts.items()
            if entry.get("kind") in kinds and self.artifact_is_release_active(artifact_id)
        }
        if gate in {"G0", "G1"}:
            return artifacts_of_kind("problem_spec")
        if gate == "G2":
            return self.expand_review_bindings(artifacts_of_kind("model_spec", "model_promotion"))
        if gate == "G3":
            return self.expand_review_bindings(artifacts_of_kind("experiment"))
        if gate == "G4":
            return self.expand_review_bindings(
                {result_id for result_id, eligible in self.result_eligibility.items() if eligible}
            )
        if gate == "G5":
            return self.expand_review_bindings(artifacts_of_kind("claims", "figures"))
        if gate == "G6":
            paper_path = (self.manifest or {}).get("entrypoints", {}).get("paper")
            pdf_path = (self.manifest or {}).get("entrypoints", {}).get("pdf")
            bindings = {"entrypoint:paper"} if paper_path else set()
            if pdf_path:
                bindings.add("entrypoint:pdf")
            bindings.update(self.expand_review_bindings(artifacts_of_kind("paper_build")))
            bindings.update(
                item.get("id")
                for item in (self.manifest or {}).get("deliverables", [])
                if item.get("required") and (
                    item.get("path") == paper_path or str(item.get("path", "")).lower().endswith(".pdf")
                )
            )
            profile = (self.manifest or {}).get("competition_profile", {})
            if isinstance(profile, dict) and profile.get("enabled") is True and isinstance(profile.get("id"), str):
                bindings.add(profile["id"])
            return bindings
        if gate == "G7":
            bindings = {
                item.get("id")
                for item in (self.manifest or {}).get("deliverables", [])
                if item.get("required")
            }
            if self.release_snapshot_digest is not None:
                bindings.add("snapshot:release")
            return bindings
        return set()

    def expand_review_bindings(self, seed_ids: set[str]) -> set[str]:
        """Expand reviewed artifacts to their complete scientific dependency closure.

        Experiment environments live in ``manifest.environment_files`` rather
        than the artifact DAG, so matching environment bytes are added
        explicitly whenever an experiment appears in the closure.
        """

        expanded = set(seed_ids)
        for artifact_id in list(seed_ids):
            expanded.update(self.artifact_dependency_closure(artifact_id))
        environment_rows = (self.manifest or {}).get("environment_files", [])
        for artifact_id in list(expanded):
            document = self.documents.get(artifact_id, {})
            if document.get("kind") != "experiment":
                continue
            environment = document.get("environment", {})
            for row in environment_rows:
                if (
                    row.get("path") == environment.get("path")
                    and row.get("sha256") == environment.get("sha256")
                    and isinstance(row.get("id"), str)
                ):
                    expanded.add(row["id"])
        return expanded

    def validate_release_deliverables(self) -> None:
        if not self.manifest or self.manifest.get("manifest_type") != "release":
            return
        deliverables = self.manifest.get("deliverables", [])
        if not deliverables:
            self.add("G7", "BLOCK", "RELEASE_WITHOUT_DELIVERABLES", "release manifest must declare final deliverables")

        entrypoints = self.manifest.get("entrypoints", {})
        paper_relative = entrypoints.get("paper")
        pdf_relative = entrypoints.get("pdf")
        run_relative = entrypoints.get("run")
        paper_entrypoint_valid = isinstance(paper_relative, str) and Path(paper_relative).suffix.lower() in {".tex", ".typ"}
        if not paper_entrypoint_valid:
            self.add("G6", "BLOCK", "RELEASE_PAPER_ENTRYPOINT_INVALID", "release entrypoints.paper must be a .tex or .typ source")
        expected_paper_media = {
            ".tex": "application/x-tex",
            ".typ": "application/x-typst",
        }.get(Path(paper_relative).suffix.lower() if isinstance(paper_relative, str) else "")
        paper_source_deliverables = [item for item in deliverables if item.get("required") and item.get("role") == "paper_source"]
        matching_paper_deliverables = [
            item
            for item in paper_source_deliverables
            if item.get("media_type") == expected_paper_media
            and item.get("path") == paper_relative
        ]
        if len(paper_source_deliverables) != 1 or len(matching_paper_deliverables) != 1:
            self.add(
                "G7",
                "BLOCK",
                "PAPER_ENTRYPOINT_NOT_HASHED_DELIVERABLE",
                "entrypoints.paper must match exactly one required paper_source deliverable with engine-specific media_type",
                path=paper_relative,
            )

        matching_pdf_deliverables = [
            item
            for item in deliverables
            if item.get("required")
            and item.get("role") == "paper_pdf"
            and item.get("media_type") == "application/pdf"
            and item.get("path") == pdf_relative
        ]
        pdf_entrypoint_valid = isinstance(pdf_relative, str) and Path(pdf_relative).suffix.lower() == ".pdf"
        if not pdf_entrypoint_valid:
            self.add("G6", "BLOCK", "RELEASE_PDF_ENTRYPOINT_INVALID", "release entrypoints.pdf must be a .pdf file")
        if len(matching_pdf_deliverables) != 1:
            self.add(
                "G7",
                "BLOCK",
                "PDF_ENTRYPOINT_NOT_HASHED_DELIVERABLE",
                "entrypoints.pdf must match exactly one required application/pdf paper_pdf deliverable",
                path=pdf_relative,
            )

        release_run_paths = {
            path
            for name, path in entrypoints.items()
            if isinstance(name, str)
            and (name == "run" or name.startswith("run_"))
            and isinstance(path, str)
        }
        final_claim_rows = [
            claim
            for artifact_id, registry in self.documents.items()
            if self.artifact_is_release_active(artifact_id)
            if registry.get("kind") == "claims"
            for claim in registry.get("claims", [])
            if claim.get("publication_status") == "final"
        ]
        if not final_claim_rows:
            self.add("G5", "BLOCK", "RELEASE_WITHOUT_FINAL_CLAIM", "release requires at least one final claim")
        requires_computational_evidence = any(
            self.id_definitions.get(item.get("ref"), (None, None, None))[2] == "results"
            for claim in final_claim_rows
            for item in claim.get("evidence_refs", [])
            if isinstance(item, dict)
        )
        proof_only_release = bool(final_claim_rows) and all(
            str(claim.get("id")) in self.valid_final_proof_claim_ids
            and not any(
                self.id_definitions.get(item.get("ref"), (None, None, None))[2] == "results"
                for item in claim.get("evidence_refs", [])
                if isinstance(item, dict)
            )
            for claim in final_claim_rows
        )
        if final_claim_rows and not requires_computational_evidence and not proof_only_release:
            self.add("G7", "BLOCK", "RELEASE_EVIDENCE_MODE_INVALID", "a non-computational release must consist entirely of hashed, reviewed theoretical proofs")
        if requires_computational_evidence:
            for required_role in ("code", "result"):
                if not any(item.get("required") and item.get("role") == required_role for item in deliverables):
                    self.add("G7", "BLOCK", "COMPUTATIONAL_DELIVERABLE_MISSING", f"computational release requires at least one required {required_role} deliverable")
        required_release_results = {
            item.get("ref")
            for claim in final_claim_rows
            for item in claim.get("evidence_refs", [])
            if isinstance(item, dict)
            and self.id_definitions.get(item.get("ref"), (None, None, None))[2] == "results"
            and self.result_eligibility.get(item.get("ref"))
        }
        for result_id in list(required_release_results):
            required_release_results.update(
                dependency
                for dependency in self.artifact_dependency_closure(str(result_id))
                if self.manifest_artifacts.get(dependency, {}).get("kind") == "results"
            )
        uncovered_results: list[str] = []
        covered_paths: set[str] = set()
        required_code_paths: set[str] = set()
        required_result_paths: set[str] = set()
        for result_id in sorted(str(item) for item in required_release_results):
            result = self.documents.get(result_id, {})
            experiment = self.documents.get(result.get("experiment_ref"), {})
            model = self.documents.get(experiment.get("model_ref"), {})
            model_entrypoint = model.get("algorithm", {}).get("entrypoint")
            code_paths = {
                item.get("path")
                for item in experiment.get("code_files", [])
                if isinstance(item, dict)
            }
            required_code_paths.update(path for path in code_paths if isinstance(path, str))
            run_record = result.get("run", {})
            executes_entrypoint, _reason = command_executes_project_path(
                self.root,
                argv=run_record.get("argv"),
                cwd=run_record.get("cwd"),
                expected_relative=model_entrypoint,
            )
            entrypoint_covered = (
                isinstance(model_entrypoint, str)
                and model_entrypoint in release_run_paths
                and model_entrypoint in code_paths
                and executes_entrypoint
            )
            if not entrypoint_covered:
                uncovered_results.append(str(result_id))
            elif isinstance(model_entrypoint, str):
                covered_paths.add(model_entrypoint)
            required_result_paths.update(
                output.get("file", {}).get("path")
                for output in result.get("outputs", [])
                if isinstance(output, dict) and isinstance(output.get("file", {}).get("path"), str)
            )
        if requires_computational_evidence and uncovered_results:
            self.add(
                "G7",
                "BLOCK",
                "RUN_ENTRYPOINT_UNREGISTERED",
                f"release run/run_* entrypoints do not reproduce every eligible selected-primary result; uncovered={uncovered_results}",
                path=run_relative,
            )
        elif requires_computational_evidence:
            self.add("G7", "PASS", "RUN_ENTRYPOINT_REGISTERED", "release run entrypoints cover every eligible selected-primary and upstream execution", path=run_relative)

        required_code_deliverables = {
            item.get("path")
            for item in deliverables
            if item.get("required") and item.get("role") == "code"
        }
        required_result_deliverables = {
            item.get("path")
            for item in deliverables
            if item.get("required") and item.get("role") == "result"
        }
        missing_code_deliverables = sorted(required_code_paths.difference(required_code_deliverables))
        missing_result_deliverables = sorted(required_result_paths.difference(required_result_deliverables))
        if requires_computational_evidence and missing_code_deliverables:
            self.add("G7", "BLOCK", "CODE_DELIVERABLE_COVERAGE_MISSING", f"required code deliverables omit executed entrypoints {missing_code_deliverables}")
        if requires_computational_evidence and missing_result_deliverables:
            self.add("G7", "BLOCK", "RESULT_DELIVERABLE_COVERAGE_MISSING", f"required result deliverables omit published result outputs {missing_result_deliverables}")
        elif proof_only_release:
            self.add("G7", "NOT_APPLICABLE", "PROOF_ONLY_RELEASE_NO_RUN_REQUIRED", "release claims are supported only by hashed theoretical proof artifacts")

        paper_builds = [
            document
            for artifact_id, document in self.documents.items()
            if document.get("kind") == "paper_build" and self.artifact_is_release_active(artifact_id)
        ]
        if len(paper_builds) != 1:
            self.add("G6", "BLOCK", "PAPER_BUILD_RECEIPT_AMBIGUOUS", f"release requires exactly one loaded paper_build receipt; found {len(paper_builds)}")
        paper_build = paper_builds[0] if len(paper_builds) == 1 else None

        try:
            from lint_paper import PaperLint
        except ImportError as exc:
            self.add("G6", "ENV_BLOCK", "PAPER_LINT_UNAVAILABLE", str(exc), path=paper_relative if isinstance(paper_relative, str) else None)
            return

        engine = "typst" if isinstance(paper_relative, str) and paper_relative.lower().endswith(".typ") else "latex"
        receipt_cwd = paper_build.get("command", {}).get("cwd") if paper_build is not None else None
        lint = PaperLint(self.root, engine, compile_cwd=receipt_cwd)
        if paper_entrypoint_valid:
            lint.load_source_tree(paper_relative)
            lint.lint_text()
        claim_registry_paths = [
            self._display_path(path)
            for artifact_id, path in self.document_paths.items()
            if self.documents.get(artifact_id, {}).get("kind") == "claims"
            and self.artifact_is_release_active(artifact_id)
        ]
        figure_registry_paths = [
            self._display_path(path)
            for artifact_id, path in self.document_paths.items()
            if self.documents.get(artifact_id, {}).get("kind") == "figures"
            and self.artifact_is_release_active(artifact_id)
        ]
        if paper_entrypoint_valid:
            for registry_path in claim_registry_paths:
                lint.lint_claim_markers(registry_path)
            lint.lint_figure_registries(figure_registry_paths)

        verified_paper_paths: set[str] = set()
        if paper_build is not None and paper_entrypoint_valid:
            verified_paper_paths = self.validate_paper_build_receipt(
                paper_build,
                paper_relative,
                pdf_relative,
                engine,
                lint,
            )

        # A proof that bypasses computational evidence must be observed in the
        # verified source/recorder closure, or be a required appendix
        # deliverable.  Receipt declarations alone are not packaging evidence.
        required_appendix_paths = {
            item.get("path")
            for item in deliverables
            if item.get("required") is True
            and item.get("role") == "appendix"
            and isinstance(item.get("path"), str)
        }
        packaged_proof_paths = verified_paper_paths.union(required_appendix_paths)
        for claim in final_claim_rows:
            claim_id = str(claim.get("id"))
            if claim_id not in self.valid_final_proof_claim_ids:
                continue
            proof_path = claim.get("proof_artifact", {}).get("path")
            if proof_path not in packaged_proof_paths:
                self.add(
                    "G7",
                    "BLOCK",
                    "PROOF_NOT_PACKAGED",
                    f"{claim_id} proof must be in the verified paper source/resource closure or a required appendix deliverable",
                    path=proof_path,
                    artifact_id=claim_id,
                )
            else:
                self.add(
                    "G7",
                    "PASS",
                    "PROOF_PACKAGED",
                    f"{claim_id} proof is included in the verified release package",
                    path=proof_path,
                    artifact_id=claim_id,
                )

        pdf_paths = {
            item.get("path")
            for item in deliverables
            if item.get("required")
            and item.get("role") == "paper_pdf"
            and item.get("media_type") == "application/pdf"
        }
        if pdf_entrypoint_valid:
            pdf_paths.add(entrypoints["pdf"])
        for pdf_relative in sorted(path for path in pdf_paths if isinstance(path, str)):
            lint.lint_pdf(pdf_relative, None)

        lint_report = lint.report(strict=True)
        for finding in lint_report.get("findings", []):
            self.add(
                "G6",
                finding.get("status", "BLOCK"),
                f"PAPER_{finding.get('code', 'LINT_FINDING')}",
                finding.get("message", "paper lint finding"),
                path=finding.get("path"),
            )

    def validate_paper_build_receipt(
        self,
        receipt: dict[str, Any],
        paper_relative: str,
        pdf_relative: Any,
        engine: str,
        lint: Any,
    ) -> set[str]:
        """Bind the compiled PDF and return its verified local input paths."""

        finding_start = len(self.findings)
        receipt_id = str(receipt.get("id"))
        required_direct_dependencies = {
            artifact_id
            for artifact_id, entry in self.manifest_artifacts.items()
            if entry.get("kind") in {"claims", "figures"} and self.artifact_is_release_active(artifact_id)
        }
        actual_direct_dependencies = set(receipt.get("depends_on", []))
        if actual_direct_dependencies != required_direct_dependencies:
            self.add(
                "G6",
                "BLOCK",
                "PAPER_BUILD_DIRECT_DEPENDENCIES_INVALID",
                f"{receipt_id} must directly depend on every claims and figures registry: {sorted(required_direct_dependencies)}",
                artifact_id=receipt_id,
            )
        expected_closure = self.artifact_dependency_closure(receipt_id)
        fingerprints = receipt.get("fingerprints", {})
        if set(fingerprints) != expected_closure:
            self.add(
                "G6",
                "STALE",
                "PAPER_BUILD_FINGERPRINT_CLOSURE_MISMATCH",
                f"{receipt_id} fingerprints must equal its complete dependency closure: {sorted(expected_closure)}",
                artifact_id=receipt_id,
            )
        for dependency in expected_closure:
            if fingerprints.get(dependency) != self.current_hashes.get(dependency):
                self.add("G6", "STALE", "PAPER_BUILD_FINGERPRINT_STALE", f"{receipt_id} no longer matches {dependency}", artifact_id=receipt_id)

        actual_sources = {
            (self._display_path(path), sha256_file(path))
            for path in lint.sources
        }
        recorded_sources = {
            (item.get("path"), item.get("sha256"))
            for item in receipt.get("source_files", [])
            if isinstance(item, dict)
        }
        if actual_sources != recorded_sources:
            self.add("G6", "STALE", "PAPER_BUILD_SOURCE_TREE_MISMATCH", f"{receipt_id} does not bind the exact transitive paper source tree", artifact_id=receipt_id)
        static_resource_paths = set(lint.local_resources)
        expected_resource_paths = set(static_resource_paths)
        if engine == "latex":
            dependency_log = receipt.get("dependency_log")
            try:
                recorder_path = safe_project_path(self.root, dependency_log.get("path"), must_exist=True)
                if recorder_path.suffix.lower() != ".fls":
                    raise ValueError("LaTeX dependency_log must be an .fls recorder file")
                if sha256_file(recorder_path) != dependency_log.get("sha256"):
                    raise ValueError("LaTeX dependency_log hash is stale")
                recorder_inputs = parse_latex_recorder(
                    self.root,
                    receipt.get("command", {}).get("cwd"),
                    recorder_path,
                )
                static_inputs = set(lint.sources).union(static_resource_paths)
                missing_from_recorder = static_inputs.difference(recorder_inputs)
                if missing_from_recorder:
                    self.add(
                        "G6",
                        "BLOCK",
                        "LATEX_RECORDER_STATIC_INPUT_MISSING",
                        f"{receipt_id} recorder omits recognized project inputs {[self._display_path(path) for path in sorted(missing_from_recorder)]}",
                        artifact_id=receipt_id,
                    )
                implicit_tex_sources = {
                    path for path in recorder_inputs.difference(lint.sources) if path.suffix.lower() == ".tex"
                }
                if implicit_tex_sources:
                    self.add(
                        "G6",
                        "BLOCK",
                        "LATEX_RECORDER_SOURCE_UNSCANNED",
                        f"{receipt_id} consumed TeX sources outside the statically reachable source tree",
                        artifact_id=receipt_id,
                    )
                expected_resource_paths = recorder_inputs.difference(lint.sources)
            except (AttributeError, OSError, TypeError, ValueError, FileNotFoundError, UnicodeError) as exc:
                self.add("G6", "BLOCK", "LATEX_RECORDER_INVALID", f"{receipt_id}: {exc}", artifact_id=receipt_id)
        elif receipt.get("dependency_log") is not None:
            self.add("G6", "BLOCK", "TYPST_DEPENDENCY_LOG_UNEXPECTED", f"{receipt_id} Typst receipts must set dependency_log to null", artifact_id=receipt_id)
        actual_resources = {
            (self._display_path(path), sha256_file(path))
            for path in expected_resource_paths
        }
        recorded_resources = {
            (item.get("path"), item.get("sha256"))
            for item in receipt.get("resource_files", [])
            if isinstance(item, dict)
        }
        if actual_resources != recorded_resources:
            scope = "recorder-observed" if engine == "latex" else "recognized static"
            self.add("G6", "STALE", "PAPER_BUILD_RESOURCE_SET_MISMATCH", f"{receipt_id} does not bind the exact {scope} local compilation resource set", artifact_id=receipt_id)
        manifest_profile = (self.manifest or {}).get("competition_profile", {})
        expected_profile = None
        if isinstance(manifest_profile, dict) and manifest_profile.get("enabled") is True:
            expected_profile = {
                "profile_ref": manifest_profile.get("id"),
                "path": manifest_profile.get("path"),
                "sha256": manifest_profile.get("sha256"),
            }
        if receipt.get("competition_profile") != expected_profile:
            self.add("G6", "STALE", "PAPER_BUILD_PROFILE_MISMATCH", f"{receipt_id} does not bind the manifest competition profile", artifact_id=receipt_id)
        source_entrypoint = receipt.get("source_entrypoint", {})
        expected_source_hash = self.current_hashes.get("entrypoint:paper")
        if source_entrypoint.get("path") != paper_relative or source_entrypoint.get("sha256") != expected_source_hash:
            self.add("G6", "STALE", "PAPER_BUILD_ENTRYPOINT_MISMATCH", f"{receipt_id} source entrypoint differs from release entrypoints.paper", artifact_id=receipt_id)
        if receipt.get("engine") != engine:
            self.add("G6", "BLOCK", "PAPER_BUILD_ENGINE_MISMATCH", f"{receipt_id} engine differs from the paper source extension", artifact_id=receipt_id)
        command = receipt.get("command", {})
        argv = command.get("argv", [])
        cwd = command.get("cwd")
        try:
            expected_paper_path = safe_project_path(self.root, paper_relative)
        except (TypeError, ValueError) as exc:
            self.add("G6", "BLOCK", "PAPER_BUILD_ENTRYPOINT_PATH_INVALID", f"{receipt_id}: {exc}", artifact_id=receipt_id)
            return set()
        source_matches = [
            index
            for index, token in enumerate(argv)
            if resolved_command_token(self.root, cwd, token)
            == expected_paper_path
        ]
        if not source_matches or source_matches == [0]:
            self.add("G6", "BLOCK", "PAPER_BUILD_COMMAND_SOURCE_MISSING", f"{receipt_id} compiler argv does not consume the registered paper entrypoint under command.cwd", artifact_id=receipt_id)
        declared_output = command.get("output_path")
        if declared_output != pdf_relative:
            self.add("G6", "BLOCK", "PAPER_BUILD_COMMAND_OUTPUT_MISMATCH", f"{receipt_id} command.output_path differs from release entrypoints.pdf", artifact_id=receipt_id)
        try:
            cwd_path = safe_project_path(self.root, cwd, must_exist=True)
            if not cwd_path.is_dir():
                raise ValueError("command.cwd is not a directory")
            if cwd_path != expected_paper_path.parent:
                self.add(
                    "G6",
                    "BLOCK",
                    "PAPER_BUILD_CWD_SOURCE_DIR_MISMATCH",
                    f"{receipt_id} direct compiler cwd must equal the paper source directory",
                    artifact_id=receipt_id,
                )
        except (TypeError, ValueError, FileNotFoundError) as exc:
            self.add("G6", "BLOCK", "PAPER_BUILD_CWD_INVALID", f"{receipt_id}: {exc}", artifact_id=receipt_id)
        compiler_name = normalized_executable_name(receipt.get("compiler", {}).get("name"))
        executable_name = normalized_executable_name(argv[0]) if argv else ""
        if not compiler_name or compiler_name != executable_name:
            self.add("G6", "BLOCK", "PAPER_BUILD_COMPILER_COMMAND_MISMATCH", f"{receipt_id} compiler.name must exactly match command argv[0] basename", artifact_id=receipt_id)
        if executable_name not in PAPER_COMPILERS_BY_ENGINE.get(engine, set()):
            self.add("G6", "BLOCK", "PAPER_BUILD_COMPILER_UNSUPPORTED", f"{receipt_id} uses unsupported {engine} compiler {executable_name!r}", artifact_id=receipt_id)
        if engine == "latex" and not any(
            isinstance(token, str) and token.casefold() in {"-recorder", "--recorder"}
            for token in argv[1:]
        ):
            self.add("G6", "BLOCK", "LATEX_RECORDER_FLAG_MISSING", f"{receipt_id} direct LaTeX command must enable -recorder", artifact_id=receipt_id)
        output_redirect_flags = sorted(
            token
            for token in argv
            if isinstance(token, str)
            and any(token.casefold().startswith(prefix) for prefix in OUTPUT_REDIRECT_FLAG_PREFIXES)
        )
        if output_redirect_flags:
            self.add("G6", "BLOCK", "PAPER_BUILD_OUTPUT_REDIRECT_UNSUPPORTED", f"{receipt_id} changes the compiler output destination through {output_redirect_flags}; use the registered default output path", artifact_id=receipt_id)
        try:
            expected_pdf_path = safe_project_path(self.root, pdf_relative)
        except (TypeError, ValueError) as exc:
            self.add("G6", "BLOCK", "PAPER_BUILD_PDF_PATH_INVALID", f"{receipt_id}: {exc}", artifact_id=receipt_id)
            expected_pdf_path = None
        if expected_pdf_path is not None:
            if engine == "latex" and expected_paper_path.with_suffix(".pdf") != expected_pdf_path:
                self.add("G6", "BLOCK", "PAPER_BUILD_LATEX_DEFAULT_OUTPUT_MISMATCH", f"{receipt_id} PDF is not the default output derived from its TeX source", artifact_id=receipt_id)
            if engine == "typst" and not any(
                resolved_command_token(self.root, cwd, token) == expected_pdf_path
                for token in argv[1:]
            ):
                self.add("G6", "BLOCK", "PAPER_BUILD_TYPST_OUTPUT_ARGUMENT_MISSING", f"{receipt_id} Typst argv does not write the registered PDF path", artifact_id=receipt_id)
        dangerous_flags = sorted(
            token
            for token in argv
            if isinstance(token, str)
            and any(
                token.casefold() == flag or token.casefold().startswith(f"{flag}=")
                for flag in DANGEROUS_BUILD_FLAGS
            )
        )
        if dangerous_flags:
            self.add("G6", "BLOCK", "PAPER_BUILD_DANGEROUS_FLAG", f"{receipt_id} enables shell execution through {dangerous_flags}", artifact_id=receipt_id)
        try:
            log_path = safe_project_path(self.root, receipt.get("log", {}).get("path"), must_exist=True)
            if log_path.stat().st_size <= 0:
                self.add("G6", "BLOCK", "PAPER_BUILD_LOG_EMPTY", f"{receipt_id} build log is empty", artifact_id=receipt_id)
            else:
                log_text = log_path.read_text(encoding="utf-8")
                if "\x00" in log_text:
                    raise ValueError("build log contains NUL bytes")
                # Typst and wrapper tools may color diagnostics.  Remove only
                # ANSI control sequences before classifying line prefixes.
                log_scan_text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log_text)
                failure = BUILD_LOG_FAILURE_RE.search(log_scan_text)
                if failure is not None:
                    excerpt = re.sub(r"\s+", " ", failure.group(0)).strip()[:160]
                    self.add(
                        "G6",
                        "BLOCK",
                        "PAPER_BUILD_LOG_FAILURE",
                        f"{receipt_id} build log records compiler failure: {excerpt}",
                        artifact_id=receipt_id,
                    )
                elif BUILD_LOG_SUCCESS_RE.search(log_scan_text) is not None:
                    self.add(
                        "G6",
                        "PASS",
                        "PAPER_BUILD_LOG_SUCCESS_MARKER",
                        f"{receipt_id} build log contains a recognized successful LaTeX/Typst completion marker",
                        artifact_id=receipt_id,
                    )
                else:
                    # Direct compiler exit_code=0, a bound readable PDF and no
                    # known failure are still usable when a compiler emits no
                    # standard success sentence.  Crucially, a known failure
                    # can never reach PAPER_BUILD_RECEIPT_VERIFIED.
                    self.add(
                        "G6",
                        "PASS",
                        "PAPER_BUILD_LOG_NO_FAILURE",
                        f"{receipt_id} non-empty build log contains no recognized compiler failure",
                        artifact_id=receipt_id,
                    )
        except (TypeError, ValueError, FileNotFoundError, OSError, UnicodeError) as exc:
            self.add("G6", "BLOCK", "PAPER_BUILD_LOG_UNAVAILABLE", f"{receipt_id}: {exc}", artifact_id=receipt_id)
        pdf_ref = receipt.get("pdf", {})
        expected_pdf_hash = self.current_hashes.get("entrypoint:pdf")
        if pdf_ref.get("path") != pdf_relative or pdf_ref.get("sha256") != expected_pdf_hash:
            self.add("G6", "STALE", "PAPER_BUILD_PDF_MISMATCH", f"{receipt_id} PDF differs from release entrypoints.pdf", artifact_id=receipt_id)
        try:
            started = parse_rfc3339(receipt.get("started_at"))
            finished = parse_rfc3339(receipt.get("finished_at"))
            if finished < started:
                raise ValueError("finished_at precedes started_at")
            if finished > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                raise ValueError("finished_at is implausibly in the future")
            active_promotion_times = [
                parse_rfc3339(document["promoted_at"])
                for artifact_id, document in self.documents.items()
                if self.artifact_is_release_active(artifact_id)
                and document.get("kind") == "model_promotion"
            ]
            if active_promotion_times and started < max(active_promotion_times):
                raise ValueError("paper build started before the latest fallback promotion")
            upstream_result_times = [
                parse_rfc3339(self.documents[artifact_id].get("run", {}).get("finished_at"))
                for artifact_id in expected_closure
                if self.documents.get(artifact_id, {}).get("kind") == "results"
            ]
            if upstream_result_times and started < max(upstream_result_times):
                raise ValueError("paper build started before its latest upstream result finished")
        except ValueError as exc:
            self.add("G6", "BLOCK", "PAPER_BUILD_TIME_INVALID", f"{receipt_id}: {exc}", artifact_id=receipt_id)

        source_text = "\n".join(lint.sources.values())
        try:
            from pypdf import PdfReader

            pdf_path = safe_project_path(self.root, pdf_relative, must_exist=True)
            reader = PdfReader(str(pdf_path))
            pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            self.add("G6", "BLOCK", "PAPER_BUILD_PDF_TEXT_UNAVAILABLE", f"{receipt_id}: {exc}", artifact_id=receipt_id)
            return set()
        for artifact_id, registry in self.documents.items():
            if not self.artifact_is_release_active(artifact_id):
                continue
            if registry.get("kind") != "claims":
                continue
            for claim in registry.get("claims", []):
                if claim.get("publication_status") != "final":
                    continue
                for assertion in claim.get("numeric_assertions", []):
                    source_token = assertion.get("source_token")
                    rendered_token = assertion.get("rendered_token")
                    if source_token not in source_text:
                        self.add("G6", "BLOCK", "PAPER_NUMERIC_ASSERTION_MISSING_FROM_SOURCE", f"{claim.get('id')} source_token {source_token!r} is absent from the source tree", artifact_id=receipt_id)
                    if rendered_token not in pdf_text:
                        self.add("G6", "BLOCK", "PAPER_NUMERIC_ASSERTION_MISSING_FROM_PDF", f"{claim.get('id')} rendered_token {rendered_token!r} is absent from extracted PDF text", artifact_id=receipt_id)
        new_failures = [
            finding
            for finding in self.findings[finding_start:]
            if finding.get("status") in {"BLOCK", "STALE", "ENV_BLOCK"}
        ]
        if not new_failures:
            self.add("G6", "PASS", "PAPER_BUILD_RECEIPT_VERIFIED", f"{receipt_id} binds source tree, upstream contracts, build metadata, log and PDF", artifact_id=receipt_id)
            return {
                self._display_path(path)
                for path in set(lint.sources).union(expected_resource_paths)
            }
        return set()

    def enforce_release_gate_passes(self) -> None:
        """Require every computed gate to be exactly PASS for a release.

        This is evaluated after automatic checks, current fingerprint-bound
        reviews, the optional format profile, and deliverable checks.  A
        disabled format profile remains legitimate during drafting; for a
        release, G6 still needs another current PASS basis such as an explicit
        paper review or lint result recorded in the gate log.
        """

        if not self.manifest or self.manifest.get("manifest_type") != "release":
            return
        # Snapshot every gate before adding any release-summary finding.  If
        # we recomputed G7 inside the loop, a BLOCK appended for (say) G5 would
        # make G7 become BLOCK and spuriously generate a second, self-induced
        # "G7 is not PASS" finding.  The snapshot preserves the actual gate
        # states that existed when release readiness was evaluated.
        gate_snapshot = {
            gate: aggregate_status(
                [finding["status"] for finding in self.findings if finding["gate"] == gate]
            )
            for gate in GATES
        }
        for gate, current in gate_snapshot.items():
            if current != "PASS":
                message = f"release requires {gate}=PASS, current status is {current}"
                if not any(
                    finding["code"] == "RELEASE_GATE_NOT_PASS"
                    and finding["message"] == message
                    for finding in self.findings
                ):
                    self.add("G7", "BLOCK", "RELEASE_GATE_NOT_PASS", message)

    def derive_workflow_state(self) -> dict[str, Any]:
        """Derive resumable state from current evidence; never trust a saved memory field."""

        hard_by_gate = {
            gate: any(
                finding["status"] in HARD_STATUSES
                for finding in self.findings
                if finding["gate"] == gate
            )
            for gate in GATES
        }
        last_valid_gate: str | None = None
        for gate in GATES:
            if gate not in self.valid_gate_approvals or hard_by_gate[gate]:
                break
            last_valid_gate = gate

        first_hard_gate = next((gate for gate in GATES if hard_by_gate[gate]), None)
        if first_hard_gate is not None:
            rollback_target = GATE_ROLLBACK_TARGET[first_hard_gate]
            return {
                "workflow_state": rollback_target,
                "last_valid_gate": last_valid_gate,
                "rollback_target": rollback_target,
                "next_legal_action": (
                    f"In the existing project, resolve {first_hard_gate} BLOCK/ENV_BLOCK/STALE "
                    f"findings and resume from {rollback_target} in place while preserving the current "
                    "project structure and confirmed files; then re-audit before collecting downstream "
                    "approvals."
                ),
            }

        next_gate = next((gate for gate in GATES if gate not in self.valid_gate_approvals), None)
        if next_gate is not None:
            return {
                "workflow_state": f"WAIT_{next_gate}",
                "last_valid_gate": last_valid_gate,
                "rollback_target": None,
                "next_legal_action": (
                    f"Collect a current {next_gate} approval set with its required independent signers "
                    "and SHA-256 evidence bindings."
                ),
            }

        if self.release_mode():
            return {
                "workflow_state": "SUBMISSION_READY",
                "last_valid_gate": "G7",
                "rollback_target": None,
                "next_legal_action": "No further workflow transition is required; preserve this immutable release snapshot.",
            }
        return {
            "workflow_state": "RELEASE_QA",
            "last_valid_gate": "G7",
            "rollback_target": None,
            "next_legal_action": (
                "Create the final release manifest, refresh every required hash, register snapshot:release, "
                "and collect a new G7 approval set against that release snapshot."
            ),
        }

    def report(self) -> dict[str, Any]:
        gate_reports: list[dict[str, Any]] = []
        for gate in GATES:
            gate_findings = [finding for finding in self.findings if finding["gate"] == gate]
            gate_status = aggregate_status([finding["status"] for finding in gate_findings])
            gate_reports.append({"gate": gate, "status": gate_status, "findings": gate_findings})
        overall = aggregate_status([item["status"] for item in gate_reports])
        state = self.derive_workflow_state()
        return {
            "status": overall,
            # Persist a portable logical root. Absolute host paths can expose
            # usernames or workstation layouts when reports enter a release.
            "project_root": ".",
            **state,
            "current_approval_sets": dict(sorted(self.current_approval_sets.items())),
            "release_snapshot_sha256": self.release_snapshot_digest,
            "gates": gate_reports,
            "disclaimer": (
                "PASS means the declared evidence structure and registered bytes passed these checks; "
                "it is not proof of mathematical correctness, model suitability, or truth of conclusions."
            ),
        }


def iter_file_refs(value: Any, location: str = "<root>") -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield nested mappings that explicitly contain both path and SHA-256."""

    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            yield location, value
        for key, child in value.items():
            yield from iter_file_refs(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_file_refs(child, f"{location}/{index}")


def iter_defined_ids(value: Any, location: str = "<root>") -> Iterable[tuple[str, str]]:
    """Collect IDs only from definition fields, not from reference fields."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}/{key}"
            if key in {"id", "run_id"} and isinstance(child, str) and TYPED_ID_RE.fullmatch(child):
                yield child_location, child
            yield from iter_defined_ids(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_defined_ids(child, f"{location}/{index}")


REFERENCE_LIST_KEYS = {
    "depends_on",
    "source_refs",
    "required_outputs",
    "addresses",
    "question_refs",
    "assumption_refs",
    "constraint_refs",
    "deliverable_refs",
    "fallback_models",
    "data_refs",
    "baseline_refs",
    "source_result_refs",
    "claim_refs",
    "evidence_refs",
}


def definition_semantic_kind(document_kind: str | None, location: str) -> str:
    """Classify a defined ID by its entity, not merely its containing file.

    This prevents a ``question:*`` row from satisfying an ``assumption_ref``
    simply because both happen to live in the same problem specification.
    Artifact root IDs retain their document kind.
    """

    if location == "<root>/id":
        return document_kind or "unknown"
    patterns = (
        (r"/questions/\d+/(?:id)$", "question"),
        (r"/data_assets/\d+/(?:id)$", "data_asset"),
        (r"/assumptions/\d+/(?:id)$", "assumption"),
        (r"/constraints/\d+/(?:id)$", "constraint"),
        (r"/deliverables/\d+/(?:id)$", "deliverable"),
        (r"/symbols/\d+/(?:id)$", "symbol"),
        (r"/formulation/(?:equations|objectives|constraints)/\d+/(?:id)$", "formula"),
        (r"/validation_plan/checks/\d+/(?:id)$", "validation_check"),
        (r"/scenario_sets/\d+/(?:id)$", "scenario_set"),
        (r"/baseline_comparison_rules/\d+/(?:id)$", "comparison_rule"),
        (r"/metrics/\d+/(?:id)$", "metric"),
        (r"/outputs/\d+/(?:id)$", "output"),
        (r"/run/run_id$", "run"),
        (r"/diagnostics/\d+/(?:id)$", "diagnostic"),
        (r"/claims/\d+/(?:id)$", "claim"),
        (r"/figures/\d+/(?:id)$", "figure"),
        (r"/reviews/\d+/(?:id)$", "review"),
    )
    for pattern, semantic_kind in patterns:
        if re.search(pattern, location):
            return semantic_kind
    return f"{document_kind or 'unknown'}:nested"


def expected_reference_kinds(source_kind: str | None, location: str) -> set[str] | None:
    """Return explicit target kinds for maintained core reference fields.

    Unknown fields return ``None`` and therefore receive existence checking
    only.  Extensions are skipped entirely by ``iter_references`` so a future
    namespaced extension is not rejected merely because the core auditor does
    not yet know its type system.
    """

    if location.endswith("/problem_ref"):
        return {"problem_spec"}
    if (
        location.endswith("/model_ref")
        or location.endswith("/baseline_model_ref")
        or location.endswith("/source_fallback_ref")
        or location.endswith("/replaces_primary_ref")
        or "/baseline_refs/" in location
        or "/fallback_models/" in location
        or "/baseline_policy/model_refs/" in location
    ):
        return {"model_spec"}
    if location.endswith("/experiment_ref"):
        return {"experiment"}
    if location.endswith("/profile_ref"):
        return {"competition_profile"}
    if location.endswith("/comparison_rule_ref"):
        return {"comparison_rule"}
    if location.endswith("/scenario_set_ref"):
        return {"scenario_set"}
    if location.endswith("/baseline_result_ref") or location.endswith("/producer_ref") or location.endswith("/trigger_result_ref"):
        return {"results"}
    if location.endswith("/symbol_ref"):
        return {"symbol"}
    if location.endswith("/data_ref") or "/data_refs/" in location:
        return {"data_asset"}
    if location.endswith("/metric_ref") or location.endswith("_metric_ref") or location.endswith("/output_ref") or location.endswith("_output_ref"):
        return {"metric"} if "metric_ref" in location else {"output"}
    if location.endswith("/check_ref") or location.endswith("/trigger_check_ref"):
        return {"validation_check"}
    if location.endswith("/trigger_diagnostic_ref"):
        return {"diagnostic"}
    if "/question_refs/" in location or "/addresses/" in location:
        return {"question"}
    if "/required_outputs/" in location or "/deliverable_refs/" in location:
        return {"deliverable"}
    if "/assumption_refs/" in location:
        return {"assumption"}
    if "/constraint_refs/" in location or "/source_constraint_refs/" in location:
        return {"constraint"}
    if "/source_result_refs/" in location:
        return {"results"}
    if "/claim_refs/" in location:
        return {"claim"}
    if source_kind == "claims" and ("/evidence_refs/" in location or "/counterevidence/" in location):
        return {"results", "model_spec"}
    if "/depends_on/" in location:
        return {
            "model_spec": {"problem_spec", "model_spec", "results"},
            "model_promotion": {"model_spec", "results"},
            "experiment": {"model_spec", "model_promotion", "problem_spec"},
            "results": {"experiment", "results"},
            "claims": {"results", "model_spec", "problem_spec"},
            "figures": {"results", "claims"},
            "paper_build": {"claims", "figures"},
            "gate_review": set(SCHEMA_BY_KIND).difference({"manifest"}),
            "manifest": set(SCHEMA_BY_KIND).difference({"manifest"}),
        }.get(source_kind)
    return None


RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def parse_rfc3339(value: Any) -> datetime:
    """Parse an RFC3339 timestamp and normalize it to absolute UTC time."""

    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        raise ValueError(f"invalid or timezone-free RFC3339 timestamp: {value!r}")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid RFC3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp has no absolute timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def is_finite_number(value: Any) -> bool:
    try:
        decimal_number(value)
    except ValueError:
        return False
    return True


def iter_references(value: Any, location: str = "<root>") -> Iterable[tuple[str, str]]:
    """Collect typed references using field semantics rather than every string."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}/{key}"
            if key == "extensions":
                continue
            if key.endswith("_ref") and isinstance(child, str) and TYPED_ID_RE.fullmatch(child):
                yield child_location, child
                continue
            elif key in REFERENCE_LIST_KEYS and isinstance(child, list):
                for index, item in enumerate(child):
                    if isinstance(item, str) and TYPED_ID_RE.fullmatch(item):
                        yield f"{child_location}/{index}", item
                    elif isinstance(item, dict) and isinstance(item.get("ref"), str):
                        yield f"{child_location}/{index}/ref", item["ref"]
                continue
            elif key == "ref" and isinstance(child, str) and TYPED_ID_RE.fullmatch(child):
                yield child_location, child
                continue
            elif key in {"fingerprints", "artifact_fingerprints"} and isinstance(child, dict):
                for reference in child:
                    if TYPED_ID_RE.fullmatch(reference):
                        yield f"{child_location}/{reference}", reference
                continue
            yield from iter_references(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_references(child, f"{location}/{index}")


def exit_code(status: str) -> int:
    return {
        "BLOCK": 10,
        "ENV_BLOCK": 11,
        "STALE": 12,
        "PASS": 0,
        "WARN": 0,
        "NOT_APPLICABLE": 0,
    }[status]


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    audit = Audit(root, args.schema_root)
    internal_error = False
    try:
        if not root.is_dir():
            # The caller already knows the supplied argument. Keep structured
            # reports portable instead of embedding a workstation path.
            audit.add("G0", "BLOCK", "PROJECT_ROOT_MISSING", "project directory not found")
        elif audit.load_schemas() and audit.load_manifest():
            audit.load_artifacts()
            audit.validate_manifest_files()
            audit.register_release_snapshot()
            audit.validate_release_activity()
            audit.validate_ids_and_refs()
            audit.verify_embedded_files()
            audit.validate_scientific_invariants()
            # All semantic checks above consume immutable captures.  Confirm
            # that those pathnames still identify the captured bytes before
            # propagating freshness and deciding the release gates.
            audit.verify_captured_files_unchanged()
            # Run dependency propagation only after scientific checks have
            # added every stale root, keeping the report idempotent.
            audit.validate_dag_and_propagate_stale()
            audit.validate_release_deliverables()
            audit.validate_reviews_and_profile()
            # Later validators can be long-running on a large evidence DAG.
            # Recheck immediately before and after release aggregation so a
            # rewrite during those phases cannot inherit an earlier PASS.
            if audit.verify_captured_files_unchanged():
                audit.validate_dag_and_propagate_stale()
            audit.enforce_release_gate_passes()
            if audit.verify_captured_files_unchanged():
                audit.validate_dag_and_propagate_stale()
                audit.enforce_release_gate_passes()
    except Exception as exc:  # Last-resort structured failure, not a traceback.
        internal_error = True
        audit.add("G0", "ENV_BLOCK", "AUDIT_INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")

    report = audit.report()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report is not None:
        try:
            write_text_exclusive(args.json_report.resolve(), rendered + "\n")
        except FileExistsError:
            print(json.dumps({"status": "BLOCK", "message": f"report already exists: {args.json_report}"}, ensure_ascii=False))
            return 13
        except Exception as exc:
            print(json.dumps({"status": "BLOCK", "message": f"cannot create report: {exc}"}, ensure_ascii=False))
            return 13
    print(rendered)
    if internal_error:
        return 14
    return exit_code(report["status"])


if __name__ == "__main__":
    sys.exit(main())
