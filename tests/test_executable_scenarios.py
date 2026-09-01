"""Registry checks for executable forward-test scenarios."""

from __future__ import annotations

from pathlib import Path
import unittest

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = REPO_ROOT / "evals"


class ExecutableScenarioRegistryTests(unittest.TestCase):
    def test_e01_and_e17_are_the_only_executable_scenarios(self) -> None:
        registry = yaml.safe_load(
            (EVAL_ROOT / "scenarios.yaml").read_text(encoding="utf-8")
        )
        scenarios = registry["scenarios"]
        by_status: dict[str, list[dict[str, object]]] = {}
        for scenario in scenarios:
            by_status.setdefault(scenario["status"], []).append(scenario)

        self.assertEqual(15, len(by_status.get("specification_only", [])))
        executable = by_status.get("executable", [])
        self.assertEqual(
            {"E01-complete-chain", "E17-held-out-resume"},
            {scenario["id"] for scenario in executable},
        )
        for scenario in executable:
            with self.subTest(scenario=scenario["id"]):
                self.assertTrue((EVAL_ROOT / str(scenario["fixture"])).is_file())
                self.assertTrue((EVAL_ROOT / str(scenario["executable"])).is_file())

    def test_e01_config_matches_the_registry_contract(self) -> None:
        scenario = yaml.safe_load(
            (EVAL_ROOT / "scenarios.yaml").read_text(encoding="utf-8")
        )["scenarios"][0]
        config = yaml.safe_load(
            (EVAL_ROOT / str(scenario["fixture"])).read_text(encoding="utf-8")
        )
        self.assertEqual("E01-complete-chain", scenario["id"])
        self.assertEqual(scenario["id"], config["id"])
        self.assertEqual("e01-optimization", config["fixture_profile"])
        self.assertLess(config["absolute_tolerance"], config["negative_repair_gain"])


if __name__ == "__main__":
    unittest.main()
