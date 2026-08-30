#!/usr/bin/env python3
"""Render the auditor's validation-coverage rules as a generated Markdown block.

The mapping values come directly from :mod:`audit_project`; the schemas remain
the authority for legal enum values and their presentation order.  Read-only
mode writes the generated block to stdout.  ``--write`` replaces only the
bytes between the two required markers in ``references/model-selection.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_ROOT = SKILL_ROOT / "references" / "schemas"
MODEL_SCHEMA_PATH = SCHEMA_ROOT / "model_spec.schema.json"
PROBLEM_SCHEMA_PATH = SCHEMA_ROOT / "problem_spec.schema.json"
DOCUMENT_PATH = SKILL_ROOT / "references" / "model-selection.md"

BEGIN_MARKER = "<!-- BEGIN GENERATED: validation-matrix -->"
END_MARKER = "<!-- END GENERATED: validation-matrix -->"
REGENERATE_COMMAND = (
    "python -X utf8 cumcm-modeling/scripts/export_validation_matrix.py --write"
)


class MatrixContentError(ValueError):
    """The schemas, mappings, or generated-block contract are inconsistent."""


class MatrixPathError(OSError):
    """A required repository path cannot be read or written."""


@dataclass(frozen=True)
class ValidationMatrix:
    """Validated source data used by both the renderer and drift tests."""

    check_types: tuple[str, ...]
    model_families: tuple[str, ...]
    task_types: tuple[str, ...]
    family_coverage: Mapping[str, frozenset[str]]
    task_coverage: Mapping[str, frozenset[str]]
    formula_checks: frozenset[str]

    @property
    def family_gaps(self) -> tuple[str, ...]:
        return tuple(
            value for value in self.model_families if value not in self.family_coverage
        )

    @property
    def task_gaps(self) -> tuple[str, ...]:
        return tuple(value for value in self.task_types if value not in self.task_coverage)

    @property
    def automatically_required_checks(self) -> frozenset[str]:
        required = set(self.formula_checks)
        for checks in self.family_coverage.values():
            required.update(checks)
        for checks in self.task_coverage.values():
            required.update(checks)
        return frozenset(required)

    @property
    def automatic_coverage_gaps(self) -> tuple[str, ...]:
        required = self.automatically_required_checks
        return tuple(value for value in self.check_types if value not in required)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export audit_project.py validation coverage as Markdown; use --write "
            "to refresh only the generated block in model-selection.md."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="replace only the content between the required generated markers",
    )
    return parser.parse_args(argv)


def _load_schema(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MatrixPathError(f"required schema is not a file: {path}")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MatrixPathError(f"cannot read schema {path}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixContentError(f"invalid JSON schema {path}: {exc}") from exc
    if not isinstance(schema, dict):
        raise MatrixContentError(f"schema root must be an object: {path}")
    return schema


def _nested_value(document: dict[str, Any], path: tuple[str, ...], source: Path) -> Any:
    current: Any = document
    for key in path:
        if not isinstance(current, dict) or key not in current:
            dotted = ".".join(path)
            raise MatrixContentError(f"{source} does not define {dotted}")
        current = current[key]
    return current


def _string_enum(document: dict[str, Any], path: tuple[str, ...], source: Path) -> tuple[str, ...]:
    values = _nested_value(document, path, source)
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value for value in values)
        or len(values) != len(set(values))
    ):
        raise MatrixContentError(
            f"{source} field {'.'.join(path)} must be a non-empty unique string enum"
        )
    return tuple(values)


def _freeze_mapping(
    value: Mapping[str, set[str]], source_name: str
) -> dict[str, frozenset[str]]:
    frozen: dict[str, frozenset[str]] = {}
    for key, checks in value.items():
        if not isinstance(key, str) or not isinstance(checks, set):
            raise MatrixContentError(f"{source_name} must map strings to sets of strings")
        if any(not isinstance(check, str) or not check for check in checks):
            raise MatrixContentError(f"{source_name}[{key!r}] contains an invalid check name")
        frozen[key] = frozenset(checks)
    return frozen


def load_validation_matrix() -> ValidationMatrix:
    """Load and cross-check the two schema vocabularies and auditor mappings."""

    # Import lazily so this script controls its own 0/10/13/14 exit contract.
    # audit_project remains the single mapping authority; no values are copied.
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(
            captured_stderr
        ):
            from audit_project import (
                FORMULA_VALIDATION_CHECKS,
                VALIDATION_COVERAGE_BY_FAMILY,
                VALIDATION_COVERAGE_BY_TASK,
            )
    except (Exception, SystemExit) as exc:
        detail = captured_stderr.getvalue().strip() or captured_stdout.getvalue().strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"cannot import validation authorities from audit_project.py{suffix}"
        ) from exc

    model_schema = _load_schema(MODEL_SCHEMA_PATH)
    problem_schema = _load_schema(PROBLEM_SCHEMA_PATH)
    check_types = _string_enum(
        model_schema, ("$defs", "validationCheckType", "enum"), MODEL_SCHEMA_PATH
    )
    model_families = _string_enum(
        model_schema, ("properties", "model_family", "enum"), MODEL_SCHEMA_PATH
    )
    task_types = _string_enum(
        problem_schema,
        ("properties", "questions", "items", "properties", "task_type", "enum"),
        PROBLEM_SCHEMA_PATH,
    )
    family_coverage = _freeze_mapping(
        VALIDATION_COVERAGE_BY_FAMILY, "VALIDATION_COVERAGE_BY_FAMILY"
    )
    task_coverage = _freeze_mapping(
        VALIDATION_COVERAGE_BY_TASK, "VALIDATION_COVERAGE_BY_TASK"
    )
    if not isinstance(FORMULA_VALIDATION_CHECKS, set) or any(
        not isinstance(check, str) or not check for check in FORMULA_VALIDATION_CHECKS
    ):
        raise MatrixContentError(
            "FORMULA_VALIDATION_CHECKS must be a set of non-empty strings"
        )
    formula_checks = frozenset(FORMULA_VALIDATION_CHECKS)

    unknown_families = set(family_coverage).difference(model_families)
    unknown_tasks = set(task_coverage).difference(task_types)
    mapped_checks = set(formula_checks)
    for checks in (*family_coverage.values(), *task_coverage.values()):
        mapped_checks.update(checks)
    unknown_checks = mapped_checks.difference(check_types)
    problems: list[str] = []
    if unknown_families:
        problems.append(f"unknown model_family keys: {sorted(unknown_families)}")
    if unknown_tasks:
        problems.append(f"unknown task_type keys: {sorted(unknown_tasks)}")
    if unknown_checks:
        problems.append(f"check names absent from validationCheckType: {sorted(unknown_checks)}")
    if problems:
        raise MatrixContentError("; ".join(problems))

    return ValidationMatrix(
        check_types=check_types,
        model_families=model_families,
        task_types=task_types,
        family_coverage=family_coverage,
        task_coverage=task_coverage,
        formula_checks=formula_checks,
    )


def _code_list(values: tuple[str, ...] | list[str]) -> str:
    return "、".join(f"`{value}`" for value in values) if values else "（无）"


def _ordered_checks(matrix: ValidationMatrix, checks: frozenset[str]) -> tuple[str, ...]:
    return tuple(value for value in matrix.check_types if value in checks)


def render_markdown(matrix: ValidationMatrix | None = None) -> str:
    """Return deterministic LF-only Markdown for the generated block."""

    matrix = matrix or load_validation_matrix()
    lines = [
        "### 审计器验证覆盖决策表",
        "",
        "> 本块由 `scripts/export_validation_matrix.py` 生成；映射取自审计器常量，合法取值与行顺序取自 Schema。不要手工编辑。",
        "",
        "#### 模型族 → 必须考虑的 checks",
        "",
        "| `model_family` | `validation_plan.checks` 必须考虑 |",
        "|---|---|",
    ]
    for family in matrix.model_families:
        checks = matrix.family_coverage.get(family)
        cell = (
            _code_list(list(_ordered_checks(matrix, checks)))
            if checks is not None
            else "—（无族级强制检查；仍可能受任务级与公式级约束）"
        )
        lines.append(f"| `{family}` | {cell} |")

    lines.extend(
        [
            "",
            "#### 任务类型 → 必须考虑的 checks",
            "",
            "| `task_type` | `validation_plan.checks` 必须考虑 |",
            "|---|---|",
        ]
    )
    for task_type in matrix.task_types:
        checks = matrix.task_coverage.get(task_type)
        cell = (
            _code_list(list(_ordered_checks(matrix, checks)))
            if checks is not None
            else "—（无任务级强制检查；仍可能受族级与公式级约束）"
        )
        lines.append(f"| `{task_type}` | {cell} |")

    formula_checks = _ordered_checks(matrix, matrix.formula_checks)
    lines.extend(
        [
            "",
            "当 `formulation.equations`、`objectives` 或 `constraints` 中任一列表非空时，审计器还会叠加公式级检查："
            + _code_list(list(formula_checks))
            + "。",
            "",
            "#### 当前覆盖盲区",
            "",
            "- **无族级映射的 `model_family`**："
            + _code_list(list(matrix.family_gaps))
            + "。这两类模型在族—任务映射中只剩任务级约束；若包含公式，仍会叠加上述公式级检查。",
            "- **无任务级映射的 `task_type`**："
            + _code_list(list(matrix.task_gaps))
            + "。该任务类型不受任务级 check 覆盖约束，仍可能受模型族与公式级约束。",
            "- **未被族、任务或公式规则自动要求的 `validationCheckType`**："
            + _code_list(list(matrix.automatic_coverage_gaps))
            + "。其中 `seed_stability` 对随机算法是关键检查，目前完全依赖人工在 `validation_plan.checks` 中主动声明；`reproducibility` 与 `other` 同样不会被自动补入。",
            "",
        ]
    )
    return "\n".join(lines)


def _marker_bounds(document: bytes) -> tuple[int, int]:
    begin = BEGIN_MARKER.encode("utf-8")
    end = END_MARKER.encode("utf-8")
    if document.count(begin) != 1 or document.count(end) != 1:
        raise MatrixContentError(
            "model-selection.md must contain exactly one validation-matrix marker pair"
        )
    begin_at = document.find(begin)
    end_at = document.find(end)
    if begin_at > 0 and document[begin_at - 1 : begin_at] != b"\n":
        raise MatrixContentError("validation-matrix begin marker must occupy its own line")
    content_start = begin_at + len(begin)
    if document[content_start : content_start + 1] != b"\n":
        raise MatrixContentError("validation-matrix begin marker must use an LF line ending")
    content_start += 1
    if end_at < content_start:
        raise MatrixContentError("validation-matrix end marker must follow the begin marker")
    if end_at > 0 and document[end_at - 1 : end_at] != b"\n":
        raise MatrixContentError("validation-matrix end marker must occupy its own line")
    after_end = end_at + len(end)
    if after_end < len(document) and document[after_end : after_end + 1] != b"\n":
        raise MatrixContentError("validation-matrix end marker must occupy its own line")
    return content_start, end_at


def extract_generated_block(document: bytes) -> bytes:
    """Return exactly the bytes between the two marker lines."""

    content_start, content_end = _marker_bounds(document)
    return document[content_start:content_end]


def replace_generated_block(document: bytes, generated: bytes) -> bytes:
    """Replace only marker-interior bytes, preserving both outside slices."""

    if not generated.endswith(b"\n") or b"\r" in generated:
        raise MatrixContentError("generated Markdown must be LF-only and end with one newline")
    content_start, content_end = _marker_bounds(document)
    return document[:content_start] + generated + document[content_end:]


def write_document(path: Path, generated: bytes) -> bool:
    if not path.is_file():
        raise MatrixPathError(f"target document is not a file: {path}")
    try:
        before = path.read_bytes()
        after = replace_generated_block(before, generated)
        if after == before:
            return False
        path.write_bytes(after)
    except MatrixContentError:
        raise
    except OSError as exc:
        raise MatrixPathError(f"cannot update {path}: {exc}") from exc
    return True


def read_document_block(path: Path) -> bytes:
    """Read the generated block without modifying the document."""

    if not path.is_file():
        raise MatrixPathError(f"target document is not a file: {path}")
    try:
        return extract_generated_block(path.read_bytes())
    except MatrixContentError:
        raise
    except OSError as exc:
        raise MatrixPathError(f"cannot read {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        generated = render_markdown().encode("utf-8")
        if not args.write:
            sys.stdout.buffer.write(generated)
            if read_document_block(DOCUMENT_PATH) != generated:
                raise MatrixContentError(
                    "model-selection.md generated block differs from current authority output"
                )
            return 0
        changed = write_document(DOCUMENT_PATH, generated)
        action = "updated" if changed else "already current"
        print(f"validation matrix {action}: {DOCUMENT_PATH}")
        return 0
    except MatrixContentError as exc:
        print(f"validation matrix content mismatch: {exc}", file=sys.stderr)
        print(f"regenerate with: {REGENERATE_COMMAND}", file=sys.stderr)
        return 10
    except MatrixPathError as exc:
        print(f"validation matrix path error: {exc}", file=sys.stderr)
        return 13
    except Exception as exc:  # pragma: no cover - last-resort structured exit
        print(f"validation matrix internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 14


if __name__ == "__main__":
    raise SystemExit(main())
