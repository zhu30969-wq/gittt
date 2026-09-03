#!/usr/bin/env python3
"""Create a CUMCM contract project without overwriting existing content.

The template manifest contains hash placeholders.  This initializer renders
the manifest last, after all missing contract files have been created, so the
manifest records the exact bytes that now exist in the target.  If a target
file already exists, it is left untouched and its current hash is used only
when a *new* manifest is being created.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from _contract_support import (
        TYPED_ID_RE,
        dump_yaml,
        load_yaml,
        safe_project_path,
        sha256_file,
        write_text_exclusive,
    )
except ImportError as exc:  # pragma: no cover - exercised when dependencies are absent
    print(
        json.dumps(
            {
                "status": "ENV_BLOCK",
                "message": f"missing script dependency: {exc}",
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(11)


SCHEMA_VERSION_RE = re.compile(r"^2\.[0-9]+\.[0-9]+$")
DEFAULT_PROBLEM_CODE = "UNSET"
DEFAULT_SEED = 42
DEFAULT_PAPER_ENGINE = "latex"
PLACEHOLDER_TO_PARAMETER = {
    "__PROJECT_ID__": "project_id",
    "__CONTEST_YEAR__": "contest_year",
    "__PROBLEM_CODE__": "problem_code",
    "__DEFAULT_SEED__": "default_seed",
    "__PAPER_ENGINE__": "paper_engine",
}
PLACEHOLDER_PATTERN = re.compile(
    "|".join(re.escape(token) for token in PLACEHOLDER_TO_PARAMETER)
)


class ExistingInitializationError(ValueError):
    """Describe malformed or mutually inconsistent recovered parameters."""

    def __init__(
        self,
        message: str,
        *,
        source_conflicts: dict[str, list[dict[str, object]]] | None = None,
        invalid_sources: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        super().__init__(message)
        self.source_conflicts = source_conflicts or {}
        self.invalid_sources = invalid_sources or {}


def json_safe(value: object) -> object:
    """Return a JSON-reportable representation for malformed YAML values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return repr(value)


def source_for_report(source: dict[str, object]) -> dict[str, object]:
    """Preserve provenance while making an existing-value report JSON-safe."""

    return {
        "value": json_safe(source["value"]),
        "source_path": source["source_path"],
        "source_field": source["source_field"],
    }


def initialization_value_is_valid(parameter: str, value: object) -> bool:
    """Validate recovered values before comparing them across sources."""

    if parameter == "project_id":
        return (
            isinstance(value, str)
            and value.startswith("project:")
            and TYPED_ID_RE.fullmatch(value) is not None
        )
    if parameter == "contest_year":
        return type(value) is int and value >= 1992
    if parameter == "problem_code":
        return isinstance(value, str) and bool(value.strip()) and value == value.strip()
    if parameter == "default_seed":
        return type(value) is int
    if parameter == "paper_engine":
        return value in {"latex", "typst"}
    raise ValueError(f"unknown initialization parameter: {parameter}")


def collect_exact_placeholders(value: object) -> set[str]:
    """Collect only YAML scalar placeholders consumed by typed rendering."""

    if isinstance(value, dict):
        found: set[str] = set()
        for item in value.values():
            found.update(collect_exact_placeholders(item))
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found.update(collect_exact_placeholders(item))
        return found
    if isinstance(value, str) and value in PLACEHOLDER_TO_PARAMETER:
        return {value}
    return set()


def inspect_template_source(source: Path) -> dict[str, Any]:
    """Read one template once and classify its rendering behavior."""

    if source.suffix in {".yaml", ".yml"}:
        document = load_yaml(source)
        return {
            "kind": "yaml",
            "content": document,
            "placeholders": collect_exact_placeholders(document),
        }

    payload = source.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return {"kind": "binary", "content": payload, "placeholders": set()}
    if "\x00" in text:
        return {"kind": "binary", "content": payload, "placeholders": set()}
    return {
        "kind": "text",
        "content": text,
        "placeholders": set(PLACEHOLDER_PATTERN.findall(text)),
    }


def render_text_template(text: str, replacements: dict[str, object]) -> str:
    """Render UTF-8 text in one pass so replacement values never cascade."""

    return PLACEHOLDER_PATTERN.sub(
        lambda match: str(replacements[match.group(0)]),
        text,
    )


def write_bytes_exclusive(path: Path, payload: bytes) -> None:
    """Create binary template output with the same no-overwrite guarantee."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def render_template_value(value: object, replacements: dict[str, object]) -> object:
    """Replace exact YAML placeholder scalars without string interpolation.

    Parsing first and replacing typed scalar values avoids creating malformed
    YAML when a problem code contains punctuation, and preserves integers as
    integers for contest years and random seeds.
    """

    if isinstance(value, dict):
        return {
            key: render_template_value(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [render_template_value(item, replacements) for item in value]
    if isinstance(value, str) and value in replacements:
        return replacements[value]
    return value


def load_existing_initialization(
    target: Path,
) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
    """Recover every recorded initializer value and cross-check provenance.

    A value is never accepted by source priority.  All supported locations are
    collected first, equal values are retained for diagnostics, and any type or
    value disagreement invalidates the existing initialization before the
    caller is allowed to create a directory or write a file.
    """

    sources: dict[str, list[dict[str, object]]] = {}

    def record(
        parameter: str,
        value: object,
        source_path: str,
        source_field: str,
    ) -> None:
        sources.setdefault(parameter, []).append(
            {
                "value": value,
                "source_path": source_path,
                "source_field": source_field,
            }
        )

    manifest_path = target / "manifest.yaml"
    if manifest_path.is_file():
        manifest = load_yaml(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("kind") != "manifest":
            raise ExistingInitializationError(
                "existing manifest.yaml is not a manifest contract"
            )
        if manifest.get("project_id") is not None:
            record(
                "project_id",
                manifest["project_id"],
                "manifest.yaml",
                "project_id",
            )
        extensions = manifest.get("extensions", {})
        initialization = (
            extensions.get("initialization", {})
            if isinstance(extensions, dict)
            else {}
        )
        if isinstance(initialization, dict):
            for key in (
                "contest_year",
                "problem_code",
                "default_seed",
                "paper_engine",
            ):
                if initialization.get(key) is not None:
                    record(
                        key,
                        initialization[key],
                        "manifest.yaml",
                        f"extensions.initialization.{key}",
                    )

        entrypoints = manifest.get("entrypoints", {})
        paper_entrypoint = (
            entrypoints.get("paper") if isinstance(entrypoints, dict) else None
        )
        if isinstance(paper_entrypoint, str):
            suffix = PurePosixPath(paper_entrypoint).suffix.lower()
            inferred_engine = {".tex": "latex", ".typ": "typst"}.get(suffix)
            if inferred_engine is not None:
                record(
                    "paper_engine",
                    inferred_engine,
                    "manifest.yaml",
                    "entrypoints.paper",
                )

    problem_path = target / "specs" / "problem_spec.yaml"
    if problem_path.is_file():
        problem = load_yaml(problem_path)
        contest = problem.get("contest", {}) if isinstance(problem, dict) else {}
        if isinstance(contest, dict):
            if contest.get("year") is not None:
                record(
                    "contest_year",
                    contest["year"],
                    "specs/problem_spec.yaml",
                    "contest.year",
                )
            if contest.get("problem_code") is not None:
                record(
                    "problem_code",
                    contest["problem_code"],
                    "specs/problem_spec.yaml",
                    "contest.problem_code",
                )

    experiment_path = target / "experiments" / "experiment.yaml"
    if experiment_path.is_file():
        experiment = load_yaml(experiment_path)
        seeds = experiment.get("seeds", []) if isinstance(experiment, dict) else []
        if isinstance(seeds, list) and seeds:
            record(
                "default_seed",
                seeds[0],
                "experiments/experiment.yaml",
                "seeds[0]",
            )

    results_path = target / "results" / "results.yaml"
    if results_path.is_file():
        results = load_yaml(results_path)
        run = results.get("run", {}) if isinstance(results, dict) else {}
        seeds = run.get("seeds", []) if isinstance(run, dict) else []
        if isinstance(seeds, list) and seeds:
            record(
                "default_seed",
                seeds[0],
                "results/results.yaml",
                "run.seeds[0]",
            )

    invalid_sources: dict[str, list[dict[str, object]]] = {}
    source_conflicts: dict[str, list[dict[str, object]]] = {}
    for parameter, entries in sources.items():
        invalid = [
            source_for_report(entry)
            for entry in entries
            if not initialization_value_is_valid(parameter, entry["value"])
        ]
        if invalid:
            invalid_sources[parameter] = invalid
        distinct_values = {
            (type(entry["value"]).__name__, repr(entry["value"]))
            for entry in entries
        }
        if len(distinct_values) > 1:
            source_conflicts[parameter] = [
                source_for_report(entry) for entry in entries
            ]

    if invalid_sources or source_conflicts:
        details: list[str] = []
        for parameter, entries in source_conflicts.items():
            rendered = ", ".join(
                f"{entry['source_path']}:{entry['source_field']}={entry['value']!r}"
                for entry in entries
            )
            details.append(f"{parameter} conflicts across {rendered}")
        for parameter, entries in invalid_sources.items():
            rendered = ", ".join(
                f"{entry['source_path']}:{entry['source_field']}={entry['value']!r}"
                for entry in entries
            )
            details.append(f"{parameter} has invalid existing value(s) at {rendered}")
        raise ExistingInitializationError(
            "; ".join(details),
            source_conflicts=source_conflicts,
            invalid_sources=invalid_sources,
        )

    settings = {
        parameter: entries[0]["value"]
        for parameter, entries in sources.items()
    }
    public_sources = {
        parameter: [source_for_report(entry) for entry in entries]
        for parameter, entries in sources.items()
    }
    return settings, public_sources


def find_initialization_conflicts(
    existing: dict[str, object], requested: dict[str, object]
) -> dict[str, dict[str, object]]:
    """Return only explicitly requested values that disagree with the project.

    Omitted CLI values are intentionally ignored so an existing project can be
    resumed without repeating its initialization choices.  Explicitly supplied
    values must never be reported as accepted when the initializer preserves a
    different value already on disk.
    """

    return {
        key: {"existing": existing[key], "requested": value}
        for key, value in requested.items()
        if value is not None and key in existing and existing[key] != value
    }


def normalize_requested_parameters(
    args: argparse.Namespace,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Validate explicit CLI values without treating falsey values as absent."""

    requested: dict[str, object] = {
        "project_id": args.project_id,
        "contest_year": args.contest_year,
        "problem_code": args.problem_code,
        "default_seed": args.default_seed,
        "paper_engine": args.paper_engine,
    }
    errors: list[dict[str, object]] = []

    project_id = requested["project_id"]
    if project_id is not None and (
        not isinstance(project_id, str)
        or not project_id.startswith("project:")
        or TYPED_ID_RE.fullmatch(project_id) is None
    ):
        errors.append(
            {
                "parameter": "project_id",
                "requested": json_safe(project_id),
                "message": "must match project:<id>",
            }
        )

    contest_year = requested["contest_year"]
    if contest_year is not None and (
        type(contest_year) is not int or contest_year < 1992
    ):
        errors.append(
            {
                "parameter": "contest_year",
                "requested": json_safe(contest_year),
                "message": "must be an integer of 1992 or later",
            }
        )

    problem_code = requested["problem_code"]
    if problem_code is not None:
        if not isinstance(problem_code, str) or not problem_code.strip():
            errors.append(
                {
                    "parameter": "problem_code",
                    "requested": json_safe(problem_code),
                    "message": "cannot be blank",
                }
            )
        else:
            requested["problem_code"] = problem_code.strip()

    default_seed = requested["default_seed"]
    if default_seed is not None and type(default_seed) is not int:
        errors.append(
            {
                "parameter": "default_seed",
                "requested": json_safe(default_seed),
                "message": "must be an integer",
            }
        )

    paper_engine = requested["paper_engine"]
    if paper_engine is not None and paper_engine not in {"latex", "typst"}:
        errors.append(
            {
                "parameter": "paper_engine",
                "requested": json_safe(paper_engine),
                "message": "must be latex or typst",
            }
        )
    return requested, errors


def existing_initialization_error_report(
    error: Exception,
) -> dict[str, object]:
    """Build one structured, JSON-safe existing-initialization failure."""

    report: dict[str, object] = {
        "status": "BLOCK",
        "code": "EXISTING_INITIALIZATION_INVALID",
        "message": str(error),
    }
    if isinstance(error, ExistingInitializationError):
        if error.source_conflicts:
            report["source_conflicts"] = error.source_conflicts
        if error.invalid_sources:
            report["invalid_sources"] = error.invalid_sources
    return report


def validate_template_contracts(template_root: Path) -> list[str]:
    """Preflight every manifest-owned contract before creating target files.

    Custom template roots are supported, but an old 1.x contract tree must be
    migrated explicitly.  Running this check before ``target.mkdir`` prevents
    a rejected template from leaving a partially initialized project.
    """

    errors: list[str] = []
    manifest_path = template_root / "manifest.yaml"
    if not manifest_path.is_file():
        return ["template manifest.yaml is missing"]
    try:
        manifest = load_yaml(manifest_path)
    except Exception as exc:
        return [f"template manifest.yaml cannot be read: {exc}"]
    if not isinstance(manifest, dict) or manifest.get("kind") != "manifest":
        return ["template manifest.yaml is not a manifest contract"]
    if not isinstance(manifest.get("schema_version"), str) or not SCHEMA_VERSION_RE.fullmatch(manifest["schema_version"]):
        errors.append("template manifest schema_version must be 2.x.x")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("template manifest artifacts must be a non-empty list")
        return errors
    for index, row in enumerate(artifacts):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            errors.append(f"template manifest artifacts[{index}] has no valid path")
            continue
        try:
            artifact_path = safe_project_path(template_root, row["path"], must_exist=True)
            document = load_yaml(artifact_path)
        except Exception as exc:
            errors.append(f"template artifact {row.get('path')!r} cannot be read: {exc}")
            continue
        if not isinstance(document, dict):
            errors.append(f"template artifact {row['path']!r} is not a contract mapping")
            continue
        version = document.get("schema_version")
        if not isinstance(version, str) or not SCHEMA_VERSION_RE.fullmatch(version):
            errors.append(f"template artifact {row['path']!r} schema_version must be 2.x.x")
        if document.get("id") != row.get("id") or document.get("kind") != row.get("kind"):
            errors.append(f"template artifact {row['path']!r} id/kind differs from its manifest row")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create missing CUMCM project contract files; never overwrite existing files."
    )
    parser.add_argument("target", type=Path, help="Target project directory")
    parser.add_argument(
        "--project-id",
        help="Stable typed ID such as project:cumcm-2026-a; required only when creating missing project files",
    )
    parser.add_argument(
        "--contest-year",
        type=int,
        help="CUMCM contest year; required for a new project and never inferred from the clock",
    )
    parser.add_argument(
        "--problem-code",
        help=f"Problem code recorded in problem_spec; defaults to {DEFAULT_PROBLEM_CODE!r} for a new project",
    )
    parser.add_argument(
        "--default-seed",
        type=int,
        help=f"Initial experiment seed; defaults to {DEFAULT_SEED} and is independent of contest year",
    )
    parser.add_argument(
        "--paper-engine",
        choices=["latex", "typst"],
        help=f"Canonical paper engine recorded in manifest extensions; defaults to {DEFAULT_PAPER_ENGINE}",
    )
    parser.add_argument(
        "--template-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "project-template",
        help="Template directory; defaults to this skill's bundled template",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report intended actions without creating directories or files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    template_root = args.template_root.resolve()
    if not template_root.is_dir():
        print(json.dumps({"status": "ENV_BLOCK", "message": f"template directory not found: {template_root}"}, ensure_ascii=False))
        return 11

    template_errors = validate_template_contracts(template_root)
    if template_errors:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "TEMPLATE_CONTRACT_INVALID",
                    "message": "template contract preflight failed",
                    "errors": template_errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 10

    target = args.target.resolve()
    # The manifest is handled last because it locks hashes of the other files.
    template_files = sorted(
        (path for path in template_root.rglob("*") if path.is_file()),
        key=lambda path: (path.name == "manifest.yaml", path.as_posix()),
    )
    findings: list[dict[str, str]] = []
    requested, request_errors = normalize_requested_parameters(args)
    if request_errors:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "INITIALIZATION_PARAMETER_INVALID",
                    "message": "one or more explicit initialization parameters are invalid",
                    "errors": request_errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 10

    missing_relatives: list[str] = []
    destinations: dict[str, Path] = {}
    for source in template_files:
        relative = source.relative_to(template_root).as_posix()
        try:
            destination = safe_project_path(target, relative)
        except ValueError as exc:
            print(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "code": "TARGET_PATH_INVALID",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
            return 10
        destinations[relative] = destination
        if not destination.exists():
            missing_relatives.append(relative)

    inspections: dict[str, dict[str, Any]] = {}
    consumers: dict[str, list[str]] = {
        parameter: [] for parameter in PLACEHOLDER_TO_PARAMETER.values()
    }
    try:
        for source in template_files:
            relative = source.relative_to(template_root).as_posix()
            if relative not in missing_relatives:
                continue
            inspection = inspect_template_source(source)
            inspections[relative] = inspection
            for placeholder in inspection["placeholders"]:
                consumers[PLACEHOLDER_TO_PARAMETER[placeholder]].append(relative)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "TEMPLATE_CONTENT_INVALID",
                    "message": f"template content preflight failed: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 10

    try:
        if target.exists():
            existing, existing_sources = load_existing_initialization(target)
        else:
            existing, existing_sources = {}, {}
    except Exception as exc:
        print(
            json.dumps(
                existing_initialization_error_report(exc),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 10

    conflicts = find_initialization_conflicts(existing, requested)
    if conflicts:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "INITIALIZATION_PARAMETER_CONFLICT",
                    "message": "requested parameters conflict with values already recorded in the project",
                    "conflicts": conflicts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 10

    unverifiable = {
        parameter: {
            "requested": json_safe(value),
            "existing_sources": existing_sources.get(parameter, []),
            "missing_consumers": consumers[parameter],
        }
        for parameter, value in requested.items()
        if value is not None
        and parameter not in existing
        and not consumers[parameter]
    }
    if unverifiable:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "INITIALIZATION_PARAMETER_UNVERIFIABLE",
                    "message": "explicit parameters are not recorded and no missing template file would consume them",
                    "parameters": unverifiable,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 10

    # Even a complete no-op is reached only after validating every existing
    # source and every explicit request.  This catches cross-source corruption
    # without rewriting or refreshing a single byte.
    if not missing_relatives:
        findings = [
            {
                "status": "NOT_APPLICABLE",
                "path": source.relative_to(template_root).as_posix(),
                "message": "existing file preserved without modification",
            }
            for source in template_files
        ]
        print(
            json.dumps(
                {"status": "PASS", "target": str(target), "findings": findings},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    project_id = (
        requested["project_id"]
        if requested["project_id"] is not None
        else existing.get("project_id")
    )
    contest_year = (
        requested["contest_year"]
        if requested["contest_year"] is not None
        else existing.get("contest_year")
    )
    problem_code = (
        requested["problem_code"]
        if requested["problem_code"] is not None
        else existing.get("problem_code", DEFAULT_PROBLEM_CODE)
    )
    default_seed = (
        requested["default_seed"]
        if requested["default_seed"] is not None
        else existing.get("default_seed", DEFAULT_SEED)
    )
    paper_engine = (
        requested["paper_engine"]
        if requested["paper_engine"] is not None
        else existing.get("paper_engine", DEFAULT_PAPER_ENGINE)
    )

    if not isinstance(project_id, str) or not TYPED_ID_RE.fullmatch(project_id) or not project_id.startswith("project:"):
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "PROJECT_ID_REQUIRED",
                    "message": "new or incomplete projects require --project-id matching project:<id>",
                },
                ensure_ascii=False,
            )
        )
        return 10
    if type(contest_year) is not int or contest_year < 1992:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "CONTEST_YEAR_REQUIRED",
                    "message": "new or incomplete projects require an explicit --contest-year of 1992 or later",
                },
                ensure_ascii=False,
            )
        )
        return 10
    if not isinstance(problem_code, str) or not problem_code.strip():
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "INITIALIZATION_PARAMETER_INVALID",
                    "message": "--problem-code cannot be blank",
                },
                ensure_ascii=False,
            )
        )
        return 10
    if type(default_seed) is not int:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "INITIALIZATION_PARAMETER_INVALID",
                    "message": "--default-seed must be an integer",
                },
                ensure_ascii=False,
            )
        )
        return 10
    if paper_engine not in {"latex", "typst"}:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "INITIALIZATION_PARAMETER_INVALID",
                    "message": "--paper-engine must be latex or typst",
                },
                ensure_ascii=False,
            )
        )
        return 10

    initialization = {
        "project_id": project_id,
        "contest_year": contest_year,
        "problem_code": problem_code.strip(),
        "default_seed": default_seed,
        "paper_engine": paper_engine,
    }
    replacements: dict[str, object] = {
        "__PROJECT_ID__": project_id,
        "__CONTEST_YEAR__": contest_year,
        "__PROBLEM_CODE__": problem_code.strip(),
        "__DEFAULT_SEED__": default_seed,
        "__PAPER_ENGINE__": paper_engine,
    }

    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    for source in template_files:
        relative = source.relative_to(template_root).as_posix()
        destination = destinations[relative]

        if destination.exists():
            findings.append(
                {
                    "status": "NOT_APPLICABLE",
                    "path": relative,
                    "message": "existing file preserved without modification",
                }
            )
            continue

        if args.dry_run:
            message = "would create missing file"
            if inspections[relative]["kind"] == "binary":
                message = (
                    "would create missing file by copying binary or non-UTF-8 "
                    "bytes without rendering"
                )
            findings.append({"status": "PASS", "path": relative, "message": message})
            continue

        try:
            inspection = inspections[relative]
            if inspection["kind"] == "yaml":
                document = render_template_value(inspection["content"], replacements)
                if relative == "manifest.yaml":
                    for artifact in document.get("artifacts", []):
                        artifact_path = safe_project_path(target, artifact["path"], must_exist=True)
                        artifact["sha256"] = sha256_file(artifact_path)
                rendered = dump_yaml(document)
                write_text_exclusive(destination, rendered)
                message = "created"
            elif inspection["kind"] == "text":
                rendered = render_text_template(inspection["content"], replacements)
                write_text_exclusive(destination, rendered)
                message = "created"
            else:
                write_bytes_exclusive(destination, inspection["content"])
                message = "created by copying binary or non-UTF-8 bytes without rendering"
            findings.append({"status": "PASS", "path": relative, "message": message})
        except FileExistsError:
            # Handles a concurrent creator without ever overwriting its file.
            findings.append(
                {
                    "status": "NOT_APPLICABLE",
                    "path": relative,
                    "message": "file appeared concurrently and was preserved",
                }
            )
        except Exception as exc:
            findings.append({"status": "BLOCK", "path": relative, "message": str(exc)})

    overall = "BLOCK" if any(item["status"] == "BLOCK" for item in findings) else "PASS"
    print(
        json.dumps(
            {
                "status": overall,
                "target": str(target),
                "initialization": initialization,
                "findings": findings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 10 if overall == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())
