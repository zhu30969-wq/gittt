"""Initializer rendering and result-time semantic regressions.

Every fixture is preserved under the system temporary directory for manual
inspection.  The tests never recursively delete generated projects.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from _contract_support import load_yaml, sha256_file  # noqa: E402
from test_audit_regressions import (  # noqa: E402
    build_release_project,
    finding_codes,
    mutate_yaml,
    run_audit,
)


CREATED_ROOTS: list[Path] = []

# Compatibility exemption: this literal deliberately remains at 2.0.0 after
# current generators move to 2.1.0.  It proves that a valid 2.0.x predecessor
# remains readable and is never rewritten by a no-op initialization.
LEGACY_2_0_COMPAT_VERSION = "2.0.0"

# Compatibility fixture for the last 2.0.x contract generated before
# decision_timing became mandatory in 2.1.0.
LEGACY_2_0_1_COMPAT_VERSION = "2.0.1"


def new_target(prefix: str) -> Path:
    holder = Path(tempfile.mkdtemp(prefix=prefix))
    CREATED_ROOTS.append(holder)
    return holder / "project"


def run_initializer(target: Path, *extra: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(SCRIPT_ROOT / "init_project.py"),
            str(target),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return completed.returncode, json.loads(completed.stdout)


def run_auditor(target: Path) -> tuple[int, dict]:
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(SCRIPT_ROOT / "audit_project.py"),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return completed.returncode, json.loads(completed.stdout)


def snapshot_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    }


class InitializationAndRunTimeTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_INITIALIZATION_FIXTURES=")
        for root in CREATED_ROOTS:
            print(root)

    def test_new_project_requires_explicit_year_without_creating_target(self) -> None:
        target = new_target("cumcm-init-year-required-")
        before = snapshot_tree(target)

        code, report = run_initializer(
            target,
            "--project-id",
            "project:year-required",
        )

        self.assertEqual(10, code, report)
        self.assertEqual("CONTEST_YEAR_REQUIRED", report.get("code"), report)
        self.assertFalse(target.exists())
        self.assertEqual(before, snapshot_tree(target))

    def test_initializer_renders_year_code_seed_and_paper_engine(self) -> None:
        target = new_target("cumcm-init-render-")
        before = snapshot_tree(target)

        code, report = run_initializer(
            target,
            "--project-id",
            "project:render-2034-c",
            "--contest-year",
            "2034",
            "--problem-code",
            "C",
            "--default-seed",
            "8675309",
            "--paper-engine",
            "typst",
        )

        self.assertEqual(0, code, report)
        self.assertEqual("PASS", report.get("status"), report)
        self.assertNotIn("code", report)
        self.assertTrue(target.is_dir())
        after = snapshot_tree(target)
        self.assertEqual({}, before)
        self.assertEqual(9, len(after))
        problem = load_yaml(target / "specs" / "problem_spec.yaml")
        experiment = load_yaml(target / "experiments" / "experiment.yaml")
        result = load_yaml(target / "results" / "results.yaml")
        manifest = load_yaml(target / "manifest.yaml")
        self.assertEqual(2034, problem["contest"]["year"])
        self.assertEqual("C", problem["contest"]["problem_code"])
        self.assertEqual([8675309], experiment["seeds"])
        self.assertEqual("here_and_now", experiment["decision_timing"])
        self.assertEqual([8675309], result["run"]["seeds"])
        self.assertIsNone(result["run"]["started_at"])
        self.assertIsNone(result["run"]["finished_at"])
        self.assertEqual(
            {
                "contest_year": 2034,
                "problem_code": "C",
                "default_seed": 8675309,
                "paper_engine": "typst",
            },
            manifest["extensions"]["initialization"],
        )
        for artifact in manifest["artifacts"]:
            self.assertEqual(
                sha256_file(target / artifact["path"]),
                artifact["sha256"],
            )

    def test_zero_default_seed_is_preserved_as_an_explicit_value(self) -> None:
        """A falsey integer is a real seed and must not fall back to 42."""

        target = new_target("cumcm-init-zero-seed-")
        before = snapshot_tree(target)

        code, report = run_initializer(
            target,
            "--project-id",
            "project:zero-seed",
            "--contest-year",
            "2034",
            "--default-seed",
            "0",
        )

        self.assertEqual(0, code, report)
        self.assertEqual("PASS", report.get("status"), report)
        self.assertNotIn("code", report)
        self.assertTrue(target.is_dir())
        after = snapshot_tree(target)
        self.assertEqual({}, before)
        self.assertEqual(9, len(after))
        manifest = load_yaml(target / "manifest.yaml")
        experiment = load_yaml(target / "experiments" / "experiment.yaml")
        result = load_yaml(target / "results" / "results.yaml")
        self.assertEqual(0, manifest["extensions"]["initialization"]["default_seed"])
        self.assertEqual([0], experiment["seeds"])
        self.assertEqual([0], result["run"]["seeds"])

    def test_repeat_initializer_needs_no_parameters_and_preserves_bytes(self) -> None:
        target = new_target("cumcm-init-repeat-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:repeat-safe",
            "--contest-year",
            "2035",
            "--default-seed",
            "12345",
        )
        self.assertEqual(0, first_code, first_report)
        self.assertEqual("PASS", first_report.get("status"), first_report)
        self.assertNotIn("code", first_report)
        self.assertTrue(target.is_dir())
        before = snapshot_tree(target)

        second_code, second_report = run_initializer(target)

        self.assertEqual(0, second_code, second_report)
        self.assertEqual("PASS", second_report.get("status"), second_report)
        self.assertNotIn("code", second_report)
        self.assertTrue(target.is_dir())
        self.assertTrue(second_report["findings"])
        self.assertTrue(
            all(item["status"] == "NOT_APPLICABLE" for item in second_report["findings"])
        )
        self.assertEqual(before, snapshot_tree(target))

    def test_repeat_initializer_rejects_explicit_conflict_without_writing(self) -> None:
        target = new_target("cumcm-init-conflict-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:conflict-safe",
            "--contest-year",
            "2035",
            "--problem-code",
            "A",
            "--default-seed",
            "12345",
            "--paper-engine",
            "latex",
        )
        self.assertEqual(0, first_code, first_report)
        self.assertEqual("PASS", first_report.get("status"), first_report)
        self.assertNotIn("code", first_report)
        self.assertTrue(target.is_dir())
        before = snapshot_tree(target)

        second_code, second_report = run_initializer(
            target,
            "--contest-year",
            "2036",
        )

        self.assertEqual(10, second_code, second_report)
        self.assertEqual(
            "INITIALIZATION_PARAMETER_CONFLICT",
            second_report.get("code"),
            second_report,
        )
        self.assertEqual(
            {"existing": 2035, "requested": 2036},
            second_report["conflicts"]["contest_year"],
        )
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))

    def test_complete_project_rejects_unverifiable_explicit_paper_engine(self) -> None:
        """A1: a preserved project must not silently ignore an unrecorded request."""

        target = new_target("cumcm-init-unverifiable-engine-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:unverifiable-engine",
            "--contest-year",
            "2035",
            "--paper-engine",
            "latex",
        )
        self.assertEqual(0, first_code, first_report)

        def remove_engine(document: dict) -> None:
            document["extensions"]["initialization"].pop("paper_engine")
            # A similarly named entrypoint must not be guessed as the paper
            # entrypoint; only the exact key "paper" is an engine source.
            document["entrypoints"]["manuscript"] = "paper/main.tex"

        mutate_yaml(target, "manifest.yaml", remove_engine)
        before = snapshot_tree(target)

        code, report = run_initializer(target, "--paper-engine", "typst")

        self.assertEqual(10, code, report)
        self.assertEqual(
            "INITIALIZATION_PARAMETER_UNVERIFIABLE",
            report.get("code"),
            report,
        )
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))

    def test_blank_problem_code_is_rejected_before_target_creation(self) -> None:
        """A2: an explicit empty string is invalid, not a request for a default."""

        target = new_target("cumcm-init-blank-code-")
        before = snapshot_tree(target)

        code, report = run_initializer(
            target,
            "--project-id",
            "project:blank-code",
            "--contest-year",
            "2035",
            "--problem-code",
            "",
        )

        self.assertEqual(10, code, report)
        self.assertEqual("INITIALIZATION_PARAMETER_INVALID", report.get("code"), report)
        self.assertFalse(target.exists())
        self.assertEqual(before, snapshot_tree(target))

    def test_non_yaml_utf8_template_placeholders_are_rendered(self) -> None:
        """A3: custom UTF-8 text templates consume the same initialization values."""

        target = new_target("cumcm-init-text-template-")
        template = target.parent / "template"
        shutil.copytree(
            REPO_ROOT / "cumcm-modeling" / "assets" / "project-template",
            template,
        )
        text_template = template / "initialization.txt"
        text_template.write_text(
            "\n".join(
                [
                    "project=__PROJECT_ID__",
                    "year=__CONTEST_YEAR__",
                    "problem=__PROBLEM_CODE__",
                    "seed=__DEFAULT_SEED__",
                    "engine=__PAPER_ENGINE__",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        binary_payload = b"\x00\xff__PROJECT_ID__\x80"
        (template / "opaque.bin").write_bytes(binary_payload)
        before = snapshot_tree(target)

        code, report = run_initializer(
            target,
            "--project-id",
            "project:text-template",
            "--contest-year",
            "2037",
            "--problem-code",
            "D",
            "--default-seed",
            "31415",
            "--paper-engine",
            "typst",
            "--template-root",
            str(template),
        )

        self.assertEqual(0, code, report)
        self.assertTrue(target.is_dir())
        self.assertEqual(before, {})
        self.assertNotEqual(before, snapshot_tree(target))
        rendered = (target / "initialization.txt").read_text(encoding="utf-8")
        self.assertEqual(
            "project=project:text-template\n"
            "year=2037\n"
            "problem=D\n"
            "seed=31415\n"
            "engine=typst\n",
            rendered,
        )
        self.assertNotRegex(rendered, r"__[A-Z0-9_]+__")
        self.assertEqual(binary_payload, (target / "opaque.bin").read_bytes())
        binary_finding = next(
            item for item in report["findings"] if item["path"] == "opaque.bin"
        )
        self.assertEqual("PASS", binary_finding["status"])
        self.assertEqual(
            "created by copying binary or non-UTF-8 bytes without rendering",
            binary_finding["message"],
        )
        self.assertNotIn("code", report)

    def test_conflicting_existing_sources_are_all_reported_without_writing(self) -> None:
        """A4: every value and provenance entry participates in conflict checks."""

        target = new_target("cumcm-init-source-conflict-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:source-conflict",
            "--contest-year",
            "2035",
            "--default-seed",
            "101",
        )
        self.assertEqual(0, first_code, first_report)
        mutate_yaml(
            target,
            "specs/problem_spec.yaml",
            lambda document: document["contest"].update(year=2036),
        )
        mutate_yaml(
            target,
            "results/results.yaml",
            lambda document: document["run"].update(seeds=[202]),
        )
        before = snapshot_tree(target)

        code, report = run_initializer(target)

        self.assertEqual(10, code, report)
        self.assertEqual("EXISTING_INITIALIZATION_INVALID", report.get("code"), report)
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))
        self.assertEqual(
            [
                {
                    "value": 2035,
                    "source_path": "manifest.yaml",
                    "source_field": "extensions.initialization.contest_year",
                },
                {
                    "value": 2036,
                    "source_path": "specs/problem_spec.yaml",
                    "source_field": "contest.year",
                },
            ],
            report["source_conflicts"]["contest_year"],
        )
        self.assertEqual(
            [
                {
                    "value": 101,
                    "source_path": "manifest.yaml",
                    "source_field": "extensions.initialization.default_seed",
                },
                {
                    "value": 101,
                    "source_path": "experiments/experiment.yaml",
                    "source_field": "seeds[0]",
                },
                {
                    "value": 202,
                    "source_path": "results/results.yaml",
                    "source_field": "run.seeds[0]",
                },
            ],
            report["source_conflicts"]["default_seed"],
        )

    def test_dry_run_new_target_reports_every_creation_without_writing(self) -> None:
        """B1: dry-run is a read-only projection of all missing template files."""

        target = new_target("cumcm-init-dry-run-")
        before = snapshot_tree(target)

        code, report = run_initializer(
            target,
            "--project-id",
            "project:dry-run",
            "--contest-year",
            "2038",
            "--dry-run",
        )

        self.assertEqual(0, code, report)
        self.assertEqual("PASS", report.get("status"), report)
        self.assertNotIn("code", report)
        self.assertFalse(target.exists())
        self.assertEqual(before, snapshot_tree(target))
        self.assertTrue(report["findings"])
        expected_paths = {
            path.relative_to(
                REPO_ROOT / "cumcm-modeling" / "assets" / "project-template"
            ).as_posix()
            for path in (
                REPO_ROOT / "cumcm-modeling" / "assets" / "project-template"
            ).rglob("*")
            if path.is_file()
        }
        self.assertEqual(len(expected_paths), len(report["findings"]))
        self.assertEqual(expected_paths, {item["path"] for item in report["findings"]})
        self.assertTrue(
            all(
                item["status"] == "PASS"
                and item["message"] == "would create missing file"
                for item in report["findings"]
            ),
            report,
        )

    def test_paper_entrypoint_inference_rejects_engine_conflict(self) -> None:
        """C1: only entrypoints.paper with a known suffix can recover the engine."""

        target = new_target("cumcm-init-paper-inference-conflict-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:paper-inference-conflict",
            "--contest-year",
            "2038",
            "--paper-engine",
            "latex",
        )
        self.assertEqual(0, first_code, first_report)

        def use_entrypoint_only(document: dict) -> None:
            document["extensions"]["initialization"].pop("paper_engine")
            document["entrypoints"]["paper"] = "paper/main.tex"

        mutate_yaml(target, "manifest.yaml", use_entrypoint_only)
        before = snapshot_tree(target)

        code, report = run_initializer(target, "--paper-engine", "typst")

        self.assertEqual(10, code, report)
        self.assertEqual("INITIALIZATION_PARAMETER_CONFLICT", report.get("code"), report)
        self.assertEqual(
            {"existing": "latex", "requested": "typst"},
            report["conflicts"]["paper_engine"],
        )
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))

    def test_incomplete_project_rejects_unconsumed_explicit_parameter(self) -> None:
        """C: a missing unrelated file cannot make an unrecorded request verifiable."""

        target = new_target("cumcm-init-incomplete-unconsumed-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:incomplete-unconsumed",
            "--contest-year",
            "2038",
        )
        self.assertEqual(0, first_code, first_report)

        def remove_engine(document: dict) -> None:
            document["extensions"]["initialization"].pop("paper_engine")

        mutate_yaml(target, "manifest.yaml", remove_engine)
        (target / ".gitignore").unlink()
        before = snapshot_tree(target)

        code, report = run_initializer(target, "--paper-engine", "typst")

        self.assertEqual(10, code, report)
        self.assertEqual(
            "INITIALIZATION_PARAMETER_UNVERIFIABLE",
            report.get("code"),
            report,
        )
        self.assertTrue(target.is_dir())
        self.assertFalse((target / ".gitignore").exists())
        self.assertEqual(before, snapshot_tree(target))

    def test_missing_manifest_consumes_explicit_unrecorded_parameters(self) -> None:
        """C: explicit values may be used when a missing template truly consumes them."""

        target = new_target("cumcm-init-missing-manifest-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:missing-manifest",
            "--contest-year",
            "2038",
            "--paper-engine",
            "latex",
        )
        self.assertEqual(0, first_code, first_report)
        (target / "manifest.yaml").unlink()
        before = snapshot_tree(target)

        code, report = run_initializer(
            target,
            "--project-id",
            "project:missing-manifest",
            "--paper-engine",
            "typst",
        )

        self.assertEqual(0, code, report)
        self.assertEqual("PASS", report.get("status"), report)
        self.assertNotIn("code", report)
        self.assertTrue(target.is_dir())
        after = snapshot_tree(target)
        self.assertEqual(before, {key: value for key, value in after.items() if key != "manifest.yaml"})
        self.assertEqual(set(before) | {"manifest.yaml"}, set(after))
        manifest = load_yaml(target / "manifest.yaml")
        self.assertEqual("project:missing-manifest", manifest["project_id"])
        self.assertEqual(
            "typst",
            manifest["extensions"]["initialization"]["paper_engine"],
        )

    def test_paper_entrypoint_inference_allows_matching_noop(self) -> None:
        """C2: a matching inferred engine is a byte-preserving complete no-op."""

        target = new_target("cumcm-init-paper-inference-match-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:paper-inference-match",
            "--contest-year",
            "2038",
            "--paper-engine",
            "latex",
        )
        self.assertEqual(0, first_code, first_report)

        def use_entrypoint_only(document: dict) -> None:
            document["extensions"]["initialization"].pop("paper_engine")
            document["entrypoints"]["paper"] = "paper/main.tex"

        mutate_yaml(target, "manifest.yaml", use_entrypoint_only)
        before = snapshot_tree(target)

        code, report = run_initializer(target, "--paper-engine", "latex")

        self.assertEqual(0, code, report)
        self.assertEqual("PASS", report.get("status"), report)
        self.assertNotIn("code", report)
        self.assertTrue(target.is_dir())
        self.assertTrue(report["findings"])
        self.assertTrue(
            all(item["status"] == "NOT_APPLICABLE" for item in report["findings"]),
            report,
        )
        self.assertEqual(before, snapshot_tree(target))

    def test_recorded_and_inferred_paper_engines_must_agree(self) -> None:
        """C3: mutually inconsistent recorded sources invalidate the project."""

        target = new_target("cumcm-init-paper-source-conflict-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:paper-source-conflict",
            "--contest-year",
            "2038",
            "--paper-engine",
            "typst",
        )
        self.assertEqual(0, first_code, first_report)
        mutate_yaml(
            target,
            "manifest.yaml",
            lambda document: document["entrypoints"].update(
                paper="paper/main.tex"
            ),
        )
        before = snapshot_tree(target)

        code, report = run_initializer(target)

        self.assertEqual(10, code, report)
        self.assertEqual("EXISTING_INITIALIZATION_INVALID", report.get("code"), report)
        self.assertEqual(
            [
                {
                    "value": "typst",
                    "source_path": "manifest.yaml",
                    "source_field": "extensions.initialization.paper_engine",
                },
                {
                    "value": "latex",
                    "source_path": "manifest.yaml",
                    "source_field": "entrypoints.paper",
                },
            ],
            report["source_conflicts"]["paper_engine"],
        )
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))

    def test_legacy_2_0_0_project_remains_noop_and_auditable(self) -> None:
        """C4: explicit 2.0.0 compatibility fixture; exempt from version replacement."""

        target = new_target("cumcm-init-legacy-2-0-0-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:legacy-2-0-0",
            "--contest-year",
            "2038",
        )
        self.assertEqual(0, first_code, first_report)

        manifest = load_yaml(target / "manifest.yaml")
        for artifact in manifest["artifacts"]:
            mutate_yaml(
                target,
                artifact["path"],
                lambda document: document.update(
                    schema_version=LEGACY_2_0_COMPAT_VERSION
                ),
            )
        mutate_yaml(
            target,
            "manifest.yaml",
            lambda document: document.update(
                schema_version=LEGACY_2_0_COMPAT_VERSION
            ),
        )
        before = snapshot_tree(target)

        code, report = run_initializer(target)

        self.assertEqual(0, code, report)
        self.assertEqual("PASS", report.get("status"), report)
        self.assertNotIn("code", report)
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))
        self.assertTrue(
            all(item["status"] == "NOT_APPLICABLE" for item in report["findings"]),
            report,
        )

        audit_code, audit_report = run_auditor(target)
        self.assertEqual(10, audit_code, audit_report)
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))
        self.assertEqual("BLOCK", audit_report.get("status"), audit_report)
        self.assertNotIn("SCHEMA_INVALID", finding_codes(audit_report))
        self.assertNotIn("SCHEMA_VERSION_UNSUPPORTED", finding_codes(audit_report))
        self.assertNotIn("FILE_HASH_MISMATCH", finding_codes(audit_report))
        self.assertNotIn("ARTIFACT_HASH_MISMATCH", finding_codes(audit_report))

    def test_legacy_2_0_1_without_decision_timing_is_noop_and_auditable(self) -> None:
        """C5: pre-2.1 experiment omission is a semantic finding, not a parse failure."""

        target = new_target("cumcm-init-legacy-2-0-1-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:legacy-2-0-1",
            "--contest-year",
            "2038",
        )
        self.assertEqual(0, first_code, first_report)

        def downgrade_contract(document: dict) -> None:
            document["schema_version"] = LEGACY_2_0_1_COMPAT_VERSION
            if document.get("kind") == "experiment":
                document.pop("decision_timing", None)

        manifest = load_yaml(target / "manifest.yaml")
        for artifact in manifest["artifacts"]:
            mutate_yaml(target, artifact["path"], downgrade_contract)
        mutate_yaml(
            target,
            "manifest.yaml",
            lambda document: document.update(
                schema_version=LEGACY_2_0_1_COMPAT_VERSION
            ),
        )
        before = snapshot_tree(target)

        code, report = run_initializer(target)

        self.assertEqual(0, code, report)
        self.assertEqual("PASS", report.get("status"), report)
        self.assertNotIn("code", report)
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))
        self.assertTrue(
            all(item["status"] == "NOT_APPLICABLE" for item in report["findings"]),
            report,
        )

        audit_code, audit_report = run_auditor(target)
        codes = finding_codes(audit_report)
        timing_findings = [
            finding
            for gate in audit_report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "DECISION_TIMING_REQUIRED"
        ]
        self.assertEqual(10, audit_code, audit_report)
        self.assertEqual("BLOCK", audit_report.get("status"), audit_report)
        self.assertEqual(before, snapshot_tree(target))
        self.assertEqual(1, len(timing_findings), timing_findings)
        self.assertEqual("G3", timing_findings[0]["gate"])
        self.assertEqual("experiment:main", timing_findings[0].get("artifact_id"))
        self.assertNotIn("SCHEMA_INVALID", codes)
        self.assertNotIn("SCHEMA_VERSION_UNSUPPORTED", codes)
        self.assertNotIn("AUDIT_INTERNAL_ERROR", codes)
        self.assertNotIn("FILE_HASH_MISMATCH", codes)
        self.assertNotIn("ARTIFACT_HASH_MISMATCH", codes)

    def test_current_2_1_0_experiment_still_requires_decision_timing_in_schema(self) -> None:
        """The legacy exception must not weaken newly generated 2.1 contracts."""

        target = new_target("cumcm-init-current-decision-timing-")
        first_code, first_report = run_initializer(
            target,
            "--project-id",
            "project:current-decision-timing",
            "--contest-year",
            "2038",
        )
        self.assertEqual(0, first_code, first_report)
        experiment = load_yaml(target / "experiments" / "experiment.yaml")
        self.assertEqual("2.1.0", experiment["schema_version"])

        mutate_yaml(
            target,
            "experiments/experiment.yaml",
            lambda document: document.pop("decision_timing"),
        )
        before = snapshot_tree(target)

        audit_code, audit_report = run_auditor(target)

        self.assertEqual(10, audit_code, audit_report)
        self.assertEqual("BLOCK", audit_report.get("status"), audit_report)
        self.assertEqual(before, snapshot_tree(target))
        self.assertIn("SCHEMA_INVALID", finding_codes(audit_report))
        self.assertNotIn("AUDIT_INTERNAL_ERROR", finding_codes(audit_report))

    def test_partial_placeholder_allows_null_timestamps(self) -> None:
        target = new_target("cumcm-init-null-time-")
        code, init_report = run_initializer(
            target,
            "--project-id",
            "project:null-time",
            "--contest-year",
            "2036",
        )
        self.assertEqual(0, code, init_report)
        self.assertEqual("PASS", init_report.get("status"), init_report)
        self.assertNotIn("code", init_report)
        self.assertTrue(target.is_dir())
        before = snapshot_tree(target)

        audit_code, report = run_auditor(target)

        self.assertEqual(10, audit_code, report)
        self.assertTrue(target.is_dir())
        self.assertEqual(before, snapshot_tree(target))
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertNotIn("RUN_TIME_INVALID", finding_codes(report))

    def test_success_result_without_timestamps_is_semantically_blocked(self) -> None:
        root = build_release_project()

        mutate_yaml(
            root,
            "results/results.yaml",
            lambda document: document["run"].update(
                started_at=None,
                finished_at=None,
            ),
        )
        before = snapshot_tree(root)
        code, report = run_auditor(root)

        self.assertEqual(10, code, report)
        self.assertTrue(root.is_dir())
        self.assertEqual(before, snapshot_tree(root))
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertIn("RUN_TIME_INVALID", finding_codes(report))

    def test_failed_result_without_timestamps_is_semantically_blocked(self) -> None:
        root = build_release_project()

        def make_failed(document: dict) -> None:
            document["run_status"] = "failed"
            document["failure_reason"] = "Synthetic failed-run history."
            document["run"]["started_at"] = None
            document["run"]["finished_at"] = None
            document["run"]["exit_code"] = 1

        mutate_yaml(root, "results/results.yaml", make_failed)
        before = snapshot_tree(root)
        code, report = run_auditor(root)

        self.assertEqual(10, code, report)
        self.assertTrue(root.is_dir())
        self.assertEqual(before, snapshot_tree(root))
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertIn("RUN_TIME_INVALID", finding_codes(report))

    def test_finished_at_before_started_at_is_semantically_blocked(self) -> None:
        root = build_release_project()

        mutate_yaml(
            root,
            "results/results.yaml",
            lambda document: document["run"].update(
                started_at="2026-08-27T00:00:02Z",
                finished_at="2026-08-27T00:00:01Z",
            ),
        )
        before = snapshot_tree(root)
        code, report = run_auditor(root)

        self.assertEqual(10, code, report)
        self.assertTrue(root.is_dir())
        self.assertEqual(before, snapshot_tree(root))
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertIn("RUN_TIME_INVALID", finding_codes(report))

    def test_half_populated_run_interval_is_semantically_blocked(self) -> None:
        """B2: one timestamp without the other is never a valid run interval."""

        root = build_release_project()
        mutate_yaml(
            root,
            "results/results.yaml",
            lambda document: document["run"].update(finished_at=None),
        )
        before = snapshot_tree(root)

        code, report = run_auditor(root)

        self.assertEqual(10, code, report)
        self.assertTrue(root.is_dir())
        self.assertEqual(before, snapshot_tree(root))
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertIn("RUN_TIME_INVALID", finding_codes(report))

    def test_future_run_interval_is_semantically_blocked(self) -> None:
        """B2: a finished_at far beyond the audit clock is not credible evidence."""

        root = build_release_project()
        mutate_yaml(
            root,
            "results/results.yaml",
            lambda document: document["run"].update(
                started_at="2999-01-01T00:00:00Z",
                finished_at="2999-01-01T00:00:01Z",
            ),
        )
        before = snapshot_tree(root)

        code, report = run_auditor(root)

        self.assertEqual(10, code, report)
        self.assertTrue(root.is_dir())
        self.assertEqual(before, snapshot_tree(root))
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertIn("RUN_TIME_INVALID", finding_codes(report))

    def test_promotion_trigger_partial_without_timestamps_is_blocked(self) -> None:
        """B2: an actually executed promotion-trigger partial needs real times."""

        from test_audit_regressions import (
            build_promotion_release_project,
            resign_release_project,
        )

        root = build_promotion_release_project()
        mutate_yaml(
            root,
            "results/results.yaml",
            lambda document: document["run"].update(
                started_at=None,
                finished_at=None,
            ),
        )
        # Keep the immutable promotion and downstream review receipts current;
        # the only intended defect in this fixture is the null/null run frame.
        resign_release_project(root)
        before = snapshot_tree(root)

        code, report = run_auditor(root)

        self.assertEqual(10, code, report)
        self.assertTrue(root.is_dir())
        self.assertEqual(before, snapshot_tree(root))
        self.assertNotIn("SCHEMA_INVALID", finding_codes(report))
        self.assertEqual("BLOCK", report.get("status"), report)
        self.assertIn("PROMOTION_TIME_INVALID", finding_codes(report))


if __name__ == "__main__":
    unittest.main()
