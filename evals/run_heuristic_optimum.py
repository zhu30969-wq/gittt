#!/usr/bin/env python3
"""Execute E03's heuristic-feasibility and claim-strength scenario."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from _forward_scenario_support import (
    REPO_ROOT,
    finding_rows,
    load_scenario,
    parse_target_args,
    prepare_target,
    print_summary,
    require_code,
    require_pass,
    run_audit,
)
from _contract_support import sha256_file
from test_audit_regressions import (
    file_ref,
    load_yaml,
    resign_release_project,
    run_audit as run_in_process_audit,
    write_text,
    write_yaml,
)


SCENARIO_PATH = Path(__file__).with_name("heuristic_optimum_scenario.json")
SCENARIO_ID = "E03-heuristic-optimum"
BUILDER = REPO_ROOT / "tests" / "build_release_fixture.py"


def build_heuristic_release(root: Path, scenario: dict[str, Any]) -> Path:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(BUILDER),
            str(root),
            "--profile",
            str(scenario["base_fixture_profile"]),
        ],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"fixture builder returned {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    model = load_yaml(root / "specs/model_spec.yaml")
    model["algorithm"].update(
        {
            "description": (
                "Construct one feasible candidate under a fixed search budget; "
                "the procedure does not produce a global-optimality certificate."
            ),
            "termination": "Stop when the registered candidate budget is exhausted.",
            "complexity_note": "The synthetic budget is constant and deliberately incomplete.",
        }
    )
    write_yaml(root, "specs/model_spec.yaml", model)

    result = load_yaml(root / "results/results.yaml")
    solver_diagnostic = next(
        row
        for row in result["diagnostics"]
        if row.get("check_type") == "solver_optimality"
    )
    solver_diagnostic.pop("objective_incumbent", None)
    solver_diagnostic.pop("objective_bound", None)
    solver_diagnostic["observation"] = (
        "The fixed candidate budget completed; no global bound or proof was produced."
    )
    solver_diagnostic["conclusion"] = (
        "The registered search-budget threshold passed without certifying global optimality."
    )
    write_yaml(root, "results/results.yaml", result)

    claims = load_yaml(root / "claims/claims.yaml")
    claim = claims["claims"][0]
    claim["statement"] = scenario["supported_claim_statement"]
    claim["claim_type"] = "optimality"
    claim["epistemic_status"] = "empirically_supported"
    claim["limitations"] = [
        "The fixed candidate budget and absence of a valid global bound do not prove global optimality."
    ]
    write_yaml(root, "claims/claims.yaml", claims)
    resign_release_project(root)
    return root


def refresh_file_hash_refs(node: Any, root: Path, relative_paths: set[str]) -> bool:
    changed = False
    if isinstance(node, dict):
        path = node.get("path")
        if path in relative_paths and "sha256" in node:
            digest = sha256_file(root / str(path))
            if node["sha256"] != digest:
                node["sha256"] = digest
                changed = True
        for value in node.values():
            changed = refresh_file_hash_refs(value, root, relative_paths) or changed
    elif isinstance(node, list):
        for value in node:
            changed = refresh_file_hash_refs(value, root, relative_paths) or changed
    return changed


def register_changed_files(root: Path, relative_paths: set[str]) -> None:
    manifest = load_yaml(root / "manifest.yaml")
    for entry in manifest["artifacts"]:
        path = root / entry["path"]
        document = load_yaml(path)
        if refresh_file_hash_refs(document, root, relative_paths):
            write_yaml(root, entry["path"], document)
    refresh_file_hash_refs(manifest, root, relative_paths)
    write_yaml(root, "manifest.yaml", manifest)


def make_constraint_violation(root: Path, violation: float) -> None:
    output_path = root / "outputs/result.json"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["max_constraint_violation"] = violation
    rendered = json.dumps(payload, sort_keys=True) + "\n"
    write_text(root, "outputs/result.json", rendered)
    write_text(root, "inputs/expected-main.json", rendered)

    result = load_yaml(root / "results/results.yaml")
    diagnostic = next(
        row
        for row in result["diagnostics"]
        if row.get("check_type") == "constraint_feasibility"
    )
    diagnostic["status"] = "BLOCK"
    diagnostic["observed"]["value"] = violation
    diagnostic["observation"] = f"Maximum registered constraint violation is {violation}."
    diagnostic["conclusion"] = "The candidate violates a hard constraint."
    write_yaml(root, "results/results.yaml", result)

    register_changed_files(
        root,
        {"outputs/result.json", "inputs/expected-main.json"},
    )
    resign_release_project(root)


def make_unsupported_global_claim(root: Path, scenario: dict[str, Any]) -> None:
    proof_path = write_text(
        root,
        "proofs/unsupported-global-optimum.md",
        "Unsupported global-optimality assertion without derivation or certificate.\n",
    )
    claims = load_yaml(root / "claims/claims.yaml")
    claim = claims["claims"][0]
    claim["statement"] = scenario["unsupported_claim_statement"]
    claim["claim_type"] = "theoretical"
    claim["epistemic_status"] = "formally_proved"
    claim["proof_artifact"] = file_ref(
        root,
        proof_path.relative_to(root).as_posix(),
    )
    write_yaml(root, "claims/claims.yaml", claims)
    resign_release_project(root)


def main() -> int:
    args = parse_target_args(
        "Run E03's non-overwriting heuristic feasibility and claim-strength scenario."
    )
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e03-heuristic-optimum")
    report_root = target / "reports"

    positive_root = build_heuristic_release(target / "positive", scenario)
    positive_report = run_audit(
        positive_root,
        report_root / "positive.json",
        {0},
    )
    require_pass(positive_report)
    constraint_passes = [
        row
        for row in finding_rows(positive_report, "DIAGNOSTIC_THRESHOLD_PASS")
        if "check:constraint-feasibility" in str(row.get("message"))
    ]
    if len(constraint_passes) != 1:
        raise AssertionError("positive heuristic fixture did not check constraint feasibility")
    positive_claim = load_yaml(positive_root / "claims/claims.yaml")["claims"][0]
    if "best feasible" not in positive_claim["statement"].lower():
        raise AssertionError("positive heuristic claim is not limited to the best feasible candidate")
    positive_result = load_yaml(positive_root / "results/results.yaml")
    solver_diagnostic = next(
        row
        for row in positive_result["diagnostics"]
        if row.get("check_type") == "solver_optimality"
    )
    if "objective_incumbent" in solver_diagnostic or "objective_bound" in solver_diagnostic:
        raise AssertionError("positive heuristic fixture unexpectedly retained a global bound interval")

    infeasible_root = build_heuristic_release(target / "infeasible", scenario)
    make_constraint_violation(
        infeasible_root,
        float(scenario["infeasible_constraint_violation"]),
    )
    infeasible_report = run_audit(
        infeasible_root,
        report_root / "infeasible.json",
        {10, 12},
    )
    require_code(infeasible_report, scenario["constraint_failure_code"])
    require_code(infeasible_report, scenario["ineligible_result_code"])
    _infeasible_report, infeasible_audit = run_in_process_audit(infeasible_root)
    if infeasible_audit.result_eligibility.get("result:main") is not False:
        raise AssertionError("constraint violation did not make the heuristic result ineligible")

    unsupported_root = build_heuristic_release(
        target / "unsupported-global-claim",
        scenario,
    )
    make_unsupported_global_claim(unsupported_root, scenario)
    unsupported_report = run_audit(
        unsupported_root,
        report_root / "unsupported-global-claim.json",
        {10, 12},
    )
    require_code(unsupported_report, scenario["unsupported_global_claim_code"])

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "verified": {
                "best_feasible_candidate": "PASS",
                "constraint_violation": scenario["constraint_failure_code"],
                "unsupported_global_claim": scenario[
                    "unsupported_global_claim_code"
                ],
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
