"""Synthetic regressions for formulation/claim and family/evidence consistency."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import unittest

from test_audit_regressions import (
    build_release_project,
    finding_codes,
    load_yaml,
    resign_release_project,
    run_audit,
    write_yaml,
)
from test_validation_facets_solver_timing import (
    build_hybrid_union_release,
    build_optimization_release,
)


LEGACY_2_3_COMPAT_VERSION = "2.3.0"
CONSISTENCY_ROOTS: list[Path] = []


def fresh_release() -> Path:
    root = build_release_project()
    CONSISTENCY_ROOTS.append(root)
    return root


def record_root(root: Path) -> Path:
    CONSISTENCY_ROOTS.append(root)
    return root


def clear_formulation(root: Path) -> None:
    model = load_yaml(root / "specs/model_spec.yaml")
    model["formulation"] = {"equations": [], "objectives": [], "constraints": []}
    write_yaml(root, "specs/model_spec.yaml", model)


def finding_rows(
    report: dict[str, Any], code: str, gate_name: str | None = None
) -> list[dict[str, Any]]:
    return [
        finding
        for gate in report["gates"]
        if gate_name is None or gate["gate"] == gate_name
        for finding in gate["findings"]
        if finding["code"] == code
    ]


class ModelEvidenceConsistencyTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_MODEL_EVIDENCE_CONSISTENCY_FIXTURES=")
        for root in CONSISTENCY_ROOTS:
            print(root)

    def assert_code(
        self, report: dict[str, Any], code: str, gate_name: str | None = None
    ) -> list[dict[str, Any]]:
        rows = finding_rows(report, code, gate_name)
        self.assertTrue(rows, finding_codes(report))
        return rows

    def test_empty_formulation_supporting_numeric_claim_is_g2_blocked(self) -> None:
        for evidence_mode in ("indirect-result", "direct-model"):
            with self.subTest(evidence_mode=evidence_mode):
                root = fresh_release()
                clear_formulation(root)
                if evidence_mode == "direct-model":
                    claims = load_yaml(root / "claims/claims.yaml")
                    claims["claims"][0]["evidence_refs"] = [
                        {"ref": "model:main", "role": "direct model evidence"}
                    ]
                    write_yaml(root, "claims/claims.yaml", claims)
                resign_release_project(root)

                report, _audit = run_audit(root)
                rows = self.assert_code(
                    report, "EMPTY_FORMULATION_SUPPORTS_CLAIM", "G2"
                )
                self.assertTrue(
                    all(
                        "model:main" in row["message"]
                        and "claim:c1" in row["message"]
                        for row in rows
                    ),
                    rows,
                )

    def test_one_objective_restores_quantitative_claim_support(self) -> None:
        root = fresh_release()
        clear_formulation(root)
        model = load_yaml(root / "specs/model_spec.yaml")
        model["formulation"]["objectives"] = [
            {
                "id": "formula:reported-score",
                "expression": "x = 0.95",
                "format": "plain",
                "defines": [],
                "uses": ["symbol:x"],
                "source_constraint_refs": [],
                "interpretation": "Bind the reported synthetic score.",
            }
        ]
        write_yaml(root, "specs/model_spec.yaml", model)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assertNotIn("EMPTY_FORMULATION_SUPPORTS_CLAIM", finding_codes(report))
        self.assert_code(report, "NUMERIC_ASSERTION_MATCH", "G5")
        self.assertEqual("PASS", report["status"], finding_codes(report))

    def test_empty_formulation_without_numeric_claim_is_not_blocked_by_rule(self) -> None:
        root = fresh_release()
        clear_formulation(root)
        claims = load_yaml(root / "claims/claims.yaml")
        claims["claims"][0]["numeric_assertions"] = []
        write_yaml(root, "claims/claims.yaml", claims)
        resign_release_project(root)

        report, _audit = run_audit(root)
        codes = finding_codes(report)
        self.assertNotIn("EMPTY_FORMULATION_SUPPORTS_CLAIM", codes)
        self.assertNotIn("EMPTY_FORMULATION_SUPPORTS_CLAIM_MIGRATION_REQUIRED", codes)
        self.assert_code(report, "RUN_SUCCESSFUL", "G4")

    def test_maximize_metric_with_descriptive_family_is_g3_blocked(self) -> None:
        root = fresh_release()
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["metrics"][0]["direction"] = "maximize"
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        rows = self.assert_code(report, "FAMILY_EVIDENCE_MISMATCH", "G3")
        message = rows[0]["message"]
        for token in (
            "metric:score direction=maximize",
            "experiment:main",
            "model_family='descriptive'",
            "validation_facets=[]",
            "'optimization'",
        ):
            self.assertIn(token, message)

    def test_solver_objective_pair_with_descriptive_family_is_g4_blocked(self) -> None:
        root = fresh_release()
        result = load_yaml(root / "results/results.yaml")
        result["diagnostics"][0]["objective_incumbent"] = {
            "value": 0.95,
            "unit": "1",
        }
        result["diagnostics"][0]["objective_bound"] = {
            "value": 1.0,
            "unit": "1",
        }
        write_yaml(root, "results/results.yaml", result)
        resign_release_project(root)

        report, audit = run_audit(root)
        rows = self.assert_code(report, "FAMILY_EVIDENCE_MISMATCH", "G4")
        self.assertIn("objective_incumbent/objective_bound", rows[0]["message"])
        self.assertIn("result:main", rows[0]["message"])
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_reconciliation_structure_with_descriptive_family_is_g4_blocked(self) -> None:
        root = fresh_release()
        result = load_yaml(root / "results/results.yaml")
        result["diagnostics"][0]["objective_reconciliation"] = {}
        write_yaml(root, "results/results.yaml", result)
        resign_release_project(root)

        report, audit = run_audit(root)
        rows = self.assert_code(report, "FAMILY_EVIDENCE_MISMATCH", "G4")
        self.assertIn("objective_reconciliation", rows[0]["message"])
        self.assertIn("result:main", rows[0]["message"])
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_optimization_family_accepts_optimization_evidence(self) -> None:
        root = record_root(build_optimization_release())
        report, audit = run_audit(root)

        self.assertNotIn("FAMILY_EVIDENCE_MISMATCH", finding_codes(report))
        self.assert_code(report, "RUN_SUCCESSFUL", "G4")
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertTrue(audit.result_eligibility["result:main"])

    def test_hybrid_optimization_facet_accepts_optimization_evidence(self) -> None:
        root = record_root(build_hybrid_union_release())
        report, audit = run_audit(root)

        self.assertNotIn("FAMILY_EVIDENCE_MISMATCH", finding_codes(report))
        self.assert_code(report, "RUN_SUCCESSFUL", "G4")
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertTrue(audit.result_eligibility["result:main"])

    def test_optimization_facet_without_optimization_signal_is_legal(self) -> None:
        root = record_root(build_hybrid_union_release())
        model = load_yaml(root / "specs/model_spec.yaml")
        model["validation_facets"] = ["optimization"]
        write_yaml(root, "specs/model_spec.yaml", model)
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["metrics"][0]["direction"] = "descriptive"
        write_yaml(root, "experiments/experiment.yaml", experiment)
        result = load_yaml(root / "results/results.yaml")
        solver_diagnostic = next(
            row
            for row in result["diagnostics"]
            if row["check_type"] == "solver_optimality"
        )
        solver_diagnostic.pop("objective_incumbent")
        solver_diagnostic.pop("objective_bound")
        write_yaml(root, "results/results.yaml", result)
        resign_release_project(root)

        report, audit = run_audit(root)
        self.assertNotIn("FAMILY_EVIDENCE_MISMATCH", finding_codes(report))
        self.assert_code(report, "DIAGNOSTIC_THRESHOLD_PASS", "G4")
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertTrue(audit.result_eligibility["result:main"])

    def test_legacy_2_3_is_readable_with_explicit_migration_finding(self) -> None:
        root = fresh_release()
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["metrics"][0]["direction"] = "maximize"
        write_yaml(root, "experiments/experiment.yaml", experiment)

        manifest = load_yaml(root / "manifest.yaml")
        for artifact in manifest["artifacts"]:
            document = load_yaml(root / artifact["path"])
            document["schema_version"] = LEGACY_2_3_COMPAT_VERSION
            write_yaml(root, artifact["path"], document)
        manifest["schema_version"] = LEGACY_2_3_COMPAT_VERSION
        write_yaml(root, "manifest.yaml", manifest)
        resign_release_project(root)

        report, _audit = run_audit(root)
        codes = finding_codes(report)
        rows = self.assert_code(
            report, "FAMILY_EVIDENCE_MISMATCH_MIGRATION_REQUIRED", "G3"
        )
        self.assertIn("schema_version='2.3.0'", rows[0]["message"])
        self.assertNotIn("FAMILY_EVIDENCE_MISMATCH", codes)
        self.assertNotIn("SCHEMA_INVALID", codes)
        self.assertNotIn("AUDIT_INTERNAL_ERROR", codes)


if __name__ == "__main__":
    unittest.main()
