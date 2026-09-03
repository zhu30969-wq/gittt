#!/usr/bin/env python3
"""Execute E20's decision-timing comparability scenario."""

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
from test_validation_facets_solver_timing import build_two_candidate_release


SCENARIO_PATH = Path(__file__).with_name(
    "decision_timing_comparability_scenario.json"
)
SCENARIO_ID = "E20-decision-timing-comparability"


def main() -> int:
    args = parse_target_args(
        "Run E20's non-overwriting decision-timing comparability scenario."
    )
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e20-decision-timing")
    report_root = target / "reports"

    mismatched_root = build_two_candidate_release(
        primary_interval=(120.0, 130.0),
        secondary_interval=(90.0, 100.0),
        primary_timing=scenario["primary_timing"],
        secondary_timing=scenario["incomparable_secondary_timing"],
        root=target / "mismatched",
    )
    mismatched_report = run_audit(
        mismatched_root,
        report_root / "mismatched.json",
        {10, 12},
    )
    require_code(mismatched_report, scenario["expected_code"])

    matched_root = build_two_candidate_release(
        primary_interval=(120.0, 130.0),
        secondary_interval=(90.0, 100.0),
        primary_timing=scenario["primary_timing"],
        secondary_timing=scenario["comparable_secondary_timing"],
        root=target / "matched",
    )
    matched_report = run_audit(
        matched_root,
        report_root / "matched.json",
        {0},
    )
    require_pass(matched_report)

    if scenario["primary_timing"] == scenario["incomparable_secondary_timing"]:
        raise AssertionError("negative fixture does not use distinct decision timings")
    if scenario["primary_timing"] != scenario["comparable_secondary_timing"]:
        raise AssertionError("positive fixture does not use matching decision timings")

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "verified": {
                "mixed_timing": scenario["expected_code"],
                "matched_timing": "PASS",
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
