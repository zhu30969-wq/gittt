#!/usr/bin/env python3
"""Render forward-test scenario status from the registry into Markdown.

``evals/scenarios.yaml`` is the only authority for scenario IDs and statuses.
Read-only mode writes the generated block to stdout and verifies the checked-in
documentation.  ``--write`` replaces only the bytes between the required
markers in ``references/forward-testing.md``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


SKILL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_ROOT.parent
SCENARIOS_PATH = REPO_ROOT / "evals" / "scenarios.yaml"
DOCUMENT_PATH = SKILL_ROOT / "references" / "forward-testing.md"

BEGIN_MARKER = "<!-- BEGIN GENERATED: scenario-status -->"
END_MARKER = "<!-- END GENERATED: scenario-status -->"
REGENERATE_COMMAND = (
    "python -X utf8 cumcm-modeling/scripts/export_scenario_status.py --write"
)
VALID_STATUSES = ("executable", "specification_only")


class ScenarioContentError(ValueError):
    """The registry or generated-block contract is inconsistent."""


class ScenarioPathError(OSError):
    """A required repository path cannot be read or written."""


@dataclass(frozen=True)
class ScenarioStatus:
    """Validated registry state in source order."""

    executable_ids: tuple[str, ...]
    specification_only_ids: tuple[str, ...]

    def ids_for(self, status: str) -> tuple[str, ...]:
        if status == "executable":
            return self.executable_ids
        if status == "specification_only":
            return self.specification_only_ids
        raise ScenarioContentError(f"unsupported scenario status: {status}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export evals/scenarios.yaml status as Markdown; use --write to "
            "refresh only the generated block in forward-testing.md."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="replace only the content between the required generated markers",
    )
    return parser.parse_args(argv)


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict[str, Any]]:
    """Read and validate the scenario registry without interpreting fixtures."""

    if not path.is_file():
        raise ScenarioPathError(f"scenario registry is not a file: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScenarioPathError(f"cannot read scenario registry {path}: {exc}") from exc
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ScenarioContentError(f"invalid scenario registry {path}: {exc}") from exc

    if not isinstance(document, dict):
        raise ScenarioContentError("scenario registry root must be a mapping")
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ScenarioContentError("scenario registry must contain a non-empty scenarios list")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ScenarioContentError(f"scenarios[{index}] must be a mapping")
        scenario_id = scenario.get("id")
        status = scenario.get("status")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ScenarioContentError(f"scenarios[{index}].id must be a non-empty string")
        if scenario_id in seen:
            raise ScenarioContentError(f"duplicate scenario id: {scenario_id}")
        if status not in VALID_STATUSES:
            raise ScenarioContentError(
                f"scenario {scenario_id} has unsupported status {status!r}; "
                f"expected one of {VALID_STATUSES}"
            )
        seen.add(scenario_id)
        validated.append(scenario)
    return validated


def load_scenario_status(path: Path = SCENARIOS_PATH) -> ScenarioStatus:
    scenarios = load_scenarios(path)
    return ScenarioStatus(
        executable_ids=tuple(
            scenario["id"] for scenario in scenarios if scenario["status"] == "executable"
        ),
        specification_only_ids=tuple(
            scenario["id"]
            for scenario in scenarios
            if scenario["status"] == "specification_only"
        ),
    )


def _code_list(values: tuple[str, ...]) -> str:
    return "、".join(f"`{value}`" for value in values) if values else "（无）"


def render_markdown(status: ScenarioStatus | None = None) -> str:
    """Return deterministic LF-only Markdown for the generated block."""

    status = status or load_scenario_status()
    lines = [
        "### 场景状态注册表",
        "",
        "> 本块由 `scripts/export_scenario_status.py` 从 `evals/scenarios.yaml` 生成；场景数量、状态与 ID 清单不得手工维护。",
        "",
        "| 状态 | 数量 | 完整 ID 清单 |",
        "|---|---:|---|",
    ]
    for name in VALID_STATUSES:
        identifiers = status.ids_for(name)
        lines.append(f"| `{name}` | {len(identifiers)} | {_code_list(identifiers)} |")
    lines.append("")
    return "\n".join(lines)


def _marker_bounds(document: bytes) -> tuple[int, int]:
    begin = BEGIN_MARKER.encode("utf-8")
    end = END_MARKER.encode("utf-8")
    if document.count(begin) != 1 or document.count(end) != 1:
        raise ScenarioContentError(
            "forward-testing.md must contain exactly one scenario-status marker pair"
        )
    begin_at = document.find(begin)
    end_at = document.find(end)
    if begin_at > 0 and document[begin_at - 1 : begin_at] != b"\n":
        raise ScenarioContentError("scenario-status begin marker must occupy its own line")
    content_start = begin_at + len(begin)
    if document[content_start : content_start + 1] != b"\n":
        raise ScenarioContentError("scenario-status begin marker must use an LF line ending")
    content_start += 1
    if end_at < content_start:
        raise ScenarioContentError("scenario-status end marker must follow the begin marker")
    if end_at > 0 and document[end_at - 1 : end_at] != b"\n":
        raise ScenarioContentError("scenario-status end marker must occupy its own line")
    after_end = end_at + len(end)
    if after_end < len(document) and document[after_end : after_end + 1] != b"\n":
        raise ScenarioContentError("scenario-status end marker must occupy its own line")
    return content_start, end_at


def extract_generated_block(document: bytes) -> bytes:
    """Return exactly the bytes between the two marker lines."""

    content_start, content_end = _marker_bounds(document)
    return document[content_start:content_end]


def replace_generated_block(document: bytes, generated: bytes) -> bytes:
    """Replace only marker-interior bytes, preserving both outside slices."""

    if not generated.endswith(b"\n") or b"\r" in generated:
        raise ScenarioContentError("generated Markdown must be LF-only and end with one newline")
    content_start, content_end = _marker_bounds(document)
    return document[:content_start] + generated + document[content_end:]


def write_document(path: Path, generated: bytes) -> bool:
    if not path.is_file():
        raise ScenarioPathError(f"target document is not a file: {path}")
    try:
        before = path.read_bytes()
        after = replace_generated_block(before, generated)
        if after == before:
            return False
        path.write_bytes(after)
    except ScenarioContentError:
        raise
    except OSError as exc:
        raise ScenarioPathError(f"cannot update {path}: {exc}") from exc
    return True


def read_document_block(path: Path) -> bytes:
    """Read the generated block without modifying the document."""

    if not path.is_file():
        raise ScenarioPathError(f"target document is not a file: {path}")
    try:
        return extract_generated_block(path.read_bytes())
    except ScenarioContentError:
        raise
    except OSError as exc:
        raise ScenarioPathError(f"cannot read {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        generated = render_markdown().encode("utf-8")
        if not args.write:
            sys.stdout.buffer.write(generated)
            if read_document_block(DOCUMENT_PATH) != generated:
                raise ScenarioContentError(
                    "forward-testing.md generated block differs from scenario registry output"
                )
            return 0
        changed = write_document(DOCUMENT_PATH, generated)
        action = "updated" if changed else "already current"
        print(f"scenario status {action}: {DOCUMENT_PATH}")
        return 0
    except ScenarioContentError as exc:
        print(f"scenario status content mismatch: {exc}", file=sys.stderr)
        print(f"regenerate with: {REGENERATE_COMMAND}", file=sys.stderr)
        return 10
    except ScenarioPathError as exc:
        print(f"scenario status path error: {exc}", file=sys.stderr)
        return 13
    except Exception as exc:  # pragma: no cover - last-resort structured exit
        print(f"scenario status internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 14


if __name__ == "__main__":
    raise SystemExit(main())
