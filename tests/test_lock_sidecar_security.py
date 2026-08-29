#!/usr/bin/env python3
"""Security regressions for persistent contract sidecar locks.

Each test uses and retains a unique system-temporary directory so failures can
be inspected without deleting or mutating repository files.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "cumcm-modeling" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _contract_support as support  # noqa: E402


def preserved_temp_dir(label: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"cumcm-lock-security-{label}-"))
    print(f"preserved lock-security fixture: {root}", file=sys.stderr, flush=True)
    return root


class LockSidecarSecurityTests(unittest.TestCase):
    def test_regular_single_link_sidecar_remains_usable(self) -> None:
        root = preserved_temp_dir("regular")
        target = root / "manifest.yaml"
        lock_path = support.sidecar_lock_path(target)

        with support.exclusive_sidecar_lock(target) as acquired_path:
            self.assertEqual(acquired_path, lock_path)

        metadata = lock_path.lstat()
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(metadata.st_nlink, 1)
        self.assertEqual(lock_path.read_bytes(), b"\0")

    def test_existing_hardlink_is_rejected_before_external_bytes_change(self) -> None:
        root = preserved_temp_dir("hardlink")
        project = root / "project"
        outside = root / "outside"
        project.mkdir()
        outside.mkdir()
        target = project / "manifest.yaml"
        lock_path = support.sidecar_lock_path(target)
        victim = outside / "victim.bin"
        victim.write_bytes(b"")
        os.link(victim, lock_path)

        with self.assertRaisesRegex(ValueError, "multiple hard links"):
            with support.exclusive_sidecar_lock(target):
                self.fail("unsafe hard-linked sidecar was acquired")

        self.assertEqual(victim.read_bytes(), b"")
        self.assertEqual(lock_path.read_bytes(), b"")

    def test_existing_symlink_is_rejected_before_external_bytes_change(self) -> None:
        root = preserved_temp_dir("symlink")
        project = root / "project"
        outside = root / "outside"
        project.mkdir()
        outside.mkdir()
        target = project / "manifest.yaml"
        lock_path = support.sidecar_lock_path(target)
        victim = outside / "victim.bin"
        victim.write_bytes(b"")
        try:
            lock_path.symlink_to(victim)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation unavailable on this host: {exc}")

        with self.assertRaisesRegex(ValueError, "symlink or reparse point"):
            with support.exclusive_sidecar_lock(target):
                self.fail("unsafe symlinked sidecar was acquired")

        self.assertEqual(victim.read_bytes(), b"")

    def test_windows_reparse_attribute_is_rejected_independently_of_mode(self) -> None:
        metadata = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_nlink=1,
            st_file_attributes=getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            ),
        )

        with self.assertRaisesRegex(ValueError, "symlink or reparse point"):
            support._validate_lock_sidecar_metadata(  # noqa: SLF001
                metadata,
                lock_path=Path(".manifest.yaml.lock"),
                source="synthetic Windows metadata",
            )

    def test_open_handle_must_match_post_open_path_identity(self) -> None:
        root = preserved_temp_dir("identity")
        target = root / "manifest.yaml"
        lock_path = support.sidecar_lock_path(target)

        with mock.patch.object(support.os.path, "samestat", return_value=False):
            with self.assertRaisesRegex(ValueError, "changed between path lookup and open"):
                with support.exclusive_sidecar_lock(target):
                    self.fail("mismatched open handle was acquired")

        self.assertTrue(lock_path.is_file())
        self.assertEqual(lock_path.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
