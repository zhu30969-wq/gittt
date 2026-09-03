"""Focused regressions for validation facets, solver bounds, and decision timing.

The fixtures are deliberately synthetic.  They extend the canonical release
fixture in ``test_audit_regressions`` so every positive control exercises the
same schema, fingerprint, review, and release-gate path as the main suite.
"""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from test_audit_regressions import (
    VALIDATION_COVERAGE_BY_FAMILY,
    build_release_project,
    file_ref,
    finding_codes,
    load_yaml,
    mutate_yaml,
    resign_release_project,
    run_audit,
    sha256_file,
    write_text,
    write_yaml,
)

from audit_project import FORMULA_VALIDATION_CHECKS


FOCUSED_ROOTS: list[Path] = []


def fresh_release(root: Path | None = None) -> Path:
    root = build_release_project(root)
    FOCUSED_ROOTS.append(root)
    return root


def not_applicable_check(check_type: str) -> dict[str, Any]:
    return {
        "id": f"check:{check_type.replace('_', '-')}",
        "check_type": check_type,
        "applicability": "not_applicable",
        "activation_condition": None,
        "criticality": "advisory",
        "rationale": "The synthetic fixture registers this coverage facet without claiming scientific applicability.",
        "procedure": "No procedure is run for this deliberately inapplicable synthetic check.",
        "pass_rule": "The check remains explicitly registered as not applicable.",
        "threshold": None,
        "failure_response": "report_only",
    }


def solver_check() -> dict[str, Any]:
    return {
        "id": "check:solver-optimality",
        "check_type": "solver_optimality",
        "applicability": "required",
        "activation_condition": None,
        "criticality": "blocking",
        "rationale": "A ranked synthetic candidate must carry a numerical solver-quality gate.",
        "procedure": "Read the registered scalar used as the deterministic solver-quality witness.",
        "pass_rule": "The registered witness is at least 0.9.",
        "threshold": {"operator": ">=", "value": 0.9, "unit": "1"},
        "failure_response": "block_result",
    }


def solver_diagnostic(
    root: Path,
    *,
    diagnostic_id: str = "diagnostic:solver-optimality",
    source_path: str = "outputs/result.json",
    incumbent: float = 100.0,
    bound: float = 110.0,
) -> dict[str, Any]:
    return {
        "id": diagnostic_id,
        "check_ref": "check:solver-optimality",
        "check_type": "solver_optimality",
        "status": "PASS",
        "condition_met": None,
        "condition_evidence": None,
        "severity": "critical",
        "procedure": "Read the deterministic solver-quality witness and recorded objective interval.",
        "observation": "The witness is 0.95 and the incumbent/bound pair is finite.",
        "observed": {"value": 0.95, "unit": "1"},
        "objective_incumbent": {"value": incumbent, "unit": "objective"},
        "objective_bound": {"value": bound, "unit": "objective"},
        "source_file": file_ref(root, source_path),
        "extractor": {"type": "json_pointer", "pointer": "/score"},
        "conclusion": "The required solver-quality check passed for the synthetic candidate.",
        "evidence_files": [],
        "comparison_bindings": [],
    }


def append_missing_checks(model: dict[str, Any], check_types: set[str]) -> None:
    checks = model["validation_plan"]["checks"]
    declared = {check["check_type"] for check in checks}
    for check_type in sorted(check_types.difference(declared)):
        checks.append(solver_check() if check_type == "solver_optimality" else not_applicable_check(check_type))


def add_solver_result_evidence(
    root: Path,
    *,
    incumbent: float = 100.0,
    bound: float = 110.0,
) -> None:
    result = load_yaml(root / "results/results.yaml")
    result["diagnostics"].append(
        solver_diagnostic(root, incumbent=incumbent, bound=bound)
    )
    write_yaml(root, "results/results.yaml", result)


def build_hybrid_descriptive_release(root: Path | None = None) -> Path:
    root = fresh_release(root)
    model = load_yaml(root / "specs/model_spec.yaml")
    model["model_family"] = "hybrid"
    model["validation_facets"] = ["descriptive"]
    write_yaml(root, "specs/model_spec.yaml", model)
    resign_release_project(root)
    return root


def build_hybrid_union_release(root: Path | None = None) -> Path:
    root = fresh_release(root)
    facets = ["optimization", "simulation"]
    required = set().union(*(VALIDATION_COVERAGE_BY_FAMILY[facet] for facet in facets))

    model = load_yaml(root / "specs/model_spec.yaml")
    model["model_family"] = "hybrid"
    model["validation_facets"] = facets
    append_missing_checks(model, required)
    write_yaml(root, "specs/model_spec.yaml", model)
    experiment = load_yaml(root / "experiments/experiment.yaml")
    experiment["metrics"][0]["direction"] = "maximize"
    write_yaml(root, "experiments/experiment.yaml", experiment)
    add_solver_result_evidence(root)
    resign_release_project(root)
    return root


def build_optimization_release(root: Path | None = None) -> Path:
    root = build_hybrid_union_release(root)
    model = load_yaml(root / "specs/model_spec.yaml")
    model["model_family"] = "optimization"
    model.pop("validation_facets", None)
    model["formulation"]["objectives"] = [
        {
            "id": "formula:test-objective",
            "expression": "x",
            "format": "plain",
            "defines": [],
            "uses": ["symbol:x"],
            "source_constraint_refs": [],
            "interpretation": "Maximize the synthetic scalar.",
        }
    ]
    model["formulation"]["constraints"] = [
        {
            "id": "formula:test-constraint",
            "expression": "x >= 0",
            "format": "plain",
            "defines": [],
            "uses": ["symbol:x"],
            "source_constraint_refs": [],
            "interpretation": "Keep the synthetic scalar nonnegative.",
        }
    ]
    append_missing_checks(model, set(FORMULA_VALIDATION_CHECKS))
    write_yaml(root, "specs/model_spec.yaml", model)
    resign_release_project(root)
    return root


def set_solver_interval(result: dict[str, Any], incumbent: float, bound: float) -> None:
    diagnostic = next(
        item for item in result["diagnostics"] if item["check_type"] == "solver_optimality"
    )
    diagnostic["objective_incumbent"] = {"value": incumbent, "unit": "objective"}
    diagnostic["objective_bound"] = {"value": bound, "unit": "objective"}


def build_two_candidate_release(
    *,
    primary_interval: tuple[float, float],
    secondary_interval: tuple[float, float],
    primary_timing: str = "here_and_now",
    secondary_timing: str = "here_and_now",
    root: Path | None = None,
) -> Path:
    root = build_optimization_release(root)
    secondary_output = "outputs/candidate-b.json"
    write_text(root, secondary_output, json.dumps({"score": 0.95}) + "\n")

    primary_experiment = load_yaml(root / "experiments/experiment.yaml")
    primary_experiment["decision_timing"] = primary_timing
    write_yaml(root, "experiments/experiment.yaml", primary_experiment)

    primary_result = load_yaml(root / "results/results.yaml")
    set_solver_interval(primary_result, *primary_interval)
    write_yaml(root, "results/results.yaml", primary_result)

    secondary_experiment = deepcopy(primary_experiment)
    secondary_experiment["id"] = "experiment:candidate-b"
    secondary_experiment["decision_timing"] = secondary_timing
    secondary_experiment["metrics"][0]["id"] = "metric:candidate-b-score"
    secondary_experiment["metrics"][0]["source_output_ref"] = "output:candidate-b"
    secondary_experiment["acceptance_rules"][0]["metric_ref"] = "metric:candidate-b-score"
    secondary_experiment["outputs"][0]["id"] = "output:candidate-b"
    secondary_experiment["outputs"][0]["path"] = secondary_output
    secondary_experiment["outputs"][0]["comparator"]["expected_sha256"] = sha256_file(
        root / secondary_output
    )
    write_yaml(root, "experiments/candidate-b.yaml", secondary_experiment)

    secondary_result = deepcopy(primary_result)
    secondary_result["id"] = "result:candidate-b"
    secondary_result["depends_on"] = ["experiment:candidate-b"]
    secondary_result["experiment_ref"] = "experiment:candidate-b"
    secondary_result["run"]["run_id"] = "run:candidate-b"
    secondary_result["outputs"][0]["output_ref"] = "output:candidate-b"
    secondary_result["outputs"][0]["file"] = file_ref(root, secondary_output)
    secondary_result["metrics"][0]["metric_ref"] = "metric:candidate-b-score"
    for diagnostic in secondary_result["diagnostics"]:
        diagnostic["id"] = f"{diagnostic['id']}-candidate-b"
        if diagnostic.get("source_file") is not None:
            diagnostic["source_file"] = file_ref(root, secondary_output)
    set_solver_interval(secondary_result, *secondary_interval)
    write_yaml(root, "results/candidate-b.yaml", secondary_result)

    claims = load_yaml(root / "claims/claims.yaml")
    claims["depends_on"] = ["problem:main", "result:main", "result:candidate-b"]
    claim = claims["claims"][0]
    claim["statement"] = "Candidate A ranks above candidate B."
    claim["claim_type"] = "comparative"
    claim["evidence_refs"] = [
        {"ref": "result:main", "role": "candidate A"},
        {"ref": "result:candidate-b", "role": "candidate B"},
    ]
    claim["numeric_assertions"] = []
    write_yaml(root, "claims/claims.yaml", claims)

    manifest = load_yaml(root / "manifest.yaml")
    for artifact in manifest["artifacts"]:
        if artifact["id"] == "claims:main":
            artifact["depends_on"] = list(claims["depends_on"])
    manifest["artifacts"].extend(
        [
            {
                "id": "experiment:candidate-b",
                "kind": "experiment",
                "path": "experiments/candidate-b.yaml",
                "sha256": sha256_file(root / "experiments/candidate-b.yaml"),
                "required": True,
                "depends_on": ["model:main"],
            },
            {
                "id": "result:candidate-b",
                "kind": "results",
                "path": "results/candidate-b.yaml",
                "sha256": sha256_file(root / "results/candidate-b.yaml"),
                "required": True,
                "depends_on": ["experiment:candidate-b"],
            },
        ]
    )
    manifest["deliverables"].append(
        {
            "id": "deliverable:result-candidate-b",
            **file_ref(root, secondary_output),
            "required": True,
            "role": "result",
            "media_type": "application/json",
        }
    )
    write_yaml(root, "manifest.yaml", manifest)
    resign_release_project(root)
    return root


class ValidationFacetsSolverTimingTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_FOCUSED_FIXTURES=")
        for root in FOCUSED_ROOTS:
            print(root)

    def assert_code(self, report: dict[str, Any], code: str) -> None:
        self.assertIn(code, finding_codes(report), finding_codes(report))

    def assert_pass_without(self, report: dict[str, Any], code: str) -> None:
        self.assertNotIn(code, finding_codes(report), finding_codes(report))
        self.assertEqual("PASS", report["status"], finding_codes(report))

    def test_hybrid_validation_facets_required_negative_and_restored_positive(self) -> None:
        root = build_hybrid_descriptive_release()
        model_path = root / "specs/model_spec.yaml"
        original_bytes = model_path.read_bytes()
        try:
            model = load_yaml(model_path)
            model.pop("validation_facets")
            write_yaml(root, "specs/model_spec.yaml", model)
            blocked, _audit = run_audit(root)
            self.assert_code(blocked, "VALIDATION_FACETS_REQUIRED")
        finally:
            model_path.write_bytes(original_bytes)

        restored, _audit = run_audit(root)
        self.assert_pass_without(restored, "VALIDATION_FACETS_REQUIRED")

    def test_hybrid_facets_use_exact_optimization_simulation_union(self) -> None:
        expected_union = set().union(
            VALIDATION_COVERAGE_BY_FAMILY["optimization"],
            VALIDATION_COVERAGE_BY_FAMILY["simulation"],
        )
        negative_root = fresh_release()

        def select_facets(model: dict[str, Any]) -> None:
            model["model_family"] = "hybrid"
            model["validation_facets"] = ["optimization", "simulation"]

        mutate_yaml(negative_root, "specs/model_spec.yaml", select_facets)
        blocked, _audit = run_audit(negative_root)
        self.assert_code(blocked, "MODEL_VALIDATION_COVERAGE_UNDECLARED")
        coverage_findings = [
            finding
            for gate in blocked["gates"]
            for finding in gate["findings"]
            if finding["code"] == "MODEL_VALIDATION_COVERAGE_UNDECLARED"
            and finding.get("artifact_id") == "model:main"
        ]
        self.assertEqual(1, len(coverage_findings), coverage_findings)
        self.assertTrue(
            coverage_findings[0]["message"].endswith(str(sorted(expected_union))),
            coverage_findings[0]["message"],
        )

        positive_root = build_hybrid_union_release()
        passed, _audit = run_audit(positive_root)
        self.assert_pass_without(passed, "MODEL_VALIDATION_COVERAGE_UNDECLARED")

    def test_solver_optimality_requires_blocking_numeric_threshold(self) -> None:
        mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            (
                "advisory",
                lambda check: check.update(criticality="advisory"),
            ),
            (
                "missing-threshold",
                lambda check: check.update(threshold=None),
            ),
        )
        for label, change in mutations:
            with self.subTest(label=label):
                root = build_optimization_release()

                def weaken_solver_check(model: dict[str, Any]) -> None:
                    target = next(
                        item
                        for item in model["validation_plan"]["checks"]
                        if item["check_type"] == "solver_optimality"
                    )
                    change(target)

                mutate_yaml(root, "specs/model_spec.yaml", weaken_solver_check)
                blocked, _audit = run_audit(root)
                self.assert_code(blocked, "SOLVER_OPTIMALITY_NOT_BLOCKING")

        positive_root = build_optimization_release()
        passed, _audit = run_audit(positive_root)
        self.assert_pass_without(passed, "SOLVER_OPTIMALITY_NOT_BLOCKING")

    def test_ranking_blocks_overlapping_solver_intervals_and_accepts_separation(self) -> None:
        overlapping = build_two_candidate_release(
            primary_interval=(100.0, 120.0),
            secondary_interval=(110.0, 130.0),
        )
        blocked, _audit = run_audit(overlapping)
        self.assert_code(blocked, "RANKING_WITHIN_SOLVER_GAP")

        separated = build_two_candidate_release(
            primary_interval=(120.0, 130.0),
            secondary_interval=(90.0, 100.0),
        )
        passed, _audit = run_audit(separated)
        self.assert_pass_without(passed, "RANKING_WITHIN_SOLVER_GAP")

    def test_comparison_blocks_mixed_decision_timing_and_accepts_match(self) -> None:
        mismatched = build_two_candidate_release(
            primary_interval=(120.0, 130.0),
            secondary_interval=(90.0, 100.0),
            primary_timing="here_and_now",
            secondary_timing="wait_and_see",
        )
        blocked, _audit = run_audit(mismatched)
        self.assert_code(blocked, "DECISION_TIMING_MISMATCH")

        matched = build_two_candidate_release(
            primary_interval=(120.0, 130.0),
            secondary_interval=(90.0, 100.0),
            primary_timing="here_and_now",
            secondary_timing="here_and_now",
        )
        passed, _audit = run_audit(matched)
        self.assert_pass_without(passed, "DECISION_TIMING_MISMATCH")


if __name__ == "__main__":
    unittest.main()
