#!/usr/bin/env python3
"""Cross-process regression tests for contract write serialization.

Every test creates a unique directory under the system temporary directory.
The directories, lock sidecars and process outputs are intentionally retained
for inspection; this module never recursively removes a fixture.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "cumcm-modeling" / "scripts"
INIT_PROJECT = SCRIPTS / "init_project.py"
MANIFEST = SCRIPTS / "manifest.py"
RECORD_REVIEW = SCRIPTS / "record_gate_review.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preserved_temp_dir(label: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"cumcm-{label}-"))
    print(f"preserved concurrency fixture: {root}", file=sys.stderr, flush=True)
    return root


def parse_json_output(completed: subprocess.CompletedProcess[str]) -> dict:
    if not completed.stdout:
        raise AssertionError(
            f"process returned no JSON; stderr={completed.stderr!r}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"invalid JSON output: {completed.stdout!r}; stderr={completed.stderr!r}"
        ) from exc


def run_command(command: list[str], *, timeout: float = 20.0) -> tuple[int, dict]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    return completed.returncode, parse_json_output(completed)


def start_command(command: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def finish_command(
    process: subprocess.Popen[str], *, timeout: float = 20.0
) -> tuple[int, dict]:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise AssertionError(
            f"process exceeded {timeout}s; stdout={stdout!r}; stderr={stderr!r}"
        )
    completed = subprocess.CompletedProcess(
        process.args, process.returncode, stdout, stderr
    )
    return process.returncode, parse_json_output(completed)


def start_lock_holder(
    target: Path, *, hold_seconds: float | None
) -> tuple[subprocess.Popen[str], Path | None]:
    """Hold the production sidecar lock and signal after acquisition.

    A finite ``hold_seconds`` provides a short barrier for the two-writer
    race tests.  ``None`` switches to an explicit release file so the timeout
    test does not depend on Python/import startup speed on the host machine.
    """

    ready = target.parent / (
        f".{target.name}.holder-{uuid.uuid4().hex[:8]}.ready"
    )
    release = (
        target.parent / f".{target.name}.holder-{uuid.uuid4().hex[:8]}.release"
        if hold_seconds is None
        else None
    )
    code = """
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from _contract_support import exclusive_sidecar_lock

target = Path(sys.argv[2])
ready = Path(sys.argv[3])
hold_mode = sys.argv[4]
release = Path(sys.argv[5]) if sys.argv[5] else None
with exclusive_sidecar_lock(target, timeout_seconds=2.0):
    ready.write_text("locked", encoding="utf-8")
    if hold_mode == "until-release":
        # Bound the helper lifetime so a failed parent test cannot leave an
        # indefinitely running process.  The normal path releases immediately
        # after the contending writer returns.
        deadline = time.monotonic() + 30.0
        while release is not None and not release.is_file():
            if time.monotonic() >= deadline:
                raise TimeoutError("test lock holder release signal was not written")
            time.sleep(0.01)
    else:
        time.sleep(float(hold_mode))
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            code,
            str(SCRIPTS),
            str(target),
            str(ready),
            "until-release" if hold_seconds is None else str(hold_seconds),
            "" if release is None else str(release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    deadline = time.monotonic() + 5.0
    while not ready.is_file():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"lock holder exited before ready; stdout={stdout!r}; stderr={stderr!r}"
            )
        if time.monotonic() >= deadline:
            process.kill()
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"lock holder was not ready; stdout={stdout!r}; stderr={stderr!r}"
            )
        time.sleep(0.01)
    return process, release


def wait_for_holder(process: subprocess.Popen[str]) -> None:
    try:
        stdout, stderr = process.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise AssertionError(
            f"lock holder did not exit; stdout={stdout!r}; stderr={stderr!r}"
        )
    if process.returncode != 0:
        raise AssertionError(
            f"lock holder failed; stdout={stdout!r}; stderr={stderr!r}"
        )


def review_command(
    root: Path, review_id: str, *extra: str
) -> list[str]:
    return [
        sys.executable,
        "-X",
        "utf8",
        str(RECORD_REVIEW),
        str(root),
        "--gate",
        "G1",
        "--decision",
        "WARN",
        "--approval-set-id",
        f"approval:{review_id.split(':', 1)[1]}",
        "--member-id",
        "member:concurrency-reviewer",
        "--member-name",
        "Concurrency Reviewer",
        "--member-role",
        "modeling",
        "--reviewer",
        "Concurrency Reviewer",
        "--rationale",
        f"concurrency test for {review_id}",
        "--review-id",
        review_id,
        *extra,
    ]


class WriteConcurrencyTests(unittest.TestCase):
    def test_unconditional_first_creation_appends_preserve_both_reviews(
        self,
    ) -> None:
        root = preserved_temp_dir("append")
        log_path = root / "reviews" / "gate-reviews.yaml"
        holder, _ = start_lock_holder(log_path, hold_seconds=0.5)

        first = start_command(
            review_command(root, "review:concurrent-first")
        )
        second = start_command(
            review_command(root, "review:concurrent-second")
        )
        wait_for_holder(holder)
        results = [finish_command(first), finish_command(second)]

        self.assertEqual([code for code, _ in results], [0, 0], results)
        self.assertEqual(
            {report["status"] for _, report in results}, {"WARN"}, results
        )
        document = yaml.safe_load(log_path.read_text(encoding="utf-8"))
        review_ids = {item["id"] for item in document["reviews"]}
        self.assertEqual(
            review_ids,
            {"review:concurrent-first", "review:concurrent-second"},
        )
        self.assertEqual(document["revision"], 3)
        self.assertTrue(
            log_path.with_name(f".{log_path.name}.lock").is_file()
        )

    def test_same_expected_review_hash_has_one_success_and_one_stale(
        self,
    ) -> None:
        root = preserved_temp_dir("review-cas")
        log_path = root / "reviews" / "gate-reviews.yaml"
        initial_code, initial_report = run_command(
            review_command(root, "review:initial")
        )
        self.assertEqual(initial_code, 0, initial_report)
        expected = sha256_file(log_path)

        holder, _ = start_lock_holder(log_path, hold_seconds=0.5)
        first = start_command(
            review_command(
                root,
                "review:expected-first",
                "--expected-log-sha256",
                expected,
            )
        )
        second = start_command(
            review_command(
                root,
                "review:expected-second",
                "--expected-log-sha256",
                expected,
            )
        )
        wait_for_holder(holder)
        results = [finish_command(first), finish_command(second)]

        self.assertEqual(sorted(code for code, _ in results), [0, 12], results)
        self.assertEqual(
            {report["status"] for _, report in results},
            {"WARN", "STALE"},
            results,
        )
        stale = next(
            report for _, report in results if report["status"] == "STALE"
        )
        self.assertEqual(stale["code"], "REVIEW_LOG_CHANGED")

        document = yaml.safe_load(log_path.read_text(encoding="utf-8"))
        ids = {item["id"] for item in document["reviews"]}
        self.assertIn("review:initial", ids)
        self.assertEqual(
            len(
                ids
                & {"review:expected-first", "review:expected-second"}
            ),
            1,
        )
        self.assertEqual(document["revision"], 3)

    def test_same_expected_manifest_hash_has_one_success_and_one_stale(
        self,
    ) -> None:
        root = preserved_temp_dir("manifest-cas")
        init_code, init_report = run_command(
            [
                sys.executable,
                "-X",
                "utf8",
                str(INIT_PROJECT),
                str(root),
                "--project-id",
                "project:concurrency-test",
            ]
        )
        self.assertEqual(init_code, 0, init_report)

        problem = root / "specs" / "problem_spec.yaml"
        with problem.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n# force a manifest hash refresh\n")
        manifest_path = root / "manifest.yaml"
        expected = sha256_file(manifest_path)

        holder, _ = start_lock_holder(manifest_path, hold_seconds=0.5)
        command = [
            sys.executable,
            "-X",
            "utf8",
            str(MANIFEST),
            str(root),
            "--write",
            "--expected-manifest-sha256",
            expected,
        ]
        first = start_command(command)
        second = start_command(command)
        wait_for_holder(holder)
        results = [finish_command(first), finish_command(second)]

        self.assertEqual(sorted(code for code, _ in results), [0, 12], results)
        self.assertEqual(
            {report["status"] for _, report in results},
            {"PASS", "STALE"},
            results,
        )
        stale = next(
            report for _, report in results if report["status"] == "STALE"
        )
        self.assertIn(
            "MANIFEST_CHANGED",
            {finding["code"] for finding in stale["findings"]},
        )
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["revision"], 2)
        self.assertTrue(
            manifest_path.with_name(f".{manifest_path.name}.lock").is_file()
        )

    def test_lock_timeout_returns_structured_block(self) -> None:
        root = preserved_temp_dir("lock-timeout")
        log_path = root / "reviews" / "gate-reviews.yaml"
        holder, release = start_lock_holder(log_path, hold_seconds=None)
        self.assertIsNotNone(release)
        try:
            code, report = run_command(
                review_command(
                    root,
                    "review:timeout",
                    "--lock-timeout-seconds",
                    "0.1",
                )
            )
        finally:
            # The holder cannot release before the writer has either timed out
            # or exposed a broken lock by returning success.
            assert release is not None
            release.write_text("release", encoding="utf-8")
            wait_for_holder(holder)

        self.assertEqual(code, 10, report)
        self.assertEqual(report["status"], "BLOCK", report)
        self.assertEqual(report["code"], "LOCK_TIMEOUT", report)
        self.assertAlmostEqual(report["timeout_seconds"], 0.1)
        self.assertFalse(log_path.exists())
        self.assertTrue(
            log_path.with_name(f".{log_path.name}.lock").is_file()
        )


if __name__ == "__main__":
    unittest.main()
