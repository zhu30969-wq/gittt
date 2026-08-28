#!/usr/bin/env python3
"""Build a synthetic, fully linked release fixture for integration testing.

The fixture contains no competition material and makes no scientific claim. It
exists only to prove that the schemas, hashes, references, numeric assertions
and G0-G7 release checks can reach PASS together.  The target must not already
exist; the script never deletes or overwrites an existing project directory.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = REPO_ROOT / "cumcm-modeling"
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _contract_support import load_yaml, sha256_file, write_text_exclusive, write_yaml_atomic  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a non-overwriting synthetic release fixture.")
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--reported-value",
        type=float,
        default=1.25,
        help="Claim value; change it to construct an intentional evidence mismatch",
    )
    parser.add_argument(
        "--claim-unit",
        default="1",
        help="Claim unit; change it to construct an intentional unit mismatch",
    )
    return parser.parse_args()


def save_yaml(root: Path, relative: str, document: dict[str, Any]) -> None:
    write_yaml_atomic(root / relative, document)


def file_ref(root: Path, relative: str, media_type: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": relative, "sha256": sha256_file(root / relative)}
    if media_type:
        result["media_type"] = media_type
    return result


def main() -> int:
    args = parse_args()
    target = args.target.resolve()
    if target.exists():
        print(json.dumps({"status": "BLOCK", "message": f"target already exists: {target}"}, ensure_ascii=False))
        return 10

    init = subprocess.run(
        [sys.executable, str(SCRIPTS / "init_project.py"), str(target), "--project-id", "project:synthetic-release"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if init.returncode != 0:
        print(init.stdout)
        print(init.stderr, file=sys.stderr)
        return init.returncode

    # Create only synthetic, deterministic support files.  The numerical
    # output is deliberately trivial so the test checks traceability rather
    # than pretending to evaluate a real model.
    files = {
        "inputs/problem-statement.txt": "Synthetic identity-model fixture.\n",
        "code/main.py": (
            "\"\"\"Synthetic fixture entrypoint; not a competition solver.\"\"\"\n"
            "import json\n"
            "from pathlib import Path\n"
            "Path('outputs').mkdir(exist_ok=True)\n"
            "Path('outputs/result.json').write_text(json.dumps({'score': 1.25}) + '\\n', encoding='utf-8')\n"
        ),
        "requirements.txt": "# Standard-library-only synthetic fixture.\n",
        "outputs/result.json": '{"score": 1.25}\n',
        "logs/run.log": "synthetic run completed\n",
        "paper/main.tex": (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "% claim:c1\n"
            "The synthetic registered score is 1.25.\n"
            "\\end{document}\n"
        ),
    }
    for relative, content in files.items():
        write_text_exclusive(target / relative, content)

    problem = load_yaml(target / "specs/problem_spec.yaml")
    problem["lifecycle_status"] = "frozen"
    problem["contest"]["year"] = 2026
    problem["contest"]["problem_code"] = "SYNTHETIC"
    problem["statement"] = {
        **file_ref(target, "inputs/problem-statement.txt", "text/plain"),
        "language": "en",
    }
    problem["questions"][0]["text"] = "Verify a deterministic identity-model evidence chain."
    problem["questions"][0]["evaluation_intent"] = "The registered score and paper value must agree."
    problem["assumptions"][0].update(
        {
            "text": "The fixture value is treated as exact test input.",
            "impact": "Only the synthetic smoke test depends on it.",
            "check": "Compare the registered result and claim numerically.",
        }
    )
    save_yaml(target, "specs/problem_spec.yaml", problem)

    model = load_yaml(target / "specs/model_spec.yaml")
    model["lifecycle_status"] = "frozen"
    model["algorithm"].update(
        {
            "description": "Read the synthetic value and emit a deterministic JSON result.",
            "termination": "One deterministic pass.",
            "complexity_note": "Constant time and space for the fixture.",
        }
    )
    model["validation_plan"] = {
        "methods": ["Exact registered-value comparison."],
        "failure_criteria": ["Any value, unit, hash or reference mismatch."],
        "human_review_required": True,
    }
    model["applicability"] = "Only this synthetic integration fixture."
    model["failure_modes"] = ["A mutated file or mismatched value must fail the audit."]
    save_yaml(target, "specs/model_spec.yaml", model)

    experiment = load_yaml(target / "experiments/experiment.yaml")
    experiment["lifecycle_status"] = "frozen"
    experiment["mode"] = "confirmatory"
    experiment["purpose"] = "Confirm the deterministic fixture output and evidence links."
    experiment["hypothesis"] = "The generated score equals 1.25."
    experiment["code_files"] = [file_ref(target, "code/main.py", "text/x-python")]
    experiment["environment"] = file_ref(target, "requirements.txt", "text/plain")
    experiment["split_strategy"] = "Not applicable: deterministic synthetic value."
    experiment["acceptance_rules"] = [
        {
            "metric_ref": "metric:primary",
            "operator": "==",
            "threshold": 1.25,
            "unit": "1",
            "registration_timing": "pre_result",
            "rationale": "Fixture constant.",
        }
    ]
    experiment["resource_note"] = "Standard-library Python; negligible resources."
    save_yaml(target, "experiments/experiment.yaml", experiment)

    result = load_yaml(target / "results/results.yaml")
    result["lifecycle_status"] = "frozen"
    result["run_status"] = "success"
    # A result binds the full upstream dependency closure.  Recording only the
    # experiment bytes would allow a changed model or problem statement to keep
    # an otherwise stale result looking current.
    result["fingerprints"] = {
        "experiment:main": sha256_file(target / "experiments/experiment.yaml"),
        "model:main": sha256_file(target / "specs/model_spec.yaml"),
        "problem:main": sha256_file(target / "specs/problem_spec.yaml"),
    }
    result["run"] = {
        "run_id": "run:synthetic-001",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:01Z",
        "argv": ["python", "code/main.py"],
        "cwd": ".",
        "exit_code": 0,
        "seeds": [2026],
        "platform": "synthetic-fixture",
        "git_commit": None,
        "git_dirty": None,
        "environment_note": "Synthetic release-fixture run.",
    }
    result["inputs"] = [file_ref(target, "inputs/problem-statement.txt", "text/plain")]
    result["outputs"] = [
        {
            "output_ref": "output:primary",
            "file": file_ref(target, "outputs/result.json", "application/json"),
            "comparison_status": "PASS",
            "comparison_note": "Exact synthetic JSON value matched.",
        }
    ]
    result["metrics"] = [
        {
            "metric_ref": "metric:primary",
            "measurement": {"value": 1.25, "unit": "1"},
            "sample_size": 1,
            "uncertainty": None,
        }
    ]
    result["diagnostics"] = ["Deterministic exact-value check passed."]
    result["warnings"] = []
    result["failure_reason"] = None
    result["logs"] = [file_ref(target, "logs/run.log", "text/plain")]
    save_yaml(target, "results/results.yaml", result)

    claims = load_yaml(target / "claims/claims.yaml")
    claims["lifecycle_status"] = "frozen"
    claim = claims["claims"][0]
    claim.update(
        {
            "statement": f"The synthetic registered score is {args.reported_value}.",
            "epistemic_status": "empirically_supported",
            "publication_status": "final",
            "scope": "Only the deterministic synthetic fixture.",
            "evidence_refs": [{"ref": "result:main", "pointer": "/metrics/0", "role": "primary metric"}],
            "limitations": ["This fixture does not establish performance on a real CUMCM problem."],
            "numeric_assertions": [
                {
                    "metric_ref": "metric:primary",
                    "reported_value": args.reported_value,
                    "absolute_tolerance": 0.0,
                    "relative_tolerance": 0.0,
                    "unit": args.claim_unit,
                }
            ],
            "paper_markers": ["claim:c1"],
            "human_review": {
                "status": "PASS",
                "reviewer": "fixture-human",
                "rationale": "Synthetic value, unit and source were checked for integration testing.",
            },
        }
    )
    save_yaml(target, "claims/claims.yaml", claims)

    figures = load_yaml(target / "figures/figures.yaml")
    figures["lifecycle_status"] = "frozen"
    save_yaml(target, "figures/figures.yaml", figures)

    # Bind every human gate to the exact evidence class it approves.  G3 also
    # binds the environment, G5 covers both claims and figures, G6 binds the
    # paper source as an entrypoint and deliverable, and G7 binds every required
    # release file.  Distinct timestamps keep latest-review selection stable.
    gate_artifacts = {
        "G0": ["problem:main"],
        "G1": ["problem:main"],
        "G2": ["model:main"],
        "G3": ["experiment:main", "environment:python"],
        "G4": ["result:main"],
        "G5": ["claims:main", "figures:main"],
        "G6": ["entrypoint:paper", "deliverable:paper"],
        "G7": ["deliverable:paper", "deliverable:code", "deliverable:result"],
    }
    artifact_paths = {
        "problem:main": "specs/problem_spec.yaml",
        "model:main": "specs/model_spec.yaml",
        "experiment:main": "experiments/experiment.yaml",
        "result:main": "results/results.yaml",
        "claims:main": "claims/claims.yaml",
        "figures:main": "figures/figures.yaml",
        "environment:python": "requirements.txt",
        "entrypoint:paper": "paper/main.tex",
        "deliverable:paper": "paper/main.tex",
        "deliverable:code": "code/main.py",
        "deliverable:result": "outputs/result.json",
    }
    review_log = load_yaml(target / "reviews/gate-reviews.yaml")
    review_log["lifecycle_status"] = "frozen"
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    review_log["reviews"] = []
    for index, (gate, artifact_ids) in enumerate(gate_artifacts.items()):
        review_log["reviews"].append(
            {
                "id": f"review:{gate.lower()}-fixture",
                "gate": gate,
                "decision": "PASS",
                "basis": "human",
                "reviewer": "fixture-human",
                "reviewed_at": (base_time + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
                "rationale": "Synthetic integration evidence was checked for this gate.",
                "evidence_refs": artifact_ids,
                "artifact_fingerprints": {
                    artifact_id: sha256_file(target / artifact_paths[artifact_id])
                    for artifact_id in artifact_ids
                },
                "conditions": [],
            }
        )
    save_yaml(target, "reviews/gate-reviews.yaml", review_log)

    manifest = load_yaml(target / "manifest.yaml")
    manifest["manifest_type"] = "release"
    manifest["lifecycle_status"] = "frozen"
    manifest["revision"] = 2
    manifest["entrypoints"] = {"run": "code/main.py", "paper": "paper/main.tex"}
    manifest["environment_files"] = [
        {"id": "environment:python", **file_ref(target, "requirements.txt", "text/plain")}
    ]
    manifest["deliverables"] = [
        {"id": "deliverable:paper", **file_ref(target, "paper/main.tex"), "required": True},
        {"id": "deliverable:code", **file_ref(target, "code/main.py"), "required": True},
        {"id": "deliverable:result", **file_ref(target, "outputs/result.json"), "required": True},
    ]
    manifest["notes"] = ["Synthetic release fixture; no real contest content or performance claim."]
    for artifact in manifest["artifacts"]:
        artifact["sha256"] = sha256_file(target / artifact["path"])
    save_yaml(target, "manifest.yaml", manifest)

    print(json.dumps({"status": "PASS", "target": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
