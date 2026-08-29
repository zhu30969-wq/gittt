"""Shared, deliberately small helpers for CUMCM contract scripts.

The public command-line scripts stay separate because they have different
mutation policies: auditing is read-only, initialization creates only missing
files, and review recording performs one explicit append.  This module keeps
their path, YAML and hash behavior identical without hiding scientific policy
inside a large framework.
"""

from __future__ import annotations

import hashlib
import errno
import json
import math
import os
import re
import stat
import tempfile
import time
import unicodedata
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import yaml


VALIDATION_STATUSES = (
    "PASS",
    "WARN",
    "BLOCK",
    "ENV_BLOCK",
    "STALE",
    "NOT_APPLICABLE",
)

# A larger number means that the status dominates aggregation.  PASS outranks
# NOT_APPLICABLE only so a gate with one real passing check is reported PASS.
STATUS_RANK = {
    "NOT_APPLICABLE": 0,
    "PASS": 1,
    "WARN": 2,
    "STALE": 3,
    "ENV_BLOCK": 4,
    "BLOCK": 5,
}

TYPED_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9._-]*$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
WINDOWS_RESERVED_COMPONENT_RE = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$",
    re.IGNORECASE,
)
WINDOWS_INVALID_PATH_CHAR_RE = re.compile(r'[\x00-\x1f\x7f<>:"|?*]')


class DuplicateKeyError(ValueError):
    """Raised when YAML contains a duplicate mapping key.

    PyYAML normally lets the last value win.  That is unsafe for contracts:
    duplicated thresholds or hashes could make a human review one value while
    the machine consumes another.
    """


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys instead of accepting the last value."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> None:
    """Reject the non-standard NaN/Infinity constants accepted by json.loads."""

    raise ValueError(f"non-finite JSON constant: {token}")


def _finite_json_float(token: str) -> float:
    """Parse a JSON number while rejecting overflow to infinity."""

    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"non-finite JSON number: {token}")
    return value


def load_json_strict(path: Path) -> Any:
    """Read UTF-8 JSON without duplicate keys or non-finite numbers."""

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_construct_unique_json_object,
        parse_constant=_reject_json_constant,
        parse_float=_finite_json_float,
    )


class LockTimeoutError(TimeoutError):
    """Raised when another process keeps a contract sidecar lock too long."""

    def __init__(self, target: Path, lock_path: Path, timeout_seconds: float):
        self.target = target
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"timed out after {timeout_seconds:.3f}s waiting for lock {lock_path}"
        )


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateKeyError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_yaml(path: Path) -> Any:
    """Read one UTF-8 YAML document with deterministic scalar handling.

    YAML admits ``.nan``, infinity and recursive aliases even though JSON,
    reproducible metrics and JSON Schema do not.  Reject them here so every
    command-line tool consumes the same finite, acyclic data model.
    """

    with path.open("r", encoding="utf-8") as handle:
        document = yaml.load(handle, Loader=UniqueKeyLoader)
    _validate_yaml_tree(document, ancestors=set())
    return document


def _validate_yaml_tree(value: Any, *, ancestors: set[int]) -> None:
    """Reject non-finite floats and recursive YAML alias graphs.

    Reusing an alias in separate branches is allowed.  Only encountering the
    same container on its active recursion path is a cycle.
    """

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("YAML contract contains NaN or infinity")
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in ancestors:
            raise ValueError("YAML contract contains a recursive alias")
        ancestors.add(identity)
        if isinstance(value, dict):
            for key, child in value.items():
                _validate_yaml_tree(key, ancestors=ancestors)
                _validate_yaml_tree(child, ancestors=ancestors)
        else:
            for child in value:
                _validate_yaml_tree(child, ancestors=ancestors)
        ancestors.remove(identity)


def dump_yaml(data: Any) -> str:
    """Serialize stable, readable UTF-8 YAML without Python-specific tags."""

    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


def sha256_file(path: Path) -> str:
    """Return a byte-level SHA-256 without loading a large dataset at once."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate_status(statuses: list[str]) -> str:
    """Aggregate only the six contract statuses using the documented order."""

    if not statuses:
        return "NOT_APPLICABLE"
    unknown = [status for status in statuses if status not in STATUS_RANK]
    if unknown:
        raise ValueError(f"unknown validation status: {unknown[0]}")
    return max(statuses, key=STATUS_RANK.__getitem__)


def safe_project_path(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    """Resolve one portable project-relative POSIX path.

    Besides directory escape and symlink escape, reject names whose meaning
    changes on Windows (reserved devices, alternate-data-stream colons,
    trailing dots/spaces), control characters, duplicate separators, and
    non-NFC Unicode.  This keeps a manifest audited on Linux usable on the
    Windows systems common in CUMCM teams.  The single path ``.`` remains
    valid for a declared working directory.  This function creates nothing.
    """

    if not isinstance(relative, str) or not relative:
        raise ValueError("path must be a non-empty string")
    if relative != unicodedata.normalize("NFC", relative):
        raise ValueError(f"path must use NFC-normalized Unicode: {relative!r}")
    if "\\" in relative or re.match(r"^[A-Za-z]:", relative):
        raise ValueError(f"path must use project-relative POSIX syntax: {relative!r}")
    raw_parts = relative.split("/")
    if any(part == "" for part in raw_parts):
        raise ValueError(f"path contains an empty component: {relative!r}")
    for part in raw_parts:
        if part == ".":
            if relative != ".":
                raise ValueError(f"path contains a redundant dot component: {relative!r}")
            continue
        if part == "..":
            raise ValueError(f"path escapes the project root: {relative!r}")
        if part.endswith((" ", ".")):
            raise ValueError(f"path component has a trailing dot or space: {part!r}")
        if WINDOWS_INVALID_PATH_CHAR_RE.search(part):
            raise ValueError(f"path component is not Windows-portable: {part!r}")
        if WINDOWS_RESERVED_COMPONENT_RE.fullmatch(part):
            raise ValueError(f"path uses a Windows reserved device name: {part!r}")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"path escapes the project root: {relative!r}")

    root_resolved = root.resolve()
    candidate = (root_resolved / Path(*pure.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"resolved path escapes the project root: {relative!r}") from exc

    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def write_text_exclusive(path: Path, text: str) -> None:
    """Create a UTF-8 file atomically with respect to an existing filename.

    Opening with ``x`` is the final race-safe guard: even if another process
    creates the file after our earlier existence check, this function refuses
    to overwrite it.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def sidecar_lock_path(target: Path) -> Path:
    """Return the persistent lock filename used by every writer of target.

    The sidecar is deliberately never unlinked.  Removing a lock file can
    split contenders across different inodes on POSIX and different file
    objects on Windows, allowing two processes into the critical section.
    """

    return target.with_name(f".{target.name}.lock")


def _try_lock(handle: Any) -> None:
    """Acquire one non-blocking exclusive byte/file lock."""

    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: Any) -> None:
    """Release the platform lock; closing remains the final release guard."""

    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_lock_contention(error: OSError) -> bool:
    """Distinguish an occupied lock from permanent I/O failures."""

    return error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        error, "winerror", None
    ) in {33, 36, 158}


def _lock_sidecar_is_reparse(metadata: os.stat_result) -> bool:
    """Return whether Windows reports a symlink/junction-like reparse point."""

    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_attribute)


def _validate_lock_sidecar_metadata(
    metadata: os.stat_result, *, lock_path: Path, source: str
) -> None:
    """Reject lock objects that could alias bytes outside the target directory."""

    if stat.S_ISLNK(metadata.st_mode) or _lock_sidecar_is_reparse(metadata):
        raise ValueError(
            f"lock sidecar must not be a symlink or reparse point ({source}): {lock_path}"
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            f"lock sidecar must be a regular file ({source}): {lock_path}"
        )
    if metadata.st_nlink > 1:
        raise ValueError(
            f"lock sidecar must not have multiple hard links ({source}): {lock_path}"
        )


def _verify_open_lock_sidecar(lock_path: Path, handle: Any) -> None:
    """Verify the opened file and current pathname identify one safe object."""

    opened_metadata = os.fstat(handle.fileno())
    _validate_lock_sidecar_metadata(
        opened_metadata, lock_path=lock_path, source="opened handle"
    )
    try:
        path_metadata = lock_path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"lock sidecar disappeared after opening: {lock_path}") from exc
    _validate_lock_sidecar_metadata(
        path_metadata, lock_path=lock_path, source="current path"
    )
    if not os.path.samestat(opened_metadata, path_metadata):
        raise ValueError(
            f"lock sidecar changed between path lookup and open: {lock_path}"
        )


@contextmanager
def exclusive_sidecar_lock(
    target: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    poll_interval_seconds: float = LOCK_POLL_INTERVAL_SECONDS,
) -> Iterator[Path]:
    """Serialize writers through a persistent cross-platform sidecar lock.

    Callers must perform their fresh read, optimistic hash check, candidate
    construction and atomic replace inside this context.  The target itself
    may not exist yet; locking the stable sidecar also serializes first
    creation.
    """

    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("lock timeout must be a finite non-negative number")
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0:
        raise ValueError("lock poll interval must be a finite positive number")

    lock_path = sidecar_lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_metadata = lock_path.lstat()
    except FileNotFoundError:
        existing_metadata = None
    if existing_metadata is not None:
        _validate_lock_sidecar_metadata(
            existing_metadata, lock_path=lock_path, source="pre-open path"
        )

    flags = os.O_RDWR | os.O_CREAT
    for optional_flag in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, optional_flag, 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        if exc.errno == getattr(errno, "ELOOP", None):
            raise ValueError(
                f"lock sidecar must not be a symlink or reparse point: {lock_path}"
            ) from exc
        raise
    handle = os.fdopen(descriptor, "r+b")
    acquired = False
    try:
        # Recheck the opened object before any sentinel write.  The pre-open
        # lstat gives a useful early error, while fstat + samestat closes the
        # lookup/open race and detects hard-link or reparse-point substitution.
        _verify_open_lock_sidecar(lock_path, handle)

        # Windows byte-range locking requires the byte to exist.  Concurrent
        # initializers may both write the same sentinel at offset zero, but
        # every contender consistently locks byte zero and file size stays one.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())

        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                _try_lock(handle)
                acquired = True
                # POSIX permits unlink/replace while the old inode is locked.
                # Refuse to enter the caller's critical section if that has
                # already split the stable pathname from this open handle.
                _verify_open_lock_sidecar(lock_path, handle)
                break
            except OSError as exc:
                if not _is_lock_contention(exc):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeoutError(
                        target, lock_path, timeout_seconds
                    ) from exc
                time.sleep(min(poll_interval_seconds, remaining))
        yield lock_path
    finally:
        if acquired:
            # Closing the descriptor releases the lock even if an explicit
            # unlock fails during interpreter or filesystem teardown.
            with suppress(OSError):
                _unlock(handle)
        handle.close()


def write_yaml_atomic(path: Path, data: Any) -> None:
    """Atomically replace one YAML file after an explicitly requested append.

    Only the named log file is replaced.  On error, at most one explicitly
    known temporary file is unlinked; this function never recursively removes
    directories or batches of user files.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dump_yaml(data))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
