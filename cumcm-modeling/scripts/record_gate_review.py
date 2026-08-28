#!/usr/bin/env python3
"""Append one human/hybrid gate review without discarding prior reviews.

This tool records a decision; it does not decide whether the project actually
passes.  ``audit_project.py`` always keeps automatic BLOCK/STALE findings even
when the newest human record says PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource

    from _contract_support import (
        DEFAULT_LOCK_TIMEOUT_SECONDS,
        LockTimeoutError,
        SHA256_RE,
        TYPED_ID_RE,
        VALIDATION_STATUSES,
        dump_yaml,
        exclusive_sidecar_lock,
        load_yaml,
        safe_project_path,
        sha256_file,
        write_yaml_atomic,
    )
except ImportError as exc:  # pragma: no cover
    print(json.dumps({"status": "ENV_BLOCK", "message": f"missing script dependency: {exc}"}, ensure_ascii=False))
    raise SystemExit(11)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append one evidence-bound gate review.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--log", default="reviews/gate-reviews.yaml", help="Project-relative review log path")
    parser.add_argument("--gate", required=True, choices=[f"G{number}" for number in range(8)])
    parser.add_argument("--decision", required=True, choices=VALIDATION_STATUSES)
    parser.add_argument("--basis", choices=["human", "hybrid"], default="human")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--evidence", action="append", default=[], help="Typed evidence ID; repeat as needed")
    parser.add_argument(
        "--fingerprint",
        action="append",
        default=[],
        metavar="ID=SHA256",
        help="Bind review to an artifact hash; repeat as needed",
    )
    parser.add_argument("--condition", action="append", default=[])
    parser.add_argument("--review-id", help="Optional typed review ID; generated when omitted")
    parser.add_argument(
        "--expected-log-sha256",
        help="Optimistic concurrency guard: refuse if the current log hash differs",
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT_SECONDS,
        help="Maximum wait for the persistent review-log sidecar lock",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_fingerprints(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"fingerprint must be ID=SHA256: {item!r}")
        artifact_id, digest = item.split("=", 1)
        if not TYPED_ID_RE.fullmatch(artifact_id):
            raise ValueError(f"invalid artifact ID in fingerprint: {artifact_id!r}")
        if not SHA256_RE.fullmatch(digest):
            raise ValueError(f"invalid SHA-256 for {artifact_id}")
        if artifact_id in result:
            raise ValueError(f"duplicate fingerprint ID: {artifact_id}")
        result[artifact_id] = digest
    return result


def validate_document(document: dict[str, Any], schema_root: Path) -> list[str]:
    schemas: dict[str, dict[str, Any]] = {}
    registry = Registry()
    for path in schema_root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema["$id"] = path.resolve().as_uri()
        schemas[path.name] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    schema = schemas["gate_review.schema.json"]
    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )
    errors = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path)):
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{location}: {error.message}")
    return errors


def _process_review(
    args: argparse.Namespace,
    root: Path,
    log_path: Path,
    fingerprints: dict[str, str],
    *,
    write_enabled: bool,
) -> int:
    """Build one append from a freshly read review-log snapshot.

    Write callers hold the sidecar lock for this entire function.  Therefore
    the expected hash, duplicate-ID check, candidate validation, atomic
    replace and final hash all refer to one serialized critical section.
    """

    if args.expected_log_sha256 is not None:
        if not SHA256_RE.fullmatch(args.expected_log_sha256):
            print(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "code": "EXPECTED_LOG_HASH_INVALID",
                        "message": "--expected-log-sha256 is invalid",
                    },
                    ensure_ascii=False,
                )
            )
            return 10
        if not log_path.is_file() or sha256_file(log_path) != args.expected_log_sha256:
            print(
                json.dumps(
                    {
                        "status": "STALE",
                        "code": "REVIEW_LOG_CHANGED",
                        "message": "review log changed since it was read",
                    },
                    ensure_ascii=False,
                )
            )
            return 12

    if log_path.exists():
        try:
            document = load_yaml(log_path)
        except Exception as exc:
            print(json.dumps({"status": "BLOCK", "message": f"cannot read review log: {exc}"}, ensure_ascii=False))
            return 10
    else:
        document = {
            "schema_version": "1.0.0",
            "kind": "gate_review",
            "id": "review:gates",
            "revision": 1,
            "lifecycle_status": "draft",
            "depends_on": [],
            "provenance": {"author_type": "human"},
            "extensions": {},
            "reviews": [],
        }
    if not isinstance(document, dict) or document.get("kind") != "gate_review":
        print(json.dumps({"status": "BLOCK", "message": "review log is not a gate_review contract"}, ensure_ascii=False))
        return 10

    now = datetime.now(timezone.utc).replace(microsecond=0)
    generated_id = f"review:{args.gate.lower()}-{now.strftime('%Y%m%dt%H%M%S')}z-{uuid.uuid4().hex[:8]}"
    review_id = args.review_id or generated_id
    if not TYPED_ID_RE.fullmatch(review_id) or not review_id.startswith("review:"):
        print(json.dumps({"status": "BLOCK", "message": "--review-id must match review:<id>"}, ensure_ascii=False))
        return 10
    if any(item.get("id") == review_id for item in document.get("reviews", [])):
        print(json.dumps({"status": "BLOCK", "message": f"review ID already exists: {review_id}"}, ensure_ascii=False))
        return 10

    entry = {
        "id": review_id,
        "gate": args.gate,
        "decision": args.decision,
        "basis": args.basis,
        "reviewer": args.reviewer.strip(),
        "reviewed_at": now.isoformat().replace("+00:00", "Z"),
        "rationale": args.rationale.strip(),
        "evidence_refs": args.evidence,
        "artifact_fingerprints": fingerprints,
        "conditions": args.condition,
    }
    if not entry["reviewer"] or not entry["rationale"]:
        print(json.dumps({"status": "BLOCK", "message": "reviewer and rationale cannot be blank"}, ensure_ascii=False))
        return 10

    candidate = dict(document)
    candidate["reviews"] = [*document.get("reviews", []), entry]
    candidate["revision"] = int(document.get("revision", 0)) + 1
    schema_root = Path(__file__).resolve().parent.parent / "references" / "schemas"
    try:
        errors = validate_document(candidate, schema_root)
    except Exception as exc:
        print(json.dumps({"status": "ENV_BLOCK", "message": f"schema validation unavailable: {exc}"}, ensure_ascii=False))
        return 11
    if errors:
        print(json.dumps({"status": "BLOCK", "message": "candidate review log is invalid", "errors": errors}, ensure_ascii=False, indent=2))
        return 10

    rendered = dump_yaml(candidate)
    predicted_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    if write_enabled:
        try:
            write_yaml_atomic(log_path, candidate)
            predicted_hash = sha256_file(log_path)
        except Exception as exc:
            print(json.dumps({"status": "BLOCK", "message": f"cannot append review: {exc}"}, ensure_ascii=False))
            return 10

    # The log changed, so any manifest row that locks it must be refreshed by
    # an explicit manifest operation.  We report the exact new hash rather than
    # silently modifying a second file.
    print(
        json.dumps(
            {
                "status": "WARN" if write_enabled else "PASS",
                "message": "review recorded; refresh the manifest hash explicitly" if write_enabled else "review candidate is valid; no file changed",
                "review": entry,
                "log_path": args.log,
                "new_log_sha256": predicted_hash,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    if not root.is_dir():
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "PROJECT_MISSING",
                    "message": f"project directory not found: {root}",
                },
                ensure_ascii=False,
            )
        )
        return 10
    try:
        log_path = safe_project_path(root, args.log)
        fingerprints = parse_fingerprints(args.fingerprint)
    except ValueError as exc:
        print(
            json.dumps(
                {"status": "BLOCK", "code": "ARGUMENT_INVALID", "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 10

    for evidence in args.evidence:
        if not TYPED_ID_RE.fullmatch(evidence):
            print(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "code": "EVIDENCE_ID_INVALID",
                        "message": f"invalid evidence ID: {evidence!r}",
                    },
                    ensure_ascii=False,
                )
            )
            return 10

    if args.decision == "PASS" and (not args.evidence or not fingerprints):
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "PASS_EVIDENCE_REQUIRED",
                    "message": "PASS review requires at least one --evidence and one --fingerprint binding",
                },
                ensure_ascii=False,
            )
        )
        return 10

    if args.dry_run:
        return _process_review(
            args, root, log_path, fingerprints, write_enabled=False
        )

    try:
        # The lock is acquired even when the review log does not exist yet.
        # This serializes competing first creators around the stable sidecar.
        with exclusive_sidecar_lock(
            log_path, timeout_seconds=args.lock_timeout_seconds
        ):
            return _process_review(
                args, root, log_path, fingerprints, write_enabled=True
            )
    except LockTimeoutError as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "LOCK_TIMEOUT",
                    "message": str(exc),
                    "lock_path": str(exc.lock_path),
                    "timeout_seconds": exc.timeout_seconds,
                },
                ensure_ascii=False,
            )
        )
        return 10
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "LOCK_ARGUMENT_INVALID",
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 10
    except OSError as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCK",
                    "code": "LOCK_FAILED",
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 10


if __name__ == "__main__":
    sys.exit(main())
