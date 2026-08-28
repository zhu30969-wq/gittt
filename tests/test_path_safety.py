"""Cross-platform path invariants for every contract command.

These tests are read-only: ``safe_project_path`` resolves candidates but does
not create them.  A path accepted on Linux must not become a device, alternate
data stream, or normalized-name collision when a CUMCM team opens the project
on Windows.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "cumcm-modeling" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _contract_support import safe_project_path  # noqa: E402


class PortablePathTests(unittest.TestCase):
    def test_portable_paths_resolve_under_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (".", "paper/main.tex", "数据/结果-01.csv"):
            with self.subTest(relative=relative):
                resolved = safe_project_path(root, relative)
                resolved.relative_to(root.resolve())

    def test_windows_ambiguous_or_nonportable_paths_are_rejected(self) -> None:
        root = Path(__file__).resolve().parents[1]
        unsafe = (
            "CON",
            "data/aux.txt",
            "results/COM1.csv",
            "paper/main.tex.",
            "paper/main.tex ",
            "data/value:stream",
            "data/bad?.csv",
            "data/control\x01.csv",
            "data//double.csv",
            "data/./value.csv",
            "../outside.csv",
            "data/e\u0301.csv",
        )
        for relative in unsafe:
            with self.subTest(relative=relative):
                with self.assertRaises(ValueError):
                    safe_project_path(root, relative)


if __name__ == "__main__":
    unittest.main()
