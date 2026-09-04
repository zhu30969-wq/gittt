"""Registry checks for executable forward-test scenarios."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import unittest

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = REPO_ROOT / "evals"
EXPECTED_EXECUTABLE_IDS = {
    "E01-complete-chain",
    "E03-heuristic-optimum",
    "E05-mechanism-convergence",
    "E11-baseline-evidence",
    "E12-structured-diagnostic",
    "E17-held-out-resume",
    "E18-hybrid-validation-facet-union",
    "E19-scenario-set-holdout-isolation",
    "E20-decision-timing-comparability",
}


class ExecutableScenarioRegistryTests(unittest.TestCase):
    @staticmethod
    def scenarios() -> list[dict[str, Any]]:
        return yaml.safe_load(
            (EVAL_ROOT / "scenarios.yaml").read_text(encoding="utf-8")
        )["scenarios"]

    @classmethod
    def scenario_by_id(cls, scenario_id: str) -> dict[str, Any]:
        return next(
            scenario for scenario in cls.scenarios() if scenario["id"] == scenario_id
        )

    @classmethod
    def fixture_for(cls, scenario_id: str) -> dict[str, Any]:
        scenario = cls.scenario_by_id(scenario_id)
        return yaml.safe_load(
            (EVAL_ROOT / str(scenario["fixture"])).read_text(encoding="utf-8")
        )

    def test_registry_has_expected_status_counts_and_executable_ids(self) -> None:
        by_status: dict[str, list[dict[str, object]]] = {}
        for scenario in self.scenarios():
            by_status.setdefault(scenario["status"], []).append(scenario)

        self.assertEqual(11, len(by_status.get("specification_only", [])))
        executable = by_status.get("executable", [])
        self.assertEqual(
            EXPECTED_EXECUTABLE_IDS,
            {scenario["id"] for scenario in executable},
        )

    def test_every_executable_declares_existing_fixture_and_harness(self) -> None:
        executable = [
            scenario
            for scenario in self.scenarios()
            if scenario["status"] == "executable"
        ]
        for scenario in executable:
            with self.subTest(scenario=scenario["id"]):
                self.assertIsInstance(scenario.get("fixture"), str)
                self.assertIsInstance(scenario.get("executable"), str)
                self.assertTrue((EVAL_ROOT / str(scenario["fixture"])).is_file())
                self.assertTrue((EVAL_ROOT / str(scenario["executable"])).is_file())

    def test_every_executable_fixture_id_matches_registry(self) -> None:
        executable = [
            scenario
            for scenario in self.scenarios()
            if scenario["status"] == "executable"
        ]
        for scenario in executable:
            with self.subTest(scenario=scenario["id"]):
                config = yaml.safe_load(
                    (EVAL_ROOT / str(scenario["fixture"])).read_text(encoding="utf-8")
                )
                self.assertEqual(scenario["id"], config["id"])

    def test_e01_config_matches_the_registry_contract(self) -> None:
        scenario = self.scenario_by_id("E01-complete-chain")
        config = self.fixture_for("E01-complete-chain")
        self.assertEqual("E01-complete-chain", scenario["id"])
        self.assertEqual(scenario["id"], config["id"])
        self.assertEqual("e01-optimization", config["fixture_profile"])
        self.assertLess(config["absolute_tolerance"], config["negative_repair_gain"])

    def test_e01_registers_objective_reconciliation_orthogonality(self) -> None:
        scenario = self.scenario_by_id("E01-complete-chain")
        orthogonality = [
            invariant
            for invariant in scenario["expected_invariants"]
            if "solver_optimality" in invariant
            and "objective_reconciliation" in invariant
        ]
        self.assertEqual(1, len(orthogonality), scenario["expected_invariants"])
        self.assertIn("same result", orthogonality[0])
        self.assertIn("passes", orthogonality[0])
        self.assertIn("blocks", orthogonality[0])

    def test_e03_config_matches_the_registry_contract(self) -> None:
        config = self.fixture_for("E03-heuristic-optimum")
        self.assertEqual("e01-optimization", config["base_fixture_profile"])
        self.assertGreater(config["infeasible_constraint_violation"], 0)
        self.assertEqual(
            "DIAGNOSTIC_THRESHOLD_FAILED",
            config["constraint_failure_code"],
        )
        self.assertEqual(
            "PROOF_ARTIFACT_INVALID",
            config["unsupported_global_claim_code"],
        )
        self.assertIn("best feasible", config["supported_claim_statement"].lower())
        self.assertIn("globally optimal", config["unsupported_claim_statement"].lower())

    def test_e05_config_matches_the_registry_contract(self) -> None:
        config = self.fixture_for("E05-mechanism-convergence")
        self.assertEqual(4.0, config["theoretical_order"])
        self.assertEqual(4, len(config["positive_steps"]))
        self.assertTrue(
            all(
                left > right
                for left, right in zip(
                    config["positive_steps"], config["positive_steps"][1:]
                )
            )
        )
        self.assertLess(
            config["convergence_tolerance_m"], config["coarse_steps"][-1]
        )
        self.assertEqual(
            "DIAGNOSTIC_THRESHOLD_FAILED", config["threshold_failure_code"]
        )

    def test_e11_config_matches_the_registry_contract(self) -> None:
        config = self.fixture_for("E11-baseline-evidence")
        self.assertEqual("BASELINE_TASK_MISMATCH", config["coverage_failure_code"])
        self.assertEqual(
            "BASELINE_EXPERIMENT_NOT_COMPARABLE",
            config["comparability_failure_code"],
        )
        self.assertEqual(
            "BASELINE_BINDING_METRIC_MISMATCH",
            config["metric_mismatch_code"],
        )
        self.assertEqual("BASELINE_RESULT_ELIGIBLE", config["eligible_code"])
        self.assertEqual(
            {
                "ARTIFACT_HASH_MISMATCH",
                "RESULT_FINGERPRINT_STALE",
                "UPSTREAM_STALE",
            },
            set(config["stale_codes"]),
        )

    def test_e12_config_matches_the_registry_contract(self) -> None:
        config = self.fixture_for("E12-structured-diagnostic")
        self.assertEqual(0.95, config["observed_score"])
        self.assertEqual("==", config["passing_operator"])
        self.assertEqual(">=", config["contradictory_operator"])
        self.assertGreater(
            config["contradictory_threshold"],
            config["observed_score"],
        )
        self.assertEqual(
            "VALIDATION_CHECK_EVIDENCE_AMBIGUOUS",
            config["evidence_ambiguity_code"],
        )
        self.assertEqual(
            "DIAGNOSTIC_STATUS_MISMATCH",
            config["status_mismatch_code"],
        )
        self.assertEqual(
            "DIAGNOSTIC_THRESHOLD_PASS",
            config["threshold_pass_code"],
        )

    def test_e18_config_matches_the_registry_contract(self) -> None:
        config = self.fixture_for("E18-hybrid-validation-facet-union")
        self.assertEqual(
            {"optimization", "simulation"}, set(config["facets"])
        )
        self.assertEqual("VALIDATION_FACETS_REQUIRED", config["missing_facets_code"])
        self.assertEqual(
            "MODEL_VALIDATION_COVERAGE_UNDECLARED",
            config["missing_union_code"],
        )
        self.assertEqual(
            "MODEL_VALIDATION_COVERAGE_UNDECLARED",
            config["optimization_escape_code"],
        )

    def test_e19_config_matches_the_registry_contract(self) -> None:
        config = self.fixture_for("E19-scenario-set-holdout-isolation")
        self.assertEqual(
            {
                "SCENARIO_SET_ROLE_COVERAGE_MISSING",
                "SCENARIO_SET_HASH_OVERLAP",
                "FINAL_CLAIM_SELECTION_SCENARIO_METRIC",
            },
            {
                config["missing_role_code"],
                config["hash_overlap_code"],
                config["selection_claim_code"],
            },
        )

    def test_e20_config_matches_the_registry_contract(self) -> None:
        config = self.fixture_for("E20-decision-timing-comparability")
        self.assertNotEqual(
            config["primary_timing"], config["incomparable_secondary_timing"]
        )
        self.assertEqual(
            config["primary_timing"], config["comparable_secondary_timing"]
        )
        self.assertEqual("DECISION_TIMING_MISMATCH", config["expected_code"])


if __name__ == "__main__":
    unittest.main()
