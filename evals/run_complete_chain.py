#!/usr/bin/env python3
"""Execute E01's full synthetic optimization evidence chain.

The harness starts from a missing target, captures the initialized INTAKE
state, builds a fully linked release, reruns both registered programs, and
requires PASS / SUBMISSION_READY.  It then preserves a negative result
mutation whose fixed-decision best response exceeds tolerance while the
solver-optimality diagnostic still passes.  Restoring the exact original
result bytes must restore PASS without refreshing hashes or approvals.

No generated file is deleted.  The project, four audit reports, and negative
result snapshot are retained for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_PATH = Path(__file__).with_name("complete_chain_scenario.json")
BUILDER = REPO_ROOT / "tests" / "build_release_fixture.py"
SCRIPTS = REPO_ROOT / "cumcm-modeling" / "scripts"
AUDIT_SCRIPT = SCRIPTS / "audit_project.py"
SCHEMA_ROOT = REPO_ROOT / "cumcm-modeling" / "references" / "schemas"
sys.path.insert(0, str(SCRIPTS))

from _contract_support import load_yaml, write_yaml_atomic  # noqa: E402
from audit_project import Audit  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run E01's non-overwriting synthetic complete-chain scenario."
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Fresh target path. It must not exist; omitted uses a unique system-temp path.",
    )
    return parser.parse_args()


def load_scenario() -> dict[str, Any]:
    with SCENARIO_PATH.open("r", encoding="utf-8") as stream:
        scenario = json.load(stream)
    if scenario.get("id") != "E01-complete-chain":
        raise ValueError("unexpected complete-chain scenario ID")
    return scenario


def run_command(
    argv: list[str],
    allowed_codes: set[int],
    *,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
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


def snapshot_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


def load_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object at {path}")
    return value


def run_in_process_audit(root: Path) -> tuple[dict[str, Any], Audit]:
    audit = Audit(root, SCHEMA_ROOT)
    if audit.load_schemas() and audit.load_manifest():
        audit.load_artifacts()
        audit.validate_manifest_files()
        audit.register_release_snapshot()
        audit.validate_release_activity()
        audit.validate_ids_and_refs()
        audit.verify_embedded_files()
        audit.validate_scientific_invariants()
        audit.verify_captured_files_unchanged()
        audit.validate_dag_and_propagate_stale()
        audit.validate_release_deliverables()
        audit.validate_reviews_and_profile()
        if audit.verify_captured_files_unchanged():
            audit.validate_dag_and_propagate_stale()
        audit.enforce_release_gate_passes()
        if audit.verify_captured_files_unchanged():
            audit.validate_dag_and_propagate_stale()
            audit.enforce_release_gate_passes()
    return audit.report(), audit


def finding_rows(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        finding
        for gate in report.get("gates", [])
        for finding in gate.get("findings", [])
        if finding.get("code") == code
    ]


def require_resume_fields(
    report: dict[str, Any], expected_fields: list[str]
) -> dict[str, Any]:
    missing = [field for field in expected_fields if field not in report]
    if missing:
        raise AssertionError(f"audit report omits recovery fields: {missing}")
    if not isinstance(report["next_legal_action"], str) or not report[
        "next_legal_action"
    ].strip():
        raise AssertionError("next_legal_action must be non-empty")
    return {field: report[field] for field in expected_fields}


def audit_to_path(
    root: Path,
    report_path: Path,
    allowed_codes: set[int],
) -> dict[str, Any]:
    if report_path.exists():
        raise FileExistsError(f"audit report path must be new: {report_path}")
    completed = run_command(
        [
            sys.executable,
            "-X",
            "utf8",
            str(AUDIT_SCRIPT),
            str(root),
            "--json-report",
            str(report_path),
        ],
        allowed_codes,
    )
    response = json.loads(completed.stdout)
    if not isinstance(response, dict):
        raise ValueError("audit CLI response is not a JSON object")
    return load_json_object(report_path)


def assert_quantitative_claim_trace(root: Path, audit: Audit) -> None:
    claims = load_yaml(root / "claims" / "claims.yaml")
    result = load_yaml(root / "results" / "results.yaml")
    metric_refs = {row.get("metric_ref") for row in result.get("metrics", [])}
    quantitative = 0
    for claim in claims.get("claims", []):
        if claim.get("publication_status") != "final":
            continue
        evidence_results = {
            row.get("ref")
            for row in claim.get("evidence_refs", [])
            if isinstance(row, dict) and str(row.get("ref", "")).startswith("result:")
        }
        for assertion in claim.get("numeric_assertions", []):
            quantitative += 1
            if assertion.get("metric_ref") not in metric_refs:
                raise AssertionError("numeric assertion does not resolve to a result metric")
            if not any(audit.result_eligibility.get(ref) for ref in evidence_results):
                raise AssertionError("numeric assertion has no eligible result evidence")
    if quantitative == 0:
        raise AssertionError("E01 must retain at least one quantitative final assertion")


def main() -> int:
    args = parse_args()
    scenario = load_scenario()
    target = (
        Path(tempfile.gettempdir()) / f"cumcm-e01-complete-chain-{uuid.uuid4().hex}"
        if args.target is None
        else args.target.resolve()
    )
    if target.exists():
        raise FileExistsError(f"E01 target must be a new path: {target}")

    report_paths = {
        name: target.parent / f"{target.name}-e01-{name}.json"
        for name in ("initial-audit", "clean-audit", "negative-audit", "restored-audit")
    }
    negative_result_snapshot = target.parent / f"{target.name}-e01-negative-results.yaml"
    occupied = [
        path for path in [*report_paths.values(), negative_result_snapshot] if path.exists()
    ]
    if occupied:
        raise FileExistsError(f"E01 evidence paths must be new: {occupied}")

    builder = run_command(
        [
            sys.executable,
            "-X",
            "utf8",
            str(BUILDER),
            str(target),
            "--profile",
            scenario["fixture_profile"],
            "--initial-audit-report",
            str(report_paths["initial-audit"]),
        ],
        {0},
    )
    builder_response = json.loads(builder.stdout)
    if builder_response.get("status") != "PASS":
        raise AssertionError("release fixture builder did not complete")

    initial_report = load_json_object(report_paths["initial-audit"])
    initial_resume = require_resume_fields(
        initial_report, scenario["expected_report_fields"]
    )
    if initial_report.get("status") != "BLOCK" or initial_resume != {
        "workflow_state": "INTAKE",
        "last_valid_gate": None,
        "rollback_target": "INTAKE",
        "next_legal_action": initial_resume["next_legal_action"],
    }:
        raise AssertionError(f"initialized fixture did not expose INTAKE: {initial_resume}")

    before_rerun = snapshot_tree(target)
    run_command(
        [sys.executable, "-X", "utf8", "code/main.py"],
        {0},
        cwd=target,
    )
    run_command(
        [sys.executable, "-X", "utf8", "code/reconcile_objective.py"],
        {0},
        cwd=target,
    )
    after_rerun = snapshot_tree(target)
    if before_rerun != after_rerun:
        raise AssertionError("clean rerun changed registered project bytes")

    clean_report = audit_to_path(
        target, report_paths["clean-audit"], {0}
    )
    clean_resume = require_resume_fields(clean_report, scenario["expected_report_fields"])
    clean_in_process, clean_audit = run_in_process_audit(target)
    if clean_report.get("status") != "PASS" or clean_in_process.get("status") != "PASS":
        raise AssertionError("completed E01 fixture did not pass")
    if clean_resume["workflow_state"] != "SUBMISSION_READY":
        raise AssertionError(f"clean E01 is not submission ready: {clean_resume}")
    if clean_resume["last_valid_gate"] != "G7" or clean_resume["rollback_target"] is not None:
        raise AssertionError(f"clean E01 gate state is inconsistent: {clean_resume}")
    if not clean_audit.result_eligibility.get("result:main"):
        raise AssertionError("clean E01 result is not eligible")
    if not finding_rows(clean_report, "QUESTION_EVIDENCE_PATH_COMPLETE"):
        raise AssertionError("E01 question does not reach a final claim")
    if len(finding_rows(clean_report, "OUTPUT_COMPARISON_RECOMPUTED")) < 2:
        raise AssertionError("E01 did not recompute both preregistered output comparators")
    if not finding_rows(clean_report, "OBJECTIVE_RECONCILIATION_PASS"):
        raise AssertionError("clean E01 lacks objective reconciliation evidence")
    if not finding_rows(clean_report, "FIGURE_CONTENT_VALID"):
        raise AssertionError("E01 figure did not traverse the release evidence chain")
    assert_quantitative_claim_trace(target, clean_audit)

    solver_objective = Decimal(str(scenario["solver_objective"]))
    solver_bound = Decimal(str(scenario["solver_bound"]))
    negative_gain = Decimal(str(scenario["negative_repair_gain"]))
    solver_gap = (solver_bound - solver_objective) / solver_objective
    repair_share = negative_gain / solver_objective
    if solver_gap != Decimal(str(scenario["solver_relative_gap"])):
        raise AssertionError(f"unexpected synthetic solver gap: {solver_gap}")
    if repair_share != Decimal(str(scenario["negative_repair_share"])):
        raise AssertionError(f"unexpected synthetic repair share: {repair_share}")

    result_path = target / "results" / "results.yaml"
    original_result_bytes = result_path.read_bytes()
    result = load_yaml(result_path)
    reconciliation = next(
        row
        for row in result["diagnostics"]
        if row.get("check_type") == "objective_reconciliation"
    )["objective_reconciliation"]
    reconciliation["best_response_objective"]["value"] = scenario[
        "negative_best_response_objective"
    ]
    reconciliation["repair_gain"]["value"] = scenario["negative_repair_gain"]
    write_yaml_atomic(result_path, result)
    negative_result_snapshot.write_bytes(result_path.read_bytes())

    negative_report = audit_to_path(
        target, report_paths["negative-audit"], {10, 12}
    )
    negative_resume = require_resume_fields(
        negative_report, scenario["expected_report_fields"]
    )
    negative_in_process, negative_audit = run_in_process_audit(target)
    if not finding_rows(negative_report, "OBJECTIVE_REPAIR_GAIN_EXCEEDED"):
        raise AssertionError("negative E01 did not expose objective repair gain")
    if negative_audit.result_eligibility.get("result:main") is not False:
        raise AssertionError("negative E01 result_eligibility was not set false")
    solver_passes = [
        row
        for row in finding_rows(negative_report, "DIAGNOSTIC_THRESHOLD_PASS")
        if "check:solver-optimality" in str(row.get("message"))
    ]
    if len(solver_passes) != 1:
        raise AssertionError("solver_optimality did not remain PASS in the negative E01 state")
    if negative_in_process.get("status") == "PASS":
        raise AssertionError("negative E01 unexpectedly passed")
    if negative_resume["workflow_state"] == "SUBMISSION_READY":
        raise AssertionError("negative E01 remained submission ready")
    if negative_resume["rollback_target"] != negative_resume["workflow_state"]:
        raise AssertionError(f"negative recovery state is inconsistent: {negative_resume}")

    result_path.write_bytes(original_result_bytes)
    if snapshot_tree(target) != before_rerun:
        raise AssertionError("restoring E01 result bytes did not restore the clean project snapshot")
    restored_report = audit_to_path(
        target, report_paths["restored-audit"], {0}
    )
    restored_resume = require_resume_fields(
        restored_report, scenario["expected_report_fields"]
    )
    restored_in_process, restored_audit = run_in_process_audit(target)
    if restored_report.get("status") != "PASS" or restored_in_process.get("status") != "PASS":
        raise AssertionError("restored E01 fixture did not return to PASS")
    if restored_resume != clean_resume:
        raise AssertionError("restored E01 recovery fields differ from the clean audit")
    if not restored_audit.result_eligibility.get("result:main"):
        raise AssertionError("restored E01 result did not regain eligibility")

    print(
        json.dumps(
            {
                "status": "PASS",
                "scenario": scenario["id"],
                "preserved_project": str(target),
                "preserved_audit_reports": {
                    name: str(path) for name, path in report_paths.items()
                },
                "preserved_negative_result": str(negative_result_snapshot),
                "project_file_count": len(before_rerun),
                "initial": initial_resume,
                "clean": clean_resume,
                "negative": negative_resume,
                "restored": restored_resume,
                "orthogonality": {
                    "solver_relative_gap": str(solver_gap),
                    "repair_share": str(repair_share),
                    "solver_optimality_passed": True,
                    "objective_reconciliation_blocked": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
