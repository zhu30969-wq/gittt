"""Adversarial build-log and proof-receipt tests.

Fixtures are created in unique system-temporary directories by the shared test
builders and intentionally remain available for inspection.  No cleanup or
recursive deletion is performed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_ROOT))

from test_audit_regressions import (  # noqa: E402
    build_proof_only_release_project,
    build_release_project,
    convert_release_to_typst,
    file_ref,
    finding_codes,
    load_yaml,
    rebind_paper_receipt,
    resign_release_project,
    run_audit,
    sha256_file,
    write_text,
    write_yaml,
)


def replace_formal_proof(root: Path, text: str, *, bound_receipt: bool) -> None:
    """Replace proof bytes and coherently refresh every enclosing receipt."""

    write_text(root, "paper/proof.tex", text)
    proof_hash = sha256_file(root / "paper/proof.tex")

    claims = load_yaml(root / "claims/claims.yaml")
    claim = claims["claims"][0]
    claim["proof_artifact"] = file_ref(root, "paper/proof.tex")
    claim["human_review"] = {
        "status": "PASS",
        "reviewer": "fixture",
        "rationale": (
            f"Verified claim:c1 proposition and derivation against proof SHA-256 {proof_hash}"
            if bound_receipt
            else "Reviewed a proof without identifying its claim or exact bytes."
        ),
    }
    write_yaml(root, "claims/claims.yaml", claims)

    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build["source_files"] = [
        file_ref(root, "paper/main.tex"),
        file_ref(root, "paper/proof.tex"),
    ]
    write_yaml(root, "paper/paper-build.yaml", paper_build)
    resign_release_project(root)


class BuildAndProofReceiptTests(unittest.TestCase):
    def test_latex_failure_log_cannot_issue_verified_receipt(self) -> None:
        root = build_release_project()
        write_text(
            root,
            "paper/build.log",
            "This is pdfTeX.\n! LaTeX Error: Undefined control sequence.\nEmergency stop.\nNo pages of output.\n",
        )
        rebind_paper_receipt(root)

        report, _audit = run_audit(root)
        codes = finding_codes(report)
        self.assertIn("PAPER_BUILD_LOG_FAILURE", codes)
        self.assertNotIn("PAPER_BUILD_RECEIPT_VERIFIED", codes)

    def test_typst_failure_log_cannot_issue_verified_receipt(self) -> None:
        root = build_release_project()
        convert_release_to_typst(root)
        write_text(
            root,
            "paper/build.log",
            "error: unknown variable\n  ┌─ main.typ:1:1\ncompilation failed\n",
        )
        paper_build = load_yaml(root / "paper/paper-build.yaml")
        paper_build["log"] = file_ref(root, "paper/build.log")
        write_yaml(root, "paper/paper-build.yaml", paper_build)
        resign_release_project(root)

        report, _audit = run_audit(root)
        codes = finding_codes(report)
        self.assertIn("PAPER_BUILD_LOG_FAILURE", codes)
        self.assertNotIn("PAPER_BUILD_RECEIPT_VERIFIED", codes)

    def test_existing_latex_and_typst_success_markers_remain_valid(self) -> None:
        for engine in ("latex", "typst"):
            with self.subTest(engine=engine):
                root = build_release_project()
                if engine == "typst":
                    convert_release_to_typst(root)
                report, _audit = run_audit(root)
                codes = finding_codes(report)
                self.assertEqual("PASS", report["status"], codes)
                self.assertIn("PAPER_BUILD_LOG_SUCCESS_MARKER", codes)
                self.assertIn("PAPER_BUILD_RECEIPT_VERIFIED", codes)

    def test_single_word_cannot_support_proof_only_release(self) -> None:
        root = build_proof_only_release_project()
        replace_formal_proof(root, "hello\n", bound_receipt=True)

        report, audit = run_audit(root)
        codes = finding_codes(report)
        self.assertIn("PROOF_ARTIFACT_INVALID", codes)
        self.assertNotIn("PROOF_ARTIFACT_VERIFIED", codes)
        self.assertNotIn("claim:c1", audit.valid_final_proof_claim_ids)

    def test_formal_proof_requires_claim_and_hash_bound_review_receipt(self) -> None:
        root = build_proof_only_release_project()
        proof_text = (root / "paper/proof.tex").read_text(encoding="utf-8")
        replace_formal_proof(root, proof_text, bound_receipt=False)

        report, audit = run_audit(root)
        codes = finding_codes(report)
        self.assertIn("FORMAL_PROOF_RECEIPT_UNBOUND", codes)
        self.assertNotIn("PROOF_ARTIFACT_VERIFIED", codes)
        self.assertNotIn("claim:c1", audit.valid_final_proof_claim_ids)

    def test_structured_formal_proof_and_bound_receipt_remain_valid(self) -> None:
        root = build_proof_only_release_project()
        report, audit = run_audit(root)
        codes = finding_codes(report)
        self.assertEqual("PASS", report["status"], codes)
        self.assertIn("PROOF_ARTIFACT_VERIFIED", codes)
        self.assertIn("claim:c1", audit.valid_final_proof_claim_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
