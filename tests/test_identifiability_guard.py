"""Contract regressions for unidentifiable parameter point-estimate claims."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import unittest

from test_audit_regressions import (
    build_release_project,
    file_ref,
    finding_codes,
    load_yaml,
    resign_release_project,
    run_audit,
    write_yaml,
)


CURRENT_VERSION = "2.5.0"
LEGACY_2_4_COMPAT_VERSION = "2.4.0"
IDENTIFICATION_ROOTS: list[Path] = []


def set_contract_version(root: Path, version: str) -> None:
    manifest = load_yaml(root / "manifest.yaml")
    for artifact in manifest["artifacts"]:
        document = load_yaml(root / artifact["path"])
        document["schema_version"] = version
        write_yaml(root, artifact["path"], document)
    manifest["schema_version"] = version
    write_yaml(root, "manifest.yaml", manifest)


def build_identification_release(
    *, expose_point_estimate: bool, version: str = CURRENT_VERSION
) -> Path:
    root = build_release_project()
    IDENTIFICATION_ROOTS.append(root)

    model = load_yaml(root / "specs/model_spec.yaml")
    model["validation_plan"]["checks"].append(
        {
            "id": "check:identifiability",
            "check_type": "identifiability",
            "applicability": "required",
            "activation_condition": None,
            "criticality": "blocking",
            "rationale": "Equivalent parameter pairs must not become separate point claims.",
            "procedure": "Evaluate the sensitivity-Jacobian condition number and column correlation.",
            "pass_rule": "Suppress point estimates when the parameterization is not identifiable.",
            "threshold": None,
            "failure_response": "block_result",
        }
    )
    write_yaml(root, "specs/model_spec.yaml", model)

    result = load_yaml(root / "results/results.yaml")
    result["diagnostics"].append(
        {
            "id": "diagnostic:identifiability",
            "check_ref": "check:identifiability",
            "check_type": "identifiability",
            "status": "PASS",
            "condition_met": None,
            "condition_evidence": None,
            "severity": "critical",
            "procedure": "Computed a rank-deficient two-column sensitivity Jacobian.",
            "observation": "Only the product k1*k2 is identified by the synthetic observations.",
            "observed": None,
            "parameter_identification": {
                "identifiable": False,
                "point_estimate": {"k1": 0.95} if expose_point_estimate else None,
                "point_estimate_metric_refs": ["metric:score"] if expose_point_estimate else [],
                "parameter_intervals": {
                    "k1": {
                        "lower": None,
                        "upper": None,
                        "level": None,
                        "method": "unbounded_due_to_nonidentifiability",
                    },
                    "k2": {
                        "lower": None,
                        "upper": None,
                        "level": None,
                        "method": "unbounded_due_to_nonidentifiability",
                    },
                },
                "identifiable_combinations": ["k1*k2"],
                "fit_residual": {"value": 0.0, "unit": "1"},
                "holdout_residual": {"value": 0.0, "unit": "1"},
                "jacobian_condition_number": 1e12,
                "maximum_column_correlation": 1.0,
                "condition_number_threshold": 1e8,
                "column_correlation_threshold": 0.995,
            },
            "source_file": None,
            "extractor": None,
            "conclusion": "The product is reportable; separate parameter points are not.",
            "evidence_files": [file_ref(root, "outputs/result.json")],
            "comparison_bindings": [],
        }
    )
    write_yaml(root, "results/results.yaml", result)
    set_contract_version(root, version)
    resign_release_project(root)
    return root


def finding_rows(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        row
        for gate in report["gates"]
        for row in gate["findings"]
        if row["code"] == code
    ]


class IdentifiabilityGuardTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_IDENTIFIABILITY_GUARD_FIXTURES=")
        for root in IDENTIFICATION_ROOTS:
            print(root)

    def test_unidentifiable_point_estimate_used_by_final_claim_is_blocked(self) -> None:
        root = build_identification_release(expose_point_estimate=True)
        report, audit = run_audit(root)
        rows = finding_rows(report, "UNIDENTIFIABLE_POINT_ESTIMATE_CLAIM")
        self.assertEqual(1, len(rows), finding_codes(report))
        self.assertIn("claim:c1", rows[0]["message"])
        self.assertIn("metric:score", rows[0]["message"])
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_suppressed_point_estimate_keeps_identifiable_combination_result_eligible(self) -> None:
        root = build_identification_release(expose_point_estimate=False)
        report, audit = run_audit(root)
        codes = finding_codes(report)
        self.assertNotIn("UNIDENTIFIABLE_POINT_ESTIMATE_CLAIM", codes)
        self.assertEqual("PASS", report["status"], codes)
        self.assertTrue(audit.result_eligibility["result:main"])

    def test_legacy_2_4_contract_is_readable_with_migration_finding(self) -> None:
        root = build_identification_release(
            expose_point_estimate=True, version=LEGACY_2_4_COMPAT_VERSION
        )
        report, audit = run_audit(root)
        rows = finding_rows(
            report,
            "UNIDENTIFIABLE_POINT_ESTIMATE_CLAIM_MIGRATION_REQUIRED",
        )
        self.assertEqual(1, len(rows), finding_codes(report))
        self.assertIn("schema_version='2.4.0'", rows[0]["message"])
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertFalse(audit.result_eligibility["result:main"])


if __name__ == "__main__":
    unittest.main()
