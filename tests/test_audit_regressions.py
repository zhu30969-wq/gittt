"""Executable regression tests for CUMCM release-audit invariants.

Each test creates a uniquely named directory under the system temporary
directory and deliberately leaves it in place.  This follows the workspace's
no-recursive-delete rule and makes every failing fixture available for manual
inspection after the test run.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, Callable
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
SCHEMA_ROOT = REPO_ROOT / "cumcm-modeling" / "references" / "schemas"
sys.path.insert(0, str(SCRIPT_ROOT))

from _contract_support import dump_yaml, load_yaml, sha256_file  # noqa: E402
from audit_project import Audit, main, parse_rfc3339  # noqa: E402


CREATED_ROOTS: list[Path] = []


def write_text(root: Path, relative: str, text: str) -> Path:
    path = root / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_yaml(root: Path, relative: str, document: dict[str, Any]) -> Path:
    return write_text(root, relative, dump_yaml(document))


def file_ref(root: Path, relative: str) -> dict[str, str]:
    return {"path": relative, "sha256": sha256_file(root / relative)}


def artifact_base(kind: str, artifact_id: str, dependencies: list[str], author: str = "human") -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "kind": kind,
        "id": artifact_id,
        "revision": 1,
        "lifecycle_status": "frozen",
        "depends_on": dependencies,
        "provenance": {"author_type": author},
        "extensions": {},
    }


def build_release_project() -> Path:
    root = Path(tempfile.mkdtemp(prefix="cumcm-audit-regression-"))
    CREATED_ROOTS.append(root)
    write_text(root, "inputs/problem.txt", "Synthetic modeling problem.\n")
    write_text(root, "code/main.py", "print('ok')\n")
    write_text(root, "requirements.txt", "PyYAML==6.0.3\n")
    write_text(root, "outputs/result.json", '{"score": 0.95}\n')
    write_text(
        root,
        "paper/main.tex",
        "\\documentclass{article}\n\\begin{document}\nValidated score. % claim:c1\n\\end{document}\n",
    )

    problem = artifact_base("problem_spec", "problem:main", [])
    problem.update(
        {
            "contest": {"name": "CUMCM", "year": 2026, "problem_code": "T"},
            "statement": {**file_ref(root, "inputs/problem.txt"), "language": "en"},
            "questions": [
                {
                    "id": "question:q1",
                    "text": "Estimate the score.",
                    "task_type": "evaluation",
                    "required_outputs": ["deliverable:q1"],
                    "evaluation_intent": "A validated numerical score.",
                }
            ],
            "data_assets": [],
            "assumptions": [
                {
                    "id": "assumption:a1",
                    "text": "Synthetic fixture assumption.",
                    "basis": "given",
                    "impact": "Fixture only.",
                    "check": "Reviewed in fixture.",
                }
            ],
            "ambiguities": [],
            "constraints": [],
            "deliverables": [
                {
                    "id": "deliverable:q1",
                    "description": "Validated score.",
                    "question_refs": ["question:q1"],
                }
            ],
            "out_of_scope": [],
        }
    )
    write_yaml(root, "specs/problem_spec.yaml", problem)

    model = artifact_base("model_spec", "model:main", ["problem:main"])
    model.update(
        {
            "problem_ref": "problem:main",
            "addresses": ["question:q1"],
            "role": "primary",
            "model_family": "descriptive",
            "assumption_refs": ["assumption:a1"],
            "symbols": [
                {
                    "id": "symbol:x",
                    "name": "x",
                    "role": "output",
                    "domain": "real",
                    "shape": "scalar",
                    "unit": "1",
                    "definition": "Synthetic score.",
                }
            ],
            "formulation": {"equations": [], "objectives": [], "constraints": []},
            "data_bindings": [],
            "algorithm": {
                "description": "Emit deterministic fixture output.",
                "entrypoint": "code/main.py",
                "termination": "One run.",
                "complexity_note": "Constant time.",
            },
            "validation_plan": {
                "methods": ["Compare registered score."],
                "failure_criteria": ["Score below threshold."],
                "human_review_required": True,
            },
            "sensitivity_plan": [],
            "applicability": "Synthetic fixture only.",
            "failure_modes": ["Fixture corruption."],
            "fallback_models": [],
        }
    )
    write_yaml(root, "specs/model_spec.yaml", model)

    experiment = artifact_base("experiment", "experiment:main", ["model:main"])
    experiment.update(
        {
            "model_ref": "model:main",
            "question_refs": ["question:q1"],
            "mode": "validation",
            "purpose": "Validate the synthetic score.",
            "hypothesis": "Score is at least 0.9.",
            "data_refs": [],
            "code_files": [file_ref(root, "code/main.py")],
            "environment": file_ref(root, "requirements.txt"),
            "command": {
                "argv": ["python", "code/main.py"],
                "cwd": ".",
                "environment_allowlist": [],
                "network_access": "not_needed",
            },
            "seeds": [1],
            "repetitions": 1,
            "parameters": {},
            "split_strategy": "Not applicable to deterministic fixture.",
            "baseline_refs": [],
            "metrics": [
                {
                    "id": "metric:score",
                    "name": "score",
                    "direction": "maximize",
                    "unit": "1",
                    "aggregation": "single run",
                }
            ],
            "acceptance_rules": [
                {
                    "metric_ref": "metric:score",
                    "operator": ">=",
                    "threshold": 0.9,
                    "unit": "1",
                    "registration_timing": "pre_result",
                    "rationale": "Fixture release threshold.",
                }
            ],
            "outputs": [
                {
                    "id": "output:primary",
                    "path": "outputs/result.json",
                    "required": True,
                    "comparator": {"type": "exact_sha256"},
                }
            ],
            "timeout_seconds": 30,
            "resource_note": "Minimal fixture.",
        }
    )
    write_yaml(root, "experiments/experiment.yaml", experiment)

    results = artifact_base("results", "result:main", ["experiment:main"], author="script")
    results.update(
        {
            "experiment_ref": "experiment:main",
            "run_status": "success",
            "fingerprints": {
                "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
                "model:main": sha256_file(root / "specs/model_spec.yaml"),
                "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
            },
            "run": {
                "run_id": "run:main",
                "started_at": "2026-08-27T00:00:00Z",
                "finished_at": "2026-08-27T00:00:01Z",
                "argv": ["python", "code/main.py"],
                "cwd": ".",
                "exit_code": 0,
                "seeds": [1],
                "platform": "test",
                "git_commit": None,
                "git_dirty": None,
                "environment_note": "Synthetic fixture.",
            },
            "inputs": [],
            "outputs": [
                {
                    "output_ref": "output:primary",
                    "file": file_ref(root, "outputs/result.json"),
                    "comparison_status": "PASS",
                    "comparison_note": "Exact fixture output.",
                }
            ],
            "metrics": [
                {
                    "metric_ref": "metric:score",
                    "measurement": {"value": 0.95, "unit": "1"},
                    "sample_size": 1,
                    "uncertainty": None,
                }
            ],
            "diagnostics": [],
            "warnings": [],
            "failure_reason": None,
            "logs": [],
        }
    )
    write_yaml(root, "results/results.yaml", results)

    claims = artifact_base("claims", "claims:main", ["result:main"])
    claims["claims"] = [
        {
            "id": "claim:c1",
            "statement": "The validated score is 0.95.",
            "claim_type": "descriptive",
            "epistemic_status": "empirically_supported",
            "publication_status": "final",
            "scope": "Synthetic fixture.",
            "evidence_refs": [{"ref": "result:main", "role": "primary"}],
            "assumption_refs": ["assumption:a1"],
            "limitations": ["Synthetic only."],
            "counterevidence": [],
            "numeric_assertions": [
                {
                    "metric_ref": "metric:score",
                    "reported_value": 0.95,
                    "absolute_tolerance": 0.0,
                    "relative_tolerance": 0.0,
                    "unit": "1",
                }
            ],
            "proof_artifact": None,
            "paper_markers": ["claim:c1"],
            "human_review": {"status": "PASS", "reviewer": "fixture", "rationale": "Checked fixture claim."},
        }
    ]
    write_yaml(root, "claims/claims.yaml", claims)

    figures = artifact_base("figures", "figures:main", ["result:main", "claims:main"])
    figures["figures"] = []
    write_yaml(root, "figures/figures.yaml", figures)

    artifact_hashes = {
        "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
        "model:main": sha256_file(root / "specs/model_spec.yaml"),
        "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
        "result:main": sha256_file(root / "results/results.yaml"),
        "claims:main": sha256_file(root / "claims/claims.yaml"),
        "figures:main": sha256_file(root / "figures/figures.yaml"),
        "environment:python": sha256_file(root / "requirements.txt"),
        "entrypoint:paper": sha256_file(root / "paper/main.tex"),
        "deliverable:paper": sha256_file(root / "paper/main.tex"),
    }
    bindings = {
        "G0": ["problem:main"],
        "G1": ["problem:main"],
        "G2": ["model:main"],
        "G3": ["experiment:main", "environment:python"],
        "G4": ["result:main"],
        "G5": ["claims:main", "figures:main"],
        "G6": ["entrypoint:paper", "deliverable:paper"],
        "G7": ["deliverable:paper"],
    }
    review_dependencies = [
        "problem:main",
        "model:main",
        "experiment:main",
        "result:main",
        "claims:main",
        "figures:main",
    ]
    reviews = artifact_base("gate_review", "review:gates", review_dependencies)
    reviews["reviews"] = [
        {
            "id": f"review:{gate.lower()}",
            "gate": gate,
            "decision": "PASS",
            "basis": "human",
            "reviewer": "fixture",
            "reviewed_at": f"2026-08-27T00:00:{index:02d}Z",
            "rationale": f"Synthetic {gate} review.",
            "evidence_refs": ids,
            "artifact_fingerprints": {item: artifact_hashes[item] for item in ids},
            "conditions": [],
        }
        for index, (gate, ids) in enumerate(bindings.items())
    ]
    write_yaml(root, "reviews/gate-reviews.yaml", reviews)

    manifest = artifact_base("manifest", "manifest:project", [])
    artifact_rows = [
        ("problem:main", "problem_spec", "specs/problem_spec.yaml", []),
        ("model:main", "model_spec", "specs/model_spec.yaml", ["problem:main"]),
        ("experiment:main", "experiment", "experiments/experiment.yaml", ["model:main"]),
        ("result:main", "results", "results/results.yaml", ["experiment:main"]),
        ("claims:main", "claims", "claims/claims.yaml", ["result:main"]),
        ("figures:main", "figures", "figures/figures.yaml", ["result:main", "claims:main"]),
        ("review:gates", "gate_review", "reviews/gate-reviews.yaml", review_dependencies),
    ]
    manifest.update(
        {
            "manifest_type": "release",
            "project_id": "project:test",
            "competition_profile": {"enabled": False, "path": None, "note": "No profile in synthetic fixture."},
            "artifacts": [
                {
                    "id": artifact_id,
                    "kind": kind,
                    "path": relative,
                    "sha256": sha256_file(root / relative),
                    "required": True,
                    "depends_on": dependencies,
                }
                for artifact_id, kind, relative, dependencies in artifact_rows
            ],
            "entrypoints": {"paper": "paper/main.tex", "run": "code/main.py"},
            "environment_files": [
                {"id": "environment:python", **file_ref(root, "requirements.txt")}
            ],
            "deliverables": [
                {"id": "deliverable:paper", **file_ref(root, "paper/main.tex"), "required": True}
            ],
            "notes": ["Synthetic audit regression fixture."],
        }
    )
    write_yaml(root, "manifest.yaml", manifest)
    return root


def run_audit(root: Path) -> tuple[dict[str, Any], Audit]:
    audit = Audit(root, SCHEMA_ROOT)
    if audit.load_schemas() and audit.load_manifest():
        audit.load_artifacts()
        audit.validate_manifest_files()
        audit.verify_embedded_files()
        audit.validate_ids_and_refs()
        audit.validate_scientific_invariants()
        audit.validate_dag_and_propagate_stale()
        audit.validate_release_deliverables()
        audit.validate_reviews_and_profile()
        audit.enforce_release_gate_passes()
    return audit.report(), audit


def finding_codes(report: dict[str, Any]) -> list[str]:
    return [finding["code"] for gate in report["gates"] for finding in gate["findings"]]


def mutate_yaml(root: Path, relative: str, change: Callable[[dict[str, Any]], None], *, refresh_artifact_hash: bool = True) -> None:
    path = root / relative
    document = load_yaml(path)
    change(document)
    write_yaml(root, relative, document)
    if refresh_artifact_hash:
        manifest = load_yaml(root / "manifest.yaml")
        for artifact in manifest["artifacts"]:
            if artifact["path"] == relative:
                artifact["sha256"] = sha256_file(path)
        write_yaml(root, "manifest.yaml", manifest)


class AuditRegressionTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        print("PRESERVED_AUDIT_FIXTURES=")
        for root in CREATED_ROOTS:
            print(root)

    def assert_code(self, report: dict[str, Any], code: str) -> None:
        self.assertIn(code, finding_codes(report), finding_codes(report))

    def test_00_synthetic_release_passes(self) -> None:
        root = build_release_project()
        report, _audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertTrue(all(gate["status"] == "PASS" for gate in report["gates"]), report["gates"])

    def test_deliverable_hash_mismatch_is_stale(self) -> None:
        root = build_release_project()
        with (root / "paper/main.tex").open("a", encoding="utf-8") as handle:
            handle.write("% changed bytes\n")
        report, _audit = run_audit(root)
        self.assert_code(report, "DELIVERABLE_HASH_MISMATCH")

    def test_environment_hash_mismatch_propagates(self) -> None:
        root = build_release_project()
        with (root / "requirements.txt").open("a", encoding="utf-8") as handle:
            handle.write("changed==1\n")
        report, _audit = run_audit(root)
        self.assert_code(report, "ENVIRONMENT_HASH_MISMATCH")
        self.assert_code(report, "UPSTREAM_STALE")

    def test_paper_todo_blocks_release(self) -> None:
        root = build_release_project()
        with (root / "paper/main.tex").open("a", encoding="utf-8") as handle:
            handle.write("TODO\n")
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_PLACEHOLDER_REMAINS")

    def test_cross_engine_pollution_blocks_strict_lint(self) -> None:
        root = build_release_project()
        with (root / "paper/main.tex").open("a", encoding="utf-8") as handle:
            handle.write("#set text(size: 10pt)\n")
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_TYPST_SYNTAX_IN_LATEX")
        self.assert_code(report, "PAPER_STRICT_WARNINGS")

    def test_success_requires_zero_exit_code(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "results/results.yaml", lambda doc: doc["run"].update(exit_code=2))
        report, _audit = run_audit(root)
        self.assert_code(report, "SUCCESS_EXIT_CODE_NONZERO")

    def test_output_comparison_must_pass(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "results/results.yaml", lambda doc: doc["outputs"][0].update(comparison_status="WARN"))
        report, _audit = run_audit(root)
        self.assert_code(report, "OUTPUT_COMPARISON_NOT_PASS")

    def test_acceptance_rule_failure_blocks(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "results/results.yaml", lambda doc: doc["metrics"][0]["measurement"].update(value=0.5))
        report, _audit = run_audit(root)
        self.assert_code(report, "ACCEPTANCE_RULE_FAILED")

    def test_result_fingerprint_must_cover_dependency_closure(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "results/results.yaml", lambda doc: doc["fingerprints"].pop("problem:main"))
        report, _audit = run_audit(root)
        self.assert_code(report, "RESULT_FINGERPRINT_CLOSURE_MISSING")

    def test_reference_kind_mismatch_blocks(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "results/results.yaml", lambda doc: doc.update(experiment_ref="model:main"))
        report, _audit = run_audit(root)
        self.assert_code(report, "REFERENCE_KIND_MISMATCH")

    def test_final_claim_cannot_use_prefix_only_fake_result(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "specs/model_spec.yaml", lambda doc: doc["symbols"].append({
            "id": "result:fake", "name": "fake", "role": "constant", "domain": "real",
            "shape": "scalar", "unit": "1", "definition": "Not a result artifact."
        }))
        mutate_yaml(root, "claims/claims.yaml", lambda doc: doc["claims"][0].update(evidence_refs=[{"ref": "result:fake", "role": "primary"}]))
        report, _audit = run_audit(root)
        self.assert_code(report, "FINAL_CLAIM_WITHOUT_ELIGIBLE_RESULT")

    def test_manifest_document_dependency_mismatch_and_union_propagation(self) -> None:
        root = build_release_project()
        manifest = load_yaml(root / "manifest.yaml")
        for artifact in manifest["artifacts"]:
            if artifact["id"] == "claims:main":
                artifact["depends_on"] = []
        write_yaml(root, "manifest.yaml", manifest)
        mutate_yaml(root, "results/results.yaml", lambda doc: doc.update(revision=2), refresh_artifact_hash=False)
        report, _audit = run_audit(root)
        self.assert_code(report, "DEPENDENCY_DECLARATION_MISMATCH")
        stale_claims = [
            finding
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "UPSTREAM_STALE" and finding.get("artifact_id") == "claims:main"
        ]
        self.assertTrue(stale_claims, finding_codes(report))

    def test_review_timestamp_tie_is_ambiguous(self) -> None:
        root = build_release_project()
        def add_tied_review(doc: dict[str, Any]) -> None:
            original = next(item for item in doc["reviews"] if item["gate"] == "G2")
            tied = dict(original)
            tied["id"] = "review:g2-tied"
            tied["reviewed_at"] = "2026-08-27T08:00:02+08:00"
            tied["decision"] = "BLOCK"
            tied["rationale"] = "Conflicting same-time review."
            doc["reviews"].append(tied)
        mutate_yaml(root, "reviews/gate-reviews.yaml", add_tied_review)
        report, _audit = run_audit(root)
        self.assert_code(report, "AMBIGUOUS_LATEST_REVIEW")

    def test_review_fingerprint_must_be_cited_evidence(self) -> None:
        root = build_release_project()
        def break_evidence(doc: dict[str, Any]) -> None:
            review = next(item for item in doc["reviews"] if item["gate"] == "G5")
            review["evidence_refs"] = ["model:main"]
        mutate_yaml(root, "reviews/gate-reviews.yaml", break_evidence)
        report, _audit = run_audit(root)
        self.assert_code(report, "REVIEW_FINGERPRINT_NOT_EVIDENCE")

    def test_question_requires_full_final_claim_path(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "claims/claims.yaml", lambda doc: doc["claims"][0].update(publication_status="draft", paper_markers=[]))
        report, _audit = run_audit(root)
        self.assert_code(report, "QUESTION_EVIDENCE_PATH_MISSING")

    def test_run_entrypoint_must_match_registered_code(self) -> None:
        root = build_release_project()
        write_text(root, "code/other.py", "print('other')\n")
        manifest = load_yaml(root / "manifest.yaml")
        manifest["entrypoints"]["run"] = "code/other.py"
        write_yaml(root, "manifest.yaml", manifest)
        report, _audit = run_audit(root)
        self.assert_code(report, "RUN_ENTRYPOINT_UNREGISTERED")

    def test_manifest_schema_error_stops_semantic_traversal(self) -> None:
        root = build_release_project()
        manifest = load_yaml(root / "manifest.yaml")
        manifest["artifacts"] = "not-a-list"
        write_yaml(root, "manifest.yaml", manifest)
        report, audit = run_audit(root)
        self.assert_code(report, "SCHEMA_INVALID")
        self.assertEqual({}, audit.documents)

    def test_reference_findings_are_deduplicated(self) -> None:
        root = build_release_project()
        report, _audit = run_audit(root)
        references = [
            (finding.get("artifact_id"), finding["message"])
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] in {"REFERENCE_RESOLVED", "REFERENCE_KIND_MISMATCH", "DANGLING_REFERENCE"}
        ]
        self.assertEqual(len(references), len(set(references)), references)

    def test_cli_synthetic_release_passes_without_name_error(self) -> None:
        """Exercise module-level helper lookup through the real CLI entrypoint.

        Direct ``Audit`` calls cannot detect a helper that is accidentally
        defined after ``sys.exit(main())`` or omitted during packaging.  A
        subprocess smoke test protects the distributed script against that
        class of integration failure.
        """

        root = build_release_project()
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_ROOT / "audit_project.py"), str(root)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
        report = json.loads(completed.stdout)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertEqual(".", report["project_root"])
        self.assertNotIn("AUDIT_INTERNAL_ERROR", finding_codes(report))

    def test_missing_root_report_does_not_expose_absolute_path(self) -> None:
        missing = Path(tempfile.gettempdir()) / f"cumcm-missing-{uuid.uuid4().hex}"
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_ROOT / "audit_project.py"), str(missing)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(10, completed.returncode, completed.stderr or completed.stdout)
        report = json.loads(completed.stdout)
        self.assertEqual(".", report["project_root"])
        self.assertNotIn(str(missing.resolve()), json.dumps(report))

    def test_failed_history_can_coexist_with_eligible_result(self) -> None:
        """A preserved failed run is history, not affirmative claim evidence."""

        root = build_release_project()
        failed = load_yaml(root / "results/results.yaml")
        failed["id"] = "result:failed-history"
        failed["run_status"] = "failed"
        failed["run"]["run_id"] = "run:failed-history"
        failed["run"]["exit_code"] = 2
        failed["outputs"][0]["comparison_status"] = "BLOCK"
        failed["failure_reason"] = "Deliberately retained failed history."
        write_yaml(root, "results/failed-history.yaml", failed)

        manifest = load_yaml(root / "manifest.yaml")
        manifest["artifacts"].append(
            {
                "id": "result:failed-history",
                "kind": "results",
                "path": "results/failed-history.yaml",
                "sha256": sha256_file(root / "results/failed-history.yaml"),
                "required": True,
                "depends_on": ["experiment:main"],
            }
        )
        write_yaml(root, "manifest.yaml", manifest)

        report, audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertFalse(audit.result_eligibility["result:failed-history"])
        self.assertTrue(audit.result_eligibility["result:main"])
        self.assert_code(report, "HISTORICAL_RESULT_NOT_SUCCESSFUL")

    def test_review_timestamp_requires_absolute_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-free"):
            parse_rfc3339("2026-08-27T00:00:00")

    def test_paper_change_stales_g6_and_g7_reviews(self) -> None:
        root = build_release_project()
        with (root / "paper/main.tex").open("a", encoding="utf-8") as handle:
            handle.write("% reviewed bytes changed\n")
        report, _audit = run_audit(root)
        stale_review_gates = {
            gate["gate"]
            for gate in report["gates"]
            if any(finding["code"] == "REVIEW_FINGERPRINT_STALE" for finding in gate["findings"])
        }
        self.assertTrue({"G6", "G7"}.issubset(stale_review_gates), report["gates"])

    def test_environment_missing_blocks_and_propagates(self) -> None:
        root = build_release_project()
        # Delete exactly one known fixture file; the containing temporary
        # directory is deliberately preserved for post-test inspection.
        (root / "requirements.txt").unlink()
        report, _audit = run_audit(root)
        self.assert_code(report, "ENVIRONMENT_MISSING")
        self.assert_code(report, "UPSTREAM_STALE")

    def test_environment_placeholder_is_stale(self) -> None:
        root = build_release_project()
        manifest = load_yaml(root / "manifest.yaml")
        manifest["environment_files"][0]["sha256"] = "0" * 64
        write_yaml(root, "manifest.yaml", manifest)
        report, _audit = run_audit(root)
        self.assert_code(report, "ENVIRONMENT_HASH_PLACEHOLDER")
        self.assert_code(report, "UPSTREAM_STALE")

    def test_required_deliverable_missing_blocks(self) -> None:
        root = build_release_project()
        (root / "paper/main.tex").unlink()
        report, _audit = run_audit(root)
        self.assert_code(report, "DELIVERABLE_MISSING")
        self.assert_code(report, "ENTRYPOINT_MISSING")

    def test_required_deliverable_placeholder_is_stale(self) -> None:
        root = build_release_project()
        manifest = load_yaml(root / "manifest.yaml")
        manifest["deliverables"][0]["sha256"] = "0" * 64
        write_yaml(root, "manifest.yaml", manifest)
        report, _audit = run_audit(root)
        self.assert_code(report, "DELIVERABLE_HASH_PLACEHOLDER")

    def test_acceptance_units_must_agree(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "experiments/experiment.yaml",
            lambda doc: doc["acceptance_rules"][0].update(unit="percent"),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "ACCEPTANCE_UNIT_MISMATCH")

    def test_post_result_rule_cannot_masquerade_as_confirmatory(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "experiments/experiment.yaml",
            lambda doc: doc["acceptance_rules"][0].update(registration_timing="post_result"),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "CONFIRMATORY_RULE_POST_HOC")

    def test_duplicate_metric_is_ambiguous(self) -> None:
        root = build_release_project()

        def duplicate_metric(doc: dict[str, Any]) -> None:
            doc["metrics"].append(dict(doc["metrics"][0]))

        mutate_yaml(root, "results/results.yaml", duplicate_metric)
        report, _audit = run_audit(root)
        self.assert_code(report, "RESULT_METRIC_AMBIGUOUS")
        self.assert_code(report, "ACCEPTANCE_METRIC_AMBIGUOUS")

    def test_run_metadata_must_match_experiment_and_time_order(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
            ("argv", lambda doc: doc["run"].update(argv=["python", "other.py"]), "RUN_ARGV_MISMATCH"),
            ("cwd", lambda doc: doc["run"].update(cwd="code"), "RUN_CWD_MISMATCH"),
            ("seeds", lambda doc: doc["run"].update(seeds=[2]), "RUN_SEEDS_MISMATCH"),
            (
                "time",
                lambda doc: doc["run"].update(
                    started_at="2026-08-27T00:00:02Z",
                    finished_at="2026-08-27T00:00:01Z",
                ),
                "RUN_TIME_INVALID",
            ),
        ]
        for label, change, expected_code in cases:
            with self.subTest(label=label):
                root = build_release_project()
                mutate_yaml(root, "results/results.yaml", change)
                report, _audit = run_audit(root)
                self.assert_code(report, expected_code)

    def test_schema_invalid_artifact_is_excluded_from_semantics(self) -> None:
        root = build_release_project()
        mutate_yaml(root, "specs/model_spec.yaml", lambda doc: doc.pop("algorithm"))
        report, audit = run_audit(root)
        self.assert_code(report, "SCHEMA_INVALID")
        self.assertNotIn("model:main", audit.documents)
        self.assertNotIn("AUDIT_INTERNAL_ERROR", finding_codes(report))

    def test_main_converts_unexpected_exception_to_exit_14(self) -> None:
        root = build_release_project()
        output = io.StringIO()
        argv = ["audit_project.py", str(root)]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(Audit, "load_schemas", side_effect=RuntimeError("synthetic crash")),
            contextlib.redirect_stdout(output),
        ):
            code = main()
        report = json.loads(output.getvalue())
        self.assertEqual(14, code)
        self.assertEqual("ENV_BLOCK", report["status"])
        self.assertIn("AUDIT_INTERNAL_ERROR", finding_codes(report))


if __name__ == "__main__":
    unittest.main(verbosity=2)
