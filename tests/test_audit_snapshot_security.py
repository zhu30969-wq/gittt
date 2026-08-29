"""Regression tests for descriptor-bound audit snapshots.

The fixture is intentionally retained in the system temporary directory.  No
test performs recursive cleanup, which keeps a failing race reproducible.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPT_ROOT))

import audit_project  # noqa: E402
from test_audit_regressions import (  # noqa: E402
    build_release_project,
    finding_codes,
    load_yaml,
    run_audit,
    sha256_file,
    write_text,
    write_yaml,
)


class AuditSnapshotSecurityTests(unittest.TestCase):
    def test_contract_replacement_after_capture_cannot_pass(self) -> None:
        """A path swap after capture must make the final report stale.

        The hook runs only after ``read_stable_bytes`` has returned the bytes
        tied to the descriptor.  The auditor must parse those captured bytes
        and then detect that the pathname no longer identifies them.
        """

        root = build_release_project()
        target = (root / "claims/claims.yaml").resolve()
        original_reader = audit_project.read_stable_bytes
        replaced = False

        def replace_after_capture(path: Path) -> bytes:
            nonlocal replaced
            captured = original_reader(path)
            if path.resolve() == target and not replaced:
                # Make the new pathname bytes invalid YAML.  If the semantic
                # parser reopens the path, it will emit YAML_INVALID; parsing
                # the descriptor-bound capture instead leaves only the
                # deliberate end-of-audit freshness failure.
                target.write_bytes(b"claims: [unterminated\n")
                replaced = True
            return captured

        with mock.patch.object(
            audit_project,
            "read_stable_bytes",
            side_effect=replace_after_capture,
        ):
            report, _audit = run_audit(root)

        self.assertTrue(replaced)
        self.assertNotEqual(report["status"], "PASS")
        self.assertIn("FILE_CHANGED_DURING_AUDIT", finding_codes(report))
        gate_status = {gate["gate"]: gate["status"] for gate in report["gates"]}
        self.assertEqual("STALE", gate_status["G5"])
        self.assertIn(gate_status["G6"], {"STALE", "BLOCK"})
        expected_g6_summary = (
            f"release requires G6=PASS, current status is {gate_status['G6']}"
        )
        self.assertTrue(
            any(
                finding["code"] == "RELEASE_GATE_NOT_PASS"
                and finding["message"] == expected_g6_summary
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )
        self.assertNotIn("YAML_INVALID", finding_codes(report))

    def test_replacement_during_late_dag_phase_cannot_pass(self) -> None:
        """A rewrite after the first consistency pass is checked again."""

        root = build_release_project()
        target = root / "claims/claims.yaml"
        original_dag = audit_project.Audit.validate_dag_and_propagate_stale

        def mutate_after_dag(audit: audit_project.Audit) -> None:
            original_dag(audit)
            target.write_bytes(target.read_bytes() + b"\n# changed during DAG\n")

        with mock.patch.object(
            audit_project.Audit,
            "validate_dag_and_propagate_stale",
            mutate_after_dag,
        ):
            report, _audit = run_audit(root)

        self.assertNotEqual(report["status"], "PASS")
        self.assertIn("FILE_CHANGED_DURING_AUDIT", finding_codes(report))
        gate_status = {gate["gate"]: gate["status"] for gate in report["gates"]}
        self.assertEqual("STALE", gate_status["G5"])
        self.assertEqual("STALE", gate_status["G6"])
        self.assertTrue(
            any(
                finding["code"] == "RELEASE_GATE_NOT_PASS"
                and finding["message"]
                == "release requires G6=PASS, current status is STALE"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )

    def test_changed_figure_contract_is_attributed_to_g5(self) -> None:
        """Snapshot freshness failures roll back to the artifact's own gate."""

        root = build_release_project()
        target = (root / "figures/figures.yaml").resolve()
        original_reader = audit_project.read_stable_bytes
        replaced = False

        def replace_after_capture(path: Path) -> bytes:
            nonlocal replaced
            captured = original_reader(path)
            if path.resolve() == target and not replaced:
                target.write_bytes(captured + b"\n# changed figure registry\n")
                replaced = True
            return captured

        with mock.patch.object(
            audit_project,
            "read_stable_bytes",
            side_effect=replace_after_capture,
        ):
            report, _audit = run_audit(root)

        changed = [
            finding
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "FILE_CHANGED_DURING_AUDIT"
            and finding.get("artifact_id") == "figures:main"
        ]
        self.assertTrue(replaced)
        self.assertEqual(["G5"], [finding["gate"] for finding in changed])
        self.assertEqual("CLAIMING", report["rollback_target"])

    def test_contract_symlink_is_rejected_without_following_it(self) -> None:
        """POSIX-capable hosts must reject a manifest-declared link path."""

        root = build_release_project()
        logical = root / "claims/claims.yaml"
        backing = root / "claims/claims-v1.yaml"
        logical.replace(backing)
        try:
            logical.symlink_to(backing.name)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable on this host: {exc}")

        report, _audit = run_audit(root)
        self.assertIn("ARTIFACT_CAPTURE_FAILED", finding_codes(report))
        self.assertNotIn("AUDIT_INTERNAL_ERROR", finding_codes(report))

    def test_optional_history_is_streamed_without_retaining_bytes(self) -> None:
        """Excluded history does not accumulate full contract bytes in RAM."""

        root = build_release_project()
        write_text(root, "history/large.yaml", "payload: " + "x" * 1_000_000 + "\n")
        manifest = load_yaml(root / "manifest.yaml")
        manifest["artifacts"].append(
            {
                "id": "results:history-large",
                "kind": "results",
                "path": "history/large.yaml",
                "sha256": sha256_file(root / "history/large.yaml"),
                "required": False,
                "depends_on": [],
            }
        )
        write_yaml(root, "manifest.yaml", manifest)

        _report, audit = run_audit(root)
        retained_paths = {path.as_posix() for path in audit.file_snapshots}
        self.assertFalse(any(path.endswith("history/large.yaml") for path in retained_paths))
        self.assertTrue(
            all(not isinstance(value, bytes) for row in audit.file_snapshots.values() for value in row)
        )

    def test_optional_history_hash_failure_is_structured(self) -> None:
        """A disappearing excluded file must not become an internal error."""

        root = build_release_project()
        write_text(root, "history/old.yaml", "payload: retained history\n")
        manifest = load_yaml(root / "manifest.yaml")
        manifest["artifacts"].append(
            {
                "id": "results:history-old",
                "kind": "results",
                "path": "history/old.yaml",
                "sha256": sha256_file(root / "history/old.yaml"),
                "required": False,
                "depends_on": [],
            }
        )
        write_yaml(root, "manifest.yaml", manifest)

        with mock.patch.object(
            audit_project,
            "sha256_stable_file",
            side_effect=PermissionError("simulated history read failure"),
        ):
            report, _audit = run_audit(root)

        codes = finding_codes(report)
        self.assertIn("HISTORICAL_ARTIFACT_UNAVAILABLE", codes)
        self.assertNotIn("AUDIT_INTERNAL_ERROR", codes)


if __name__ == "__main__":
    unittest.main()
