"""Keep the documented validation matrix synchronized with audit semantics."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
MODEL_SELECTION_PATH = REPO_ROOT / "cumcm-modeling" / "references" / "model-selection.md"
sys.path.insert(0, str(SCRIPT_ROOT))

import export_validation_matrix as matrix_doc  # noqa: E402
from audit_project import (  # noqa: E402
    FORMULA_VALIDATION_CHECKS,
    VALIDATION_COVERAGE_BY_FAMILY,
    VALIDATION_COVERAGE_BY_TASK,
)


REGENERATE_MESSAGE = (
    "\nRegenerate the documented matrix with:\n"
    f"  {matrix_doc.REGENERATE_COMMAND}"
)


class ValidationMatrixDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            model_schema = json.loads(matrix_doc.MODEL_SCHEMA_PATH.read_text(encoding="utf-8"))
            problem_schema = json.loads(
                matrix_doc.PROBLEM_SCHEMA_PATH.read_text(encoding="utf-8")
            )
            cls.check_types = tuple(model_schema["$defs"]["validationCheckType"]["enum"])
            cls.model_families = tuple(
                model_schema["properties"]["model_family"]["enum"]
            )
            cls.task_types = tuple(
                problem_schema["properties"]["questions"]["items"]["properties"]
                ["task_type"]["enum"]
            )
            cls.family_coverage = {
                key: set(checks) for key, checks in VALIDATION_COVERAGE_BY_FAMILY.items()
            }
            cls.task_coverage = {
                key: set(checks) for key, checks in VALIDATION_COVERAGE_BY_TASK.items()
            }
            cls.formula_checks = set(FORMULA_VALIDATION_CHECKS)
            cls.document = MODEL_SELECTION_PATH.read_bytes()
            cls.generated_block = matrix_doc.extract_generated_block(cls.document)
        except Exception as exc:
            raise AssertionError(f"{exc}{REGENERATE_MESSAGE}") from exc

    def test_document_block_matches_current_generator_byte_for_byte(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(SCRIPT_ROOT / "export_validation_matrix.py"),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(
            0,
            completed.returncode,
            completed.stderr.decode("utf-8", errors="replace") + REGENERATE_MESSAGE,
        )
        self.assertEqual(completed.stdout, self.generated_block, REGENERATE_MESSAGE)

    def test_every_mapped_check_is_declared_by_validation_check_type(self) -> None:
        mapped = set(self.formula_checks)
        for checks in (*self.family_coverage.values(), *self.task_coverage.values()):
            mapped.update(checks)
        undeclared = mapped.difference(self.check_types)
        self.assertEqual(set(), undeclared, REGENERATE_MESSAGE)

    def test_documented_blind_spots_equal_current_set_differences(self) -> None:
        expected_family_gaps = set(self.model_families).difference(self.family_coverage)
        expected_task_gaps = set(self.task_types).difference(self.task_coverage)
        automatically_required = set(self.formula_checks)
        for checks in (*self.family_coverage.values(), *self.task_coverage.values()):
            automatically_required.update(checks)
        expected_check_gaps = set(self.check_types).difference(automatically_required)

        rendered = self.generated_block.decode("utf-8")
        documented = {
            "family": self._documented_values(
                rendered, "- **无族级映射的 `model_family`**："
            ),
            "task": self._documented_values(
                rendered, "- **无任务级映射的 `task_type`**："
            ),
            "check": self._documented_values(
                rendered,
                "- **未被族、任务或公式规则自动要求的 `validationCheckType`**：",
            ),
        }
        self.assertEqual(expected_family_gaps, documented["family"], REGENERATE_MESSAGE)
        self.assertEqual(expected_task_gaps, documented["task"], REGENERATE_MESSAGE)
        self.assertEqual(expected_check_gaps, documented["check"], REGENERATE_MESSAGE)

    def test_marker_replacement_preserves_every_outside_byte(self) -> None:
        before = (
            b"prefix\n"
            + matrix_doc.BEGIN_MARKER.encode("utf-8")
            + b"\nold bytes\n"
            + matrix_doc.END_MARKER.encode("utf-8")
            + b"\nsuffix\n"
        )
        generated = b"new bytes\n"
        after = matrix_doc.replace_generated_block(before, generated)
        self.assertEqual(
            b"prefix\n" + matrix_doc.BEGIN_MARKER.encode("utf-8") + b"\n",
            after[: after.index(generated)],
            REGENERATE_MESSAGE,
        )
        self.assertTrue(
            after.endswith(matrix_doc.END_MARKER.encode("utf-8") + b"\nsuffix\n"),
            REGENERATE_MESSAGE,
        )
        self.assertEqual(
            generated,
            matrix_doc.extract_generated_block(after),
            REGENERATE_MESSAGE,
        )

    def test_missing_markers_are_a_content_error(self) -> None:
        with self.assertRaises(matrix_doc.MatrixContentError):
            matrix_doc.replace_generated_block(b"no generated markers\n", b"content\n")

    @staticmethod
    def _documented_values(rendered: str, prefix: str) -> set[str]:
        match = re.search(re.escape(prefix) + r"([^。\n]+)。", rendered)
        if match is None:
            raise AssertionError(f"blind-spot statement is missing: {prefix}{REGENERATE_MESSAGE}")
        return set(re.findall(r"`([^`]+)`", match.group(1)))


if __name__ == "__main__":
    unittest.main()
