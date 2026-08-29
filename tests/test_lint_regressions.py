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
SCRIPT_ROOT = LINTER.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPT_ROOT))

from lint_paper import PaperLint  # noqa: E402


CREATED_ROOTS: list[Path] = []


def make_project() -> Path:
    root = Path(tempfile.mkdtemp(prefix="cumcm-lint-regression-"))
    CREATED_ROOTS.append(root)
    return root


def write_text(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


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
    @classmethod
    def tearDownClass(cls) -> None:
        # The repository safety policy forbids recursive cleanup.  Preserve
        # every generated fixture so a failing lexical case remains auditable.
        print("PRESERVED_LINT_FIXTURES=")
        for root in CREATED_ROOTS:
            print(root)

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

    def test_local_latex_compile_inputs_are_fully_bound(self) -> None:
        root = make_project()
        write_text(
            root,
            "paper/main.tex",
            r"""\documentclass{localclass}
\usepackage{localpkg,amsmath}
\addbibresource{refs}
\begin{document}
\lstinputlisting[language=Python]{listings/listed.py}
\inputminted[linenos]{python}{listings/minted.py}
\pgfplotstableread[col sep=comma]{data/read.csv}\datatable
\pgfplotstabletypeset[col sep=comma]{data/typeset.csv}
\addplot table [x=x,y=y] {data/plot.dat};
\includepdf[pages=-]{appendix}
\end{document}
""",
        )
        for relative, text in {
            "paper/localclass.cls": "% local class\n",
            "paper/localpkg.sty": "% local package\n",
            "paper/refs.bib": "% bibliography without citations\n",
            "paper/listings/listed.py": "print('listed')\n",
            "paper/listings/minted.py": "print('minted')\n",
            "paper/data/read.csv": "x,y\n1,2\n",
            "paper/data/typeset.csv": "x,y\n3,4\n",
            "paper/data/plot.dat": "x y\n5 6\n",
            "paper/appendix.pdf": "%PDF fixture bytes\n",
        }.items():
            write_text(root, relative, text)

        lint = PaperLint(root, "latex")
        lint.load_source_tree("paper/main.tex")
        lint.lint_text()
        report = lint.report(strict=True)

        expected = {
            (root / relative).resolve()
            for relative in {
                "paper/localclass.cls",
                "paper/localpkg.sty",
                "paper/refs.bib",
                "paper/listings/listed.py",
                "paper/listings/minted.py",
                "paper/data/read.csv",
                "paper/data/typeset.csv",
                "paper/data/plot.dat",
                "paper/appendix.pdf",
            }
        }
        self.assertEqual("PASS", report["status"], report)
        self.assertEqual(expected, lint.local_resources)

    def test_dynamic_and_unknown_latex_inputs_block_strict_release(self) -> None:
        root = make_project()
        write_text(
            root,
            "paper/main.tex",
            r"""\documentclass{article}
\newcommand{\listingfile}{listings/generated.py}
\begin{document}
\lstinputlisting{\listingfile}
\verbatiminput{listings/unknown-reader.txt}
\end{document}
""",
        )

        lint = PaperLint(root, "latex")
        lint.load_source_tree("paper/main.tex")
        lint.lint_text()
        report = lint.report(strict=True)
        codes = self.finding_codes(report)

        self.assertEqual("BLOCK", report["status"], report)
        self.assertIn("LATEX_DYNAMIC_COMPILE_INPUT", codes)
        self.assertIn("LATEX_COMPILE_INPUT_UNKNOWN", codes)
        self.assertIn("STRICT_WARNINGS", codes)

    def test_latex_compile_input_cannot_escape_project_root(self) -> None:
        root = make_project()
        write_text(
            root,
            "paper/main.tex",
            "\\documentclass{article}\n\\lstinputlisting{../../outside.py}\n",
        )

        lint = PaperLint(root, "latex")
        lint.load_source_tree("paper/main.tex")
        lint.lint_text()
        report = lint.report(strict=True)

        self.assertEqual("BLOCK", report["status"], report)
        self.assertIn("LATEX_COMPILE_INPUT_PATH_UNSAFE", self.finding_codes(report))
        self.assertEqual(set(), lint.local_resources)

    def test_local_latex_support_tree_is_recursive_and_scanned(self) -> None:
        root = make_project()
        write_text(root, "paper/main.tex", "\\documentclass{localclass}\n\\begin{document}ok\\end{document}\n")
        write_text(
            root,
            "paper/localclass.cls",
            "\\RequirePackage{localpkg}\n\\input{class.cfg}\n",
        )
        write_text(root, "paper/localpkg.sty", "\\input{package.cfg}\n")
        write_text(root, "paper/class.cfg", "\\input{localclass.cls}\n")
        write_text(root, "paper/package.cfg", "% stable package configuration\n")

        lint = PaperLint(root, "latex")
        lint.load_source_tree("paper/main.tex")
        lint.lint_text()
        self.assertEqual("PASS", lint.report(strict=True)["status"], lint.findings)
        expected = {
            (root / relative).resolve()
            for relative in {
                "paper/localclass.cls",
                "paper/localpkg.sty",
                "paper/class.cfg",
                "paper/package.cfg",
            }
        }
        self.assertEqual(expected, lint.local_resources)

        write_text(root, "paper/unsafe.cfg", "\\write18{echo forbidden}\n")
        write_text(root, "paper/package.cfg", "\\input{unsafe.cfg}\n")
        unsafe = PaperLint(root, "latex")
        unsafe.load_source_tree("paper/main.tex")
        unsafe.lint_text()
        self.assertEqual("BLOCK", unsafe.report(strict=True)["status"], unsafe.findings)
        self.assertIn("LATEX_SHELL_ESCAPE", self.finding_codes(unsafe.report(strict=True)))

    def test_typst_static_loader_closure_masks_examples_and_blocks_dynamic_paths(self) -> None:
        root = make_project()
        write_text(
            root,
            "paper/main.typ",
            '#include  "module.typ"\n#image( "plot.png")\n',
        )
        write_text(
            root,
            "paper/module.typ",
            '''#let a = csv("data.csv")
#let b = json("data.json")
#let c = yaml("data.yaml")
#let d = read("notes.txt")
#let demo = "#csv(\\"missing.csv\\")"
/* #json("missing.json") */
```typ
#yaml("missing.yaml")
```
''',
        )
        for relative, content in {
            "paper/plot.png": "synthetic image bytes",
            "paper/data.csv": "x\n1\n",
            "paper/data.json": '{"x": 1}\n',
            "paper/data.yaml": "x: 1\n",
            "paper/notes.txt": "bound note\n",
        }.items():
            write_text(root, relative, content)

        lint = PaperLint(root, "typst")
        lint.load_source_tree("paper/main.typ")
        lint.lint_text()
        report = lint.report(strict=True)
        self.assertEqual("PASS", report["status"], report)
        self.assertEqual(
            {
                (root / relative).resolve()
                for relative in {
                    "paper/plot.png",
                    "paper/data.csv",
                    "paper/data.json",
                    "paper/data.yaml",
                    "paper/notes.txt",
                }
            },
            lint.local_resources,
        )

        write_text(root, "paper/dynamic.typ", '#let path = "data.csv"\n#csv(path)\n')
        dynamic = PaperLint(root, "typst")
        dynamic.load_source_tree("paper/dynamic.typ")
        dynamic.lint_text()
        dynamic_report = dynamic.report(strict=True)
        self.assertEqual("BLOCK", dynamic_report["status"], dynamic_report)
        self.assertIn("TYPST_DYNAMIC_COMPILE_INPUT", self.finding_codes(dynamic_report))

    def test_ambiguous_registry_finding_uses_registered_image_path(self) -> None:
        root = make_project()
        first = write_text(root, "paper/a.png", "a")
        second = write_text(root, "paper/z.png", "z")
        registry = {
            "figures": [
                {"id": "figure:a1", "publication_status": "final", "output": {"path": "paper/a.png"}},
                {"id": "figure:a2", "publication_status": "final", "output": {"path": "paper/a.png"}},
                {"id": "figure:z", "publication_status": "final", "output": {"path": "paper/z.png"}},
            ]
        }
        write_text(root, "figures/figures.yaml", json.dumps(registry))

        lint = PaperLint(root, "latex")
        lint.local_images.update({first.resolve(), second.resolve()})
        lint.lint_figure_registry("figures/figures.yaml")
        ambiguous = [
            finding
            for finding in lint.findings
            if finding["code"] == "PAPER_IMAGE_REGISTRY_AMBIGUOUS"
        ]

        self.assertEqual(1, len(ambiguous), lint.findings)
        self.assertEqual("paper/a.png", ambiguous[0]["path"])
        self.assertFalse(hasattr(PaperLint, "_legacy_lint_figure_registry"))


if __name__ == "__main__":
    unittest.main()
