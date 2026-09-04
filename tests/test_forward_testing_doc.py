"""Keep forward-testing.md synchronized with the scenario registry."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
EVAL_ROOT = REPO_ROOT / "evals"
FORWARD_TESTING_PATH = (
    REPO_ROOT / "cumcm-modeling" / "references" / "forward-testing.md"
)
sys.path.insert(0, str(SCRIPT_ROOT))

import export_scenario_status as scenario_doc  # noqa: E402


REGENERATE_MESSAGE = (
    "\nRegenerate the documented scenario status with:\n"
    f"  {scenario_doc.REGENERATE_COMMAND}"
)


class ForwardTestingDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.document = FORWARD_TESTING_PATH.read_bytes()
            cls.generated_block = scenario_doc.extract_generated_block(cls.document)
            cls.scenarios = scenario_doc.load_scenarios()
        except Exception as exc:
            raise AssertionError(f"{exc}{REGENERATE_MESSAGE}") from exc

    def test_document_block_matches_current_generator_byte_for_byte(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(SCRIPT_ROOT / "export_scenario_status.py"),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stderr.decode("utf-8", errors="replace")
            + REGENERATE_MESSAGE,
        )
        self.assertEqual(completed.stdout, self.generated_block, REGENERATE_MESSAGE)

    def test_every_executable_declares_existing_fixture_and_harness(self) -> None:
        for scenario in self.scenarios:
            if scenario["status"] != "executable":
                continue
            with self.subTest(scenario=scenario["id"]):
                fixture = scenario.get("fixture")
                executable = scenario.get("executable")
                self.assertIsInstance(fixture, str, REGENERATE_MESSAGE)
                self.assertIsInstance(executable, str, REGENERATE_MESSAGE)
                self.assertTrue(
                    (EVAL_ROOT / str(fixture)).is_file(),
                    f"missing fixture for {scenario['id']}: {fixture}{REGENERATE_MESSAGE}",
                )
                self.assertTrue(
                    (EVAL_ROOT / str(executable)).is_file(),
                    f"missing executable for {scenario['id']}: "
                    f"{executable}{REGENERATE_MESSAGE}",
                )


if __name__ == "__main__":
    unittest.main()
