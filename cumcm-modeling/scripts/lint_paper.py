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
TYPST_DYNAMIC_INCLUDE_RE = re.compile(r"#(?:include|import)(?![ \t]*\")[ \t]+([^\r\n]+)")
LATEX_IMAGE_RE = re.compile(r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]+)\}")
TYPST_IMAGE_RE = re.compile(r"(?:#)?image\s*\(\s*\"([^\"]+)\"")
TYPST_DYNAMIC_IMAGE_RE = re.compile(r"(?:#)?image\s*\((?![ \t]*\")[ \t]*([^,)\r\n]+)")
LATEX_LABEL_RE = re.compile(r"\\label\s*\{([^}]+)\}")
LATEX_REF_RE = re.compile(
    r"\\(?:ref|eqref|autoref|pageref|cref|Cref)\*?\s*\{([^}]+)\}"
)
LATEX_CITE_RE = re.compile(
    r"\\(?:[Cc]ite\w*|[Pp]arencite|[Tt]extcite|[Aa]utocite|[Ff]ootcite|[Ss]martcite)"
    r"\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]+)\}"
)
LATEX_BIB_RE = re.compile(r"\\bibliography\s*\{([^}]+)\}|\\addbibresource(?:\[[^\]]*\])?\s*\{([^}]+)\}")
LATEX_BIBSTYLE_RE = re.compile(r"\\bibliographystyle\s*\{([^}]+)\}")
LATEX_DOCUMENTCLASS_RE = re.compile(
    r"\\documentclass\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_USEPACKAGE_RE = re.compile(
    r"\\usepackage\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_REQUIREPACKAGE_RE = re.compile(
    r"\\RequirePackage(?:WithOptions)?\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_LOADCLASS_RE = re.compile(
    r"\\LoadClass(?:WithOptions)?\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_LSTINPUTLISTING_RE = re.compile(
    r"\\lstinputlisting\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_INPUTMINTED_RE = re.compile(
    r"\\inputminted\s*(?:\[[^\]]*\]\s*)?\{[^}]+\}\s*\{([^}]+)\}"
)
LATEX_PGFPLOTSTABLE_READ_RE = re.compile(
    r"\\pgfplotstableread\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_PGFPLOTSTABLE_TYPESET_RE = re.compile(
    r"\\pgfplotstabletypeset\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_ADDPLOT_TABLE_RE = re.compile(
    r"\\addplot\+?\s*(?:\[[^\]]*\]\s*)?table\s*"
    r"(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
LATEX_INCLUDEPDF_RE = re.compile(
    r"\\includepdf\s*(?:\[[^\]]*\]\s*)?\{([^}]+)\}"
)
# This deliberately catches only commands whose names strongly suggest that
# TeX may read another file.  Known commands are handled by dedicated parsers
# below; an unknown command is surfaced as WARN so strict release lint blocks
# instead of silently omitting a possible compilation input from the receipt.
LATEX_POSSIBLE_INPUT_COMMAND_RE = re.compile(
    r"\\(?P<command>[A-Za-z@]*(?:input|include|load|read|file|import|external)[A-Za-z@]*)"
    r"\*?\s*(?:\[[^\]]*\]\s*)?\{(?P<argument>[^{}]*)\}",
    re.IGNORECASE,
)
LATEX_DYNAMIC_SEARCH_PATH_RE = re.compile(r"\\(?:graphicspath|input@path)\b")
LATEX_UNBRACED_INPUT_RE = re.compile(r"\\(?:input|include)\s+(?!\{)([^\s%]+)")
LATEX_UNSUPPORTED_PRIMITIVE_READ_RE = re.compile(r"\\(?:openin|pdfximage)\b", re.IGNORECASE)
LATEX_LOCAL_FONT_PATH_RE = re.compile(
    r"\\(?:setmainfont|setsansfont|setmonofont|newfontfamily)\b[^\r\n]*\bPath\s*=",
    re.IGNORECASE,
)
LATEX_DANGEROUS_RE = re.compile(r"\\(?:write18|immediate\s*\\write18|ShellEscape|pdfshellescape)\b", re.IGNORECASE)
LATEX_IN_TYPST_RE = re.compile(
    r"\\(?:begin|end|section|subsection|text|times|frac|alpha|beta|gamma|theta|cite|ref)\b"
)
TYPST_IN_LATEX_RE = re.compile(r"(?m)^\s*#(?:set|show|let|import|include)\b")
LATEX_LITERAL_BEGIN_RE = re.compile(
    r"\\begin\s*\{(?P<name>verbatim\*?|lstlisting|minted)\}"
)
TYPST_FILE_CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_])#?(?P<function>csv|json|yaml|toml|xml|cbor|read|bibliography|plugin)"
    r"\s*\(\s*(?P<argument>\"(?:[^\"\\]|\\.)*\"|[^,)\r\n]*)",
    re.IGNORECASE,
)

KNOWN_LATEX_INPUT_COMMANDS = {
    "addbibresource",
    "bibliography",
    "bibliographystyle",
    "documentclass",
    "include",
    "includegraphics",
    "includepdf",
    "input",
    "inputencoding",
    "inputminted",
    "lstinputlisting",
    "loadclass",
    "loadclasswithoptions",
    "pgfplotstableread",
    "pgfplotstabletypeset",
    "requirepackage",
    "requirepackagewithoptions",
    "subfile",
    "usepackage",
}

LATEX_TEXT_RESOURCE_SUFFIXES = {".tex", ".cls", ".sty", ".cfg", ".def", ".ltx", ".clo", ".fd"}
TYPST_FILE_EXTENSIONS = {
    "csv": (".csv",),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "toml": (".toml",),
    "xml": (".xml",),
    "cbor": (".cbor",),
    "read": (),
    "bibliography": (".bib", ".yaml", ".yml"),
    "plugin": (".wasm",),
}


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
    """Mask Typst comments and raw blocks while preserving executable strings.

    Static loader arguments remain visible for resource discovery.  Text in
    line comments, nested block comments and backtick raw/code blocks is
    masked so examples such as ``#csv(\"demo.csv\")`` are not mistaken for
    compilation inputs.  Comment text is retained separately for claim-marker
    checks.
    """

    buffer = list(text)
    comments: list[str] = []
    cursor = 0
    in_string = False
    escaped = False
    while cursor < len(text):
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
        if text.startswith("//", cursor):
            end = text.find("\n", cursor + 2)
            end = len(text) if end == -1 else end
            comments.append(text[cursor + 2 : end])
            _mask_range(buffer, cursor, end)
            cursor = end
            continue
        if text.startswith("/*", cursor):
            start = cursor
            depth = 1
            cursor += 2
            while cursor < len(text) and depth:
                if text.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                elif text.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    cursor += 1
            comments.append(text[start + 2 : cursor - 2 if depth == 0 else len(text)])
            _mask_range(buffer, start, cursor)
            continue
        if character == "`":
            start = cursor
            delimiter_length = 1
            while cursor + delimiter_length < len(text) and text[cursor + delimiter_length] == "`":
                delimiter_length += 1
            delimiter = "`" * delimiter_length
            end_at = text.find(delimiter, cursor + delimiter_length)
            cursor = len(text) if end_at == -1 else end_at + delimiter_length
            _mask_range(buffer, start, cursor)
            continue
        cursor += 1
    return "".join(buffer), comments


def typst_executable_mask(text: str) -> list[bool]:
    """Mark positions outside quoted strings in a comment/raw-masked view."""

    result = [True] * len(text)
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            result[index] = False
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            result[index] = False
            in_string = True
    return result


def marker_pattern(marker: str) -> re.Pattern[str]:
    """Match a complete marker token, so claim:c1 cannot match claim:c10."""

    token_character = r"A-Za-z0-9_.:-"
    return re.compile(
        rf"(?<![{token_character}]){re.escape(marker)}(?![{token_character}])"
    )


def latex_reference_is_dynamic(reference: str) -> bool:
    """Return whether a TeX file argument cannot be resolved lexically.

    Control sequences, parameter tokens and environment-variable-like syntax
    can construct a path at compilation time.  Guessing their expansion would
    make a paper-build receipt incomplete, so callers surface a WARN that
    strict lint promotes to BLOCK.
    """

    stripped = reference.strip()
    return not stripped or any(token in stripped for token in ("\\", "#", "$", "{"))


def latex_reference_is_explicitly_local(reference: str, suffixes: tuple[str, ...]) -> bool:
    """Distinguish an explicit project path from a TeX-distribution name."""

    stripped = reference.strip()
    suffix = Path(stripped).suffix.lower()
    return (
        "/" in stripped
        or stripped.startswith(".")
        or suffix in {item.lower() for item in suffixes}
    )


def latex_table_argument_is_inline(reference: str) -> bool:
    """Recognize pgfplots inline rows, which are already hashed source text."""

    return "\n" in reference or "\r" in reference or "\\\\" in reference


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint CUMCM paper sources and an optional compiled PDF without modifying them.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--engine", required=True, choices=["latex", "typst"])
    parser.add_argument("--source", required=True, help="Project-relative main .tex or .typ file")
    parser.add_argument("--cwd", help="Project-relative compiler working directory; LaTeX defaults to the source directory")
    parser.add_argument("--claims", default="claims/claims.yaml", help="Project-relative claims registry")
    parser.add_argument("--figures", default="figures/figures.yaml", help="Project-relative figures registry")
    parser.add_argument("--pdf", help="Optional project-relative compiled PDF")
    parser.add_argument("--max-pages", type=int, help="Optional project-specific page ceiling; no permanent rule is hard-coded")
    parser.add_argument("--json-report", type=Path, help="Create a new JSON report; existing files are never overwritten")
    parser.add_argument("--strict", action="store_true", help="Promote WARN findings to BLOCK")
    return parser.parse_args()


class PaperLint:
    """Accumulate deterministic G6 findings for one source tree."""

    def __init__(self, root: Path, engine: str, compile_cwd: str | None = None) -> None:
        self.root = root.resolve()
        self.engine = engine
        self.findings: list[dict[str, Any]] = []
        self.sources: dict[Path, str] = {}
        self.scan_sources: dict[Path, str] = {}
        self.source_comments: dict[Path, list[str]] = {}
        self.local_images: set[Path] = set()
        self.local_resources: set[Path] = set()
        self.latex_resource_scans: dict[Path, str] = {}
        self.compile_cwd: Path | None = None
        if compile_cwd is not None:
            try:
                candidate = safe_project_path(self.root, compile_cwd, must_exist=True)
                if not candidate.is_dir():
                    raise ValueError("compiler cwd is not a directory")
                self.compile_cwd = candidate
            except (TypeError, ValueError, FileNotFoundError) as exc:
                self.add("BLOCK", "COMPILE_CWD_INVALID", str(exc), path=compile_cwd)

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
        base_path = self.compile_cwd if self.engine == "latex" and self.compile_cwd is not None else owner.parent
        base = base_path.relative_to(self.root)
        candidate_rel = (base / Path(reference)).as_posix()
        candidate = safe_project_path(self.root, candidate_rel)
        candidates = [candidate]
        if not candidate.suffix:
            candidates.extend(candidate.with_suffix(extension) for extension in extensions)
        for item in candidates:
            if item.is_file():
                return item
        return candidates[0]

    def bind_latex_resource(
        self,
        owner: Path,
        reference: str,
        *,
        extensions: tuple[str, ...],
        role: str,
        missing_code: str,
        allow_tex_distribution: bool = False,
    ) -> Path | None:
        """Resolve and register one static local LaTeX compilation input.

        Bare class and package names may come from the TeX distribution.  An
        explicit path or suffix, however, is a project-owned declaration and a
        missing file is therefore a deterministic compilation error.
        """

        reference = reference.strip()
        if latex_reference_is_dynamic(reference):
            self.add(
                "WARN",
                "LATEX_DYNAMIC_COMPILE_INPUT",
                f"{role} uses a dynamic path that cannot be fingerprinted: {reference!r}",
                path=self.display(owner),
            )
            return None
        try:
            resource = self.resolve_owned_reference(
                owner,
                reference,
                extensions=extensions,
            )
        except ValueError as exc:
            self.add(
                "BLOCK",
                "LATEX_COMPILE_INPUT_PATH_UNSAFE",
                f"{role}: {exc}",
                path=self.display(owner),
            )
            return None
        if resource is None:
            self.add(
                "WARN",
                "REMOTE_COMPILE_INPUT_UNCHECKED",
                f"external {role} was not fingerprinted: {reference}",
                path=self.display(owner),
            )
            return None
        if resource.is_file():
            resolved = resource.resolve()
            if resolved not in self.sources:
                self.local_resources.add(resolved)
            return resolved
        if allow_tex_distribution and not latex_reference_is_explicitly_local(
            reference, extensions
        ):
            # article.cls, amsmath.sty and similar bare names are owned by the
            # TeX installation rather than the paper project.  The compiler
            # version in paper_build is responsible for that environment.
            return None
        self.add(
            "BLOCK",
            missing_code,
            f"{role} file not found: {reference}",
            path=self.display(owner),
        )
        return None

    def lint_latex_compile_inputs(
        self,
        source: Path,
        text: str,
        *,
        bind_resource_includes: bool = False,
    ) -> set[Path]:
        """Bind static local files consumed by common LaTeX commands.

        This lexical inventory complements the recursive ``input/include``
        source tree, images and bibliographies.  Unsupported or dynamically
        constructed file reads remain visible as WARN and therefore block a
        strict release instead of disappearing from the build receipt.
        """

        discovered: set[Path] = set()

        for match in LATEX_DOCUMENTCLASS_RE.finditer(text):
            resource = self.bind_latex_resource(
                source,
                match.group(1),
                extensions=(".cls",),
                role="document class",
                missing_code="LOCAL_DOCUMENTCLASS_MISSING",
                allow_tex_distribution=True,
            )
            if resource is not None:
                discovered.add(resource)

        for match in LATEX_USEPACKAGE_RE.finditer(text):
            for package in match.group(1).split(","):
                if package.strip():
                    resource = self.bind_latex_resource(
                        source,
                        package,
                        extensions=(".sty",),
                        role="package",
                        missing_code="LOCAL_PACKAGE_MISSING",
                        allow_tex_distribution=True,
                    )
                    if resource is not None:
                        discovered.add(resource)

        for pattern, extensions, role in (
            (LATEX_REQUIREPACKAGE_RE, (".sty",), "required package"),
            (LATEX_LOADCLASS_RE, (".cls",), "loaded class"),
        ):
            for match in pattern.finditer(text):
                for name in match.group(1).split(","):
                    if not name.strip():
                        continue
                    resource = self.bind_latex_resource(
                        source,
                        name,
                        extensions=extensions,
                        role=role,
                        missing_code="LOCAL_TEX_SUPPORT_FILE_MISSING",
                        allow_tex_distribution=True,
                    )
                    if resource is not None:
                        discovered.add(resource)

        if bind_resource_includes:
            for match in LATEX_INCLUDE_RE.finditer(text):
                resource = self.bind_latex_resource(
                    source,
                    match.group(1),
                    extensions=(".tex", ".cfg", ".sty", ".cls", ".def", ".ltx", ".clo", ".fd"),
                    role="support-file include",
                    missing_code="SUPPORT_INPUT_MISSING",
                )
                if resource is not None:
                    discovered.add(resource)

        static_inputs = (
            (
                LATEX_LSTINPUTLISTING_RE,
                (),
                "lstinputlisting input",
                "LISTING_INPUT_MISSING",
            ),
            (
                LATEX_INPUTMINTED_RE,
                (),
                "inputminted input",
                "MINTED_INPUT_MISSING",
            ),
            (
                LATEX_INCLUDEPDF_RE,
                (".pdf",),
                "included PDF",
                "INCLUDED_PDF_MISSING",
            ),
        )
        for pattern, extensions, role, missing_code in static_inputs:
            for match in pattern.finditer(text):
                resource = self.bind_latex_resource(
                    source,
                    match.group(1),
                    extensions=extensions,
                    role=role,
                    missing_code=missing_code,
                )
                if resource is not None:
                    discovered.add(resource)

        table_inputs = (
            (LATEX_PGFPLOTSTABLE_READ_RE, "pgfplotstable input"),
            (LATEX_PGFPLOTSTABLE_TYPESET_RE, "pgfplotstable table"),
            (LATEX_ADDPLOT_TABLE_RE, "addplot table input"),
        )
        for pattern, role in table_inputs:
            for match in pattern.finditer(text):
                reference = match.group(1)
                if latex_table_argument_is_inline(reference):
                    continue
                resource = self.bind_latex_resource(
                    source,
                    reference,
                    extensions=(".csv", ".dat", ".tsv", ".txt"),
                    role=role,
                    missing_code="TABLE_INPUT_MISSING",
                )
                if resource is not None:
                    discovered.add(resource)

        if bind_resource_includes:
            for match in LATEX_IMAGE_RE.finditer(text):
                resource = self.bind_latex_resource(
                    source,
                    match.group(1),
                    extensions=(".pdf", ".png", ".jpg", ".jpeg", ".svg"),
                    role="support-file image",
                    missing_code="IMAGE_MISSING",
                )
                if resource is not None:
                    discovered.add(resource)
            for match in LATEX_BIB_RE.finditer(text):
                raw_group = match.group(1) or match.group(2) or ""
                for raw in raw_group.split(","):
                    if not raw.strip():
                        continue
                    resource = self.bind_latex_resource(
                        source,
                        raw,
                        extensions=(".bib",),
                        role="support-file bibliography",
                        missing_code="BIB_FILE_MISSING",
                    )
                    if resource is not None:
                        discovered.add(resource)
            for match in LATEX_BIBSTYLE_RE.finditer(text):
                resource = self.bind_latex_resource(
                    source,
                    match.group(1),
                    extensions=(".bst",),
                    role="bibliography style",
                    missing_code="BIB_STYLE_MISSING",
                    allow_tex_distribution=True,
                )
                if resource is not None:
                    discovered.add(resource)

        # A local bibliography style declared directly by the paper is also a
        # build input, although its contents are not TeX source-scanned.
        if not bind_resource_includes:
            for match in LATEX_BIBSTYLE_RE.finditer(text):
                resource = self.bind_latex_resource(
                    source,
                    match.group(1),
                    extensions=(".bst",),
                    role="bibliography style",
                    missing_code="BIB_STYLE_MISSING",
                    allow_tex_distribution=True,
                )
                if resource is not None:
                    discovered.add(resource)

        for match in LATEX_POSSIBLE_INPUT_COMMAND_RE.finditer(text):
            command = match.group("command")
            if command.casefold() in KNOWN_LATEX_INPUT_COMMANDS:
                continue
            self.add(
                "WARN",
                "LATEX_COMPILE_INPUT_UNKNOWN",
                f"unsupported file-reading command \\{command} may consume an untracked local input",
                path=self.display(source),
            )
        if LATEX_DYNAMIC_SEARCH_PATH_RE.search(text):
            self.add(
                "WARN",
                "LATEX_COMPILE_SEARCH_PATH_DYNAMIC",
                "custom TeX search paths cannot be resolved into a complete local input set",
                path=self.display(source),
            )
        if LATEX_UNBRACED_INPUT_RE.search(text):
            self.add(
                "WARN",
                "LATEX_UNBRACED_COMPILE_INPUT",
                "unbraced input/include syntax cannot be resolved into a complete local input set",
                path=self.display(source),
            )
        if LATEX_UNSUPPORTED_PRIMITIVE_READ_RE.search(text):
            self.add(
                "WARN",
                "LATEX_COMPILE_INPUT_UNKNOWN",
                "unsupported primitive file read may consume an untracked local input",
                path=self.display(source),
            )
        if LATEX_LOCAL_FONT_PATH_RE.search(text):
            self.add(
                "WARN",
                "LATEX_LOCAL_FONT_UNTRACKED",
                "fontspec Path-based local fonts are not statically fingerprinted",
                path=self.display(source),
            )
        return discovered

    def load_latex_resource_tree(self) -> None:
        """Recursively scan project-owned TeX classes, packages and support files.

        The main paper ``input/include`` tree remains in ``sources``.  Files
        pulled in by a local class/package are compilation resources: they are
        hash-bound, scanned for further reads and dangerous primitives, but do
        not participate in paper claim-marker or section-text checks.
        """

        queue: deque[Path] = deque()
        for source, text in self.scan_sources.items():
            queue.extend(self.lint_latex_compile_inputs(source, text))
        while queue:
            resource = queue.popleft().resolve()
            if resource in self.sources or resource in self.latex_resource_scans:
                continue
            if resource.suffix.lower() not in LATEX_TEXT_RESOURCE_SUFFIXES:
                continue
            try:
                text = resource.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                self.add("BLOCK", "LATEX_RESOURCE_READ_FAILED", str(exc), path=self.display(resource))
                continue
            scan_text, _comments = prepare_latex_source(text)
            self.latex_resource_scans[resource] = scan_text
            if LATEX_DANGEROUS_RE.search(scan_text):
                self.add(
                    "BLOCK",
                    "LATEX_SHELL_ESCAPE",
                    "local TeX compilation resource contains an unapproved shell-execution primitive",
                    path=self.display(resource),
                )
            queue.extend(
                self.lint_latex_compile_inputs(
                    resource,
                    scan_text,
                    bind_resource_includes=True,
                )
            )

    def lint_typst_compile_inputs(self, source: Path, text: str) -> None:
        """Bind static Typst file loaders and expose dynamic paths to strict lint."""

        executable_mask = typst_executable_mask(text)
        for match in TYPST_DYNAMIC_INCLUDE_RE.finditer(text):
            if not executable_mask[match.start()]:
                continue
            self.add(
                "WARN",
                "TYPST_DYNAMIC_COMPILE_INPUT",
                f"Typst include/import path is not a static string: {match.group(1).strip()!r}",
                path=self.display(source),
            )
        for match in TYPST_DYNAMIC_IMAGE_RE.finditer(text):
            if not executable_mask[match.start()]:
                continue
            self.add(
                "WARN",
                "TYPST_DYNAMIC_COMPILE_INPUT",
                f"Typst image path is not a static string: {match.group(1).strip()!r}",
                path=self.display(source),
            )
        for match in TYPST_FILE_CALL_RE.finditer(text):
            if not executable_mask[match.start()]:
                continue
            function = match.group("function").casefold()
            argument = match.group("argument").strip()
            if len(argument) < 2 or not (argument.startswith('"') and argument.endswith('"')):
                self.add(
                    "WARN",
                    "TYPST_DYNAMIC_COMPILE_INPUT",
                    f"Typst {function} path is not a static string: {argument!r}",
                    path=self.display(source),
                )
                continue
            reference = argument[1:-1]
            try:
                resource = self.resolve_owned_reference(
                    source,
                    reference,
                    extensions=TYPST_FILE_EXTENSIONS[function],
                )
            except ValueError as exc:
                self.add("BLOCK", "TYPST_COMPILE_INPUT_PATH_UNSAFE", f"{function}: {exc}", path=self.display(source))
                continue
            if resource is None:
                self.add("WARN", "REMOTE_COMPILE_INPUT_UNCHECKED", f"external Typst {function} input was not fingerprinted: {reference}", path=self.display(source))
            elif not resource.is_file():
                self.add("BLOCK", "TYPST_COMPILE_INPUT_MISSING", f"Typst {function} input not found: {reference}", path=self.display(source))
            else:
                self.local_resources.add(resource.resolve())

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
        if self.engine == "latex" and self.compile_cwd is None:
            self.compile_cwd = main.parent.resolve()

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
            executable_mask = typst_executable_mask(scan_text) if self.engine == "typst" else None
            for match in include_re.finditer(scan_text):
                if executable_mask is not None and not executable_mask[match.start()]:
                    continue
                reference = match.group(1)
                if self.engine == "latex" and latex_reference_is_dynamic(reference):
                    self.add(
                        "WARN",
                        "LATEX_DYNAMIC_COMPILE_INPUT",
                        f"included source uses a dynamic path that cannot be fingerprinted: {reference!r}",
                        path=self.display(source),
                    )
                    continue
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
        if self.engine == "latex":
            self.load_latex_resource_tree()
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
            else:
                self.lint_typst_compile_inputs(source, text)
                if LATEX_IN_TYPST_RE.search(text):
                    self.add("BLOCK", "LATEX_SYNTAX_IN_TYPST", "Typst source contains likely LaTeX commands", path=self.display(source))

            image_re = LATEX_IMAGE_RE if self.engine == "latex" else TYPST_IMAGE_RE
            image_extensions = (".pdf", ".png", ".jpg", ".jpeg", ".svg")
            executable_mask = typst_executable_mask(text) if self.engine == "typst" else None
            for match in image_re.finditer(text):
                if executable_mask is not None and not executable_mask[match.start()]:
                    continue
                reference = match.group(1)
                if self.engine == "latex" and latex_reference_is_dynamic(reference):
                    self.add(
                        "WARN",
                        "LATEX_DYNAMIC_COMPILE_INPUT",
                        f"image uses a dynamic path that cannot be fingerprinted: {reference!r}",
                        path=self.display(source),
                    )
                    continue
                try:
                    image = self.resolve_owned_reference(source, reference, extensions=image_extensions)
                except ValueError as exc:
                    self.add("BLOCK", "IMAGE_PATH_UNSAFE", str(exc), path=self.display(source))
                    continue
                if image is None:
                    self.add("WARN", "REMOTE_IMAGE_UNCHECKED", f"external image was not inspected: {reference}", path=self.display(source))
                elif not image.is_file():
                    self.add("BLOCK", "IMAGE_MISSING", f"image not found: {reference}", path=self.display(source))
                else:
                    self.local_images.add(image.resolve())
                    self.local_resources.add(image.resolve())

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
        bib_paths: set[Path] = set()
        declaration_sources = {**self.scan_sources, **self.latex_resource_scans}
        for source, text in declaration_sources.items():
            for match in LATEX_BIB_RE.finditer(text):
                raw_group = match.group(1) or match.group(2) or ""
                for raw in raw_group.split(","):
                    raw = raw.strip()
                    if not raw:
                        continue
                    bib = self.bind_latex_resource(
                        source,
                        raw,
                        extensions=(".bib",),
                        role="bibliography",
                        missing_code="BIB_FILE_MISSING",
                    )
                    if bib is not None:
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
        self.lint_figure_registries([relative])

    def lint_figure_registries(self, relatives: list[str]) -> None:
        """Require every local paper image to have one final registry row.

        Draft and archived registry rows are intentionally allowed to remain
        unused.  The publication invariant is one-way: every image consumed by
        the paper must be registered; historical alternatives need not appear
        in the final source.
        """

        registered: dict[Path, list[str]] = {}
        loaded_any = False
        for relative in relatives:
            try:
                path = safe_project_path(self.root, relative)
            except ValueError as exc:
                self.add("BLOCK", "FIGURES_PATH_UNSAFE", str(exc), path=relative)
                continue
            if not path.is_file():
                self.add("NOT_APPLICABLE", "FIGURES_REGISTRY_MISSING", "no figures registry was available for paper lint", path=relative)
                continue
            try:
                document = load_yaml(path)
            except Exception as exc:
                self.add("BLOCK", "FIGURES_READ_FAILED", str(exc), path=relative)
                continue
            loaded_any = True
            for figure in document.get("figures", []) if isinstance(document, dict) else []:
                if figure.get("publication_status") != "final":
                    continue
                output = figure.get("output", {}).get("path")
                if not isinstance(output, str):
                    continue
                try:
                    resolved = safe_project_path(self.root, output)
                except ValueError as exc:
                    self.add("BLOCK", "FIGURE_OUTPUT_PATH_UNSAFE", str(exc), path=output)
                    continue
                registered.setdefault(resolved.resolve(), []).append(str(figure.get("id")))

        if not loaded_any and not relatives:
            self.add("NOT_APPLICABLE", "FIGURES_REGISTRY_MISSING", "no figures registry was declared")
        for image in sorted(self.local_images, key=lambda item: item.as_posix()):
            owners = registered.get(image, [])
            if len(owners) == 0:
                self.add(
                    "BLOCK",
                    "PAPER_IMAGE_UNREGISTERED",
                    "local image referenced by the paper has no final figures registry row",
                    path=self.display(image),
                )
        for registered_path, owners in sorted(registered.items(), key=lambda item: item[0].as_posix()):
            if registered_path not in self.local_images:
                self.add(
                    "BLOCK",
                    "FINAL_FIGURE_NOT_REFERENCED",
                    f"final figure registry row is not referenced by the paper source: {owners}",
                    path=self.display(registered_path),
                )
            elif len(owners) > 1:
                self.add(
                    "BLOCK",
                    "PAPER_IMAGE_REGISTRY_AMBIGUOUS",
                    f"local image is registered by multiple final figure IDs: {owners}",
                    path=self.display(registered_path),
                )

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
    lint = PaperLint(root, args.engine, compile_cwd=args.cwd)
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
