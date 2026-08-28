#!/usr/bin/env python3
"""Regression tests for paper source lexical linting.

These tests exercise observable command behavior.  They do not compile TeX or
Typst and therefore do not treat structural lint PASS as compilation proof.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LINTER = REPO_ROOT / "cumcm-modeling" / "scripts" / "lint_paper.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def run_lint(fixture: str, engine: str, source: str, *extra: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(LINTER),
            str(FIXTURES / fixture),
            "--engine",
            engine,
            "--source",
            source,
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if not completed.stdout:
        raise AssertionError(f"linter produced no JSON output: {completed.stderr}")
    return completed.returncode, json.loads(completed.stdout)


class PaperLintRegressionTests(unittest.TestCase):
    def finding_codes(self, report: dict) -> set[str]:
        return {finding["code"] for finding in report["findings"]}

    def test_comments_literal_environments_cref_and_parencite_pass(self) -> None:
        returncode, report = run_lint(
            "paper-comments-pass",
            "latex",
            "paper/main.tex",
            "--claims",
            "claims/claims.yaml",
            "--strict",
        )
        self.assertEqual(returncode, 0, report)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["project_root"], ".")
        self.assertIn("CLAIM_MARKER_FOUND", self.finding_codes(report))

    def test_missing_cref_and_biblatex_citation_block(self) -> None:
        returncode, report = run_lint(
            "paper-biblatex-fail", "latex", "paper/main.tex"
        )
        codes = self.finding_codes(report)
        self.assertEqual(returncode, 10, report)
        self.assertEqual(report["status"], "BLOCK", report)
        self.assertIn("UNRESOLVED_REFERENCE", codes)
        self.assertIn("CITATION_KEY_MISSING", codes)

    def test_duplicate_comment_marker_blocks(self) -> None:
        returncode, report = run_lint(
            "paper-marker-duplicate",
            "latex",
            "paper/main.tex",
            "--claims",
            "claims/claims.yaml",
        )
        self.assertEqual(returncode, 10, report)
        self.assertIn("CLAIM_MARKER_DUPLICATE", self.finding_codes(report))

    def test_real_dangerous_latex_command_still_blocks(self) -> None:
        returncode, report = run_lint(
            "paper-dangerous-fail", "latex", "paper/main.tex"
        )
        self.assertEqual(returncode, 10, report)
        self.assertIn("LATEX_SHELL_ESCAPE", self.finding_codes(report))

    def test_typst_marker_comes_from_complete_comment_token(self) -> None:
        returncode, report = run_lint(
            "paper-typst-marker-pass",
            "typst",
            "paper/main.typ",
            "--claims",
            "claims/claims.yaml",
            "--strict",
        )
        self.assertEqual(returncode, 0, report)
        self.assertEqual(report["status"], "PASS", report)
        self.assertIn("CLAIM_MARKER_FOUND", self.finding_codes(report))

    def test_missing_root_report_does_not_expose_absolute_path(self) -> None:
        missing = Path(tempfile.gettempdir()) / f"cumcm-missing-{uuid.uuid4().hex}"
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(LINTER),
                str(missing),
                "--engine",
                "latex",
                "--source",
                "paper/main.tex",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(10, completed.returncode, completed.stderr or completed.stdout)
        report = json.loads(completed.stdout)
        self.assertEqual(".", report["project_root"])
        self.assertNotIn(str(missing.resolve()), json.dumps(report))


if __name__ == "__main__":
    unittest.main()
