#!/usr/bin/env python3
"""Execute E19's scenario selection/holdout isolation checks."""

from __future__ import annotations

from pathlib import Path

from _forward_scenario_support import (
    load_scenario,
    parse_target_args,
    prepare_target,
    print_summary,
    require_code,
    require_pass,
    run_audit,
)
from test_audit_regressions import load_yaml, resign_release_project, write_yaml
from test_scenario_sets import build_random_optimization_release


SCENARIO_PATH = Path(__file__).with_name(
    "scenario_set_holdout_isolation_scenario.json"
)
SCENARIO_ID = "E19-scenario-set-holdout-isolation"


def save_experiment_and_resign(root: Path, experiment: dict[str, object]) -> None:
    write_yaml(root, "experiments/experiment.yaml", experiment)
    resign_release_project(root)


def main() -> int:
    args = parse_target_args(
        "Run E19's non-overwriting scenario selection/holdout isolation checks."
    )
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e19-scenario-isolation")
    report_root = target / "reports"

    positive_root = build_random_optimization_release(root=target / "positive")
    positive_experiment = load_yaml(
        positive_root / "experiments/experiment.yaml"
    )
    roles = {row["role"] for row in positive_experiment["scenario_sets"]}
    if roles != {"selection", "holdout"}:
        raise AssertionError(f"positive fixture lacks both scenario roles: {roles}")
    selection_hashes = {
        row["scenario_sha256"]
        for row in positive_experiment["scenario_sets"]
        if row["role"] == "selection"
    }
    holdout_hashes = {
        row["scenario_sha256"]
        for row in positive_experiment["scenario_sets"]
        if row["role"] == "holdout"
    }
    if selection_hashes.intersection(holdout_hashes):
        raise AssertionError("positive fixture reuses selection and holdout scenario bytes")
    positive_report = run_audit(
        positive_root, report_root / "positive.json", {0}
    )
    require_pass(positive_report)

    missing_role_root = build_random_optimization_release(
        root=target / "missing-role"
    )
    experiment = load_yaml(missing_role_root / "experiments/experiment.yaml")
    experiment["scenario_sets"] = [
        row for row in experiment["scenario_sets"] if row["role"] == "selection"
    ]
    save_experiment_and_resign(missing_role_root, experiment)
    missing_role_report = run_audit(
        missing_role_root,
        report_root / "missing-role.json",
        {10, 12},
    )
    require_code(missing_role_report, scenario["missing_role_code"])

    overlap_root = build_random_optimization_release(root=target / "hash-overlap")
    experiment = load_yaml(overlap_root / "experiments/experiment.yaml")
    selection = next(
        row for row in experiment["scenario_sets"] if row["role"] == "selection"
    )
    holdout = next(
        row for row in experiment["scenario_sets"] if row["role"] == "holdout"
    )
    holdout["scenario_sha256"] = selection["scenario_sha256"]
    save_experiment_and_resign(overlap_root, experiment)
    overlap_report = run_audit(
        overlap_root,
        report_root / "hash-overlap.json",
        {10, 12},
    )
    require_code(overlap_report, scenario["hash_overlap_code"])

    selection_claim_root = build_random_optimization_release(
        metric_role="selection", root=target / "selection-claim"
    )
    selection_claim_report = run_audit(
        selection_claim_root,
        report_root / "selection-claim.json",
        {10, 12},
    )
    require_code(selection_claim_report, scenario["selection_claim_code"])

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "verified": {
                "disjoint_holdout": "PASS",
                "missing_role": scenario["missing_role_code"],
                "hash_overlap": scenario["hash_overlap_code"],
                "selection_claim": scenario["selection_claim_code"],
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
