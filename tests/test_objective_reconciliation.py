"""Synthetic regressions for fixed-decision objective reconciliation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
import unittest

from test_audit_regressions import (
    file_ref,
    finding_codes,
    load_yaml,
    resign_release_project,
    run_audit,
    write_text,
    write_yaml,
)
from test_validation_facets_solver_timing import (
    build_optimization_release,
    set_solver_interval,
)


OBJECTIVE_ROOTS: list[Path] = []


def reconciliation_diagnostic(root: Path) -> dict[str, Any]:
    return {
        "id": "diagnostic:objective-reconciliation",
        "check_ref": "check:objective-reconciliation",
        "check_type": "objective_reconciliation",
        "status": "PASS",
        "condition_met": None,
        "condition_evidence": None,
        "severity": "critical",
        "procedure": "Fix the primary decision and independently optimize the auxiliary response.",
        "observation": "The independent synthetic best response preserves the registered objective.",
        "observed": {"value": 0.0, "unit": "1"},
        "objective_reconciliation": {
            "objective_metric_ref": "metric:score",
            "fixed_primary_decisions": ["symbol:primary-decision"],
            "reoptimized_auxiliary_variables": ["symbol:auxiliary-response"],
            "solver_objective": {"value": 0.95, "unit": "1"},
            "best_response_objective": {"value": 0.95, "unit": "1"},
            "repair_gain": {"value": 0.0, "unit": "1"},
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
            "registration_timing": "pre_result",
            "reconciliation_code_file": file_ref(root, "code/reconcile_objective.py"),
            "reconciliation_method": "Synthetic exhaustive auxiliary best-response enumeration",
        },
        "source_file": None,
        "extractor": None,
        "conclusion": "The independent fixed-decision objective reconciliation passed.",
        "evidence_files": [file_ref(root, "outputs/result.json")],
        "comparison_bindings": [],
    }


def build_objective_reconciliation_release() -> Path:
    root = build_optimization_release()
    OBJECTIVE_ROOTS.append(root)
    write_text(
        root,
        "code/reconcile_objective.py",
        "\"\"\"Independent synthetic auxiliary best-response fixture.\"\"\"\nprint('0.95')\n",
    )

    model = load_yaml(root / "specs/model_spec.yaml")
    model["symbols"].extend(
        [
            {
                "id": "symbol:primary-decision",
                "name": "primary_decision",
                "role": "decision",
                "domain": "real",
                "shape": "scalar",
                "unit": "1",
                "definition": "Synthetic primary decision held fixed during reconciliation.",
            },
            {
                "id": "symbol:auxiliary-response",
                "name": "auxiliary_response",
                "role": "state",
                "domain": "real",
                "shape": "scalar",
                "unit": "1",
                "definition": "Synthetic auxiliary variable independently reoptimized after fixing the primary decision.",
            },
        ]
    )
    check = next(
        item
        for item in model["validation_plan"]["checks"]
        if item["check_type"] == "objective_reconciliation"
    )
    check.update(
        {
            "applicability": "required",
            "activation_condition": None,
            "criticality": "blocking",
            "rationale": "A feasible solution can still contain a nonoptimal auxiliary response.",
            "procedure": "Fix primary decisions and independently reoptimize auxiliary variables.",
            "pass_rule": "The direction-aware repair gain is within the preregistered tolerance.",
            "threshold": None,
            "failure_response": "block_result",
        }
    )
    write_yaml(root, "specs/model_spec.yaml", model)

    experiment = load_yaml(root / "experiments/experiment.yaml")
    experiment["code_files"].append(file_ref(root, "code/reconcile_objective.py"))
    write_yaml(root, "experiments/experiment.yaml", experiment)

    result = load_yaml(root / "results/results.yaml")
    result["diagnostics"].append(reconciliation_diagnostic(root))
    write_yaml(root, "results/results.yaml", result)

    manifest = load_yaml(root / "manifest.yaml")
    manifest["deliverables"].append(
        {
            "id": "deliverable:objective-reconciliation-code",
            **file_ref(root, "code/reconcile_objective.py"),
            "required": True,
            "role": "code",
            "media_type": "text/x-python",
        }
    )
    write_yaml(root, "manifest.yaml", manifest)
    resign_release_project(root)
    return root


def mutate_reconciliation(
    root: Path,
    change: Callable[[dict[str, Any], dict[str, Any]], None],
) -> None:
    result = load_yaml(root / "results/results.yaml")
    diagnostic = next(
        item
        for item in result["diagnostics"]
        if item["check_type"] == "objective_reconciliation"
    )
    change(diagnostic, diagnostic["objective_reconciliation"])
    write_yaml(root, "results/results.yaml", result)
    resign_release_project(root)


def findings_for_code(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        finding
        for gate in report["gates"]
        for finding in gate["findings"]
        if finding["code"] == code
    ]


class ObjectiveReconciliationTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_OBJECTIVE_RECONCILIATION_FIXTURES=")
        for root in OBJECTIVE_ROOTS:
            print(root)

    def assert_code(self, report: dict[str, Any], code: str) -> None:
        self.assertIn(code, finding_codes(report), finding_codes(report))

    def test_optimization_without_check_is_g2_blocked(self) -> None:
        root = build_objective_reconciliation_release()
        model = load_yaml(root / "specs/model_spec.yaml")
        model["validation_plan"]["checks"] = [
            item
            for item in model["validation_plan"]["checks"]
            if item["check_type"] != "objective_reconciliation"
        ]
        write_yaml(root, "specs/model_spec.yaml", model)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "MODEL_VALIDATION_COVERAGE_UNDECLARED")
        coverage = findings_for_code(report, "MODEL_VALIDATION_COVERAGE_UNDECLARED")
        self.assertTrue(
            any("objective_reconciliation" in finding["message"] for finding in coverage),
            coverage,
        )

    def test_incomplete_structure_is_blocked(self) -> None:
        root = build_objective_reconciliation_release()
        mutate_reconciliation(
            root,
            lambda _diagnostic, payload: payload.pop("reconciliation_method"),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "OBJECTIVE_RECONCILIATION_INCOMPLETE")

    def test_empty_or_overlapping_scopes_are_blocked(self) -> None:
        mutations: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
            ("empty", lambda payload: payload.update(fixed_primary_decisions=[])),
            (
                "overlap",
                lambda payload: payload.update(
                    reoptimized_auxiliary_variables=["symbol:primary-decision"]
                ),
            ),
        )
        for label, change in mutations:
            with self.subTest(label=label):
                root = build_objective_reconciliation_release()
                mutate_reconciliation(root, lambda _diagnostic, payload: change(payload))
                report, _audit = run_audit(root)
                self.assert_code(report, "OBJECTIVE_RECONCILIATION_SCOPE_INVALID")

    def test_main_entrypoint_reuse_is_blocked(self) -> None:
        root = build_objective_reconciliation_release()
        mutate_reconciliation(
            root,
            lambda _diagnostic, payload: payload.update(
                reconciliation_code_file=file_ref(root, "code/main.py")
            ),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "OBJECTIVE_RECONCILIATION_NOT_INDEPENDENT")

    def test_registered_repair_gain_mismatch_is_blocked(self) -> None:
        root = build_objective_reconciliation_release()
        mutate_reconciliation(
            root,
            lambda _diagnostic, payload: payload["repair_gain"].update(value=0.01),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "OBJECTIVE_RECONCILIATION_MISMATCH")

    def test_repair_gain_above_tolerance_blocks_eligibility(self) -> None:
        root = build_objective_reconciliation_release()

        def exceed(_diagnostic: dict[str, Any], payload: dict[str, Any]) -> None:
            payload["best_response_objective"]["value"] = 1.0
            payload["repair_gain"]["value"] = 0.05

        mutate_reconciliation(root, exceed)
        report, audit = run_audit(root)
        self.assert_code(report, "OBJECTIVE_REPAIR_GAIN_EXCEEDED")
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_complete_reconciliation_within_tolerance_passes(self) -> None:
        root = build_objective_reconciliation_release()
        report, audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assert_code(report, "OBJECTIVE_RECONCILIATION_PASS")
        self.assertTrue(audit.result_eligibility["result:main"])

    def test_post_result_tolerance_cannot_support_confirmatory_result(self) -> None:
        root = build_objective_reconciliation_release()
        mutate_reconciliation(
            root,
            lambda _diagnostic, payload: payload.update(
                registration_timing="post_result"
            ),
        )
        report, audit = run_audit(root)
        self.assert_code(
            report,
            "CONFIRMATORY_OBJECTIVE_RECONCILIATION_TOLERANCE_POST_HOC",
        )
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_small_repair_gain_blocks_even_when_solver_gap_check_passes(self) -> None:
        root = build_objective_reconciliation_release()
        result = load_yaml(root / "results/results.yaml")
        set_solver_interval(result, incumbent=0.95, bound=1.00434)
        reconciliation = next(
            item
            for item in result["diagnostics"]
            if item["check_type"] == "objective_reconciliation"
        )["objective_reconciliation"]
        reconciliation["best_response_objective"]["value"] = 0.950057
        reconciliation["repair_gain"]["value"] = 0.000057
        write_yaml(root, "results/results.yaml", result)
        resign_release_project(root)

        solver_gap = (Decimal("1.00434") - Decimal("0.95")) / Decimal("0.95")
        repair_share = Decimal("0.000057") / Decimal("0.95")
        self.assertEqual(Decimal("0.0572"), solver_gap)
        self.assertEqual(Decimal("0.00006"), repair_share)

        report, audit = run_audit(root)
        self.assert_code(report, "OBJECTIVE_REPAIR_GAIN_EXCEEDED")
        solver_passes = [
            finding
            for finding in findings_for_code(report, "DIAGNOSTIC_THRESHOLD_PASS")
            if "check:solver-optimality" in finding["message"]
        ]
        self.assertEqual(1, len(solver_passes), solver_passes)
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_legacy_2_1_optimization_is_readable_with_explicit_finding(self) -> None:
        root = build_objective_reconciliation_release()
        manifest = load_yaml(root / "manifest.yaml")
        for artifact in manifest["artifacts"]:
            document = load_yaml(root / artifact["path"])
            document["schema_version"] = "2.1.0"
            if document.get("kind") == "model_spec":
                document["validation_plan"]["checks"] = [
                    item
                    for item in document["validation_plan"]["checks"]
                    if item["check_type"] != "objective_reconciliation"
                ]
            if document.get("kind") == "results":
                document["diagnostics"] = [
                    item
                    for item in document["diagnostics"]
                    if item["check_type"] != "objective_reconciliation"
                ]
            write_yaml(root, artifact["path"], document)
        manifest["schema_version"] = "2.1.0"
        write_yaml(root, "manifest.yaml", manifest)
        resign_release_project(root)

        report, _audit = run_audit(root)
        codes = finding_codes(report)
        self.assert_code(report, "OBJECTIVE_RECONCILIATION_REQUIRED")
        self.assertNotIn("MODEL_VALIDATION_COVERAGE_UNDECLARED", codes)
        self.assertNotIn("SCHEMA_INVALID", codes)
        self.assertNotIn("AUDIT_INTERNAL_ERROR", codes)


if __name__ == "__main__":
    unittest.main()
