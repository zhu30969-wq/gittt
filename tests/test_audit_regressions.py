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
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "cumcm-modeling" / "scripts"
SCHEMA_ROOT = REPO_ROOT / "cumcm-modeling" / "references" / "schemas"
sys.path.insert(0, str(SCRIPT_ROOT))

from _contract_support import dump_yaml, load_yaml, sha256_file  # noqa: E402
from audit_project import Audit, VALIDATION_COVERAGE_BY_FAMILY, main, parse_rfc3339  # noqa: E402


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


def minimal_text_pdf(text: str) -> str:
    """Return a deterministic one-page ASCII PDF with extractable text."""

    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET\n"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream.encode('ascii'))} >>\nstream\n{stream}endstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    rendered = "%PDF-1.4\n"
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(rendered.encode("ascii")))
        rendered += f"{index} 0 obj\n{body}\nendobj\n"
    xref_offset = len(rendered.encode("ascii"))
    rendered += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    rendered += "".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    rendered += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    return rendered


def artifact_base(kind: str, artifact_id: str, dependencies: list[str], author: str = "human") -> dict[str, Any]:
    return {
        "schema_version": "2.1.0",
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
    write_text(root, "paper/build.log", "synthetic LaTeX build completed\n")
    write_text(root, "paper/main.fls", "INPUT main.tex\n")
    write_text(
        root,
        "paper/main.tex",
        "\\documentclass{article}\n\\begin{document}\nValidated score 0.95. % claim:c1\n\\end{document}\n",
    )
    write_text(root, "paper/main.pdf", minimal_text_pdf("Synthetic validated score 0.95"))

    problem = artifact_base("problem_spec", "problem:main", [])
    problem.update(
        {
            "contest": {"name": "CUMCM", "year": 2026, "problem_code": "T"},
            "statement": {**file_ref(root, "inputs/problem.txt"), "language": "en"},
            "questions": [
                {
                    "id": "question:q1",
                    "text": "Estimate the score.",
                    "task_type": "description",
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
            "method_selection": {
                "decision": "selected",
                "rationale": "The deterministic descriptive model directly produces the requested fixture output.",
                "baseline_policy": {
                    "status": "waived",
                    "model_refs": [],
                    "rationale": "A duplicate constant emitter would not be a discriminating baseline.",
                },
                "alternatives": [],
            },
            "assumption_refs": ["assumption:a1"],
            "constraint_refs": [],
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
                "checks": [
                    {
                        "id": "check:input-integrity",
                        "check_type": "input_integrity",
                        "applicability": "required",
                        "activation_condition": None,
                        "criticality": "blocking",
                        "rationale": "The deterministic registered score must remain unchanged.",
                        "procedure": "Compare the observed result with the predeclared fixture constant.",
                        "pass_rule": "Observed score equals 0.95.",
                        "threshold": {"operator": "==", "value": 0.95, "unit": "1"},
                        "failure_response": "block_result",
                    }
                ],
                "human_review_required": True,
            },
            "sensitivity_plan": [],
            "applicability": "Synthetic fixture only.",
            "failure_modes": ["Fixture corruption."],
            "fallback_models": [],
            "fallback_rules": [],
        }
    )
    write_yaml(root, "specs/model_spec.yaml", model)

    experiment = artifact_base("experiment", "experiment:main", ["model:main"])
    experiment.update(
        {
            "model_ref": "model:main",
            "question_refs": ["question:q1"],
            "mode": "validation",
            "decision_timing": "here_and_now",
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
            "baseline_comparison_rules": [],
            "metrics": [
                {
                    "id": "metric:score",
                    "name": "score",
                    "direction": "maximize",
                    "unit": "1",
                    "aggregation": "single run",
                    "source_output_ref": "output:primary",
                    "extractor": {"type": "json_pointer", "pointer": "/score"},
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
                    "comparator": {
                        "type": "exact_sha256",
                        "expected_sha256": sha256_file(root / "outputs/result.json"),
                        "reference_file": None,
                    },
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
                "repetitions_completed": 1,
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
            "diagnostics": [
                {
                    "id": "diagnostic:input-integrity",
                    "check_ref": "check:input-integrity",
                    "check_type": "input_integrity",
                    "status": "PASS",
                    "condition_met": None,
                    "condition_evidence": None,
                    "severity": "critical",
                    "procedure": "Compared the result with the fixture constant.",
                    "observation": "Observed score is 0.95.",
                    "observed": {"value": 0.95, "unit": "1"},
                    "source_file": file_ref(root, "outputs/result.json"),
                    "extractor": {"type": "json_pointer", "pointer": "/score"},
                    "conclusion": "The input-integrity threshold passed.",
                    "evidence_files": [],
                    "comparison_bindings": [],
                }
            ],
            "warnings": [],
            "failure_reason": None,
            "logs": [],
        }
    )
    write_yaml(root, "results/results.yaml", results)

    claims = artifact_base("claims", "claims:main", ["problem:main", "result:main"])
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
            "deliverable_refs": ["deliverable:q1"],
            "limitations": ["Synthetic only."],
            "counterevidence": [],
            "numeric_assertions": [
                {
                    "metric_ref": "metric:score",
                    "reported_value": 0.95,
                    "source_token": "0.95",
                    "rendered_token": "0.95",
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

    paper_build = artifact_base("paper_build", "build:paper", ["claims:main", "figures:main"], author="script")
    paper_build.update(
        {
            "source_entrypoint": file_ref(root, "paper/main.tex"),
            "fingerprints": {
                "claims:main": sha256_file(root / "claims/claims.yaml"),
                "figures:main": sha256_file(root / "figures/figures.yaml"),
                "result:main": sha256_file(root / "results/results.yaml"),
                "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
                "model:main": sha256_file(root / "specs/model_spec.yaml"),
                "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
            },
            "source_files": [file_ref(root, "paper/main.tex")],
            "resource_files": [],
            "competition_profile": None,
            "engine": "latex",
            "compiler": {"name": "latexmk", "version": "synthetic-1"},
            "command": {"argv": ["latexmk", "-recorder", "main.tex"], "cwd": "paper", "output_path": "paper/main.pdf"},
            "started_at": "2026-08-27T00:00:01Z",
            "finished_at": "2026-08-27T00:00:02Z",
            "exit_code": 0,
            "log": file_ref(root, "paper/build.log"),
            "dependency_log": file_ref(root, "paper/main.fls"),
            "pdf": file_ref(root, "paper/main.pdf"),
        }
    )
    write_yaml(root, "paper/paper-build.yaml", paper_build)

    artifact_hashes = {
        "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
        "model:main": sha256_file(root / "specs/model_spec.yaml"),
        "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
        "result:main": sha256_file(root / "results/results.yaml"),
        "claims:main": sha256_file(root / "claims/claims.yaml"),
        "figures:main": sha256_file(root / "figures/figures.yaml"),
        "build:paper": sha256_file(root / "paper/paper-build.yaml"),
        "environment:python": sha256_file(root / "requirements.txt"),
        "entrypoint:paper": sha256_file(root / "paper/main.tex"),
        "entrypoint:pdf": sha256_file(root / "paper/main.pdf"),
        "deliverable:paper": sha256_file(root / "paper/main.tex"),
        "deliverable:pdf": sha256_file(root / "paper/main.pdf"),
        "deliverable:code": sha256_file(root / "code/main.py"),
        "deliverable:result": sha256_file(root / "outputs/result.json"),
    }
    bindings = {
        "G0": ["problem:main"],
        "G1": ["problem:main"],
        "G2": ["problem:main", "model:main"],
        "G3": ["problem:main", "model:main", "experiment:main", "environment:python"],
        "G4": ["problem:main", "model:main", "experiment:main", "environment:python", "result:main"],
        "G5": ["problem:main", "model:main", "experiment:main", "environment:python", "result:main", "claims:main", "figures:main"],
        "G6": ["problem:main", "model:main", "experiment:main", "environment:python", "result:main", "claims:main", "figures:main", "build:paper", "entrypoint:paper", "entrypoint:pdf", "deliverable:paper", "deliverable:pdf"],
        "G7": ["deliverable:paper", "deliverable:pdf", "deliverable:code", "deliverable:result"],
    }
    review_dependencies = [
        "problem:main",
        "model:main",
        "experiment:main",
        "result:main",
        "claims:main",
        "figures:main",
        "build:paper",
    ]
    reviews = artifact_base("gate_review", "review:gates", review_dependencies)
    reviews["team_members"] = [
        {"id": "member:modeler", "display_name": "Fixture Modeler", "primary_role": "modeling"},
        {"id": "member:coder", "display_name": "Fixture Computation Reviewer", "primary_role": "computation"},
        {"id": "member:writer", "display_name": "Fixture Writing Reviewer", "primary_role": "writing"},
    ]
    reviews["reviews"] = []
    write_yaml(root, "reviews/gate-reviews.yaml", reviews)

    manifest = artifact_base("manifest", "manifest:project", [])
    artifact_rows = [
        ("problem:main", "problem_spec", "specs/problem_spec.yaml", []),
        ("model:main", "model_spec", "specs/model_spec.yaml", ["problem:main"]),
        ("experiment:main", "experiment", "experiments/experiment.yaml", ["model:main"]),
        ("result:main", "results", "results/results.yaml", ["experiment:main"]),
        ("claims:main", "claims", "claims/claims.yaml", ["problem:main", "result:main"]),
        ("figures:main", "figures", "figures/figures.yaml", ["result:main", "claims:main"]),
        ("build:paper", "paper_build", "paper/paper-build.yaml", ["claims:main", "figures:main"]),
        ("review:gates", "gate_review", "reviews/gate-reviews.yaml", review_dependencies),
    ]
    manifest.update(
        {
            "manifest_type": "release",
            "project_id": "project:test",
            "competition_profile": {
                "enabled": False,
                "id": None,
                "path": None,
                "sha256": None,
                "note": "No profile in synthetic fixture.",
            },
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
            "entrypoints": {"paper": "paper/main.tex", "pdf": "paper/main.pdf", "run": "code/main.py"},
            "environment_files": [
                {"id": "environment:python", **file_ref(root, "requirements.txt")}
            ],
            "deliverables": [
                {
                    "id": "deliverable:paper",
                    **file_ref(root, "paper/main.tex"),
                    "required": True,
                    "role": "paper_source",
                    "media_type": "application/x-tex",
                },
                {
                    "id": "deliverable:pdf",
                    **file_ref(root, "paper/main.pdf"),
                    "required": True,
                    "role": "paper_pdf",
                    "media_type": "application/pdf",
                },
                {
                    "id": "deliverable:code",
                    **file_ref(root, "code/main.py"),
                    "required": True,
                    "role": "code",
                    "media_type": "text/x-python",
                },
                {
                    "id": "deliverable:result",
                    **file_ref(root, "outputs/result.json"),
                    "required": True,
                    "role": "result",
                    "media_type": "application/json",
                },
            ],
            "notes": ["Synthetic audit regression fixture."],
        }
    )
    write_yaml(root, "manifest.yaml", manifest)
    rebuild_release_reviews(root)
    return root


def run_audit(root: Path) -> tuple[dict[str, Any], Audit]:
    audit = Audit(root, SCHEMA_ROOT)
    if audit.load_schemas() and audit.load_manifest():
        audit.load_artifacts()
        audit.validate_manifest_files()
        audit.register_release_snapshot()
        audit.validate_release_activity()
        audit.validate_ids_and_refs()
        audit.verify_embedded_files()
        audit.validate_scientific_invariants()
        audit.verify_captured_files_unchanged()
        audit.validate_dag_and_propagate_stale()
        audit.validate_release_deliverables()
        audit.validate_reviews_and_profile()
        if audit.verify_captured_files_unchanged():
            audit.validate_dag_and_propagate_stale()
        audit.enforce_release_gate_passes()
        if audit.verify_captured_files_unchanged():
            audit.validate_dag_and_propagate_stale()
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


def refresh_manifest_artifact_hashes(root: Path) -> None:
    """Refresh fixture artifact hashes after constructing a coherent variant.

    This helper is intentionally used only while building a positive fixture.
    Negative tests continue to mutate one declared byte or field without
    silently repairing downstream receipts.
    """

    manifest = load_yaml(root / "manifest.yaml")
    for artifact in manifest.get("artifacts", []):
        path = root / artifact["path"]
        if path.is_file():
            artifact["sha256"] = sha256_file(path)
    write_yaml(root, "manifest.yaml", manifest)


def rebuild_release_reviews(root: Path, *, first_second: int = 10) -> None:
    """Rebuild PASS reviews from the auditor's computed gate bindings.

    The fixture first installs an empty, valid review log.  A read-only audit
    then computes the exact current scientific closure for every gate.  This
    avoids duplicating production dependency logic in the tests while still
    making every review fingerprint explicit and inspectable.
    """

    manifest = load_yaml(root / "manifest.yaml")
    review_row = next(item for item in manifest["artifacts"] if item["id"] == "review:gates")
    active_artifact_ids = {
        item["id"]
        for item in manifest["artifacts"]
        if item.get("required") is True and item["id"] != "review:gates"
    }
    review_log = load_yaml(root / "reviews/gate-reviews.yaml")
    review_log["reviews"] = []
    write_yaml(root, "reviews/gate-reviews.yaml", review_log)
    review_row["depends_on"] = list(review_log["depends_on"])
    review_row["sha256"] = sha256_file(root / "reviews/gate-reviews.yaml")
    for artifact in manifest["artifacts"]:
        artifact["sha256"] = sha256_file(root / artifact["path"])
    write_yaml(root, "manifest.yaml", manifest)

    def binding_audit() -> Audit:
        candidate = Audit(root, SCHEMA_ROOT)
        if not (candidate.load_schemas() and candidate.load_manifest()):
            raise AssertionError(f"fixture manifest could not be loaded: {finding_codes(candidate.report())}")
        candidate.load_artifacts()
        candidate.validate_manifest_files()
        candidate.register_release_snapshot()
        candidate.validate_release_activity()
        candidate.validate_ids_and_refs()
        candidate.verify_embedded_files()
        candidate.validate_scientific_invariants()
        candidate.verify_captured_files_unchanged()
        candidate.validate_dag_and_propagate_stale()
        candidate.validate_release_deliverables()
        if candidate.verify_captured_files_unchanged():
            candidate.validate_dag_and_propagate_stale()
        return candidate

    audit = binding_audit()
    preliminary_bindings = {
        gate: audit.required_review_bindings(gate)
        for gate in ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7")
    }
    desired_dependencies = sorted(
        active_artifact_ids.intersection(
            artifact_id
            for evidence_refs in preliminary_bindings.values()
            for artifact_id in evidence_refs
        )
    )
    if review_log.get("depends_on") != desired_dependencies:
        review_log["depends_on"] = desired_dependencies
        write_yaml(root, "reviews/gate-reviews.yaml", review_log)
        manifest = load_yaml(root / "manifest.yaml")
        review_row = next(item for item in manifest["artifacts"] if item["id"] == "review:gates")
        review_row["depends_on"] = desired_dependencies
        for artifact in manifest["artifacts"]:
            artifact["sha256"] = sha256_file(root / artifact["path"])
        write_yaml(root, "manifest.yaml", manifest)
        audit = binding_audit()

    team_names = {
        member["id"]: member["display_name"]
        for member in review_log["team_members"]
    }
    signers_by_gate = {
        "G0": ["member:modeler"],
        "G1": ["member:modeler", "member:coder", "member:writer"],
        "G2": ["member:modeler", "member:coder", "member:writer"],
        "G3": ["member:modeler", "member:coder"],
        "G4": ["member:modeler", "member:coder"],
        "G5": ["member:modeler", "member:coder", "member:writer"],
        "G6": ["member:modeler", "member:coder", "member:writer"],
        "G7": ["member:modeler", "member:coder", "member:writer"],
    }
    reviews: list[dict[str, Any]] = []
    for index, gate in enumerate(("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7")):
        evidence_refs = audit.required_review_bindings(gate)
        # A release PASS review must always identify affirmative evidence,
        # even when a proof-only project has no experiment at G3/G4.
        if not evidence_refs:
            evidence_refs = {"problem:main"}
        missing_hashes = sorted(item for item in evidence_refs if item not in audit.current_hashes)
        if missing_hashes:
            raise AssertionError(f"fixture review {gate} lacks current hashes for {missing_hashes}")
        ordered_refs = sorted(evidence_refs)
        approval_set_id = f"approval:{gate.lower()}-rebuilt-{first_second}"
        for signer_index, member_id in enumerate(signers_by_gate[gate]):
            reviews.append(
                {
                    "id": f"review:{gate.lower()}-{member_id.split(':', 1)[1]}-rebuilt-{first_second}",
                    "gate": gate,
                    "decision": "PASS",
                    "basis": "human",
                    "approval_set_id": approval_set_id,
                    "member_id": member_id,
                    "reviewer": team_names[member_id],
                    "reviewed_at": f"2026-08-27T00:{index:02d}:{first_second + signer_index:02d}Z",
                    "rationale": f"Rebuilt synthetic {gate} evidence review.",
                    "evidence_refs": ordered_refs,
                    "artifact_fingerprints": {
                        artifact_id: audit.current_hashes[artifact_id]
                        for artifact_id in ordered_refs
                    },
                    "conditions": [],
                }
            )

    review_log["reviews"] = reviews
    review_log["depends_on"] = sorted(
        active_artifact_ids.intersection(
            artifact_id
            for review in reviews
            for artifact_id in review["evidence_refs"]
        )
    )
    write_yaml(root, "reviews/gate-reviews.yaml", review_log)
    manifest = load_yaml(root / "manifest.yaml")
    review_row = next(item for item in manifest["artifacts"] if item["id"] == "review:gates")
    review_row["depends_on"] = list(review_log["depends_on"])
    review_row["sha256"] = sha256_file(root / "reviews/gate-reviews.yaml")
    write_yaml(root, "manifest.yaml", manifest)


def build_promoted_release_project(
    *,
    trigger_score: float = 0.5,
    trigger_status: str = "BLOCK",
    promoted_at: str = "2026-08-27T00:00:02Z",
    promoted_started_at: str = "2026-08-27T00:00:03Z",
    promoted_success: bool = True,
    conditional_trigger_active: bool | None = None,
) -> Path:
    """Create a complete immutable fallback-promotion release transaction."""

    root = build_release_project()
    write_text(root, "outputs/trigger.json", json.dumps({"score": trigger_score}) + "\n")

    primary = load_yaml(root / "specs/model_spec.yaml")
    primary["depends_on"] = ["problem:main", "model:fallback"]
    primary["fallback_models"] = ["model:fallback"]
    primary["fallback_rules"] = [
        {
            "model_ref": "model:fallback",
            "trigger_check_ref": "check:input-integrity",
            "activation_condition": "The primary score fails its predeclared blocking threshold.",
            "action": "promote_to_primary",
            "rationale": "Activate the predeclared fallback after a reproducible blocking failure.",
        }
    ]
    primary_check = primary["validation_plan"]["checks"][0]
    primary_check["pass_rule"] = "Primary score is at least 0.9."
    primary_check["threshold"] = {"operator": ">=", "value": 0.9, "unit": "1"}
    if conditional_trigger_active is not None:
        primary_check["applicability"] = "conditional"
        primary_check["activation_condition"] = "The registered primary route is evaluated."
    write_yaml(root, "specs/model_spec.yaml", primary)

    fallback = load_yaml(root / "specs/model_spec.yaml")
    fallback.update(
        {
            "id": "model:fallback",
            "depends_on": ["problem:main"],
            "role": "fallback",
            "method_selection": {
                "decision": "conditional",
                "rationale": "This unchanged route was predeclared before observing the trigger result.",
                "baseline_policy": {
                    "status": "waived",
                    "model_refs": [],
                    "rationale": "A duplicate constant emitter would not discriminate between routes.",
                },
                "alternatives": [],
            },
            "symbols": [
                {
                    "id": "symbol:fallback-score",
                    "name": "y",
                    "role": "output",
                    "domain": "real",
                    "shape": "scalar",
                    "unit": "1",
                    "definition": "Synthetic fallback score.",
                }
            ],
            "fallback_models": [],
            "fallback_rules": [],
        }
    )
    fallback["validation_plan"] = {
        "checks": [
            {
                "id": "check:fallback-integrity",
                "check_type": "input_integrity",
                "applicability": "required",
                "activation_condition": None,
                "criticality": "blocking",
                "rationale": "The activated fallback must reproduce its registered output.",
                "procedure": "Extract the fallback score from the hashed JSON output.",
                "pass_rule": "Fallback score equals 0.95.",
                "threshold": {"operator": "==", "value": 0.95, "unit": "1"},
                "failure_response": "block_result",
            }
        ],
        "human_review_required": True,
    }
    write_yaml(root, "specs/model-fallback.yaml", fallback)

    primary_experiment = load_yaml(root / "experiments/experiment.yaml")
    primary_experiment["purpose"] = "Run the original primary and evaluate its predeclared trigger."
    primary_experiment["outputs"][0]["path"] = "outputs/trigger.json"
    primary_experiment["outputs"][0]["comparator"]["expected_sha256"] = sha256_file(root / "outputs/trigger.json")
    primary_experiment["acceptance_rules"] = [
        {
            "metric_ref": "metric:score",
            "operator": ">=",
            "threshold": 0.0,
            "unit": "1",
            "registration_timing": "pre_result",
            "rationale": "Execution and extraction must complete before the scientific trigger is judged.",
        }
    ]
    write_yaml(root, "experiments/experiment.yaml", primary_experiment)

    trigger_result = load_yaml(root / "results/results.yaml")
    trigger_result["run_status"] = "partial"
    trigger_result["fingerprints"] = {
        "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
        "model:main": sha256_file(root / "specs/model_spec.yaml"),
        "model:fallback": sha256_file(root / "specs/model-fallback.yaml"),
        "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
    }
    trigger_result["outputs"][0].update(
        {
            "file": file_ref(root, "outputs/trigger.json"),
            "comparison_status": "PASS",
            "comparison_note": "The trigger output matches its predeclared exact hash.",
        }
    )
    trigger_result["metrics"][0]["measurement"]["value"] = trigger_score
    trigger_diagnostic = trigger_result["diagnostics"][0]
    trigger_diagnostic.update(
        {
            "id": "diagnostic:primary-trigger",
            "status": trigger_status,
            "condition_met": conditional_trigger_active,
            "condition_evidence": (
                "The registered conditional primary route was evaluated."
                if conditional_trigger_active is True
                else None
            ),
            "observation": f"Primary score is {trigger_score}.",
            "observed": {"value": trigger_score, "unit": "1"},
            "source_file": file_ref(root, "outputs/trigger.json"),
            "conclusion": "The predeclared primary threshold was evaluated.",
        }
    )
    trigger_result["failure_reason"] = "Completed execution; the predeclared blocking diagnostic rejected the primary route."
    write_yaml(root, "results/results.yaml", trigger_result)

    promotion = artifact_base(
        "model_promotion",
        "promotion:q1",
        ["model:fallback", "model:main", "result:main"],
    )
    promotion.update(
        {
            "status": "activated",
            "source_fallback_ref": "model:fallback",
            "replaces_primary_ref": "model:main",
            "trigger_result_ref": "result:main",
            "trigger_diagnostic_ref": "diagnostic:primary-trigger",
            "fingerprints": {
                "model:fallback": sha256_file(root / "specs/model-fallback.yaml"),
                "model:main": sha256_file(root / "specs/model_spec.yaml"),
                "result:main": sha256_file(root / "results/results.yaml"),
            },
            "promoted_at": promoted_at,
            "approved_by": "fixture",
            "rationale": "The predeclared blocking trigger was recomputed from immutable run evidence.",
        }
    )
    write_yaml(root, "specs/model-promotion.yaml", promotion)

    promoted_experiment = load_yaml(root / "experiments/experiment.yaml")
    promoted_experiment.update(
        {
            "id": "experiment:promoted",
            "depends_on": ["model:fallback", "promotion:q1"],
            "model_ref": "model:fallback",
            "purpose": "Validate the activated, unchanged fallback route.",
            "hypothesis": "The fallback score is at least 0.9.",
            "metrics": [
                {
                    "id": "metric:fallback-score",
                    "name": "fallback score",
                    "direction": "maximize",
                    "unit": "1",
                    "aggregation": "single run",
                    "source_output_ref": "output:fallback",
                    "extractor": {"type": "json_pointer", "pointer": "/score"},
                }
            ],
            "acceptance_rules": [
                {
                    "metric_ref": "metric:fallback-score",
                    "operator": ">=",
                    "threshold": 0.9,
                    "unit": "1",
                    "registration_timing": "pre_result",
                    "rationale": "The activated fallback must meet the registered release threshold.",
                }
            ],
            "outputs": [
                {
                    "id": "output:fallback",
                    "path": "outputs/result.json",
                    "required": True,
                    "comparator": {
                        "type": "exact_sha256",
                        "expected_sha256": sha256_file(root / "outputs/result.json"),
                        "reference_file": None,
                    },
                }
            ],
        }
    )
    write_yaml(root, "experiments/experiment-promoted.yaml", promoted_experiment)

    promoted_result = load_yaml(root / "results/results.yaml")
    promoted_result.update(
        {
            "id": "result:promoted",
            "depends_on": ["experiment:promoted"],
            "experiment_ref": "experiment:promoted",
            "run_status": "success" if promoted_success else "failed",
            "fingerprints": {
                "experiment:promoted": sha256_file(root / "experiments/experiment-promoted.yaml"),
                "promotion:q1": sha256_file(root / "specs/model-promotion.yaml"),
                "model:fallback": sha256_file(root / "specs/model-fallback.yaml"),
                "model:main": sha256_file(root / "specs/model_spec.yaml"),
                "result:main": sha256_file(root / "results/results.yaml"),
                "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
                "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
            },
        }
    )
    promoted_result["run"].update(
        {
            "run_id": "run:promoted",
            "started_at": promoted_started_at,
            "finished_at": "2026-08-27T00:00:04Z",
            "exit_code": 0 if promoted_success else 2,
        }
    )
    promoted_result["outputs"] = [
        {
            "output_ref": "output:fallback",
            "file": file_ref(root, "outputs/result.json"),
            "comparison_status": "PASS" if promoted_success else "BLOCK",
            "comparison_note": "The fallback output was checked against the registered exact hash.",
        }
    ]
    promoted_result["metrics"] = [
        {
            "metric_ref": "metric:fallback-score",
            "measurement": {"value": 0.95, "unit": "1"},
            "sample_size": 1,
            "uncertainty": None,
        }
    ]
    promoted_result["diagnostics"] = [
        {
            "id": "diagnostic:fallback-integrity",
            "check_ref": "check:fallback-integrity",
            "check_type": "input_integrity",
            "status": "PASS" if promoted_success else "BLOCK",
            "condition_met": None,
            "condition_evidence": None,
            "severity": "critical",
            "procedure": "Extracted the fallback score from the hashed JSON output.",
            "observation": "Fallback score is 0.95.",
            "observed": {"value": 0.95, "unit": "1"},
            "source_file": file_ref(root, "outputs/result.json"),
            "extractor": {"type": "json_pointer", "pointer": "/score"},
            "conclusion": "The fallback threshold passed." if promoted_success else "The run was deliberately failed.",
            "evidence_files": [],
            "comparison_bindings": [],
        }
    ]
    promoted_result["failure_reason"] = None if promoted_success else "Deliberately failed promoted route."
    write_yaml(root, "results/result-promoted.yaml", promoted_result)

    claims = load_yaml(root / "claims/claims.yaml")
    claims["depends_on"] = ["problem:main", "result:promoted"]
    claims["claims"][0]["evidence_refs"] = [{"ref": "result:promoted", "role": "primary"}]
    claims["claims"][0]["numeric_assertions"][0]["metric_ref"] = "metric:fallback-score"
    write_yaml(root, "claims/claims.yaml", claims)
    figures = load_yaml(root / "figures/figures.yaml")
    figures["depends_on"] = ["result:promoted", "claims:main"]
    write_yaml(root, "figures/figures.yaml", figures)

    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build["started_at"] = "2026-08-27T00:00:05Z"
    paper_build["finished_at"] = "2026-08-27T00:00:06Z"
    paper_build["fingerprints"] = {
        "claims:main": sha256_file(root / "claims/claims.yaml"),
        "figures:main": sha256_file(root / "figures/figures.yaml"),
        "result:promoted": sha256_file(root / "results/result-promoted.yaml"),
        "experiment:promoted": sha256_file(root / "experiments/experiment-promoted.yaml"),
        "promotion:q1": sha256_file(root / "specs/model-promotion.yaml"),
        "model:fallback": sha256_file(root / "specs/model-fallback.yaml"),
        "model:main": sha256_file(root / "specs/model_spec.yaml"),
        "result:main": sha256_file(root / "results/results.yaml"),
        "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
        "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
    }
    write_yaml(root, "paper/paper-build.yaml", paper_build)

    manifest = load_yaml(root / "manifest.yaml")
    row_by_id = {item["id"]: item for item in manifest["artifacts"]}
    row_by_id["model:main"]["depends_on"] = ["problem:main", "model:fallback"]
    row_by_id["claims:main"]["depends_on"] = ["problem:main", "result:promoted"]
    row_by_id["figures:main"]["depends_on"] = ["result:promoted", "claims:main"]
    manifest["artifacts"].extend(
        [
            {
                "id": "model:fallback",
                "kind": "model_spec",
                "path": "specs/model-fallback.yaml",
                "sha256": sha256_file(root / "specs/model-fallback.yaml"),
                "required": True,
                "depends_on": ["problem:main"],
            },
            {
                "id": "promotion:q1",
                "kind": "model_promotion",
                "path": "specs/model-promotion.yaml",
                "sha256": sha256_file(root / "specs/model-promotion.yaml"),
                "required": True,
                "depends_on": ["model:fallback", "model:main", "result:main"],
            },
            {
                "id": "experiment:promoted",
                "kind": "experiment",
                "path": "experiments/experiment-promoted.yaml",
                "sha256": sha256_file(root / "experiments/experiment-promoted.yaml"),
                "required": True,
                "depends_on": ["model:fallback", "promotion:q1"],
            },
            {
                "id": "result:promoted",
                "kind": "results",
                "path": "results/result-promoted.yaml",
                "sha256": sha256_file(root / "results/result-promoted.yaml"),
                "required": True,
                "depends_on": ["experiment:promoted"],
            },
        ]
    )
    manifest["deliverables"].append(
        {
            "id": "deliverable:trigger-result",
            **file_ref(root, "outputs/trigger.json"),
            "required": True,
            "role": "result",
            "media_type": "application/json",
        }
    )
    write_yaml(root, "manifest.yaml", manifest)
    refresh_manifest_artifact_hashes(root)
    rebuild_release_reviews(root, first_second=20)
    return root


def enable_competition_profile(root: Path) -> None:
    """Bind a synthetic competition-format profile into manifest and build receipt."""

    write_text(
        root,
        "profiles/cumcm-format.yaml",
        "name: synthetic-cumcm-format\npaper_size: A4\nengine: latex\n",
    )
    profile = {
        "enabled": True,
        "id": "profile:cumcm-format",
        "path": "profiles/cumcm-format.yaml",
        "sha256": sha256_file(root / "profiles/cumcm-format.yaml"),
        "note": "Synthetic hash-bound format profile.",
    }
    manifest = load_yaml(root / "manifest.yaml")
    manifest["competition_profile"] = profile
    write_yaml(root, "manifest.yaml", manifest)
    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build["competition_profile"] = {
        "profile_ref": profile["id"],
        "path": profile["path"],
        "sha256": profile["sha256"],
    }
    write_yaml(root, "paper/paper-build.yaml", paper_build)
    refresh_manifest_artifact_hashes(root)
    rebuild_release_reviews(root, first_second=20)


def rebind_paper_receipt(
    root: Path,
    *,
    source_files: list[str] | None = None,
    resource_files: list[str] | None = None,
) -> None:
    """Refresh a synthetic paper receipt after an intentional fixture build."""

    source_paths = source_files or ["paper/main.tex"]
    resource_paths = resource_files or []
    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build["source_entrypoint"] = file_ref(root, "paper/main.tex")
    paper_build["source_files"] = [file_ref(root, path) for path in source_paths]
    paper_build["resource_files"] = [file_ref(root, path) for path in resource_paths]
    paper_build["dependency_log"] = file_ref(root, "paper/main.fls")
    paper_build["log"] = file_ref(root, "paper/build.log")
    paper_build["pdf"] = file_ref(root, "paper/main.pdf")
    write_yaml(root, "paper/paper-build.yaml", paper_build)

    manifest = load_yaml(root / "manifest.yaml")
    for deliverable in manifest["deliverables"]:
        if deliverable["path"] in {"paper/main.tex", "paper/main.pdf"}:
            deliverable["sha256"] = sha256_file(root / deliverable["path"])
    write_yaml(root, "manifest.yaml", manifest)
    refresh_manifest_artifact_hashes(root)
    rebuild_release_reviews(root, first_second=20)


def build_proof_only_release_project() -> Path:
    """Create a release whose only final claim is a packaged formal proof."""

    root = build_release_project()
    write_text(
        root,
        "paper/proof.tex",
        "Claim-ID: claim:c1\n"
        "Proposition: Every real square is nonnegative.\n"
        "Proof:\n"
        "Let x be any real number. Since x and x have the same sign, x^2 = x\\cdot x \\ge 0.\n"
        "Therefore every real square is nonnegative. QED.\n",
    )
    write_text(
        root,
        "paper/main.tex",
        "\\documentclass{article}\n\\begin{document}\n"
        "\\input{proof.tex}\nThe requested proposition is proved. % claim:c1\n"
        "\\end{document}\n",
    )
    write_text(root, "paper/main.fls", "PWD .\nINPUT main.tex\nINPUT proof.tex\n")
    write_text(root, "paper/main.pdf", minimal_text_pdf("The requested proposition is proved."))

    claims = load_yaml(root / "claims/claims.yaml")
    claims["depends_on"] = ["problem:main"]
    claim = claims["claims"][0]
    claim.update(
        {
            "statement": "Every real square is nonnegative.",
            "claim_type": "theoretical",
            "epistemic_status": "formally_proved",
            "evidence_refs": [],
            "numeric_assertions": [],
            "proof_artifact": file_ref(root, "paper/proof.tex"),
            "limitations": ["The proof covers only the stated real-valued proposition."],
            "human_review": {
                "status": "PASS",
                "reviewer": "fixture",
                "rationale": (
                    "Verified claim:c1 proposition and derivation against proof SHA-256 "
                    f"{sha256_file(root / 'paper/proof.tex')}"
                ),
            },
        }
    )
    write_yaml(root, "claims/claims.yaml", claims)
    figures = load_yaml(root / "figures/figures.yaml")
    figures["depends_on"] = ["claims:main"]
    write_yaml(root, "figures/figures.yaml", figures)

    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build["fingerprints"] = {
        "claims:main": sha256_file(root / "claims/claims.yaml"),
        "figures:main": sha256_file(root / "figures/figures.yaml"),
        "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
    }
    paper_build["source_entrypoint"] = file_ref(root, "paper/main.tex")
    paper_build["source_files"] = [
        file_ref(root, "paper/main.tex"),
        file_ref(root, "paper/proof.tex"),
    ]
    paper_build["resource_files"] = []
    paper_build["dependency_log"] = file_ref(root, "paper/main.fls")
    paper_build["pdf"] = file_ref(root, "paper/main.pdf")
    write_yaml(root, "paper/paper-build.yaml", paper_build)

    manifest = load_yaml(root / "manifest.yaml")
    active_dependencies = ["problem:main", "claims:main", "figures:main", "build:paper"]
    for artifact in manifest["artifacts"]:
        if artifact["id"] in {"model:main", "experiment:main", "result:main"}:
            artifact["required"] = False
        elif artifact["id"] == "claims:main":
            artifact["depends_on"] = ["problem:main"]
        elif artifact["id"] == "figures:main":
            artifact["depends_on"] = ["claims:main"]
        elif artifact["id"] == "review:gates":
            artifact["depends_on"] = active_dependencies
    manifest["entrypoints"].pop("run", None)
    manifest["environment_files"] = []
    manifest["deliverables"] = [
        {
            "id": "deliverable:paper",
            **file_ref(root, "paper/main.tex"),
            "required": True,
            "role": "paper_source",
            "media_type": "application/x-tex",
        },
        {
            "id": "deliverable:pdf",
            **file_ref(root, "paper/main.pdf"),
            "required": True,
            "role": "paper_pdf",
            "media_type": "application/pdf",
        },
    ]
    write_yaml(root, "manifest.yaml", manifest)
    review_log = load_yaml(root / "reviews/gate-reviews.yaml")
    review_log["depends_on"] = active_dependencies
    review_log["reviews"] = []
    write_yaml(root, "reviews/gate-reviews.yaml", review_log)
    refresh_manifest_artifact_hashes(root)
    rebuild_release_reviews(root, first_second=20)
    return root


def add_runtime_helper(root: Path, *, package_helper: bool) -> None:
    """Declare a second runtime code file and optionally package it."""

    write_text(root, "code/helper.py", "VALUE = 0.95\n")
    experiment = load_yaml(root / "experiments/experiment.yaml")
    experiment["code_files"].append(file_ref(root, "code/helper.py"))
    write_yaml(root, "experiments/experiment.yaml", experiment)

    result = load_yaml(root / "results/results.yaml")
    result["fingerprints"]["experiment:main"] = sha256_file(root / "experiments/experiment.yaml")
    write_yaml(root, "results/results.yaml", result)
    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build["fingerprints"]["experiment:main"] = sha256_file(root / "experiments/experiment.yaml")
    paper_build["fingerprints"]["result:main"] = sha256_file(root / "results/results.yaml")
    write_yaml(root, "paper/paper-build.yaml", paper_build)

    manifest = load_yaml(root / "manifest.yaml")
    if package_helper:
        manifest["deliverables"].append(
            {
                "id": "deliverable:helper-code",
                **file_ref(root, "code/helper.py"),
                "required": True,
                "role": "code",
                "media_type": "text/x-python",
            }
        )
    write_yaml(root, "manifest.yaml", manifest)
    refresh_manifest_artifact_hashes(root)
    rebuild_release_reviews(root, first_second=20)


def convert_release_to_typst(root: Path) -> None:
    """Replace the synthetic LaTeX receipt with a static Typst receipt."""

    write_text(root, "paper/main.typ", "Validated score 0.95. // claim:c1\n")
    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build.update(
        {
            "source_entrypoint": file_ref(root, "paper/main.typ"),
            "source_files": [file_ref(root, "paper/main.typ")],
            "resource_files": [],
            "engine": "typst",
            "compiler": {"name": "typst", "version": "synthetic-1"},
            "command": {
                "argv": ["typst", "compile", "main.typ", "main.pdf"],
                "cwd": "paper",
                "output_path": "paper/main.pdf",
            },
            "dependency_log": None,
        }
    )
    write_yaml(root, "paper/paper-build.yaml", paper_build)
    manifest = load_yaml(root / "manifest.yaml")
    manifest["entrypoints"]["paper"] = "paper/main.typ"
    paper_deliverable = next(
        item for item in manifest["deliverables"] if item["id"] == "deliverable:paper"
    )
    paper_deliverable.update(
        {
            "path": "paper/main.typ",
            "sha256": sha256_file(root / "paper/main.typ"),
            "media_type": "application/x-typst",
        }
    )
    write_yaml(root, "manifest.yaml", manifest)
    refresh_manifest_artifact_hashes(root)
    rebuild_release_reviews(root, first_second=20)


def _artifact_closure(artifact_id: str, entries: dict[str, dict[str, Any]]) -> set[str]:
    """Return the transitive artifact dependencies of one manifest entry."""

    closure: set[str] = set()
    queue = list(entries.get(artifact_id, {}).get("depends_on", []))
    while queue:
        dependency = queue.pop()
        if dependency in closure or dependency not in entries:
            continue
        closure.add(dependency)
        queue.extend(entries[dependency].get("depends_on", []))
    return closure


def resign_release_project(root: Path) -> None:
    """Refresh dependency fingerprints, reviews and manifest hashes.

    Tests use this after an intentional *current* mutation.  It differs from a
    stale-evidence test, which deliberately updates only the changed manifest
    row and leaves downstream receipts untouched.
    """

    manifest = load_yaml(root / "manifest.yaml")
    entries = {entry["id"]: entry for entry in manifest["artifacts"]}
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []

    def visit(artifact_id: str) -> None:
        if artifact_id in visited:
            return
        if artifact_id in visiting:
            raise AssertionError(f"fixture dependency cycle at {artifact_id}")
        visiting.add(artifact_id)
        for dependency in entries[artifact_id].get("depends_on", []):
            if dependency in entries:
                visit(dependency)
        visiting.remove(artifact_id)
        visited.add(artifact_id)
        order.append(artifact_id)

    for artifact_id in entries:
        visit(artifact_id)

    for artifact_id in order:
        entry = entries[artifact_id]
        if entry["kind"] == "gate_review":
            continue
        path = root / entry["path"]
        document = load_yaml(path)
        if entry["kind"] == "model_promotion":
            dependencies = set(entry.get("depends_on", []))
            document["fingerprints"] = {
                dependency: sha256_file(root / entries[dependency]["path"])
                for dependency in sorted(dependencies)
            }
            write_yaml(root, entry["path"], document)
        elif entry["kind"] in {"results", "paper_build"}:
            dependencies = _artifact_closure(artifact_id, entries)
            document["fingerprints"] = {
                dependency: sha256_file(root / entries[dependency]["path"])
                for dependency in sorted(dependencies)
            }
            write_yaml(root, entry["path"], document)

    artifact_paths = {artifact_id: root / entry["path"] for artifact_id, entry in entries.items()}
    environment_paths = {
        row["id"]: root / row["path"]
        for row in manifest.get("environment_files", [])
    }
    entrypoint_paths = {
        f"entrypoint:{name}": root / path
        for name, path in manifest.get("entrypoints", {}).items()
    }
    deliverable_paths = {
        row["id"]: root / row["path"]
        for row in manifest.get("deliverables", [])
    }
    resolvable_paths = {
        **artifact_paths,
        **environment_paths,
        **entrypoint_paths,
        **deliverable_paths,
    }
    for entry in entries.values():
        if entry["kind"] != "gate_review":
            continue
        review_log = load_yaml(root / entry["path"])
        review_log["reviews"] = []
        write_yaml(root, entry["path"], review_log)

    for row in manifest.get("environment_files", []):
        row["sha256"] = sha256_file(root / row["path"])
    for row in manifest.get("deliverables", []):
        row["sha256"] = sha256_file(root / row["path"])
    for entry in manifest["artifacts"]:
        entry["sha256"] = sha256_file(root / entry["path"])
    write_yaml(root, "manifest.yaml", manifest)
    rebuild_release_reviews(root, first_second=30)


def build_promotion_release_project() -> Path:
    """Build a complete partial-trigger → immutable promotion → release chain."""

    root = build_release_project()
    write_text(root, "outputs/trigger.json", '{"score": 0.5}\n')

    primary = load_yaml(root / "specs/model_spec.yaml")
    primary["depends_on"] = ["problem:main", "model:fallback"]
    primary["validation_plan"]["checks"][0]["pass_rule"] = "Primary score is at least 0.9."
    primary["validation_plan"]["checks"][0]["threshold"] = {"operator": ">=", "value": 0.9, "unit": "1"}
    primary["fallback_models"] = ["model:fallback"]
    primary["fallback_rules"] = [
        {
            "model_ref": "model:fallback",
            "trigger_check_ref": "check:input-integrity",
            "activation_condition": "Primary score is below 0.9.",
            "action": "promote_to_primary",
            "rationale": "Activate the predeclared fallback after the blocking integrity check fails.",
        }
    ]
    write_yaml(root, "specs/model_spec.yaml", primary)

    fallback = deepcopy(primary)
    fallback.update(id="model:fallback", depends_on=["problem:main"], role="fallback")
    fallback["method_selection"]["decision"] = "conditional"
    fallback["method_selection"]["rationale"] = "Predeclared fallback route for the synthetic fixture."
    fallback["symbols"][0].update(id="symbol:fallback-score", name="fallback_score")
    fallback["validation_plan"]["checks"][0].update(
        id="check:fallback-integrity",
        pass_rule="Fallback score equals 0.95.",
        threshold={"operator": "==", "value": 0.95, "unit": "1"},
    )
    fallback["fallback_models"] = []
    fallback["fallback_rules"] = []
    write_yaml(root, "specs/model-fallback.yaml", fallback)

    original_experiment = load_yaml(root / "experiments/experiment.yaml")
    trigger_experiment = deepcopy(original_experiment)
    trigger_experiment["purpose"] = "Run the original primary and evaluate its predeclared blocking trigger."
    trigger_experiment["acceptance_rules"][0].update(
        threshold=0.0,
        rationale="The execution must complete; route selection is governed by the planned diagnostic.",
    )
    trigger_experiment["outputs"][0]["path"] = "outputs/trigger.json"
    trigger_experiment["outputs"][0]["comparator"]["expected_sha256"] = sha256_file(root / "outputs/trigger.json")
    write_yaml(root, "experiments/experiment.yaml", trigger_experiment)

    original_result = load_yaml(root / "results/results.yaml")
    trigger_result = deepcopy(original_result)
    trigger_result["run_status"] = "partial"
    trigger_result["outputs"][0]["file"] = file_ref(root, "outputs/trigger.json")
    trigger_result["outputs"][0]["comparison_note"] = "The partial run output matches its registered bytes."
    trigger_result["metrics"][0]["measurement"]["value"] = 0.5
    trigger_result["diagnostics"][0].update(
        id="diagnostic:primary-trigger",
        status="BLOCK",
        observation="Primary score is 0.5.",
        observed={"value": 0.5, "unit": "1"},
        source_file=file_ref(root, "outputs/trigger.json"),
        conclusion="The predeclared blocking primary threshold failed.",
    )
    trigger_result["failure_reason"] = "The completed execution activated a predeclared blocking diagnostic."
    write_yaml(root, "results/results.yaml", trigger_result)

    promotion = {
        **artifact_base(
            "model_promotion",
            "promotion:q1",
            ["model:fallback", "model:main", "result:main"],
        ),
        "status": "activated",
        "source_fallback_ref": "model:fallback",
        "replaces_primary_ref": "model:main",
        "trigger_result_ref": "result:main",
        "trigger_diagnostic_ref": "diagnostic:primary-trigger",
        "fingerprints": {
            "model:fallback": sha256_file(root / "specs/model-fallback.yaml"),
            "model:main": sha256_file(root / "specs/model_spec.yaml"),
            "result:main": sha256_file(root / "results/results.yaml"),
        },
        "promoted_at": "2026-08-27T00:00:02Z",
        "approved_by": "fixture-human",
        "rationale": "The blocking trigger was recomputed from immutable run bytes.",
    }
    write_yaml(root, "specs/model-promotion.yaml", promotion)

    promoted_experiment = deepcopy(original_experiment)
    promoted_experiment.update(
        id="experiment:promoted",
        depends_on=["model:fallback", "promotion:q1"],
        model_ref="model:fallback",
        purpose="Validate the activated fallback route.",
    )
    promoted_experiment["metrics"][0].update(
        id="metric:fallback-score",
        source_output_ref="output:fallback",
    )
    promoted_experiment["acceptance_rules"][0]["metric_ref"] = "metric:fallback-score"
    promoted_experiment["outputs"][0]["id"] = "output:fallback"
    write_yaml(root, "experiments/experiment-promoted.yaml", promoted_experiment)

    promoted_result = deepcopy(original_result)
    promoted_result.update(
        id="result:promoted",
        depends_on=["experiment:promoted"],
        experiment_ref="experiment:promoted",
    )
    promoted_result["run"].update(
        run_id="run:promoted",
        started_at="2026-08-27T00:00:03Z",
        finished_at="2026-08-27T00:00:04Z",
    )
    promoted_result["outputs"][0]["output_ref"] = "output:fallback"
    promoted_result["metrics"][0]["metric_ref"] = "metric:fallback-score"
    promoted_result["diagnostics"][0].update(
        id="diagnostic:fallback-integrity",
        check_ref="check:fallback-integrity",
    )
    write_yaml(root, "results/result-promoted.yaml", promoted_result)

    claims = load_yaml(root / "claims/claims.yaml")
    claims["depends_on"] = ["problem:main", "result:promoted"]
    claims["claims"][0]["evidence_refs"] = [
        {"ref": "result:promoted", "pointer": "/metrics/0", "role": "promoted fallback metric"}
    ]
    claims["claims"][0]["numeric_assertions"][0]["metric_ref"] = "metric:fallback-score"
    write_yaml(root, "claims/claims.yaml", claims)

    figures = load_yaml(root / "figures/figures.yaml")
    figures["depends_on"] = ["result:promoted", "claims:main"]
    write_yaml(root, "figures/figures.yaml", figures)

    paper_build = load_yaml(root / "paper/paper-build.yaml")
    paper_build["started_at"] = "2026-08-27T00:00:05Z"
    paper_build["finished_at"] = "2026-08-27T00:00:06Z"
    write_yaml(root, "paper/paper-build.yaml", paper_build)

    manifest = load_yaml(root / "manifest.yaml")
    entries = {entry["id"]: entry for entry in manifest["artifacts"]}
    entries["model:main"]["depends_on"] = ["problem:main", "model:fallback"]
    entries["result:main"]["depends_on"] = ["experiment:main"]
    entries["claims:main"]["depends_on"] = ["problem:main", "result:promoted"]
    entries["figures:main"]["depends_on"] = ["result:promoted", "claims:main"]
    new_entries = [
        {"id": "model:fallback", "kind": "model_spec", "path": "specs/model-fallback.yaml", "required": True, "depends_on": ["problem:main"]},
        {"id": "promotion:q1", "kind": "model_promotion", "path": "specs/model-promotion.yaml", "required": True, "depends_on": ["model:fallback", "model:main", "result:main"]},
        {"id": "experiment:promoted", "kind": "experiment", "path": "experiments/experiment-promoted.yaml", "required": True, "depends_on": ["model:fallback", "promotion:q1"]},
        {"id": "result:promoted", "kind": "results", "path": "results/result-promoted.yaml", "required": True, "depends_on": ["experiment:promoted"]},
    ]
    for entry in new_entries:
        entry["sha256"] = sha256_file(root / entry["path"])
        manifest["artifacts"].append(entry)
    scientific_ids = [entry["id"] for entry in manifest["artifacts"] if entry["kind"] != "gate_review"]
    entries["review:gates"]["depends_on"] = scientific_ids
    manifest["deliverables"].append(
        {
            "id": "deliverable:trigger-result",
            **file_ref(root, "outputs/trigger.json"),
            "required": True,
            "role": "result",
            "media_type": "application/json",
        }
    )
    for entry in manifest["artifacts"]:
        entry["sha256"] = sha256_file(root / entry["path"])
    write_yaml(root, "manifest.yaml", manifest)

    evidence_by_gate = {
        "G0": ["problem:main"],
        "G1": ["problem:main"],
        "G2": ["problem:main", "model:main", "model:fallback", "experiment:main", "result:main", "promotion:q1", "environment:python"],
        "G3": ["problem:main", "model:main", "model:fallback", "experiment:main", "result:main", "promotion:q1", "experiment:promoted", "environment:python"],
        "G4": ["problem:main", "model:main", "model:fallback", "experiment:main", "result:main", "promotion:q1", "experiment:promoted", "result:promoted", "environment:python"],
        "G5": ["problem:main", "model:main", "model:fallback", "experiment:main", "result:main", "promotion:q1", "experiment:promoted", "result:promoted", "claims:main", "figures:main", "environment:python"],
        "G6": ["problem:main", "model:main", "model:fallback", "experiment:main", "result:main", "promotion:q1", "experiment:promoted", "result:promoted", "claims:main", "figures:main", "build:paper", "environment:python", "entrypoint:paper", "entrypoint:pdf", "deliverable:paper", "deliverable:pdf"],
        "G7": ["deliverable:paper", "deliverable:pdf", "deliverable:code", "deliverable:result", "deliverable:trigger-result"],
    }
    review_log = artifact_base("gate_review", "review:gates", scientific_ids)
    review_log["team_members"] = [
        {"id": "member:modeler", "display_name": "Fixture Modeler", "primary_role": "modeling"},
        {"id": "member:coder", "display_name": "Fixture Computation Reviewer", "primary_role": "computation"},
        {"id": "member:writer", "display_name": "Fixture Writing Reviewer", "primary_role": "writing"},
    ]
    review_log["reviews"] = [
        {
            "id": f"review:{gate.lower()}-promotion",
            "gate": gate,
            "decision": "PASS",
            "basis": "human",
            "reviewer": "fixture-human",
            "reviewed_at": f"2026-08-27T00:00:{10 + index:02d}Z",
            "rationale": "Synthetic promotion-chain evidence was checked for this gate.",
            "evidence_refs": evidence_refs,
            "artifact_fingerprints": {},
            "conditions": [],
        }
        for index, (gate, evidence_refs) in enumerate(evidence_by_gate.items())
    ]
    write_yaml(root, "reviews/gate-reviews.yaml", review_log)
    resign_release_project(root)
    return root


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

    def test_complete_immutable_fallback_promotion_release_passes(self) -> None:
        root = build_promoted_release_project()
        immutable_paths = [
            root / "specs/model_spec.yaml",
            root / "specs/model-fallback.yaml",
            root / "results/results.yaml",
        ]
        before = {path: sha256_file(path) for path in immutable_paths}
        report, audit = run_audit(root)
        after = {path: sha256_file(path) for path in immutable_paths}

        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertEqual(before, after, "the read-only promotion audit rewrote immutable evidence")
        self.assertEqual({"model:fallback"}, audit.effective_primary_model_ids)
        verified = [
            finding
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
        ]
        self.assertEqual(["promotion:q1"], [item.get("artifact_id") for item in verified])

    def test_promotion_recomputes_manual_block_against_actual_pass_bytes(self) -> None:
        root = build_promoted_release_project(trigger_score=0.95, trigger_status="BLOCK")
        report, _audit = run_audit(root)
        self.assert_code(report, "PROMOTION_TRIGGER_RECOMPUTE_MISMATCH")
        self.assertFalse(
            any(
                finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
                and finding.get("artifact_id") == "promotion:q1"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )

    def test_conditional_promotion_trigger_must_be_active(self) -> None:
        root = build_promoted_release_project(conditional_trigger_active=False)
        report, _audit = run_audit(root)
        self.assert_code(report, "PROMOTION_TRIGGER_NOT_BLOCKING")
        self.assertFalse(
            any(
                finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
                and finding.get("artifact_id") == "promotion:q1"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )

    def test_promotion_event_fingerprint_must_remain_current(self) -> None:
        root = build_promoted_release_project()
        mutate_yaml(
            root,
            "specs/model-promotion.yaml",
            lambda doc: doc["fingerprints"].update({"model:main": "1" * 64}),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "PROMOTION_FINGERPRINT_STALE")
        self.assertFalse(
            any(
                finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
                and finding.get("artifact_id") == "promotion:q1"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )

    def test_promoted_result_must_start_after_activation(self) -> None:
        root = build_promoted_release_project(promoted_started_at="2026-08-27T00:00:01Z")
        report, audit = run_audit(root)
        self.assert_code(report, "RUN_TIME_INVALID")
        self.assertFalse(audit.result_eligibility["result:promoted"])
        self.assertFalse(
            any(
                finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
                and finding.get("artifact_id") == "promotion:q1"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )

    def test_promotion_needs_an_eligible_post_activation_result(self) -> None:
        root = build_promoted_release_project(promoted_success=False)
        report, audit = run_audit(root)
        self.assert_code(report, "PROMOTED_ROUTE_WITHOUT_ELIGIBLE_RESULT")
        self.assertFalse(audit.result_eligibility["result:promoted"])
        self.assertFalse(
            any(
                finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
                and finding.get("artifact_id") == "promotion:q1"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )

    def test_promotion_trigger_must_pass_its_full_result_contract(self) -> None:
        root = build_promotion_release_project()
        primary = load_yaml(root / "specs/model_spec.yaml")
        primary["validation_plan"]["checks"].append(
            {
                "id": "check:secondary-required",
                "check_type": "other",
                "applicability": "required",
                "activation_condition": None,
                "criticality": "blocking",
                "rationale": "A second predeclared check must not disappear during promotion.",
                "procedure": "Verify a second registered invariant.",
                "pass_rule": "The second invariant passes.",
                "threshold": None,
                "failure_response": "block_result",
            }
        )
        write_yaml(root, "specs/model_spec.yaml", primary)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "VALIDATION_CHECK_EVIDENCE_AMBIGUOUS")
        self.assertNotIn("FALLBACK_PROMOTION_EVENT_VERIFIED", finding_codes(report))

    def test_same_effective_route_cannot_be_replaced_twice(self) -> None:
        root = build_promoted_release_project()
        duplicate = load_yaml(root / "specs/model-promotion.yaml")
        duplicate["id"] = "promotion:q2"
        duplicate["approved_by"] = "second fixture reviewer"
        duplicate["rationale"] = "Deliberately duplicated replacement for an adversarial test."
        write_yaml(root, "specs/model-promotion-duplicate.yaml", duplicate)
        manifest = load_yaml(root / "manifest.yaml")
        manifest["artifacts"].append(
            {
                "id": "promotion:q2",
                "kind": "model_promotion",
                "path": "specs/model-promotion-duplicate.yaml",
                "sha256": sha256_file(root / "specs/model-promotion-duplicate.yaml"),
                "required": True,
                "depends_on": ["model:fallback", "model:main", "result:main"],
            }
        )
        write_yaml(root, "manifest.yaml", manifest)
        refresh_manifest_artifact_hashes(root)
        rebuild_release_reviews(root, first_second=30)

        report, _audit = run_audit(root)
        duplicated = [
            finding
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "PROMOTION_REPLACED_ROUTE_NOT_EFFECTIVE"
            and finding.get("artifact_id") == "promotion:q2"
        ]
        self.assertTrue(duplicated, finding_codes(report))
        self.assertFalse(
            any(
                finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
                and finding.get("artifact_id") == "promotion:q2"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )

    def test_enabled_competition_profile_is_bound_through_g6(self) -> None:
        root = build_release_project()
        enable_competition_profile(root)
        report, _audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assert_code(report, "FORMAT_PROFILE_BOUND")
        self.assert_code(report, "PAPER_BUILD_RECEIPT_VERIFIED")

    def test_changed_competition_profile_stales_release(self) -> None:
        root = build_release_project()
        enable_competition_profile(root)
        with (root / "profiles/cumcm-format.yaml").open("a", encoding="utf-8") as handle:
            handle.write("margin: changed-after-review\n")
        report, _audit = run_audit(root)
        self.assert_code(report, "COMPETITION_PROFILE_HASH_MISMATCH")
        self.assert_code(report, "FORMAT_PROFILE_STALE")
        self.assertNotEqual("PASS", report["status"])

    def test_direct_latex_build_requires_recorder_flag(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "paper/paper-build.yaml",
            lambda doc: doc["command"].update(argv=["latexmk", "main.tex"]),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "LATEX_RECORDER_FLAG_MISSING")

    def test_latex_recorder_must_contain_real_local_inputs(self) -> None:
        for content in ("", "PWD .\n", "INPUT missing-local-file.tex\n"):
            with self.subTest(content=content):
                root = build_release_project()
                write_text(root, "paper/main.fls", content)
                rebind_paper_receipt(root)
                report, _audit = run_audit(root)
                self.assert_code(report, "LATEX_RECORDER_INVALID")

    def test_latex_recorder_pwd_must_equal_declared_compile_cwd(self) -> None:
        root = build_release_project()
        write_text(root, "paper/main.fls", "PWD ..\nINPUT paper/main.tex\n")
        rebind_paper_receipt(root)
        report, _audit = run_audit(root)
        self.assert_code(report, "LATEX_RECORDER_INVALID")

    def test_recorder_observed_local_resource_must_be_registered(self) -> None:
        root = build_release_project()
        write_text(
            root,
            "paper/local.cls",
            "\\NeedsTeXFormat{LaTeX2e}\n\\ProvidesClass{local}\n\\LoadClass{article}\n",
        )
        write_text(
            root,
            "paper/main.tex",
            "\\documentclass{local}\n\\begin{document}\nValidated score 0.95. % claim:c1\n\\end{document}\n",
        )
        write_text(root, "paper/main.fls", "PWD .\nINPUT main.tex\nINPUT local.cls\n")
        # Deliberately omit local.cls from resource_files.
        rebind_paper_receipt(root)
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_BUILD_RESOURCE_SET_MISMATCH")

    def test_recorder_observed_resource_change_stales_receipt(self) -> None:
        root = build_release_project()
        write_text(
            root,
            "paper/local.cls",
            "\\NeedsTeXFormat{LaTeX2e}\n\\ProvidesClass{local}\n\\LoadClass{article}\n",
        )
        write_text(
            root,
            "paper/main.tex",
            "\\documentclass{local}\n\\begin{document}\nValidated score 0.95. % claim:c1\n\\end{document}\n",
        )
        write_text(root, "paper/main.fls", "PWD .\nINPUT main.tex\nINPUT local.cls\n")
        rebind_paper_receipt(root, resource_files=["paper/local.cls"])
        positive, _audit = run_audit(root)
        self.assertEqual("PASS", positive["status"], finding_codes(positive))

        with (root / "paper/local.cls").open("a", encoding="utf-8") as handle:
            handle.write("% changed after compilation\n")
        report, _audit = run_audit(root)
        self.assert_code(report, "FILE_HASH_MISMATCH")
        self.assert_code(report, "PAPER_BUILD_RESOURCE_SET_MISMATCH")

    def test_packaged_proof_only_release_passes_without_run_entrypoint(self) -> None:
        root = build_proof_only_release_project()
        report, audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertEqual(set(), audit.effective_primary_model_ids)
        self.assert_code(report, "PROOF_ARTIFACT_VERIFIED")
        self.assert_code(report, "PROOF_PACKAGED")
        self.assert_code(report, "PROOF_ONLY_RELEASE_NO_RUN_REQUIRED")

    def test_zero_byte_proof_is_rejected(self) -> None:
        root = build_proof_only_release_project()
        write_text(root, "paper/proof.tex", "")
        mutate_yaml(
            root,
            "claims/claims.yaml",
            lambda doc: doc["claims"][0].update(proof_artifact=file_ref(root, "paper/proof.tex")),
        )
        report, audit = run_audit(root)
        self.assert_code(report, "PROOF_ARTIFACT_INVALID")
        self.assertNotIn("claim:c1", audit.valid_final_proof_claim_ids)

    def test_arbitrary_binary_proof_is_rejected(self) -> None:
        root = build_proof_only_release_project()
        binary_path = root / "paper/proof.bin"
        binary_path.write_bytes(b"\x00\xff\x10not-a-proof")
        mutate_yaml(
            root,
            "claims/claims.yaml",
            lambda doc: doc["claims"][0].update(proof_artifact=file_ref(root, "paper/proof.bin")),
        )
        report, audit = run_audit(root)
        self.assert_code(report, "PROOF_ARTIFACT_INVALID")
        self.assertNotIn("claim:c1", audit.valid_final_proof_claim_ids)

    def test_blank_pdf_proof_is_rejected(self) -> None:
        from pypdf import PdfWriter

        root = build_proof_only_release_project()
        proof_path = root / "paper/blank-proof.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with proof_path.open("wb") as handle:
            writer.write(handle)
        mutate_yaml(
            root,
            "claims/claims.yaml",
            lambda doc: doc["claims"][0].update(proof_artifact=file_ref(root, "paper/blank-proof.pdf")),
        )
        report, audit = run_audit(root)
        self.assert_code(report, "PROOF_ARTIFACT_INVALID")
        self.assertNotIn("claim:c1", audit.valid_final_proof_claim_ids)

    def test_valid_but_detached_proof_is_not_release_evidence(self) -> None:
        root = build_proof_only_release_project()
        write_text(
            root,
            "paper/main.tex",
            "\\documentclass{article}\n\\begin{document}\n"
            "The requested proposition is proved. % claim:c1\n\\end{document}\n",
        )
        write_text(root, "paper/main.fls", "PWD .\nINPUT main.tex\n")
        rebind_paper_receipt(root, source_files=["paper/main.tex"])
        report, _audit = run_audit(root)
        self.assert_code(report, "PROOF_NOT_PACKAGED")
        self.assertNotEqual("PASS", report["status"])

    def test_unverified_receipt_declaration_cannot_package_proof(self) -> None:
        root = build_proof_only_release_project()
        write_text(
            root,
            "paper/main.tex",
            "\\documentclass{article}\n\\begin{document}\n"
            "The requested proposition is proved. % claim:c1\n\\end{document}\n",
        )
        write_text(root, "paper/main.fls", "PWD .\nINPUT main.tex\n")
        rebind_paper_receipt(
            root,
            source_files=["paper/main.tex"],
            resource_files=["paper/proof.tex"],
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_BUILD_RESOURCE_SET_MISMATCH")
        self.assert_code(report, "PROOF_NOT_PACKAGED")
        self.assertNotIn("PROOF_PACKAGED", finding_codes(report))

    def test_required_appendix_can_package_detached_proof(self) -> None:
        root = build_proof_only_release_project()
        write_text(
            root,
            "paper/main.tex",
            "\\documentclass{article}\n\\begin{document}\n"
            "The requested proposition is proved. % claim:c1\n\\end{document}\n",
        )
        write_text(root, "paper/main.fls", "PWD .\nINPUT main.tex\n")
        rebind_paper_receipt(root, source_files=["paper/main.tex"])
        manifest = load_yaml(root / "manifest.yaml")
        manifest["deliverables"].append(
            {
                "id": "deliverable:proof-appendix",
                **file_ref(root, "paper/proof.tex"),
                "required": True,
                "role": "appendix",
                "media_type": "application/x-tex",
            }
        )
        write_yaml(root, "manifest.yaml", manifest)
        rebuild_release_reviews(root, first_second=30)
        report, _audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assert_code(report, "PROOF_PACKAGED")

    def test_release_must_package_every_declared_runtime_code_file(self) -> None:
        root = build_release_project()
        add_runtime_helper(root, package_helper=False)
        report, _audit = run_audit(root)
        self.assert_code(report, "CODE_DELIVERABLE_COVERAGE_MISSING")
        messages = [
            finding["message"]
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "CODE_DELIVERABLE_COVERAGE_MISSING"
        ]
        self.assertTrue(any("code/helper.py" in message for message in messages), messages)

    def test_packaged_runtime_helper_preserves_release_pass(self) -> None:
        root = build_release_project()
        add_runtime_helper(root, package_helper=True)
        report, _audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))

    def test_static_typst_build_receipt_passes(self) -> None:
        root = build_release_project()
        convert_release_to_typst(root)
        report, _audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assert_code(report, "PAPER_BUILD_RECEIPT_VERIFIED")

    def test_typst_command_must_name_registered_pdf_output(self) -> None:
        root = build_release_project()
        convert_release_to_typst(root)
        mutate_yaml(
            root,
            "paper/paper-build.yaml",
            lambda doc: doc["command"].update(argv=["typst", "compile", "main.typ"]),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_BUILD_TYPST_OUTPUT_ARGUMENT_MISSING")

    def test_paper_compiler_shell_execution_flag_is_blocked(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "paper/paper-build.yaml",
            lambda doc: doc["command"].update(
                argv=["latexmk", "-recorder", "-shell-escape", "main.tex"]
            ),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_BUILD_DANGEROUS_FLAG")

    def test_empty_paper_build_log_is_blocked_even_when_hashed(self) -> None:
        root = build_release_project()
        write_text(root, "paper/build.log", "")
        rebind_paper_receipt(root)
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_BUILD_LOG_EMPTY")

    def test_paper_build_cannot_predate_upstream_result(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "paper/paper-build.yaml",
            lambda doc: doc.update(
                started_at="2026-08-26T23:59:58Z",
                finished_at="2026-08-26T23:59:59Z",
            ),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_BUILD_TIME_INVALID")
        messages = [
            finding["message"]
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "PAPER_BUILD_TIME_INVALID"
        ]
        self.assertTrue(any("upstream result" in message for message in messages), messages)

    def test_dummy_result_deliverable_cannot_replace_published_output(self) -> None:
        root = build_release_project()
        write_text(root, "outputs/dummy.json", '{"placeholder": true}\n')
        manifest = load_yaml(root / "manifest.yaml")
        result_deliverable = next(
            item for item in manifest["deliverables"] if item["id"] == "deliverable:result"
        )
        result_deliverable.update(file_ref(root, "outputs/dummy.json"))
        write_yaml(root, "manifest.yaml", manifest)
        rebuild_release_reviews(root, first_second=20)
        report, _audit = run_audit(root)
        self.assert_code(report, "RESULT_DELIVERABLE_COVERAGE_MISSING")

    def test_01_complete_promotion_release_passes_all_gates(self) -> None:
        root = build_promotion_release_project()
        report, audit = run_audit(root)
        self.assertEqual("PASS", report["status"], finding_codes(report))
        self.assertTrue(all(gate["status"] == "PASS" for gate in report["gates"]), report["gates"])
        self.assertFalse(audit.result_eligibility["result:main"])
        self.assertTrue(audit.result_eligibility["result:promoted"])
        self.assert_code(report, "FALLBACK_PROMOTION_EVENT_VERIFIED")
        self.assert_code(report, "PROMOTED_ROUTE_RESULT_ELIGIBLE")

    def test_promotion_recomputes_handwritten_block_status(self) -> None:
        root = build_promotion_release_project()
        model = load_yaml(root / "specs/model_spec.yaml")
        model["validation_plan"]["checks"][0]["threshold"]["operator"] = "<="
        write_yaml(root, "specs/model_spec.yaml", model)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "PROMOTION_TRIGGER_RECOMPUTE_MISMATCH")
        self.assertNotIn("FALLBACK_PROMOTION_EVENT_VERIFIED", finding_codes(report))

    def test_promotion_must_follow_predeclared_fallback_route(self) -> None:
        root = build_promotion_release_project()
        model = load_yaml(root / "specs/model_spec.yaml")
        model["fallback_rules"] = []
        write_yaml(root, "specs/model_spec.yaml", model)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "PROMOTION_RULE_AMBIGUOUS")
        self.assertNotIn("FALLBACK_PROMOTION_EVENT_VERIFIED", finding_codes(report))

    def test_promotion_rejects_stale_event_and_trigger_fingerprints(self) -> None:
        root = build_promotion_release_project()
        mutate_yaml(
            root,
            "specs/model_spec.yaml",
            lambda doc: doc["method_selection"].update(
                rationale="The selected route changed after the promotion receipt was signed."
            ),
        )

        report, _audit = run_audit(root)
        self.assertTrue(
            {"PROMOTION_FINGERPRINT_STALE", "PROMOTION_TRIGGER_FINGERPRINT_STALE"}.intersection(
                finding_codes(report)
            ),
            finding_codes(report),
        )
        self.assertNotIn("FALLBACK_PROMOTION_EVENT_VERIFIED", finding_codes(report))

    def test_promoted_run_cannot_start_before_activation(self) -> None:
        root = build_promotion_release_project()
        result = load_yaml(root / "results/result-promoted.yaml")
        result["run"].update(
            started_at="2026-08-27T00:00:01Z",
            finished_at="2026-08-27T00:00:03Z",
        )
        write_yaml(root, "results/result-promoted.yaml", result)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "RUN_TIME_INVALID")
        self.assertNotIn("FALLBACK_PROMOTION_EVENT_VERIFIED", finding_codes(report))

    def test_duplicate_promotion_cannot_replace_an_already_replaced_route(self) -> None:
        root = build_promotion_release_project()
        duplicate = load_yaml(root / "specs/model-promotion.yaml")
        duplicate["id"] = "promotion:q1-duplicate"
        duplicate["promoted_at"] = "2026-08-27T00:00:07Z"
        write_yaml(root, "specs/model-promotion-duplicate.yaml", duplicate)

        manifest = load_yaml(root / "manifest.yaml")
        manifest["artifacts"].append(
            {
                "id": "promotion:q1-duplicate",
                "kind": "model_promotion",
                "path": "specs/model-promotion-duplicate.yaml",
                "sha256": sha256_file(root / "specs/model-promotion-duplicate.yaml"),
                "required": True,
                "depends_on": ["model:fallback", "model:main", "result:main"],
            }
        )
        write_yaml(root, "manifest.yaml", manifest)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "PROMOTION_REPLACED_ROUTE_NOT_EFFECTIVE")
        self.assert_code(report, "PROMOTION_FALLBACK_ALREADY_ACTIVATED")
        self.assertTrue(
            any(
                finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
                and finding.get("artifact_id") == "promotion:q1"
                for gate in report["gates"]
                for finding in gate["findings"]
            )
        )
        duplicate_verified = [
            finding
            for gate in report["gates"]
            for finding in gate["findings"]
            if finding["code"] == "FALLBACK_PROMOTION_EVENT_VERIFIED"
            and finding.get("artifact_id") == "promotion:q1-duplicate"
        ]
        self.assertEqual([], duplicate_verified)

    def test_every_experiment_code_file_is_a_required_deliverable(self) -> None:
        root = build_release_project()
        write_text(root, "code/helper.py", "VALUE = 1\n")
        experiment = load_yaml(root / "experiments/experiment.yaml")
        experiment["code_files"].append(file_ref(root, "code/helper.py"))
        write_yaml(root, "experiments/experiment.yaml", experiment)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "CODE_DELIVERABLE_COVERAGE_MISSING")

    def test_valid_proof_cannot_remain_detached_from_release_package(self) -> None:
        root = build_release_project()
        write_text(
            root,
            "proofs/claim-proof.md",
            "Claim-ID: claim:c1\n"
            "Proposition: The validated score is 0.95.\n"
            "Derivation:\n"
            "Let the registered synthetic score be s. By construction s = 0.95.\n"
            "Therefore the validated score is 0.95. QED.\n",
        )
        claims = load_yaml(root / "claims/claims.yaml")
        claim = claims["claims"][0]
        claim.update(
            claim_type="theoretical",
            epistemic_status="analytically_derived",
            proof_artifact=file_ref(root, "proofs/claim-proof.md"),
        )
        write_yaml(root, "claims/claims.yaml", claims)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "PROOF_ARTIFACT_VERIFIED")
        self.assert_code(report, "PROOF_NOT_PACKAGED")

    def test_empty_proof_file_cannot_support_a_final_claim(self) -> None:
        root = build_release_project()
        write_text(root, "proofs/empty-proof.md", "")
        claims = load_yaml(root / "claims/claims.yaml")
        claim = claims["claims"][0]
        claim.update(
            claim_type="theoretical",
            epistemic_status="analytically_derived",
            proof_artifact=file_ref(root, "proofs/empty-proof.md"),
        )
        write_yaml(root, "claims/claims.yaml", claims)
        resign_release_project(root)

        report, _audit = run_audit(root)
        self.assert_code(report, "PROOF_ARTIFACT_INVALID")
        self.assertNotIn("PROOF_ARTIFACT_VERIFIED", finding_codes(report))

    def test_result_template_cannot_be_accepted_as_model_data(self) -> None:
        root = build_release_project()

        def add_bad_asset(doc: dict[str, Any]) -> None:
            doc["data_assets"].append(
                {
                    "id": "data:result-template",
                    "availability": "bundled",
                    "role": "result_template",
                    "classification_basis": "human_confirmed",
                    "usable_for_modeling": True,
                    "immutable_raw": True,
                    "intended_use": "Incorrectly treated as an input.",
                    "question_refs": ["question:q1"],
                    "exclusion_reason": None,
                    "file": file_ref(root, "inputs/problem.txt"),
                    "source": "Synthetic fixture.",
                    "license": "Synthetic fixture.",
                    "columns": [],
                }
            )

        mutate_yaml(root, "specs/problem_spec.yaml", add_bad_asset)
        report, _audit = run_audit(root)
        self.assert_code(report, "INPUT_ROLE_NOT_MODEL_DATA")

    def test_filename_heuristic_cannot_confirm_usable_input(self) -> None:
        root = build_release_project()

        def add_heuristic_asset(doc: dict[str, Any]) -> None:
            doc["data_assets"].append(
                {
                    "id": "data:heuristic",
                    "availability": "bundled",
                    "role": "raw_data",
                    "classification_basis": "filename_heuristic",
                    "usable_for_modeling": True,
                    "immutable_raw": True,
                    "intended_use": "Candidate input pending content inspection.",
                    "question_refs": ["question:q1"],
                    "exclusion_reason": None,
                    "file": file_ref(root, "inputs/problem.txt"),
                    "source": "Synthetic fixture.",
                    "license": "Synthetic fixture.",
                    "columns": [],
                }
            )

        mutate_yaml(root, "specs/problem_spec.yaml", add_heuristic_asset)
        report, _audit = run_audit(root)
        self.assert_code(report, "HEURISTIC_INPUT_UNCONFIRMED")

    def test_excluded_asset_cannot_enter_experiment(self) -> None:
        root = build_release_project()

        def add_excluded_asset(doc: dict[str, Any]) -> None:
            doc["data_assets"].append(
                {
                    "id": "data:instruction",
                    "availability": "bundled",
                    "role": "instruction",
                    "classification_basis": "content_inspected",
                    "usable_for_modeling": False,
                    "immutable_raw": True,
                    "intended_use": "Read-only instructions.",
                    "question_refs": [],
                    "exclusion_reason": "Instructions are not observations.",
                    "file": file_ref(root, "inputs/problem.txt"),
                    "source": "Synthetic fixture.",
                    "license": "Synthetic fixture.",
                    "columns": [],
                }
            )

        mutate_yaml(root, "specs/problem_spec.yaml", add_excluded_asset)
        mutate_yaml(
            root,
            "experiments/experiment.yaml",
            lambda doc: doc.update(data_refs=["data:instruction"]),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "EXPERIMENT_USES_EXCLUDED_INPUT")

    def test_bundled_experiment_input_must_be_captured_by_result(self) -> None:
        root = build_release_project()

        def add_raw_asset(doc: dict[str, Any]) -> None:
            doc["data_assets"].append(
                {
                    "id": "data:raw",
                    "availability": "bundled",
                    "role": "raw_data",
                    "classification_basis": "content_inspected",
                    "usable_for_modeling": True,
                    "immutable_raw": True,
                    "intended_use": "Synthetic scalar input.",
                    "question_refs": ["question:q1"],
                    "exclusion_reason": None,
                    "file": file_ref(root, "inputs/problem.txt"),
                    "source": "Synthetic fixture.",
                    "license": "Synthetic fixture.",
                    "columns": [],
                }
            )

        mutate_yaml(root, "specs/problem_spec.yaml", add_raw_asset)
        mutate_yaml(
            root,
            "experiments/experiment.yaml",
            lambda doc: doc.update(data_refs=["data:raw"]),
        )
        report, audit = run_audit(root)
        self.assert_code(report, "RESULT_INPUT_NOT_CAPTURED")
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_family_validation_coverage_is_explicit(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "specs/model_spec.yaml",
            lambda doc: doc["validation_plan"]["checks"][0].update(check_type="other"),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "MODEL_VALIDATION_COVERAGE_UNDECLARED")

    def test_every_specialized_model_family_has_coverage_gate(self) -> None:
        for family, required_types in sorted(VALIDATION_COVERAGE_BY_FAMILY.items()):
            if family == "descriptive":
                continue
            with self.subTest(family=family):
                root = build_release_project()

                def select_family(doc: dict[str, Any], selected: str = family) -> None:
                    doc["model_family"] = selected
                    if selected == "optimization":
                        doc["formulation"]["objectives"] = [
                            {
                                "id": "formula:test-objective",
                                "expression": "x",
                                "format": "plain",
                                "defines": [],
                                "uses": ["symbol:x"],
                                "source_constraint_refs": [],
                                "interpretation": "Synthetic objective for schema coverage.",
                            }
                        ]
                        doc["formulation"]["constraints"] = [
                            {
                                "id": "formula:test-constraint",
                                "expression": "x >= 0",
                                "format": "plain",
                                "defines": [],
                                "uses": ["symbol:x"],
                                "source_constraint_refs": [],
                                "interpretation": "Synthetic constraint for schema coverage.",
                            }
                        ]

                mutate_yaml(
                    root,
                    "specs/model_spec.yaml",
                    select_family,
                )
                report, _audit = run_audit(root)
                self.assert_code(report, "MODEL_VALIDATION_COVERAGE_UNDECLARED")
                messages = [
                    finding["message"]
                    for gate in report["gates"]
                    for finding in gate["findings"]
                    if finding["code"] == "MODEL_VALIDATION_COVERAGE_UNDECLARED"
                ]
                self.assertTrue(any(sorted(required_types)[0] in message for message in messages), messages)

    def test_numeric_diagnostic_status_is_recomputed(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "results/results.yaml",
            lambda doc: doc["diagnostics"][0]["observed"].update(value=0.5),
        )
        report, audit = run_audit(root)
        self.assert_code(report, "DIAGNOSTIC_STATUS_MISMATCH")
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_derived_figure_cannot_use_ineligible_result(self) -> None:
        root = build_release_project()

        def add_figure(doc: dict[str, Any]) -> None:
            doc["figures"].append(
                {
                    "id": "figure:diagnostic",
                    "publication_status": "final",
                    "provenance_type": "derived",
                    "source_result_refs": ["result:main"],
                    "source_files": [file_ref(root, "outputs/result.json")],
                    "generator_files": [file_ref(root, "code/main.py")],
                    "generator_argv": ["python", "code/main.py"],
                    "output": file_ref(root, "outputs/result.json"),
                    "panels": [],
                    "encodings": [],
                    "axes": [],
                    "caption": "Synthetic diagnostic registry entry.",
                    "alt_text": "Synthetic diagnostic output.",
                    "claim_refs": ["claim:c1"],
                    "postprocess": [],
                    "external_source": None,
                    "license": None,
                }
            )

        mutate_yaml(root, "figures/figures.yaml", add_figure)
        mutate_yaml(
            root,
            "results/results.yaml",
            lambda doc: doc["outputs"][0].update(comparison_status="BLOCK"),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "FIGURE_SOURCE_RESULT_INELIGIBLE")

    def test_every_planned_required_check_needs_one_diagnostic(self) -> None:
        root = build_release_project()

        def add_check(doc: dict[str, Any]) -> None:
            doc["validation_plan"]["checks"].append(
                {
                    "id": "check:reproducibility",
                    "check_type": "reproducibility",
                    "applicability": "required",
                    "activation_condition": None,
                    "criticality": "blocking",
                    "rationale": "A required second check for the negative fixture.",
                    "procedure": "Repeat the deterministic run.",
                    "pass_rule": "The repeated bytes match.",
                    "threshold": None,
                    "failure_response": "block_result",
                }
            )

        mutate_yaml(root, "specs/model_spec.yaml", add_check)
        report, audit = run_audit(root)
        self.assert_code(report, "VALIDATION_CHECK_EVIDENCE_AMBIGUOUS")
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_run_must_complete_declared_repetitions(self) -> None:
        root = build_release_project()
        mutate_yaml(
            root,
            "results/results.yaml",
            lambda doc: doc["run"].update(repetitions_completed=0),
        )
        report, audit = run_audit(root)
        self.assert_code(report, "RUN_REPETITIONS_MISMATCH")
        self.assertFalse(audit.result_eligibility["result:main"])

    def test_selected_baseline_requires_comparable_experiment_and_result(self) -> None:
        root = build_release_project()
        baseline = load_yaml(root / "specs/model_spec.yaml")
        baseline["id"] = "model:baseline"
        baseline["role"] = "baseline"
        baseline["symbols"][0]["id"] = "symbol:baseline-x"
        baseline["validation_plan"]["checks"][0]["id"] = "check:baseline-input-integrity"
        write_yaml(root, "specs/baseline.yaml", baseline)

        manifest = load_yaml(root / "manifest.yaml")
        manifest["artifacts"].append(
            {
                "id": "model:baseline",
                "kind": "model_spec",
                "path": "specs/baseline.yaml",
                "sha256": sha256_file(root / "specs/baseline.yaml"),
                "required": True,
                "depends_on": ["problem:main"],
            }
        )
        for artifact in manifest["artifacts"]:
            if artifact["id"] == "model:main":
                artifact["depends_on"] = ["problem:main", "model:baseline"]
        write_yaml(root, "manifest.yaml", manifest)

        def select_baseline(doc: dict[str, Any]) -> None:
            doc["depends_on"] = ["problem:main", "model:baseline"]
            doc["method_selection"]["baseline_policy"] = {
                "status": "required",
                "model_refs": ["model:baseline"],
                "rationale": "The primary must beat the deterministic baseline.",
            }
            doc["validation_plan"]["checks"].append(
                {
                    "id": "check:baseline-comparison",
                    "check_type": "baseline_comparison",
                    "applicability": "required",
                    "activation_condition": None,
                    "criticality": "blocking",
                    "rationale": "Compare the selected primary and baseline.",
                    "procedure": "Run both models with the same metric.",
                    "pass_rule": "The primary is no worse than the baseline.",
                    "threshold": None,
                    "failure_response": "block_result",
                }
            )

        mutate_yaml(root, "specs/model_spec.yaml", select_baseline)
        mutate_yaml(
            root,
            "experiments/experiment.yaml",
            lambda doc: doc.update(baseline_refs=["model:baseline"]),
        )
        report, _audit = run_audit(root)
        self.assert_code(report, "BASELINE_EXPERIMENT_NOT_COMPARABLE")
        self.assert_code(report, "BASELINE_RESULT_INELIGIBLE")

    def test_primary_result_can_bind_eligible_baseline_result(self) -> None:
        root = build_release_project()
        write_text(root, "code/baseline.py", "print('baseline')\n")
        write_text(root, "outputs/baseline.json", '{"score": 0.95}\n')

        baseline_model = load_yaml(root / "specs/model_spec.yaml")
        baseline_model["id"] = "model:baseline"
        baseline_model["role"] = "baseline"
        baseline_model["symbols"][0]["id"] = "symbol:baseline-x"
        baseline_model["algorithm"]["entrypoint"] = "code/baseline.py"
        baseline_model["algorithm"]["description"] = "Emit the independent deterministic baseline output."
        baseline_model["validation_plan"]["checks"][0]["id"] = "check:baseline-input-integrity"
        write_yaml(root, "specs/baseline.yaml", baseline_model)

        primary_model = load_yaml(root / "specs/model_spec.yaml")
        primary_model["depends_on"] = ["problem:main", "model:baseline"]
        primary_model["method_selection"]["baseline_policy"] = {
            "status": "required",
            "model_refs": ["model:baseline"],
            "rationale": "The primary is compared with the synthetic baseline.",
        }
        primary_model["validation_plan"]["checks"].append(
            {
                "id": "check:baseline-comparison",
                "check_type": "baseline_comparison",
                "applicability": "required",
                "activation_condition": None,
                "criticality": "blocking",
                "rationale": "A selected baseline must be evaluated on the same metric.",
                "procedure": "Compare the primary score with the eligible baseline score.",
                "pass_rule": "The comparison is recorded against the bound baseline result.",
                "threshold": None,
                "failure_response": "block_result",
            }
        )
        write_yaml(root, "specs/model_spec.yaml", primary_model)

        baseline_experiment = load_yaml(root / "experiments/experiment.yaml")
        baseline_experiment["id"] = "experiment:baseline"
        baseline_experiment["depends_on"] = ["model:baseline"]
        baseline_experiment["model_ref"] = "model:baseline"
        baseline_experiment["baseline_refs"] = []
        baseline_experiment["code_files"] = [file_ref(root, "code/baseline.py")]
        baseline_experiment["command"]["argv"] = ["python", "code/baseline.py"]
        baseline_experiment["metrics"][0]["id"] = "metric:baseline-score"
        baseline_experiment["metrics"][0]["source_output_ref"] = "output:baseline"
        baseline_experiment["acceptance_rules"][0]["metric_ref"] = "metric:baseline-score"
        baseline_experiment["outputs"][0]["id"] = "output:baseline"
        baseline_experiment["outputs"][0]["path"] = "outputs/baseline.json"
        write_yaml(root, "experiments/baseline.yaml", baseline_experiment)

        primary_experiment = load_yaml(root / "experiments/experiment.yaml")
        primary_experiment["baseline_refs"] = ["model:baseline"]
        primary_experiment["baseline_comparison_rules"] = [
            {
                "id": "comparison:main-vs-baseline-score",
                "check_ref": "check:baseline-comparison",
                "baseline_model_ref": "model:baseline",
                "primary_metric_ref": "metric:score",
                "delta_definition": "primary_minus_baseline",
                "operator": ">=",
                "threshold": 0.0,
                "unit": "1",
                "rationale": "The primary score must be no lower than the selected baseline score.",
            }
        ]
        write_yaml(root, "experiments/experiment.yaml", primary_experiment)

        baseline_result = load_yaml(root / "results/results.yaml")
        baseline_result["id"] = "result:baseline"
        baseline_result["depends_on"] = ["experiment:baseline"]
        baseline_result["experiment_ref"] = "experiment:baseline"
        baseline_result["fingerprints"] = {
            "experiment:baseline": sha256_file(root / "experiments/baseline.yaml"),
            "model:baseline": sha256_file(root / "specs/baseline.yaml"),
            "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
        }
        baseline_result["run"]["run_id"] = "run:baseline"
        baseline_result["run"]["argv"] = ["python", "code/baseline.py"]
        baseline_result["outputs"][0]["output_ref"] = "output:baseline"
        baseline_result["outputs"][0]["file"] = file_ref(root, "outputs/baseline.json")
        baseline_result["metrics"][0]["metric_ref"] = "metric:baseline-score"
        baseline_result["diagnostics"][0]["id"] = "diagnostic:baseline-input-integrity"
        baseline_result["diagnostics"][0]["check_ref"] = "check:baseline-input-integrity"
        baseline_result["diagnostics"][0]["source_file"] = file_ref(root, "outputs/baseline.json")
        baseline_result["diagnostics"][0]["extractor"] = {"type": "json_pointer", "pointer": "/score"}
        write_yaml(root, "results/baseline.yaml", baseline_result)

        primary_result = load_yaml(root / "results/results.yaml")
        primary_result["depends_on"] = ["experiment:main", "result:baseline"]
        primary_result["fingerprints"] = {
            "experiment:main": sha256_file(root / "experiments/experiment.yaml"),
            "model:main": sha256_file(root / "specs/model_spec.yaml"),
            "model:baseline": sha256_file(root / "specs/baseline.yaml"),
            "problem:main": sha256_file(root / "specs/problem_spec.yaml"),
            "result:baseline": sha256_file(root / "results/baseline.yaml"),
            "experiment:baseline": sha256_file(root / "experiments/baseline.yaml"),
        }
        primary_result["diagnostics"].append(
            {
                "id": "diagnostic:baseline-comparison",
                "check_ref": "check:baseline-comparison",
                "check_type": "baseline_comparison",
                "status": "PASS",
                "condition_met": None,
                "condition_evidence": None,
                "severity": "major",
                "procedure": "Compared the primary and bound baseline results.",
                "observation": "Both deterministic scores are 0.95.",
                "observed": None,
                "source_file": None,
                "extractor": None,
                "conclusion": "The primary is no worse than the selected baseline.",
                "evidence_files": [file_ref(root, "outputs/result.json")],
                "comparison_bindings": [
                    {
                        "baseline_model_ref": "model:baseline",
                        "baseline_result_ref": "result:baseline",
                        "primary_metric_ref": "metric:score",
                        "baseline_metric_ref": "metric:baseline-score",
                        "comparison_rule_ref": "comparison:main-vs-baseline-score",
                        "observed_delta": {"value": 0.0, "unit": "1"},
                        "status": "PASS",
                    }
                ],
            }
        )
        write_yaml(root, "results/results.yaml", primary_result)

        manifest = load_yaml(root / "manifest.yaml")
        manifest["manifest_type"] = "project"
        for artifact in manifest["artifacts"]:
            if artifact["id"] == "model:main":
                artifact["depends_on"] = ["problem:main", "model:baseline"]
            elif artifact["id"] == "result:main":
                artifact["depends_on"] = ["experiment:main", "result:baseline"]
        manifest["artifacts"].extend(
            [
                {
                    "id": "model:baseline",
                    "kind": "model_spec",
                    "path": "specs/baseline.yaml",
                    "sha256": sha256_file(root / "specs/baseline.yaml"),
                    "required": True,
                    "depends_on": ["problem:main"],
                },
                {
                    "id": "experiment:baseline",
                    "kind": "experiment",
                    "path": "experiments/baseline.yaml",
                    "sha256": sha256_file(root / "experiments/baseline.yaml"),
                    "required": True,
                    "depends_on": ["model:baseline"],
                },
                {
                    "id": "result:baseline",
                    "kind": "results",
                    "path": "results/baseline.yaml",
                    "sha256": sha256_file(root / "results/baseline.yaml"),
                    "required": True,
                    "depends_on": ["experiment:baseline"],
                },
            ]
        )
        for artifact in manifest["artifacts"]:
            artifact["sha256"] = sha256_file(root / artifact["path"])
        write_yaml(root, "manifest.yaml", manifest)

        report, audit = run_audit(root)
        self.assertTrue(audit.result_eligibility["result:baseline"], finding_codes(report))
        self.assertTrue(audit.result_eligibility["result:main"], finding_codes(report))
        self.assert_code(report, "BASELINE_RESULT_ELIGIBLE")

    def test_release_pdf_entrypoint_must_be_real_pdf_deliverable(self) -> None:
        root = build_release_project()
        manifest = load_yaml(root / "manifest.yaml")
        manifest["entrypoints"]["pdf"] = "paper/main.tex"
        write_yaml(root, "manifest.yaml", manifest)
        report, _audit = run_audit(root)
        self.assert_code(report, "RELEASE_PDF_ENTRYPOINT_INVALID")
        self.assert_code(report, "PDF_ENTRYPOINT_NOT_HASHED_DELIVERABLE")

    def test_corrupt_release_pdf_is_blocked(self) -> None:
        root = build_release_project()
        write_text(root, "paper/main.pdf", "%PDF-1.4\ntruncated without objects or xref\n")
        report, _audit = run_audit(root)
        self.assert_code(report, "PAPER_PDF_READ_FAILED")

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
        self.assert_code(report, "OUTPUT_COMPARISON_STATUS_MISMATCH")

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
            tied["approval_set_id"] = "approval:g2-tied"
            tied["reviewed_at"] = original["reviewed_at"]
            tied["decision"] = "BLOCK"
            tied["rationale"] = "Conflicting same-time review."
            doc["reviews"].append(tied)
        mutate_yaml(root, "reviews/gate-reviews.yaml", add_tied_review)
        report, _audit = run_audit(root)
        self.assert_code(report, "AMBIGUOUS_LATEST_APPROVAL_SET")

    def test_review_fingerprint_must_be_cited_evidence(self) -> None:
        root = build_release_project()
        def break_evidence(doc: dict[str, Any]) -> None:
            review = next(item for item in doc["reviews"] if item["gate"] == "G5")
            review["evidence_refs"] = ["model:main"]
        mutate_yaml(root, "reviews/gate-reviews.yaml", break_evidence)
        report, _audit = run_audit(root)
        self.assert_code(report, "REVIEW_FINGERPRINT_NOT_EVIDENCE")

    def test_one_identity_cannot_sign_for_the_full_team(self) -> None:
        root = build_release_project()

        def collapse_signers(doc: dict[str, Any]) -> None:
            for review in doc["reviews"]:
                review["member_id"] = "member:modeler"
                review["reviewer"] = "Fixture Modeler"

        mutate_yaml(root, "reviews/gate-reviews.yaml", collapse_signers)
        report, _audit = run_audit(root)
        self.assert_code(report, "APPROVAL_SET_DUPLICATE_MEMBER")
        self.assertNotEqual("PASS", report["status"])

    def test_g3_requires_an_independent_computation_signer(self) -> None:
        root = build_release_project()

        def remove_computation_signature(doc: dict[str, Any]) -> None:
            doc["reviews"] = [
                review
                for review in doc["reviews"]
                if not (review["gate"] == "G3" and review["member_id"] == "member:coder")
            ]

        mutate_yaml(root, "reviews/gate-reviews.yaml", remove_computation_signature)
        report, _audit = run_audit(root)
        self.assert_code(report, "APPROVAL_ROLE_COVERAGE_MISSING")
        self.assertNotEqual("PASS", report["status"])

    def test_release_snapshot_binds_manifest_notes_and_scope(self) -> None:
        root = build_release_project()
        before, _audit = run_audit(root)
        manifest = load_yaml(root / "manifest.yaml")
        manifest["notes"].append("Changed limitation after G7 approval.")
        write_yaml(root, "manifest.yaml", manifest)

        after, _audit = run_audit(root)
        self.assertNotEqual(before["release_snapshot_sha256"], after["release_snapshot_sha256"])
        self.assert_code(after, "REVIEW_FINGERPRINT_STALE")
        self.assertEqual("RELEASE_QA", after["rollback_target"])

    def test_release_report_exposes_deterministic_resume_fields(self) -> None:
        root = build_release_project()
        first, _audit = run_audit(root)
        second, _audit = run_audit(root)
        expected = {
            "workflow_state": "SUBMISSION_READY",
            "last_valid_gate": "G7",
            "rollback_target": None,
        }
        self.assertEqual(expected, {key: first[key] for key in expected})
        self.assertEqual(
            {key: first[key] for key in (*expected, "next_legal_action")},
            {key: second[key] for key in (*expected, "next_legal_action")},
        )
        self.assertTrue(first["next_legal_action"])
        self.assertRegex(first["release_snapshot_sha256"], r"^[0-9a-f]{64}$")

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
        failed["diagnostics"][0]["id"] = "diagnostic:failed-history-input-integrity"
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
        rebuild_release_reviews(root, first_second=30)

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
