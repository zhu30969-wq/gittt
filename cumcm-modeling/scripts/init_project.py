#!/usr/bin/env python3
"""Create a CUMCM contract project without overwriting existing content.

The template manifest contains hash placeholders.  This initializer renders
the manifest last, after all missing contract files have been created, so the
manifest records the exact bytes that now exist in the target.  If a target
file already exists, it is left untouched and its current hash is used only
when a *new* manifest is being created.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from _contract_support import (
        TYPED_ID_RE,
        dump_yaml,
        load_yaml,
        safe_project_path,
        sha256_file,
        write_text_exclusive,
    )
except ImportError as exc:  # pragma: no cover - exercised when dependencies are absent
    print(
        json.dumps(
            {
                "status": "ENV_BLOCK",
                "message": f"missing script dependency: {exc}",
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(11)


SCHEMA_VERSION_RE = re.compile(r"^2\.[0-9]+\.[0-9]+$")


def validate_template_contracts(template_root: Path) -> list[str]:
    """Preflight every manifest-owned contract before creating target files.

    Custom template roots are supported, but an old 1.x contract tree must be
    migrated explicitly.  Running this check before ``target.mkdir`` prevents
    a rejected template from leaving a partially initialized project.
    """

    errors: list[str] = []
    manifest_path = template_root / "manifest.yaml"
    if not manifest_path.is_file():
        return ["template manifest.yaml is missing"]
    try:
        manifest = load_yaml(manifest_path)
    except Exception as exc:
        return [f"template manifest.yaml cannot be read: {exc}"]
    if not isinstance(manifest, dict) or manifest.get("kind") != "manifest":
        return ["template manifest.yaml is not a manifest contract"]
    if not isinstance(manifest.get("schema_version"), str) or not SCHEMA_VERSION_RE.fullmatch(manifest["schema_version"]):
        errors.append("template manifest schema_version must be 2.x.x")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("template manifest artifacts must be a non-empty list")
        return errors
    for index, row in enumerate(artifacts):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            errors.append(f"template manifest artifacts[{index}] has no valid path")
            continue
        try:
            artifact_path = safe_project_path(template_root, row["path"], must_exist=True)
            document = load_yaml(artifact_path)
        except Exception as exc:
            errors.append(f"template artifact {row.get('path')!r} cannot be read: {exc}")
            continue
        if not isinstance(document, dict):
            errors.append(f"template artifact {row['path']!r} is not a contract mapping")
            continue
        version = document.get("schema_version")
        if not isinstance(version, str) or not SCHEMA_VERSION_RE.fullmatch(version):
            errors.append(f"template artifact {row['path']!r} schema_version must be 2.x.x")
        if document.get("id") != row.get("id") or document.get("kind") != row.get("kind"):
            errors.append(f"template artifact {row['path']!r} id/kind differs from its manifest row")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create missing CUMCM project contract files; never overwrite existing files."
    )
    parser.add_argument("target", type=Path, help="Target project directory")
    parser.add_argument(
        "--project-id",
        required=True,
        help="Stable typed ID such as project:cumcm-2026-a",
    )
    parser.add_argument(
        "--template-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "project-template",
        help="Template directory; defaults to this skill's bundled template",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report intended actions without creating directories or files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not TYPED_ID_RE.fullmatch(args.project_id) or not args.project_id.startswith("project:"):
        print(json.dumps({"status": "BLOCK", "message": "--project-id must match project:<id>"}, ensure_ascii=False))
        return 10

    template_root = args.template_root.resolve()
    if not template_root.is_dir():
        print(json.dumps({"status": "ENV_BLOCK", "message": f"template directory not found: {template_root}"}, ensure_ascii=False))
        return 11

    template_errors = validate_template_contracts(template_root)
    if template_errors:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "TEMPLATE_CONTRACT_INVALID",
                    "message": "template contract preflight failed",
                    "errors": template_errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 10

    target = args.target.resolve()
    # The manifest is handled last because it locks hashes of the other files.
    template_files = sorted(
        (path for path in template_root.rglob("*") if path.is_file()),
        key=lambda path: (path.name == "manifest.yaml", path.as_posix()),
    )
    findings: list[dict[str, str]] = []

    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    for source in template_files:
        relative = source.relative_to(template_root).as_posix()
        try:
            destination = safe_project_path(target, relative)
        except ValueError as exc:
            findings.append({"status": "BLOCK", "path": relative, "message": str(exc)})
            continue

        if destination.exists():
            findings.append(
                {
                    "status": "NOT_APPLICABLE",
                    "path": relative,
                    "message": "existing file preserved without modification",
                }
            )
            continue

        if args.dry_run:
            findings.append({"status": "PASS", "path": relative, "message": "would create missing file"})
            continue

        try:
            if relative == "manifest.yaml":
                manifest = load_yaml(source)
                manifest["project_id"] = args.project_id
                for artifact in manifest.get("artifacts", []):
                    artifact_path = safe_project_path(target, artifact["path"], must_exist=True)
                    artifact["sha256"] = sha256_file(artifact_path)
                rendered = dump_yaml(manifest)
            else:
                rendered = source.read_text(encoding="utf-8").replace("__PROJECT_ID__", args.project_id)
            write_text_exclusive(destination, rendered)
            findings.append({"status": "PASS", "path": relative, "message": "created"})
        except FileExistsError:
            # Handles a concurrent creator without ever overwriting its file.
            findings.append(
                {
                    "status": "NOT_APPLICABLE",
                    "path": relative,
                    "message": "file appeared concurrently and was preserved",
                }
            )
        except Exception as exc:
            findings.append({"status": "BLOCK", "path": relative, "message": str(exc)})

    overall = "BLOCK" if any(item["status"] == "BLOCK" for item in findings) else "PASS"
    print(json.dumps({"status": overall, "target": str(target), "findings": findings}, ensure_ascii=False, indent=2))
    return 10 if overall == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())
