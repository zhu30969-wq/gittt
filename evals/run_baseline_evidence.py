#!/usr/bin/env python3
"""Execute E11's baseline coverage, comparability, and freshness scenario."""

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
from _contract_support import sha256_file
from test_audit_regressions import (
    build_release_project,
    file_ref,
    load_yaml,
    resign_release_project,
    run_audit as run_in_process_audit,
    write_text,
    write_yaml,
)


SCENARIO_PATH = Path(__file__).with_name("baseline_evidence_scenario.json")
SCENARIO_ID = "E11-baseline-evidence"


def baseline_check_id(check_type: str) -> str:
    return f"check:baseline-{check_type.replace('_', '-')}"


def build_baseline_release(root: Path) -> Path:
    root = build_release_project(root)
    write_text(root, "code/baseline.py", "print('independent baseline')\n")
    write_text(root, "outputs/baseline.json", '{"score": 0.95}\n')

    problem = load_yaml(root / "specs/problem_spec.yaml")
    problem["questions"].append(
        {
            "id": "question:q2",
            "text": "Check the same score under a second declared requirement.",
            "task_type": "description",
            "required_outputs": ["deliverable:q2"],
            "evaluation_intent": "The same comparable synthetic score.",
        }
    )
    problem["deliverables"].append(
        {
            "id": "deliverable:q2",
            "description": "Second-question comparable score.",
            "question_refs": ["question:q2"],
        }
    )
    write_yaml(root, "specs/problem_spec.yaml", problem)

    original_model = load_yaml(root / "specs/model_spec.yaml")
    baseline_model = deepcopy(original_model)
    baseline_model["id"] = "model:baseline"
    baseline_model["role"] = "baseline"
    baseline_model["addresses"] = ["question:q1", "question:q2"]
    baseline_model["symbols"][0]["id"] = "symbol:baseline-x"
    baseline_model["formulation"]["equations"][0]["id"] = "formula:baseline-identity"
    baseline_model["formulation"]["equations"][0]["uses"] = [
        "symbol:baseline-x"
    ]
    for check in baseline_model["validation_plan"]["checks"]:
        check["id"] = baseline_check_id(check["check_type"])
    baseline_model["algorithm"].update(
        {
            "description": "Emit an independent deterministic simple baseline.",
            "entrypoint": "code/baseline.py",
            "termination": "One independent baseline run.",
        }
    )
    write_yaml(root, "specs/baseline.yaml", baseline_model)

    primary_model = original_model
    primary_model["addresses"] = ["question:q1", "question:q2"]
    primary_model["depends_on"] = ["problem:main", "model:baseline"]
    primary_model["method_selection"]["baseline_policy"] = {
        "status": "required",
        "model_refs": ["model:baseline"],
        "rationale": "Compare the primary method with one independent simple baseline.",
    }
    primary_model["validation_plan"]["checks"].append(
        {
            "id": "check:baseline-comparison",
            "check_type": "baseline_comparison",
            "applicability": "required",
            "activation_condition": None,
            "criticality": "blocking",
            "rationale": "The selected baseline must cover both questions under the same metric definition.",
            "procedure": "Run both independent implementations and compare their registered score metrics.",
            "pass_rule": "The primary score is no lower than the eligible baseline score.",
            "threshold": None,
            "failure_response": "block_result",
        }
    )
    write_yaml(root, "specs/model_spec.yaml", primary_model)

    original_experiment = load_yaml(root / "experiments/experiment.yaml")
    baseline_experiment = deepcopy(original_experiment)
    baseline_experiment["id"] = "experiment:baseline"
    baseline_experiment["depends_on"] = ["model:baseline"]
    baseline_experiment["model_ref"] = "model:baseline"
    baseline_experiment["question_refs"] = ["question:q1", "question:q2"]
    baseline_experiment["purpose"] = "Run the independent simple baseline on the same comparison frame."
    baseline_experiment["hypothesis"] = "The baseline score is at least the common acceptance threshold."
    baseline_experiment["code_files"] = [file_ref(root, "code/baseline.py")]
    baseline_experiment["command"]["argv"] = ["python", "code/baseline.py"]
    baseline_experiment["baseline_refs"] = []
    baseline_experiment["baseline_comparison_rules"] = []
    baseline_experiment["metrics"][0]["id"] = "metric:baseline-score"
    baseline_experiment["metrics"][0]["source_output_ref"] = "output:baseline"
    baseline_experiment["acceptance_rules"][0]["metric_ref"] = "metric:baseline-score"
    baseline_experiment["outputs"][0] = {
        "id": "output:baseline",
        "path": "outputs/baseline.json",
        "required": True,
        "comparator": {
            "type": "exact_sha256",
            "expected_sha256": sha256_file(root / "outputs/baseline.json"),
            "reference_file": None,
        },
    }
    write_yaml(root, "experiments/baseline.yaml", baseline_experiment)

    primary_experiment = original_experiment
    primary_experiment["question_refs"] = ["question:q1", "question:q2"]
    primary_experiment["baseline_refs"] = ["model:baseline"]
    primary_experiment["baseline_comparison_rules"] = [
        {
            "id": "comparison:main-vs-baseline-score",
            "check_ref": "check:baseline-comparison",
            "baseline_model_ref": "model:baseline",
            "primary_metric_ref": "metric:score",
            "delta_definition": "primary_minus_baseline",
            "operator": ">=",
            "threshold": 0.0,
            "unit": "1",
            "rationale": "The primary score must be no lower than the selected baseline score.",
        }
    ]
    write_yaml(root, "experiments/experiment.yaml", primary_experiment)

    original_result = load_yaml(root / "results/results.yaml")
    baseline_result = deepcopy(original_result)
    baseline_result["id"] = "result:baseline"
    baseline_result["depends_on"] = ["experiment:baseline"]
    baseline_result["experiment_ref"] = "experiment:baseline"
    baseline_result["run"]["run_id"] = "run:baseline"
    baseline_result["run"]["argv"] = ["python", "code/baseline.py"]
    baseline_result["outputs"][0]["output_ref"] = "output:baseline"
    baseline_result["outputs"][0]["file"] = file_ref(
        root,
        "outputs/baseline.json",
    )
    baseline_result["metrics"][0]["metric_ref"] = "metric:baseline-score"
    baseline_diagnostic = baseline_result["diagnostics"][0]
    baseline_diagnostic["id"] = "diagnostic:baseline-input-integrity"
    baseline_diagnostic["check_ref"] = baseline_check_id("input_integrity")
    baseline_diagnostic["source_file"] = file_ref(root, "outputs/baseline.json")
    baseline_diagnostic["extractor"] = {
        "type": "json_pointer",
        "pointer": "/score",
    }
    write_yaml(root, "results/baseline.yaml", baseline_result)

    primary_result = original_result
    primary_result["depends_on"] = ["experiment:main", "result:baseline"]
    primary_result["diagnostics"].append(
        {
            "id": "diagnostic:baseline-comparison",
            "check_ref": "check:baseline-comparison",
            "check_type": "baseline_comparison",
            "status": "PASS",
            "condition_met": None,
            "condition_evidence": None,
            "severity": "major",
            "procedure": "Compared the primary and exact bound baseline result.",
            "observation": "Both deterministic score metrics equal 0.95.",
            "observed": None,
            "source_file": None,
            "extractor": None,
            "conclusion": "The primary is no worse than the selected baseline.",
            "evidence_files": [file_ref(root, "outputs/result.json")],
            "comparison_bindings": [
                {
                    "baseline_model_ref": "model:baseline",
                    "baseline_result_ref": "result:baseline",
                    "primary_metric_ref": "metric:score",
                    "baseline_metric_ref": "metric:baseline-score",
                    "comparison_rule_ref": "comparison:main-vs-baseline-score",
                    "observed_delta": {"value": 0.0, "unit": "1"},
                    "status": "PASS",
                }
            ],
        }
    )
    write_yaml(root, "results/results.yaml", primary_result)

    claims = load_yaml(root / "claims/claims.yaml")
    claims["depends_on"] = ["problem:main", "result:main", "result:baseline"]
    claim = claims["claims"][0]
    claim["statement"] = "The primary score is no lower than the eligible simple baseline score."
    claim["claim_type"] = "comparative"
    claim["evidence_refs"] = [
        {"ref": "result:main", "role": "primary comparison result"},
        {"ref": "result:baseline", "role": "bound baseline result"},
    ]
    claim["deliverable_refs"] = ["deliverable:q1", "deliverable:q2"]
    claim["limitations"] = ["The synthetic equality does not establish real-problem performance."]
    write_yaml(root, "claims/claims.yaml", claims)

    manifest = load_yaml(root / "manifest.yaml")
    entries = {entry["id"]: entry for entry in manifest["artifacts"]}
    entries["model:main"]["depends_on"] = ["problem:main", "model:baseline"]
    entries["result:main"]["depends_on"] = ["experiment:main", "result:baseline"]
    entries["claims:main"]["depends_on"] = [
        "problem:main",
        "result:main",
        "result:baseline",
    ]
    manifest["artifacts"].extend(
        [
            {
                "id": "model:baseline",
                "kind": "model_spec",
                "path": "specs/baseline.yaml",
                "sha256": sha256_file(root / "specs/baseline.yaml"),
                "required": True,
                "depends_on": ["problem:main"],
            },
            {
                "id": "experiment:baseline",
                "kind": "experiment",
                "path": "experiments/baseline.yaml",
                "sha256": sha256_file(root / "experiments/baseline.yaml"),
                "required": True,
                "depends_on": ["model:baseline"],
            },
            {
                "id": "result:baseline",
                "kind": "results",
                "path": "results/baseline.yaml",
                "sha256": sha256_file(root / "results/baseline.yaml"),
                "required": True,
                "depends_on": ["experiment:baseline"],
            },
        ]
    )
    manifest["deliverables"].extend(
        [
            {
                "id": "deliverable:baseline-code",
                "path": "code/baseline.py",
                "sha256": sha256_file(root / "code/baseline.py"),
                "required": True,
                "role": "code",
                "media_type": "text/x-python",
            },
            {
                "id": "deliverable:baseline-result",
                "path": "outputs/baseline.json",
                "sha256": sha256_file(root / "outputs/baseline.json"),
                "required": True,
                "role": "result",
                "media_type": "application/json",
            },
        ]
    )
    manifest["entrypoints"]["run_baseline"] = "code/baseline.py"
    write_yaml(root, "manifest.yaml", manifest)
    resign_release_project(root)
    return root


def mutate_and_resign(root: Path, relative: str, change: Any) -> None:
    document = load_yaml(root / relative)
    change(document)
    write_yaml(root, relative, document)
    resign_release_project(root)


def main() -> int:
    args = parse_target_args(
        "Run E11's non-overwriting baseline evidence-closure scenario."
    )
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e11-baseline-evidence")
    report_root = target / "reports"

    positive_root = build_baseline_release(target / "positive")
    positive_report = run_audit(
        positive_root,
        report_root / "positive.json",
        {0},
    )
    require_pass(positive_report)
    require_code(positive_report, scenario["eligible_code"])
    _positive_report, positive_audit = run_in_process_audit(positive_root)
    for result_id in (scenario["primary_result_id"], scenario["baseline_result_id"]):
        if positive_audit.result_eligibility.get(result_id) is not True:
            raise AssertionError(f"positive baseline fixture did not retain eligible {result_id}")
    primary_result = load_yaml(positive_root / "results/results.yaml")
    if scenario["baseline_result_id"] not in primary_result["depends_on"]:
        raise AssertionError("primary result does not depend on the exact baseline result")

    coverage_root = build_baseline_release(target / "coverage-mismatch")
    mutate_and_resign(
        coverage_root,
        "specs/baseline.yaml",
        lambda model: model.update(addresses=["question:q1"]),
    )
    coverage_report = run_audit(
        coverage_root,
        report_root / "coverage-mismatch.json",
        {10, 12},
    )
    require_code(coverage_report, scenario["coverage_failure_code"])

    metric_root = build_baseline_release(target / "metric-mismatch")
    mutate_and_resign(
        metric_root,
        "experiments/baseline.yaml",
        lambda experiment: experiment["metrics"][0].update(
            aggregation="incomparable synthetic aggregation"
        ),
    )
    metric_report = run_audit(
        metric_root,
        report_root / "metric-mismatch.json",
        {10, 12},
    )
    require_code(metric_report, scenario["comparability_failure_code"])
    require_code(metric_report, scenario["metric_mismatch_code"])

    stale_root = build_baseline_release(target / "stale-baseline")
    baseline_result = load_yaml(stale_root / "results/baseline.yaml")
    baseline_result["revision"] += 1
    write_yaml(stale_root, "results/baseline.yaml", baseline_result)
    stale_report = run_audit(
        stale_root,
        report_root / "stale-baseline.json",
        {10, 12},
    )
    for code in scenario["stale_codes"]:
        require_code(stale_report, code)
    require_code(stale_report, scenario["ineligible_claim_code"])
    stale_result_artifacts = {
        row.get("artifact_id")
        for row in finding_rows(stale_report, "RESULT_FINGERPRINT_STALE")
    }
    if scenario["primary_result_id"] not in stale_result_artifacts:
        raise AssertionError(
            "baseline byte change did not stale the primary comparison result"
        )
    upstream_stale_artifacts = {
        row.get("artifact_id")
        for row in finding_rows(stale_report, "UPSTREAM_STALE")
    }
    if "claims:main" not in upstream_stale_artifacts:
        raise AssertionError(
            "baseline byte change did not propagate stale state to the downstream claim"
        )

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "verified": {
                "eligible_primary_and_baseline": "PASS",
                "coverage_mismatch": scenario["coverage_failure_code"],
                "metric_mismatch": [
                    scenario["comparability_failure_code"],
                    scenario["metric_mismatch_code"],
                ],
                "stale_propagation": scenario["stale_codes"],
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
