"""Shared non-overwriting helpers for executable forward-test scenarios."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = REPO_ROOT / "tests"
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
AUDIT_SCRIPT = SCRIPT_ROOT / "audit_project.py"

for import_root in (SCRIPT_ROOT, TEST_ROOT):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)


def parse_target_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Fresh scenario-bundle path. It must not exist; omitted uses a unique system-temp path.",
    )
    return parser.parse_args()


def prepare_target(candidate: Path | None, prefix: str) -> Path:
    target = (
        Path(tempfile.gettempdir()) / f"{prefix}-{uuid.uuid4().hex}"
        if candidate is None
        else candidate.resolve()
    )
    if target.exists():
        raise FileExistsError(f"forward-scenario target must be a new path: {target}")
    target.mkdir(parents=True)
    return target


def load_scenario(path: Path, expected_id: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"scenario fixture must be a JSON object: {path}")
    if value.get("id") != expected_id:
        raise ValueError(
            f"scenario fixture ID mismatch: expected {expected_id!r}, got {value.get('id')!r}"
        )
    return value


def run_audit(
    project: Path,
    report_path: Path,
    allowed_codes: set[int],
) -> dict[str, Any]:
    if report_path.exists():
        raise FileExistsError(f"audit report path must be new: {report_path}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(AUDIT_SCRIPT),
            str(project),
            "--json-report",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode not in allowed_codes:
        raise RuntimeError(
            f"audit returned {completed.returncode}, expected {sorted(allowed_codes)} for {project}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    with report_path.open("r", encoding="utf-8") as stream:
        report = json.load(stream)
    if not isinstance(report, dict):
        raise ValueError(f"audit report must be a JSON object: {report_path}")
    return report


def finding_rows(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        finding
        for gate in report.get("gates", [])
        for finding in gate.get("findings", [])
        if finding.get("code") == code
    ]


def require_code(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    rows = finding_rows(report, code)
    if not rows:
        present = sorted(
            {
                str(finding.get("code"))
                for gate in report.get("gates", [])
                for finding in gate.get("findings", [])
            }
        )
        raise AssertionError(f"expected finding {code!r}; observed {present}")
    return rows


def require_pass(report: dict[str, Any]) -> None:
    if report.get("status") != "PASS":
        present = sorted(
            {
                str(finding.get("code"))
                for gate in report.get("gates", [])
                for finding in gate.get("findings", [])
                if finding.get("status") in {"BLOCK", "ENV_BLOCK", "STALE"}
            }
        )
        raise AssertionError(
            f"expected PASS, got {report.get('status')!r}; blocking findings={present}"
        )


def print_summary(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
