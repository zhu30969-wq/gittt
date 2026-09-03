#!/usr/bin/env python3
"""Execute E18's hybrid validation-facet union scenario."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _forward_scenario_support import (
    finding_rows,
    load_scenario,
    parse_target_args,
    prepare_target,
    print_summary,
    require_code,
    require_pass,
    run_audit,
)
from audit_project import VALIDATION_COVERAGE_BY_FAMILY
from test_audit_regressions import load_yaml, resign_release_project, write_yaml
from test_validation_facets_solver_timing import (
    build_hybrid_descriptive_release,
    build_hybrid_union_release,
    build_optimization_release,
    fresh_release,
)


SCENARIO_PATH = Path(__file__).with_name(
    "hybrid_validation_facet_union_scenario.json"
)
SCENARIO_ID = "E18-hybrid-validation-facet-union"


def save_model_and_resign(root: Path, model: dict[str, Any]) -> None:
    write_yaml(root, "specs/model_spec.yaml", model)
    resign_release_project(root)


def main() -> int:
    args = parse_target_args(
        "Run E18's non-overwriting hybrid validation-facet union scenario."
    )
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e18-hybrid-facets")
    report_root = target / "reports"

    missing_facets_root = build_hybrid_descriptive_release(
        target / "missing-facets"
    )
    model = load_yaml(missing_facets_root / "specs/model_spec.yaml")
    model.pop("validation_facets")
    save_model_and_resign(missing_facets_root, model)
    missing_facets_report = run_audit(
        missing_facets_root,
        report_root / "missing-facets.json",
        {10, 12},
    )
    require_code(missing_facets_report, scenario["missing_facets_code"])

    facets = list(scenario["facets"])
    expected_union = sorted(
        set().union(*(VALIDATION_COVERAGE_BY_FAMILY[facet] for facet in facets))
    )
    missing_union_root = fresh_release(target / "missing-union")
    model = load_yaml(missing_union_root / "specs/model_spec.yaml")
    model["model_family"] = "hybrid"
    model["validation_facets"] = facets
    save_model_and_resign(missing_union_root, model)
    missing_union_report = run_audit(
        missing_union_root,
        report_root / "missing-union.json",
        {10, 12},
    )
    union_rows = require_code(missing_union_report, scenario["missing_union_code"])
    model_union_rows = [
        row for row in union_rows if row.get("artifact_id") == "model:main"
    ]
    if len(model_union_rows) != 1 or not model_union_rows[0]["message"].endswith(
        str(expected_union)
    ):
        raise AssertionError(
            "hybrid coverage finding did not expose the exact optimization/simulation union"
        )

    positive_root = build_hybrid_union_release(target / "positive-union")
    positive_report = run_audit(
        positive_root,
        report_root / "positive-union.json",
        {0},
    )
    require_pass(positive_report)
    if finding_rows(positive_report, scenario["missing_union_code"]):
        raise AssertionError("complete hybrid facet union still reports missing coverage")

    escape_root = build_optimization_release(target / "optimization-escape")
    model = load_yaml(escape_root / "specs/model_spec.yaml")
    witness = scenario["optimization_check_witness"]
    if witness not in VALIDATION_COVERAGE_BY_FAMILY["optimization"]:
        raise AssertionError(f"configured witness is not an optimization check: {witness}")
    model["validation_facets"] = ["simulation"]
    before = len(model["validation_plan"]["checks"])
    model["validation_plan"]["checks"] = [
        check
        for check in model["validation_plan"]["checks"]
        if check.get("check_type") != witness
    ]
    if len(model["validation_plan"]["checks"]) != before - 1:
        raise AssertionError(f"optimization witness was not declared exactly once: {witness}")
    save_model_and_resign(escape_root, model)
    escape_report = run_audit(
        escape_root,
        report_root / "optimization-escape.json",
        {10, 12},
    )
    escape_rows = require_code(escape_report, scenario["optimization_escape_code"])
    if not any(witness in str(row.get("message")) for row in escape_rows):
        raise AssertionError(
            "declaring a simulation facet hid the missing optimization check"
        )

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "expected_union": expected_union,
            "verified": {
                "missing_facets": scenario["missing_facets_code"],
                "hybrid_union": "PASS",
                "optimization_escape": scenario["optimization_escape_code"],
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
