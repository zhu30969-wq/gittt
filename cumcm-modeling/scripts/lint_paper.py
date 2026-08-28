#!/usr/bin/env python3
"""Read-only structural lint for a CUMCM LaTeX or Typst paper.

The linter checks source reachability, cross-engine contamination, unresolved
LaTeX references, registered claim markers, local image paths and an optional
compiled PDF.  It never edits, compiles or executes paper source, and a PASS is
not a judgment about mathematical correctness or writing quality.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable

try:
    from _contract_support import (
        VALIDATION_STATUSES,
        aggregate_status,
        load_yaml,
        safe_project_path,
        write_text_exclusive,
    )
except ImportError as exc:  # pragma: no cover - clean environment failure
    print(json.dumps({"status": "ENV_BLOCK", "message": f"missing script dependency: {exc}"}, ensure_ascii=False))
    raise SystemExit(11)


PLACEHOLDER_RE = re.compile(
    r"\b(?:TODO|FIXME|TBD|PLACEHOLDER|LOREM\s+IPSUM)\b|待补|待填写|占位文本",
    re.IGNORECASE,
)
LATEX_INCLUDE_RE = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
TYPST_INCLUDE_RE = re.compile(r"#(?:include|import)\s+\"([^\"]+)\"")
LATEX_IMAGE_RE = re.compile(r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}")
TYPST_IMAGE_RE = re.compile(r"(?:#)?image\s*\(\s*\"([^\"]+)\"")
LATEX_LABEL_RE = re.compile(r"\\label\s*\{([^}]+)\}")
LATEX_REF_RE = re.compile(
    r"\\(?:ref|eqref|autoref|pageref|cref|Cref)\*?\s*\{([^}]+)\}"
)
LATEX_CITE_RE = re.compile(
    r"\\(?:[Cc]ite\w*|[Pp]arencite|[Tt]extcite|[Aa]utocite|[Ff]ootcite|[Ss]martcite)"
    r"\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]+)\}"
)
LATEX_BIB_RE = re.compile(r"\\bibliography\s*\{([^}]+)\}|\\addbibresource(?:\[[^\]]*\])?\s*\{([^}]+)\}")
LATEX_DANGEROUS_RE = re.compile(r"\\(?:write18|immediate\s*\\write18|ShellEscape|pdfshellescape)\b", re.IGNORECASE)
LATEX_IN_TYPST_RE = re.compile(
    r"\\(?:begin|end|section|subsection|text|times|frac|alpha|beta|gamma|theta|cite|ref)\b"
)
TYPST_IN_LATEX_RE = re.compile(r"(?m)^\s*#(?:set|show|let|import|include)\b")
LATEX_LITERAL_BEGIN_RE = re.compile(
    r"\\begin\s*\{(?P<name>verbatim\*?|lstlisting|minted)\}"
)


def _mask_range(buffer: list[str], start: int, end: int) -> None:
    """Replace source characters with spaces while preserving line endings."""

    for index in range(start, end):
        if buffer[index] not in {"\r", "\n"}:
            buffer[index] = " "


def _unescaped_percent(text: str, start: int, end: int) -> int | None:
    """Return the first TeX comment marker not escaped by an odd slash run."""

    index = text.find("%", start, end)
    while index != -1:
        slash_count = 0
        cursor = index - 1
        while cursor >= start and text[cursor] == "\\":
            slash_count += 1
            cursor -= 1
        if slash_count % 2 == 0:
            return index
        index = text.find("%", index + 1, end)
    return None


def prepare_latex_source(text: str) -> tuple[str, list[str]]:
    """Create a scan view and collect real TeX line comments.

    The scan view masks unescaped ``%`` comments and literal-code environments
    without changing line numbers.  Comment-like text inside verbatim,
    verbatim*, lstlisting or minted is not treated as a paper claim marker.
    This is a lexical pre-check, not a TeX parser or compilation proof.
    """

    buffer = list(text)
    comments: list[str] = []
    literal_environment: str | None = None
    line_start = 0
    while line_start < len(text):
        newline = text.find("\n", line_start)
        line_end = len(text) if newline == -1 else newline + 1
        content_end = line_end
        while content_end > line_start and text[content_end - 1] in {"\r", "\n"}:
            content_end -= 1

        cursor = line_start
        while cursor < content_end:
            if literal_environment is not None:
                end_re = re.compile(
                    rf"\\end\s*\{{{re.escape(literal_environment)}\}}"
                )
                end_match = end_re.search(text, cursor, content_end)
                if end_match is None:
                    _mask_range(buffer, cursor, content_end)
                    cursor = content_end
                    continue
                _mask_range(buffer, cursor, end_match.end())
                cursor = end_match.end()
                literal_environment = None
                continue

            comment_at = _unescaped_percent(text, cursor, content_end)
            code_end = comment_at if comment_at is not None else content_end
            begin_match = LATEX_LITERAL_BEGIN_RE.search(text, cursor, code_end)
            if begin_match is not None:
                _mask_range(buffer, begin_match.start(), begin_match.end())
                cursor = begin_match.end()
                literal_environment = begin_match.group("name")
                continue
            if comment_at is not None:
                comments.append(text[comment_at + 1 : content_end])
                _mask_range(buffer, comment_at, content_end)
            cursor = content_end

        line_start = line_end
    return "".join(buffer), comments


def prepare_typst_source(text: str) -> tuple[str, list[str]]:
    """Mask ``//`` comments outside quoted strings and collect their text."""

    buffer = list(text)
    comments: list[str] = []
    line_start = 0
    while line_start < len(text):
        newline = text.find("\n", line_start)
        line_end = len(text) if newline == -1 else newline + 1
        content_end = line_end
        while content_end > line_start and text[content_end - 1] in {"\r", "\n"}:
            content_end -= 1

        in_string = False
        escaped = False
        cursor = line_start
        comment_at: int | None = None
        while cursor < content_end:
            character = text[cursor]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                cursor += 1
                continue
            if character == '"':
                in_string = True
                cursor += 1
                continue
            if character == "/" and cursor + 1 < content_end and text[cursor + 1] == "/":
                comment_at = cursor
                break
            cursor += 1
        if comment_at is not None:
            comments.append(text[comment_at + 2 : content_end])
            _mask_range(buffer, comment_at, content_end)
        line_start = line_end
    return "".join(buffer), comments


def marker_pattern(marker: str) -> re.Pattern[str]:
    """Match a complete marker token, so claim:c1 cannot match claim:c10."""

    token_character = r"A-Za-z0-9_.:-"
    return re.compile(
        rf"(?<![{token_character}]){re.escape(marker)}(?![{token_character}])"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint CUMCM paper sources and an optional compiled PDF without modifying them.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--engine", required=True, choices=["latex", "typst"])
    parser.add_argument("--source", required=True, help="Project-relative main .tex or .typ file")
    parser.add_argument("--claims", default="claims/claims.yaml", help="Project-relative claims registry")
    parser.add_argument("--figures", default="figures/figures.yaml", help="Project-relative figures registry")
    parser.add_argument("--pdf", help="Optional project-relative compiled PDF")
    parser.add_argument("--max-pages", type=int, help="Optional project-specific page ceiling; no permanent rule is hard-coded")
    parser.add_argument("--json-report", type=Path, help="Create a new JSON report; existing files are never overwritten")
    parser.add_argument("--strict", action="store_true", help="Promote WARN findings to BLOCK")
    return parser.parse_args()


class PaperLint:
    """Accumulate deterministic G6 findings for one source tree."""

    def __init__(self, root: Path, engine: str) -> None:
        self.root = root.resolve()
        self.engine = engine
        self.findings: list[dict[str, Any]] = []
        self.sources: dict[Path, str] = {}
        self.scan_sources: dict[Path, str] = {}
        self.source_comments: dict[Path, list[str]] = {}

    def add(self, status: str, code: str, message: str, *, path: str | None = None) -> None:
        if status not in VALIDATION_STATUSES:
            raise ValueError(f"invalid lint status: {status}")
        finding: dict[str, Any] = {"gate": "G6", "status": status, "code": code, "message": message}
        if path is not None:
            finding["path"] = path
        self.findings.append(finding)

    def display(self, path: Path) -> str:
        try:
            return path.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def resolve_owned_reference(
        self,
        owner: Path,
        reference: str,
        *,
        extensions: tuple[str, ...],
    ) -> Path | None:
        """Resolve a reference relative to its declaring source file.

        References with URL schemes are external and therefore not treated as
        local files.  Every local candidate must remain inside the project.
        """

        reference = reference.strip()
        if reference.startswith("@") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", reference):
            return None
        if "\\" in reference or re.match(r"^[A-Za-z]:", reference) or reference.startswith("/"):
            raise ValueError(f"unsafe or non-portable source reference: {reference!r}")
        base = owner.parent.relative_to(self.root)
        candidate_rel = (base / Path(reference)).as_posix()
        candidate = safe_project_path(self.root, candidate_rel)
        candidates = [candidate]
        if not candidate.suffix:
            candidates.extend(candidate.with_suffix(extension) for extension in extensions)
        for item in candidates:
            if item.is_file():
                return item
        return candidates[0]

    def load_source_tree(self, main_relative: str) -> None:
        extension = ".tex" if self.engine == "latex" else ".typ"
        try:
            main = safe_project_path(self.root, main_relative)
        except ValueError as exc:
            self.add("BLOCK", "SOURCE_PATH_UNSAFE", str(exc), path=main_relative)
            return
        if not main.is_file():
            self.add("BLOCK", "SOURCE_MISSING", "main paper source does not exist", path=main_relative)
            return
        if main.suffix.lower() != extension:
            self.add("BLOCK", "SOURCE_ENGINE_MISMATCH", f"{self.engine} expects a {extension} main source", path=main_relative)
            return

        queue: deque[Path] = deque([main])
        include_re = LATEX_INCLUDE_RE if self.engine == "latex" else TYPST_INCLUDE_RE
        while queue:
            source = queue.popleft()
            if source in self.sources:
                continue
            try:
                text = source.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                self.add("BLOCK", "SOURCE_READ_FAILED", str(exc), path=self.display(source))
                continue
            self.sources[source] = text
            if self.engine == "latex":
                scan_text, comments = prepare_latex_source(text)
            else:
                scan_text, comments = prepare_typst_source(text)
            self.scan_sources[source] = scan_text
            self.source_comments[source] = comments
            for match in include_re.finditer(scan_text):
                reference = match.group(1)
                try:
                    included = self.resolve_owned_reference(source, reference, extensions=(extension,))
                except ValueError as exc:
                    self.add("BLOCK", "INCLUDE_PATH_UNSAFE", str(exc), path=self.display(source))
                    continue
                if included is None:
                    self.add("WARN", "REMOTE_INCLUDE_UNCHECKED", f"external include was not inspected: {reference}", path=self.display(source))
                elif not included.is_file():
                    self.add("BLOCK", "INCLUDE_MISSING", f"included source not found: {reference}", path=self.display(source))
                else:
                    queue.append(included)

        if self.sources:
            self.add("PASS", "SOURCE_TREE_LOADED", f"loaded {len(self.sources)} paper source file(s)")

    def lint_text(self) -> None:
        if not self.scan_sources:
            return
        combined = "\n".join(self.scan_sources.values())
        for source, text in self.scan_sources.items():
            for match in PLACEHOLDER_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                self.add("BLOCK", "PLACEHOLDER_REMAINS", f"unfinished marker {match.group(0)!r} at line {line}", path=self.display(source))

            if self.engine == "latex":
                if LATEX_DANGEROUS_RE.search(text):
                    self.add("BLOCK", "LATEX_SHELL_ESCAPE", "paper source contains an unapproved shell-execution primitive", path=self.display(source))
                if TYPST_IN_LATEX_RE.search(text):
                    self.add("WARN", "TYPST_SYNTAX_IN_LATEX", "source appears to contain Typst directives", path=self.display(source))
            elif LATEX_IN_TYPST_RE.search(text):
                self.add("BLOCK", "LATEX_SYNTAX_IN_TYPST", "Typst source contains likely LaTeX commands", path=self.display(source))

            image_re = LATEX_IMAGE_RE if self.engine == "latex" else TYPST_IMAGE_RE
            image_extensions = (".pdf", ".png", ".jpg", ".jpeg", ".svg")
            for match in image_re.finditer(text):
                reference = match.group(1)
                try:
                    image = self.resolve_owned_reference(source, reference, extensions=image_extensions)
                except ValueError as exc:
                    self.add("BLOCK", "IMAGE_PATH_UNSAFE", str(exc), path=self.display(source))
                    continue
                if image is None:
                    self.add("WARN", "REMOTE_IMAGE_UNCHECKED", f"external image was not inspected: {reference}", path=self.display(source))
                elif not image.is_file():
                    self.add("BLOCK", "IMAGE_MISSING", f"image not found: {reference}", path=self.display(source))

        if self.engine == "latex":
            labels = LATEX_LABEL_RE.findall(combined)
            for label, count in Counter(labels).items():
                if count > 1:
                    self.add("BLOCK", "DUPLICATE_LABEL", f"LaTeX label {label!r} is defined {count} times")
            references = {
                reference.strip()
                for group in LATEX_REF_RE.findall(combined)
                for reference in group.split(",")
                if reference.strip()
            }
            for reference in sorted(references.difference(labels)):
                self.add("BLOCK", "UNRESOLVED_REFERENCE", f"LaTeX reference has no matching label: {reference}")
            self._lint_latex_bibliography()

    def _lint_latex_bibliography(self) -> None:
        combined = "\n".join(self.scan_sources.values())
        cited = {
            key.strip()
            for group in LATEX_CITE_RE.findall(combined)
            for key in group.split(",")
            if key.strip()
        }
        if not cited:
            return
        bib_paths: set[Path] = set()
        for source, text in self.scan_sources.items():
            for match in LATEX_BIB_RE.finditer(text):
                raw_group = match.group(1) or match.group(2) or ""
                for raw in raw_group.split(","):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        bib = self.resolve_owned_reference(source, raw, extensions=(".bib",))
                    except ValueError as exc:
                        self.add("BLOCK", "BIB_PATH_UNSAFE", str(exc), path=self.display(source))
                        continue
                    if bib is None or not bib.is_file():
                        self.add("BLOCK", "BIB_FILE_MISSING", f"bibliography file not found: {raw}", path=self.display(source))
                    else:
                        bib_paths.add(bib)
        keys: set[str] = set()
        for bib in bib_paths:
            try:
                keys.update(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bib.read_text(encoding="utf-8"), re.IGNORECASE))
            except (OSError, UnicodeError) as exc:
                self.add("BLOCK", "BIB_READ_FAILED", str(exc), path=self.display(bib))
        if cited and not bib_paths:
            self.add("BLOCK", "CITATIONS_WITHOUT_BIBLIOGRAPHY", "citations exist but no readable bibliography was declared")
        for key in sorted(cited.difference(keys)):
            self.add("BLOCK", "CITATION_KEY_MISSING", f"citation key not found in declared bibliography: {key}")

    def lint_claim_markers(self, relative: str) -> None:
        try:
            path = safe_project_path(self.root, relative)
        except ValueError as exc:
            self.add("BLOCK", "CLAIMS_PATH_UNSAFE", str(exc), path=relative)
            return
        if not path.is_file():
            self.add("NOT_APPLICABLE", "CLAIMS_REGISTRY_MISSING", "no claims registry was available for marker lint", path=relative)
            return
        try:
            document = load_yaml(path)
        except Exception as exc:
            self.add("BLOCK", "CLAIMS_READ_FAILED", str(exc), path=relative)
            return
        combined_comments = "\n".join(
            comment
            for comments in self.source_comments.values()
            for comment in comments
        )
        final_claims = [item for item in document.get("claims", []) if item.get("publication_status") == "final"] if isinstance(document, dict) else []
        marker_owners: dict[str, list[str]] = {}
        for claim in final_claims:
            for marker in claim.get("paper_markers", []):
                marker_owners.setdefault(marker, []).append(str(claim.get("id")))
        for marker, owners in marker_owners.items():
            if len(owners) > 1:
                self.add(
                    "BLOCK",
                    "CLAIM_MARKER_REUSED",
                    f"paper marker {marker!r} is assigned to multiple final claims: {', '.join(owners)}",
                    path=relative,
                )
        for claim in final_claims:
            markers = claim.get("paper_markers", [])
            if not markers:
                self.add("BLOCK", "FINAL_CLAIM_WITHOUT_MARKER", f"{claim.get('id')} has no paper marker", path=relative)
            for marker in markers:
                count = len(marker_pattern(marker).findall(combined_comments))
                if count == 0:
                    self.add("BLOCK", "CLAIM_MARKER_MISSING", f"paper source does not contain marker {marker!r} for {claim.get('id')}", path=relative)
                elif count > 1:
                    self.add(
                        "BLOCK",
                        "CLAIM_MARKER_DUPLICATE",
                        f"paper source contains marker {marker!r} {count} times for {claim.get('id')}",
                        path=relative,
                    )
                else:
                    self.add("PASS", "CLAIM_MARKER_FOUND", f"located {marker!r} for {claim.get('id')}", path=relative)

    def lint_figure_registry(self, relative: str) -> None:
        try:
            path = safe_project_path(self.root, relative)
        except ValueError as exc:
            self.add("BLOCK", "FIGURES_PATH_UNSAFE", str(exc), path=relative)
            return
        if not path.is_file():
            self.add("NOT_APPLICABLE", "FIGURES_REGISTRY_MISSING", "no figures registry was available for paper lint", path=relative)
            return
        try:
            document = load_yaml(path)
        except Exception as exc:
            self.add("BLOCK", "FIGURES_READ_FAILED", str(exc), path=relative)
            return
        combined = "\n".join(self.scan_sources.values())
        for figure in document.get("figures", []) if isinstance(document, dict) else []:
            output = figure.get("output", {}).get("path")
            if not isinstance(output, str):
                continue
            if output not in combined and Path(output).name not in combined:
                self.add("WARN", "REGISTERED_FIGURE_NOT_REFERENCED", f"registered figure is not visibly referenced by the paper source: {figure.get('id')}", path=output)

    def lint_pdf(self, relative: str, max_pages: int | None) -> None:
        try:
            path = safe_project_path(self.root, relative)
        except ValueError as exc:
            self.add("BLOCK", "PDF_PATH_UNSAFE", str(exc), path=relative)
            return
        if not path.is_file():
            self.add("BLOCK", "PDF_MISSING", "compiled PDF does not exist", path=relative)
            return
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            self.add("ENV_BLOCK", "PDF_READER_MISSING", f"install pypdf to inspect the compiled PDF: {exc}", path=relative)
            return
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                self.add("BLOCK", "PDF_ENCRYPTED", "compiled PDF is encrypted and cannot be audited", path=relative)
                return
            page_count = len(reader.pages)
            if page_count < 1:
                self.add("BLOCK", "PDF_EMPTY", "compiled PDF has no pages", path=relative)
                return
            self.add("PASS", "PDF_READABLE", f"compiled PDF contains {page_count} page(s)", path=relative)
            if max_pages is not None and page_count > max_pages:
                self.add("BLOCK", "PDF_PAGE_LIMIT", f"PDF has {page_count} pages, exceeding configured maximum {max_pages}", path=relative)
            for index, page in enumerate(reader.pages, start=1):
                extracted = (page.extract_text() or "").strip()
                if not extracted:
                    self.add("WARN", "PDF_PAGE_NO_EXTRACTABLE_TEXT", f"page {index} has no extractable text; inspect visually", path=relative)
        except Exception as exc:
            self.add("BLOCK", "PDF_READ_FAILED", str(exc), path=relative)

    def report(self, *, strict: bool) -> dict[str, Any]:
        if not self.findings:
            self.add("PASS", "NO_STRUCTURAL_ISSUES", "no structural paper-lint issue was found")
        if strict and any(item["status"] == "WARN" for item in self.findings):
            self.add("BLOCK", "STRICT_WARNINGS", "--strict promotes one or more WARN findings to BLOCK")
        status = aggregate_status([item["status"] for item in self.findings])
        return {
            "status": status,
            "engine": self.engine,
            # Reports may be shared with the paper package, so do not persist
            # the caller's absolute workstation path.
            "project_root": ".",
            "findings": self.findings,
            "disclaimer": "PASS covers only these structural checks; it does not prove mathematical correctness, visual quality, citation truth, or compliance with an unconfigured format profile.",
        }


def exit_code(status: str) -> int:
    return {"PASS": 0, "WARN": 0, "NOT_APPLICABLE": 0, "BLOCK": 10, "ENV_BLOCK": 11, "STALE": 12}[status]


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    lint = PaperLint(root, args.engine)
    if not root.is_dir():
        # Avoid persisting a username or workstation layout in shareable QA.
        lint.add("BLOCK", "PROJECT_ROOT_MISSING", "project directory not found")
    elif args.max_pages is not None and args.max_pages < 1:
        lint.add("BLOCK", "MAX_PAGES_INVALID", "--max-pages must be at least 1")
    else:
        lint.load_source_tree(args.source)
        lint.lint_text()
        lint.lint_claim_markers(args.claims)
        lint.lint_figure_registry(args.figures)
        if args.pdf:
            lint.lint_pdf(args.pdf, args.max_pages)
        elif args.max_pages is not None:
            lint.add("BLOCK", "MAX_PAGES_WITHOUT_PDF", "--max-pages requires --pdf")

    report = lint.report(strict=args.strict)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_report is not None:
        try:
            write_text_exclusive(args.json_report.resolve(), rendered + "\n")
        except FileExistsError:
            print(json.dumps({"status": "BLOCK", "message": f"report already exists: {args.json_report}"}, ensure_ascii=False))
            return 13
        except Exception as exc:
            print(json.dumps({"status": "BLOCK", "message": f"cannot create report: {exc}"}, ensure_ascii=False))
            return 13
    print(rendered)
    return exit_code(report["status"])


if __name__ == "__main__":
    sys.exit(main())
