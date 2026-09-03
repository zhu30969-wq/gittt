#!/usr/bin/env python3
"""Execute E12's structured-diagnostic evidence and threshold scenario."""

from __future__ import annotations

from copy import deepcopy
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
from test_audit_regressions import (
    build_release_project,
    file_ref,
    load_yaml,
    resign_release_project,
    run_audit as run_in_process_audit,
    write_yaml,
)


SCENARIO_PATH = Path(__file__).with_name("structured_diagnostic_scenario.json")
SCENARIO_ID = "E12-structured-diagnostic"
REQUIRED_CHECK_REF = "check:input-integrity"
CONDITIONAL_CHECK_REF = "check:reproducibility"


def build_structured_diagnostic_release(
    root: Path,
    scenario: dict[str, Any],
) -> Path:
    """Build a release with one required and one activated conditional check."""

    root = build_release_project(root)
    score = float(scenario["observed_score"])

    model = load_yaml(root / "specs/model_spec.yaml")
    model["validation_plan"]["checks"].append(
        {
            "id": CONDITIONAL_CHECK_REF,
            "check_type": "reproducibility",
            "applicability": "conditional",
            "activation_condition": (
                "Execute when the deterministic score supports a final quantitative claim."
            ),
            "criticality": "blocking",
            "rationale": (
                "The final numeric claim activates a byte-backed reproducibility check."
            ),
            "procedure": "Extract the repeated score from the registered output.",
            "pass_rule": f"The repeated score equals {score}.",
            "threshold": {
                "operator": str(scenario["passing_operator"]),
                "value": score,
                "unit": "1",
            },
            "failure_response": "block_result",
        }
    )
    write_yaml(root, "specs/model_spec.yaml", model)

    result = load_yaml(root / "results/results.yaml")
    result["diagnostics"].append(
        {
            "id": "diagnostic:reproducibility",
            "check_ref": CONDITIONAL_CHECK_REF,
            "check_type": "reproducibility",
            "status": "PASS",
            "condition_met": True,
            "condition_evidence": (
                "The registered final numeric claim uses this deterministic score."
            ),
            "severity": "critical",
            "procedure": "Extracted the repeated score from the hashed run output.",
            "observation": f"The repeated score is {score}.",
            "observed": {"value": score, "unit": "1"},
            "source_file": file_ref(root, "outputs/result.json"),
            "extractor": {"type": "json_pointer", "pointer": "/score"},
            "conclusion": "The activated reproducibility threshold passed.",
            "evidence_files": [],
            "comparison_bindings": [],
        }
    )
    write_yaml(root, "results/results.yaml", result)
    resign_release_project(root)
    return root


def remove_required_diagnostic(root: Path) -> None:
    result = load_yaml(root / "results/results.yaml")
    result["diagnostics"] = [
        row
        for row in result["diagnostics"]
        if row.get("check_ref") != REQUIRED_CHECK_REF
    ]
    write_yaml(root, "results/results.yaml", result)
    resign_release_project(root)


def duplicate_conditional_diagnostic(root: Path) -> None:
    result = load_yaml(root / "results/results.yaml")
    diagnostic = next(
        row
        for row in result["diagnostics"]
        if row.get("check_ref") == CONDITIONAL_CHECK_REF
    )
    duplicate = deepcopy(diagnostic)
    duplicate["id"] = "diagnostic:reproducibility-duplicate"
    result["diagnostics"].append(duplicate)
    write_yaml(root, "results/results.yaml", result)
    resign_release_project(root)


def contradict_required_threshold(
    root: Path,
    scenario: dict[str, Any],
) -> None:
    model = load_yaml(root / "specs/model_spec.yaml")
    check = next(
        row
        for row in model["validation_plan"]["checks"]
        if row.get("id") == REQUIRED_CHECK_REF
    )
    check["pass_rule"] = (
        f"The observed score is {scenario['contradictory_operator']} "
        f"{scenario['contradictory_threshold']}."
    )
    check["threshold"] = {
        "operator": str(scenario["contradictory_operator"]),
        "value": float(scenario["contradictory_threshold"]),
        "unit": "1",
    }
    write_yaml(root, "specs/model_spec.yaml", model)
    resign_release_project(root)


def assert_result_ineligible(root: Path, context: str) -> None:
    _report, audit = run_in_process_audit(root)
    if audit.result_eligibility.get("result:main") is not False:
        raise AssertionError(f"{context} did not make result:main ineligible")


def main() -> int:
    args = parse_target_args(
        "Run E12's non-overwriting structured-diagnostic evidence scenario."
    )
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e12-structured-diagnostic")
    report_root = target / "reports"

    positive_root = build_structured_diagnostic_release(
        target / "positive",
        scenario,
    )
    positive_report = run_audit(
        positive_root,
        report_root / "positive.json",
        {0},
    )
    require_pass(positive_report)
    positive_result = load_yaml(positive_root / "results/results.yaml")
    diagnostic_counts = {
        check_ref: sum(
            row.get("check_ref") == check_ref
            for row in positive_result["diagnostics"]
        )
        for check_ref in (REQUIRED_CHECK_REF, CONDITIONAL_CHECK_REF)
    }
    if diagnostic_counts != {
        REQUIRED_CHECK_REF: 1,
        CONDITIONAL_CHECK_REF: 1,
    }:
        raise AssertionError(
            f"positive fixture lacks one-to-one diagnostic evidence: {diagnostic_counts}"
        )
    threshold_passes = finding_rows(
        positive_report,
        str(scenario["threshold_pass_code"]),
    )
    for check_ref in (REQUIRED_CHECK_REF, CONDITIONAL_CHECK_REF):
        matching = [
            row
            for row in threshold_passes
            if check_ref in str(row.get("message"))
        ]
        if len(matching) != 1:
            raise AssertionError(
                f"positive fixture did not recompute exactly one PASS for {check_ref}"
            )

    missing_root = build_structured_diagnostic_release(
        target / "missing-required",
        scenario,
    )
    remove_required_diagnostic(missing_root)
    missing_report = run_audit(
        missing_root,
        report_root / "missing-required.json",
        {10, 12},
    )
    require_code(missing_report, str(scenario["evidence_ambiguity_code"]))
    assert_result_ineligible(missing_root, "missing required diagnostic")

    duplicate_root = build_structured_diagnostic_release(
        target / "duplicate-conditional",
        scenario,
    )
    duplicate_conditional_diagnostic(duplicate_root)
    duplicate_report = run_audit(
        duplicate_root,
        report_root / "duplicate-conditional.json",
        {10, 12},
    )
    require_code(duplicate_report, str(scenario["evidence_ambiguity_code"]))
    assert_result_ineligible(duplicate_root, "duplicate conditional diagnostic")

    mismatch_root = build_structured_diagnostic_release(
        target / "contradictory-pass",
        scenario,
    )
    contradict_required_threshold(mismatch_root, scenario)
    mismatch_report = run_audit(
        mismatch_root,
        report_root / "contradictory-pass.json",
        {10, 12},
    )
    require_code(mismatch_report, str(scenario["status_mismatch_code"]))
    assert_result_ineligible(mismatch_root, "hand-entered contradictory PASS")

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "verified": {
                "one_diagnostic_per_actionable_check": "PASS",
                "required_diagnostic_missing": scenario["evidence_ambiguity_code"],
                "conditional_diagnostic_duplicate": scenario[
                    "evidence_ambiguity_code"
                ],
                "hand_entered_pass_contradiction": scenario[
                    "status_mismatch_code"
                ],
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
