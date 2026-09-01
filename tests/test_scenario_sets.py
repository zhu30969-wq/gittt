"""Synthetic regressions for scenario selection/holdout separation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import unittest

from test_audit_regressions import (
    file_ref,
    finding_codes,
    load_yaml,
    resign_release_project,
    run_audit,
    sha256_file,
    write_yaml,
)
from test_validation_facets_solver_timing import build_optimization_release


LEGACY_2_2_COMPAT_VERSION = "2.2.0"
SCENARIO_ROOTS: list[Path] = []


def holdout_diagnostic(root: Path) -> dict[str, Any]:
    return {
        "id": "diagnostic:holdout-leakage",
        "check_ref": "check:holdout-leakage",
        "check_type": "holdout_leakage",
        "status": "PASS",
        "condition_met": None,
        "condition_evidence": None,
        "severity": "critical",
        "procedure": "Compared disjoint registered selection and holdout scenario hashes.",
        "observation": "The synthetic scenario sets are role-separated and byte-distinct.",
        "observed": None,
        "source_file": None,
        "extractor": None,
        "conclusion": "No selection/holdout scenario reuse was registered.",
        "evidence_files": [file_ref(root, "outputs/result.json")],
        "comparison_bindings": [],
    }


def registered_scenario_sets(root: Path) -> list[dict[str, Any]]:
    generator_sha = sha256_file(root / "code/main.py")
    return [
        {
            "id": "scenario:selection",
            "role": "selection",
            "seed": 1,
            "generator_sha256": generator_sha,
            "scenario_sha256": "a" * 64,
        },
        {
            "id": "scenario:holdout",
            "role": "holdout",
            "seed": 1,
            "generator_sha256": generator_sha,
            "scenario_sha256": "b" * 64,
        },
    ]


def build_random_optimization_release(*, metric_role: str = "holdout") -> Path:
    root = build_optimization_release()
    SCENARIO_ROOTS.append(root)

    model = load_yaml(root / "specs/model_spec.yaml")
    holdout_check = next(
        row
        for row in model["validation_plan"]["checks"]
        if row["check_type"] == "holdout_leakage"
    )
    holdout_check.update(
        {
            "applicability": "required",
            "activation_condition": None,
            "criticality": "blocking",
            "rationale": "Scenario selection and final evaluation must use disjoint registered bytes.",
            "procedure": "Compare scenario_sha256 values across selection and holdout roles.",
            "pass_rule": "Both roles exist and no scenario hash occurs in both roles.",
            "threshold": None,
            "failure_response": "block_result",
        }
    )
    write_yaml(root, "specs/model_spec.yaml", model)

    experiment = load_yaml(root / "experiments/experiment.yaml")
    experiment["scenario_sets"] = registered_scenario_sets(root)
    experiment["metrics"][0]["scenario_set_ref"] = f"scenario:{metric_role}"
    write_yaml(root, "experiments/experiment.yaml", experiment)

    result = load_yaml(root / "results/results.yaml")
    result["diagnostics"].append(holdout_diagnostic(root))
    write_yaml(root, "results/results.yaml", result)
    resign_release_project(root)
    return root


def finding_rows(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        finding
        for gate in report["gates"]
        for finding in gate["findings"]
        if finding["code"] == code
    ]


class ScenarioSetTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_SCENARIO_SET_FIXTURES=")
        for root in SCENARIO_ROOTS:
            print(root)

    def assert_code(self, report: dict[str, Any], code: str) -> None:
        self.assertIn(code, finding_codes(report), finding_codes(report))

    def test_current_random_optimization_requires_nonempty_scenario_sets(self) -> None:
        root = build_random_optimization_release()
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["scenario_sets"] = []
        experiment["metrics"][0]["scenario_set_ref"] = None
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "SCENARIO_SETS_REQUIRED")
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))

    def test_selection_and_holdout_hash_overlap_is_blocked(self) -> None:
        root = build_random_optimization_release()
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["scenario_sets"][1]["scenario_sha256"] = experiment[
            "scenario_sets"
        ][0]["scenario_sha256"]
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "SCENARIO_SET_HASH_OVERLAP")

    def test_selection_and_holdout_roles_are_both_required(self) -> None:
        root = build_random_optimization_release()
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["scenario_sets"] = [experiment["scenario_sets"][0]]
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "SCENARIO_SET_ROLE_COVERAGE_MISSING")

    def test_metric_scenario_reference_must_resolve_inside_experiment(self) -> None:
        root = build_random_optimization_release()
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["metrics"][0]["scenario_set_ref"] = "scenario:unknown"
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "METRIC_SCENARIO_SET_NOT_LOCAL")

    def test_registered_scenarios_require_actionable_holdout_check(self) -> None:
        root = build_optimization_release()
        SCENARIO_ROOTS.append(root)
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["scenario_sets"] = registered_scenario_sets(root)
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "SCENARIO_HOLDOUT_CHECK_INAPPLICABLE")

    def test_final_claim_cannot_publish_selection_metric(self) -> None:
        root = build_random_optimization_release(metric_role="selection")

        report, audit = run_audit(root)
        self.assert_code(report, "FINAL_CLAIM_SELECTION_SCENARIO_METRIC")
        self.assertTrue(audit.result_eligibility["result:main"])

    def test_final_claim_metric_requires_scenario_binding(self) -> None:
        root = build_random_optimization_release()
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["metrics"][0]["scenario_set_ref"] = None
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "FINAL_CLAIM_METRIC_SCENARIO_UNBOUND")

    def test_holdout_metric_with_disjoint_sets_passes(self) -> None:
        root = build_random_optimization_release()

        report, audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertTrue(audit.result_eligibility["result:main"])
        self.assertFalse(finding_rows(report, "SCENARIO_SET_HASH_OVERLAP"))

    def test_deterministic_optimization_with_empty_sets_is_not_harmed(self) -> None:
        root = build_optimization_release()
        SCENARIO_ROOTS.append(root)

        report, _audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertNotIn("SCENARIO_SETS_REQUIRED", finding_codes(report))

    def test_legacy_2_2_is_readable_with_explicit_migration_findings(self) -> None:
        root = build_random_optimization_release()
        manifest = load_yaml(root / "manifest.yaml")
        for artifact in manifest["artifacts"]:
            document = load_yaml(root / artifact["path"])
            document["schema_version"] = LEGACY_2_2_COMPAT_VERSION
            if document.get("kind") == "model_spec":
                document["validation_plan"]["checks"] = [
                    row
                    for row in document["validation_plan"]["checks"]
                    if row["check_type"] != "holdout_leakage"
                ]
            elif document.get("kind") == "experiment":
                document.pop("scenario_sets", None)
                for metric in document["metrics"]:
                    metric.pop("scenario_set_ref", None)
            elif document.get("kind") == "results":
                document["diagnostics"] = [
                    row
                    for row in document["diagnostics"]
                    if row["check_type"] != "holdout_leakage"
                ]
            write_yaml(root, artifact["path"], document)
        manifest["schema_version"] = LEGACY_2_2_COMPAT_VERSION
        write_yaml(root, "manifest.yaml", manifest)
        resign_release_project(root)

        report, _audit = run_audit(root)
        codes = finding_codes(report)
        self.assert_code(report, "SCENARIO_HOLDOUT_CHECK_REQUIRED")
        self.assert_code(report, "SCENARIO_SETS_LEGACY_MIGRATION_REQUIRED")
        self.assertNotIn("MODEL_VALIDATION_COVERAGE_UNDECLARED", codes)
        self.assertNotIn("SCHEMA_INVALID", codes)
        self.assertNotIn("AUDIT_INTERNAL_ERROR", codes)


if __name__ == "__main__":
    unittest.main()
