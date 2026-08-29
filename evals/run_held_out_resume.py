#!/usr/bin/env python3
"""Run an offline, non-overwriting held-out recovery scenario.

The harness deliberately uses the public initializer and auditor as separate
processes.  It creates a new project, adds one valid YAML comment to simulate
interrupted work, invokes the initializer again, and proves that every byte is
preserved.  Two read-only audits must then derive the same recovery state and
next legal action.  All generated files are retained for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = Path(__file__).with_name("held_out_resume_scenario.json")
INIT_SCRIPT = REPO_ROOT / "cumcm-modeling" / "scripts" / "init_project.py"
AUDIT_SCRIPT = REPO_ROOT / "cumcm-modeling" / "scripts" / "audit_project.py"

WORKFLOW_STATES = {
    "INTAKE",
    "WAIT_G0",
    "FRAMING",
    "WAIT_G1",
    "MODELING",
    "WAIT_G2",
    "EXPERIMENT_DESIGN",
    "WAIT_G3",
    "COMPUTING",
    "VALIDATING",
    "WAIT_G4",
    "CLAIMING",
    "WAIT_G5",
    "WRITING",
    "FINAL_QA",
    "WAIT_G6",
    "RELEASE_QA",
    "WAIT_G7",
    "SUBMISSION_READY",
}
GATES = {f"G{index}" for index in range(8)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise deterministic CUMCM project recovery without overwriting or deleting files."
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Fresh target path. It must not exist; omitted uses a unique system-temp path.",
    )
    return parser.parse_args()


def load_scenario() -> dict[str, Any]:
    """Load the small held-out scenario without importing project helpers."""

    with SCENARIO_PATH.open("r", encoding="utf-8") as stream:
        scenario = json.load(stream)
    if scenario.get("id") != "E17-held-out-resume":
        raise ValueError("unexpected held-out scenario ID")
    return scenario


def run_command(argv: list[str], allowed_codes: set[int]) -> subprocess.CompletedProcess[str]:
    """Run one public CLI and reject unplanned exit codes with full output."""

    completed = subprocess.run(
        argv,
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode not in allowed_codes:
        raise RuntimeError(
            f"command returned {completed.returncode}, expected {sorted(allowed_codes)}: {argv!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def parse_stdout_json(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Parse a CLI JSON response and require a mapping at the root."""

    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("CLI response is not a JSON object")
    return value


def snapshot_tree(root: Path) -> dict[str, str]:
    """Return a portable SHA-256 snapshot without modifying the project."""

    snapshot: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def validate_recovery_report(report: dict[str, Any], expected_fields: list[str]) -> dict[str, Any]:
    """Check report-level recovery invariants without fixing one draft state."""

    missing = [field for field in expected_fields if field not in report]
    if missing:
        raise AssertionError(f"audit report omits recovery fields: {missing}")

    workflow_state = report["workflow_state"]
    last_valid_gate = report["last_valid_gate"]
    rollback_target = report["rollback_target"]
    next_legal_action = report["next_legal_action"]
    if workflow_state not in WORKFLOW_STATES:
        raise AssertionError(f"unknown workflow_state: {workflow_state!r}")
    if last_valid_gate is not None and last_valid_gate not in GATES:
        raise AssertionError(f"invalid last_valid_gate: {last_valid_gate!r}")
    if rollback_target is not None and rollback_target not in WORKFLOW_STATES:
        raise AssertionError(f"invalid rollback_target: {rollback_target!r}")
    if rollback_target is not None and workflow_state != rollback_target:
        raise AssertionError("a hard-failure recovery state must equal rollback_target")
    if not isinstance(next_legal_action, str) or not next_legal_action.strip():
        raise AssertionError("next_legal_action must be a deterministic non-empty string")
    if "initializ" in next_legal_action.casefold():
        raise AssertionError("resume guidance must continue from evidence, not initialize the project again")
    if workflow_state == "SUBMISSION_READY":
        raise AssertionError("an initialized placeholder project cannot be submission ready")
    return {
        "workflow_state": workflow_state,
        "last_valid_gate": last_valid_gate,
        "rollback_target": rollback_target,
        "next_legal_action": next_legal_action,
    }


def main() -> int:
    args = parse_args()
    scenario = load_scenario()
    if args.target is None:
        target = Path(tempfile.gettempdir()) / f"cumcm-held-out-resume-{uuid.uuid4().hex}"
    else:
        target = args.target.resolve()
    if target.exists():
        raise FileExistsError(f"held-out target must be a new path: {target}")

    first_init = run_command(
        [
            sys.executable,
            "-X",
            "utf8",
            str(INIT_SCRIPT),
            str(target),
            "--project-id",
            scenario["project_id"],
        ],
        {0},
    )
    if parse_stdout_json(first_init).get("status") != "PASS":
        raise AssertionError("first initialization did not complete")

    mutation = scenario["mutation"]
    mutation_path = target / mutation["path"]
    if not mutation_path.is_file():
        raise FileNotFoundError(f"scenario mutation target is missing: {mutation_path}")
    with mutation_path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(mutation["append_text"])
    before_resume = snapshot_tree(target)

    second_init = run_command(
        [
            sys.executable,
            "-X",
            "utf8",
            str(INIT_SCRIPT),
            str(target),
            "--project-id",
            scenario["project_id"],
        ],
        {0},
    )
    second_response = parse_stdout_json(second_init)
    if any(item.get("status") != "NOT_APPLICABLE" for item in second_response.get("findings", [])):
        raise AssertionError("resume initialization attempted to create or replace a template file")
    after_resume = snapshot_tree(target)
    if before_resume != after_resume:
        raise AssertionError("initializer changed project bytes during resume")

    recovery_values: list[dict[str, Any]] = []
    audit_paths: list[Path] = []
    for index in (1, 2):
        audit_path = target.parent / f"{target.name}-audit-{index}.json"
        if audit_path.exists():
            raise FileExistsError(f"held-out audit path must be new: {audit_path}")
        completed = run_command(
            [
                sys.executable,
                "-X",
                "utf8",
                str(AUDIT_SCRIPT),
                str(target),
                "--json-report",
                str(audit_path),
            ],
            {10, 12},
        )
        # The initialized template intentionally contains placeholders, so a
        # clean PASS would mean the held-out scenario stopped testing recovery.
        parse_stdout_json(completed)
        with audit_path.open("r", encoding="utf-8") as stream:
            report = json.load(stream)
        recovery_values.append(
            validate_recovery_report(report, scenario["expected_report_fields"])
        )
        audit_paths.append(audit_path)

    if recovery_values[0] != recovery_values[1]:
        raise AssertionError("identical project bytes produced different recovery decisions")
    if snapshot_tree(target) != after_resume:
        raise AssertionError("read-only audits modified the held-out project")

    print(
        json.dumps(
            {
                "status": "PASS",
                "scenario": scenario["id"],
                "preserved_project": str(target),
                "preserved_audit_reports": [str(path) for path in audit_paths],
                "project_file_count": len(after_resume),
                "recovery": recovery_values[0],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
