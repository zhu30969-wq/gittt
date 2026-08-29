"""Adversarial parsing, version, and schema-root regression tests.

The fixtures are intentionally preserved under the system temporary directory
so failures can be inspected without recursive cleanup.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
SCHEMA_ROOT = REPO_ROOT / "cumcm-modeling" / "references" / "schemas"
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from _contract_support import dump_yaml, load_json_strict, load_yaml, sha256_file  # noqa: E402
from audit_project import Audit, extract_metric_value  # noqa: E402
from test_audit_regressions import (  # noqa: E402
    build_promotion_release_project,
    build_release_project,
    finding_codes,
    mutate_yaml,
    refresh_manifest_artifact_hashes,
    run_audit,
)


CREATED_ROOTS: list[Path] = []


def new_root(prefix: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    CREATED_ROOTS.append(root)
    return root


def run_json_command(argv: list[str]) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", *argv],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return completed.returncode, json.loads(completed.stdout)


class ContractParsingTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_CONTRACT_FIXTURES=")
        for root in CREATED_ROOTS:
            print(root)

    def test_json_contract_and_metric_parsers_reject_duplicate_and_nonfinite_values(self) -> None:
        root = new_root("cumcm-contract-json-")
        cases = {
            "duplicate": '{"score": 0.95, "score": 0.10}',
            "nan": '{"score": NaN}',
            "positive-infinity": '{"score": Infinity}',
            "negative-infinity": '{"score": -Infinity}',
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                path = root / f"{label}.json"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_json_strict(path)
                with self.assertRaises(ValueError):
                    extract_metric_value(path, {"type": "json_pointer", "pointer": "/score"})

    def test_yaml_contract_parser_rejects_duplicate_nonfinite_and_recursive_values(self) -> None:
        root = new_root("cumcm-contract-yaml-")
        cases = {
            "duplicate": "score: 0.95\nscore: 0.10\n",
            "nan": "score: .nan\n",
            "infinity": "score: .inf\n",
            "recursive": "loop: &loop [*loop]\n",
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                path = root / f"{label}.yaml"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_yaml(path)

    def test_manifest_writer_rejects_legacy_contract_without_changing_bytes(self) -> None:
        root = new_root("cumcm-legacy-manifest-")
        artifact = root / "spec.yaml"
        artifact.write_text("legacy fixture\n", encoding="utf-8")
        manifest_path = root / "manifest.yaml"
        manifest_path.write_text(
            dump_yaml(
                {
                    "schema_version": "1.9.9",
                    "kind": "manifest",
                    "revision": 7,
                    "artifacts": [
                        {
                            "id": "problem:legacy",
                            "kind": "problem_spec",
                            "path": "spec.yaml",
                            "sha256": "0" * 64,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        before_bytes = manifest_path.read_bytes()
        before_hash = sha256_file(manifest_path)

        code, report = run_json_command(
            [str(SCRIPT_ROOT / "manifest.py"), str(root), "--write"]
        )

        self.assertEqual(10, code, report)
        self.assertIn("SCHEMA_VERSION_UNSUPPORTED", {item["code"] for item in report["findings"]})
        self.assertEqual(before_bytes, manifest_path.read_bytes())
        self.assertEqual(before_hash, sha256_file(manifest_path))
        self.assertEqual(7, load_yaml(manifest_path)["revision"])

    def test_initializer_rejects_legacy_custom_template_before_creating_target(self) -> None:
        root = new_root("cumcm-legacy-template-")
        template = root / "template"
        template.mkdir()
        (template / "problem.yaml").write_text(
            dump_yaml(
                {
                    "schema_version": "1.0.0",
                    "kind": "problem_spec",
                    "id": "problem:legacy",
                }
            ),
            encoding="utf-8",
        )
        (template / "manifest.yaml").write_text(
            dump_yaml(
                {
                    "schema_version": "1.0.0",
                    "kind": "manifest",
                    "artifacts": [
                        {
                            "id": "problem:legacy",
                            "kind": "problem_spec",
                            "path": "problem.yaml",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        target = root / "must-not-exist"

        code, report = run_json_command(
            [
                str(SCRIPT_ROOT / "init_project.py"),
                str(target),
                "--project-id",
                "project:legacy-rejected",
                "--template-root",
                str(template),
            ]
        )

        self.assertEqual(10, code, report)
        self.assertEqual("TEMPLATE_CONTRACT_INVALID", report["code"])
        self.assertFalse(target.exists())

    def test_whitespace_only_promotion_rationale_fails_schema(self) -> None:
        root = build_promotion_release_project()
        mutate_yaml(
            root,
            "specs/model-promotion.yaml",
            lambda document: document.update(rationale=" \t \n "),
        )
        refresh_manifest_artifact_hashes(root)

        report, _audit = run_audit(root)

        self.assertIn("SCHEMA_INVALID", finding_codes(report))
        self.assertNotIn("FALLBACK_PROMOTION_EVENT_VERIFIED", finding_codes(report))

    def test_release_audit_rejects_nonbundled_schema_root(self) -> None:
        root = build_release_project()
        holder = new_root("cumcm-custom-schema-root-")
        custom_schema_root = holder / "schemas"
        shutil.copytree(SCHEMA_ROOT, custom_schema_root)

        audit = Audit(root, custom_schema_root)
        self.assertTrue(audit.load_schemas())
        self.assertFalse(audit.load_manifest())
        self.assertIn("RELEASE_SCHEMA_ROOT_UNTRUSTED", finding_codes(audit.report()))


if __name__ == "__main__":
    unittest.main()
