#!/usr/bin/env python3
"""Diff or explicitly refresh the manifest's top-level artifact hashes.

Default behavior is strictly read-only.  ``--write`` updates only
``manifest.artifacts[*].sha256`` and increments the manifest revision.  It
never edits hashes embedded in problem/model/experiment/result/claim/figure
contracts and never refreshes result fingerprints; those values describe the
scientific state at a particular decision or run and must not be laundered by
a convenience command.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from _contract_support import (
        DEFAULT_LOCK_TIMEOUT_SECONDS,
        LockTimeoutError,
        SHA256_RE,
        aggregate_status,
        exclusive_sidecar_lock,
        load_yaml,
        safe_project_path,
        sha256_file,
        write_yaml_atomic,
    )
except ImportError as exc:  # pragma: no cover
    print(json.dumps({"status": "ENV_BLOCK", "message": f"missing script dependency: {exc}"}, ensure_ascii=False))
    raise SystemExit(11)


ZERO_HASH = "0" * 64
SCHEMA_VERSION_RE = re.compile(r"^2\.[0-9]+\.[0-9]+$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show manifest artifact hash differences; use --write to refresh only those hashes."
    )
    parser.add_argument("project_root", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Explicitly update manifest.artifacts hashes; default is read-only",
    )
    parser.add_argument(
        "--expected-manifest-sha256",
        help="With --write, refuse if manifest.yaml changed since the caller read it",
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT_SECONDS,
        help="Maximum wait for the manifest sidecar lock during --write",
    )
    return parser.parse_args()


def exit_code(status: str) -> int:
    return {
        "PASS": 0,
        "WARN": 0,
        "NOT_APPLICABLE": 0,
        "BLOCK": 10,
        "ENV_BLOCK": 11,
        "STALE": 12,
    }[status]


def emit(status: str, mode: str, manifest_path: Path, findings: list[dict[str, Any]]) -> int:
    print(
        json.dumps(
            {
                "status": status,
                "mode": mode,
                "manifest": str(manifest_path),
                "findings": findings,
                "scientific_fingerprints_refreshed": False,
                "message": (
                    "Only manifest artifact hashes were considered. Embedded file hashes and result/review fingerprints were not changed."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code(status)


def _process_manifest(
    args: argparse.Namespace, root: Path, manifest_path: Path
) -> int:
    """Read, compare and optionally replace one current manifest snapshot.

    For write mode the caller runs this entire function while holding the
    manifest sidecar lock, so the expected-hash check and candidate build use
    the same snapshot that is atomically replaced.
    """

    findings: list[dict[str, Any]] = []

    if not root.is_dir() or not manifest_path.is_file():
        findings.append({"status": "BLOCK", "code": "MANIFEST_MISSING", "message": "project manifest.yaml was not found"})
        return emit("BLOCK", "write" if args.write else "diff", manifest_path, findings)

    if args.expected_manifest_sha256 is not None:
        if not args.write:
            findings.append({"status": "BLOCK", "code": "EXPECTED_HASH_WITHOUT_WRITE", "message": "--expected-manifest-sha256 is only meaningful with --write"})
            return emit("BLOCK", "diff", manifest_path, findings)
        if not SHA256_RE.fullmatch(args.expected_manifest_sha256):
            findings.append({"status": "BLOCK", "code": "EXPECTED_HASH_INVALID", "message": "expected manifest hash is not a lowercase SHA-256"})
            return emit("BLOCK", "write", manifest_path, findings)
        if sha256_file(manifest_path) != args.expected_manifest_sha256:
            findings.append({"status": "STALE", "code": "MANIFEST_CHANGED", "message": "manifest changed since the caller read it"})
            return emit("STALE", "write", manifest_path, findings)

    try:
        manifest = load_yaml(manifest_path)
    except Exception as exc:
        findings.append({"status": "BLOCK", "code": "MANIFEST_YAML_INVALID", "message": str(exc)})
        return emit("BLOCK", "write" if args.write else "diff", manifest_path, findings)
    if not isinstance(manifest, dict) or manifest.get("kind") != "manifest":
        findings.append({"status": "BLOCK", "code": "NOT_A_MANIFEST", "message": "manifest.yaml is not a manifest contract"})
        return emit("BLOCK", "write" if args.write else "diff", manifest_path, findings)
    if not isinstance(manifest.get("schema_version"), str) or not SCHEMA_VERSION_RE.fullmatch(manifest["schema_version"]):
        findings.append(
            {
                "status": "BLOCK",
                "code": "SCHEMA_VERSION_UNSUPPORTED",
                "message": "manifest.py only accepts schema_version 2.x.x; migrate the project before refreshing hashes",
            }
        )
        return emit("BLOCK", "write" if args.write else "diff", manifest_path, findings)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        findings.append({"status": "BLOCK", "code": "ARTIFACT_LIST_EMPTY", "message": "manifest.artifacts must be a non-empty list"})
        return emit("BLOCK", "write" if args.write else "diff", manifest_path, findings)

    replacements: dict[int, str] = {}
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            findings.append({"status": "BLOCK", "code": "ARTIFACT_ROW_INVALID", "message": f"artifacts[{index}] is not a mapping"})
            continue
        artifact_id = artifact.get("id")
        relative = artifact.get("path")
        if not isinstance(relative, str):
            findings.append({"status": "BLOCK", "code": "ARTIFACT_PATH_INVALID", "artifact_id": artifact_id, "message": "artifact path is missing or not a string"})
            continue
        try:
            path = safe_project_path(root, relative)
        except ValueError as exc:
            findings.append({"status": "BLOCK", "code": "ARTIFACT_PATH_UNSAFE", "artifact_id": artifact_id, "path": relative, "message": str(exc)})
            continue
        if not path.is_file():
            findings.append({"status": "BLOCK", "code": "ARTIFACT_MISSING", "artifact_id": artifact_id, "path": relative, "message": "cannot hash a missing artifact"})
            continue

        actual = sha256_file(path)
        expected = artifact.get("sha256")
        if expected == actual:
            findings.append({"status": "PASS", "code": "HASH_MATCH", "artifact_id": artifact_id, "path": relative, "sha256": actual})
        else:
            findings.append(
                {
                    "status": "STALE",
                    "code": "HASH_PLACEHOLDER" if expected == ZERO_HASH else "HASH_DIFF",
                    "artifact_id": artifact_id,
                    "path": relative,
                    "expected": expected,
                    "actual": actual,
                }
            )
            replacements[index] = actual

    prewrite_status = aggregate_status([item["status"] for item in findings])
    if not args.write:
        return emit(prewrite_status, "diff", manifest_path, findings)
    if prewrite_status == "BLOCK":
        # All-or-nothing: do not partially refresh a manifest whose declared
        # artifact set contains an unsafe or missing file.
        return emit("BLOCK", "write", manifest_path, findings)
    if not replacements:
        findings.append({"status": "NOT_APPLICABLE", "code": "NO_CHANGES", "message": "all artifact hashes are already current"})
        return emit("PASS", "write", manifest_path, findings)

    updated = copy.deepcopy(manifest)
    for index, digest in replacements.items():
        updated["artifacts"][index]["sha256"] = digest
    updated["revision"] = int(manifest.get("revision", 0)) + 1
    try:
        write_yaml_atomic(manifest_path, updated)
    except Exception as exc:
        findings.append({"status": "BLOCK", "code": "MANIFEST_WRITE_FAILED", "message": str(exc)})
        return emit("BLOCK", "write", manifest_path, findings)

    for finding in findings:
        if finding["status"] == "STALE":
            finding["status"] = "PASS"
            finding["code"] = "HASH_REFRESHED"
    return emit("PASS", "write", manifest_path, findings)


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    manifest_path = root / "manifest.yaml"

    if not args.write:
        return _process_manifest(args, root, manifest_path)

    # Avoid creating a sidecar directory for an invalid/missing project.  The
    # manifest is checked again inside the lock to handle deletion races.
    if not root.is_dir() or not manifest_path.is_file():
        return _process_manifest(args, root, manifest_path)

    try:
        with exclusive_sidecar_lock(
            manifest_path, timeout_seconds=args.lock_timeout_seconds
        ):
            return _process_manifest(args, root, manifest_path)
    except LockTimeoutError as exc:
        return emit(
            "BLOCK",
            "write",
            manifest_path,
            [
                {
                    "status": "BLOCK",
                    "code": "LOCK_TIMEOUT",
                    "message": str(exc),
                    "lock_path": str(exc.lock_path),
                    "timeout_seconds": exc.timeout_seconds,
                }
            ],
        )
    except ValueError as exc:
        return emit(
            "BLOCK",
            "write",
            manifest_path,
            [
                {
                    "status": "BLOCK",
                    "code": "LOCK_ARGUMENT_INVALID",
                    "message": str(exc),
                }
            ],
        )
    except OSError as exc:
        return emit(
            "BLOCK",
            "write",
            manifest_path,
            [
                {
                    "status": "BLOCK",
                    "code": "LOCK_FAILED",
                    "message": str(exc),
                }
            ],
        )


if __name__ == "__main__":
    sys.exit(main())
