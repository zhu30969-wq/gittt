#!/usr/bin/env python3
"""Build a synthetic, fully linked release fixture for integration testing.

The fixture contains no competition material and makes no scientific claim. It
exists only to prove that the schemas, hashes, references, numeric assertions
and G0-G7 release checks can reach PASS together.  The target must not already
exist; the script never deletes or overwrites an existing project directory.
"""

from __future__ import annotations

import argparse
import hashlib
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
        "--profile",
        choices=("identity", "e01-optimization"),
        default="identity",
        help="Synthetic fixture profile; the default preserves the historical identity fixture",
    )
    parser.add_argument(
        "--initial-audit-report",
        type=Path,
        help="Optional new path for the initialized-placeholder audit captured before fixture completion",
    )
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


def validation_check(
    check_type: str,
    *,
    applicability: str = "not_applicable",
    threshold: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one explicit synthetic validation-plan row."""

    actionable = applicability == "required"
    return {
        "id": f"check:{check_type.replace('_', '-')}",
        "check_type": check_type,
        "applicability": applicability,
        "activation_condition": None,
        "criticality": "blocking" if actionable else "advisory",
        "rationale": (
            f"The synthetic E01 fixture executes {check_type}."
            if actionable
            else f"The synthetic E01 fixture records why {check_type} is outside this micro-problem."
        ),
        "procedure": (
            f"Recompute the registered {check_type} evidence."
            if actionable
            else "No procedure is run for this deliberately inapplicable synthetic check."
        ),
        "pass_rule": (
            f"The registered {check_type} evidence satisfies its predeclared rule."
            if actionable
            else "The check remains explicitly registered as not applicable."
        ),
        "threshold": threshold,
        "failure_response": "block_result" if actionable else "report_only",
    }


def result_diagnostic(
    root: Path,
    check_type: str,
    *,
    observed_value: float,
    source_path: str,
    pointer: str,
    evidence_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Return a threshold-backed diagnostic for the E01 fixture."""

    return {
        "id": f"diagnostic:{check_type.replace('_', '-')}",
        "check_ref": f"check:{check_type.replace('_', '-')}",
        "check_type": check_type,
        "status": "PASS",
        "condition_met": None,
        "condition_evidence": None,
        "severity": "critical",
        "procedure": f"Recomputed {check_type} from a hashed synthetic output.",
        "observation": f"The synthetic {check_type} witness satisfies its rule.",
        "observed": {"value": observed_value, "unit": "1"},
        "source_file": file_ref(root, source_path, "application/json"),
        "extractor": {"type": "json_pointer", "pointer": pointer},
        "conclusion": f"The planned {check_type} check passed.",
        "evidence_files": [
            file_ref(root, path, "application/json" if path.endswith(".json") else "text/plain")
            for path in (evidence_paths or [])
        ],
        "comparison_bindings": [],
    }


def release_snapshot_sha256(root: Path, manifest: dict[str, Any]) -> str:
    """Mirror audit_project's cycle-free canonical G7 release snapshot."""

    canonical = json.loads(json.dumps(manifest, ensure_ascii=False))
    for row in canonical.get("artifacts", []):
        if isinstance(row, dict) and row.get("kind") == "gate_review":
            row["sha256"] = "<gate-review-self-reference-elided>"
    payload = {
        "snapshot_version": "1",
        "manifest": canonical,
        "entrypoint_sha256": {
            f"entrypoint:{name}": sha256_file(root / relative)
            for name, relative in sorted(manifest.get("entrypoints", {}).items())
        },
    }
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


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


def main() -> int:
    args = parse_args()
    target = args.target.resolve()
    if target.exists():
        print(json.dumps({"status": "BLOCK", "message": f"target already exists: {target}"}, ensure_ascii=False))
        return 10

    init = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "init_project.py"),
            str(target),
            "--project-id",
            "project:synthetic-release",
            "--contest-year",
            "2026",
            "--problem-code",
            "SYNTHETIC",
            "--default-seed",
            "2026",
            "--paper-engine",
            "latex",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if init.returncode != 0:
        print(init.stdout)
        print(init.stderr, file=sys.stderr)
        return init.returncode

    if args.initial_audit_report is not None:
        initial_report = args.initial_audit_report.resolve()
        if initial_report.exists():
            print(
                json.dumps(
                    {
                        "status": "BLOCK",
                        "message": f"initial audit report already exists: {initial_report}",
                    },
                    ensure_ascii=False,
                )
            )
            return 10
        initial_audit = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "audit_project.py"),
                str(target),
                "--json-report",
                str(initial_report),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if initial_audit.returncode not in {10, 12}:
            print(initial_audit.stdout)
            print(initial_audit.stderr, file=sys.stderr)
            return initial_audit.returncode or 14

    e01_optimization = args.profile == "e01-optimization"

    # Create only synthetic, deterministic support files.  The numerical
    # output is deliberately trivial so the test checks traceability rather
    # than pretending to evaluate a real model.
    e01_main_payload = {
        "auxiliary_response": 0.25,
        "max_constraint_violation": 0.0,
        "objective_bound": 1.3215,
        "primary_decision": 1.0,
        "score": 1.25,
        "solver_quality": 0.95,
    }
    e01_reconciliation_payload = {
        "best_response_objective": 1.25,
        "fixed_primary_decision": 1.0,
        "repair_gain": 0.0,
    }
    e01_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="160" viewBox="0 0 320 160">\n'
        '  <title>Synthetic fixed-decision objective</title>\n'
        '  <rect width="320" height="160" fill="white"/>\n'
        '  <line x1="48" y1="128" x2="288" y2="128" stroke="black"/>\n'
        '  <rect x="112" y="48" width="96" height="80" fill="#4c78a8"/>\n'
        '  <text x="160" y="40" text-anchor="middle" font-size="16">1.25</text>\n'
        '  <text x="160" y="150" text-anchor="middle" font-size="12">objective</text>\n'
        '</svg>\n'
    )
    files = {
        "inputs/problem-statement.txt": "Synthetic identity-model fixture.\n",
        "inputs/data.txt": "score=1.25\n",
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
        "paper/build.log": "synthetic LaTeX build completed\n",
        "paper/main.fls": "INPUT main.tex\n",
        "paper/main.tex": (
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "% claim:c1\n"
            "The synthetic registered score is 1.25.\n"
            "\\end{document}\n"
        ),
        "paper/main.pdf": minimal_text_pdf("Synthetic release fixture score 1.25"),
    }
    if e01_optimization:
        files.update(
            {
                "inputs/data.txt": '{"aux_cap": 0.25, "primary": 1.0}\n',
                "inputs/expected-main.json": json.dumps(
                    e01_main_payload, sort_keys=True
                )
                + "\n",
                "inputs/expected-reconciliation.json": json.dumps(
                    e01_reconciliation_payload, sort_keys=True
                )
                + "\n",
                "code/main.py": (
                    '"""Deterministic synthetic optimization entrypoint."""\n'
                    "import json\n"
                    "from pathlib import Path\n"
                    "data = json.loads(Path('inputs/data.txt').read_text(encoding='utf-8'))\n"
                    "primary = float(data['primary'])\n"
                    "auxiliary = float(data['aux_cap'])\n"
                    "payload = {\n"
                    "    'auxiliary_response': auxiliary,\n"
                    "    'max_constraint_violation': max(0.0, auxiliary - float(data['aux_cap'])),\n"
                    "    'objective_bound': 1.3215,\n"
                    "    'primary_decision': primary,\n"
                    "    'score': primary + auxiliary,\n"
                    "    'solver_quality': 0.95,\n"
                    "}\n"
                    "Path('outputs').mkdir(exist_ok=True)\n"
                    "Path('outputs/result.json').write_text(\n"
                    "    json.dumps(payload, sort_keys=True) + '\\n', encoding='utf-8', newline='\\n'\n"
                    ")\n"
                    f"svg = {e01_svg!r}\n"
                    "Path('paper/figures').mkdir(parents=True, exist_ok=True)\n"
                    "Path('paper/figures/objective.svg').write_text(svg, encoding='utf-8', newline='\\n')\n"
                ),
                "code/reconcile_objective.py": (
                    '"""Independently enumerate the auxiliary best response for fixed primary decisions."""\n'
                    "import json\n"
                    "from pathlib import Path\n"
                    "data = json.loads(Path('inputs/data.txt').read_text(encoding='utf-8'))\n"
                    "primary = float(data['primary'])\n"
                    "candidates = [0.0, float(data['aux_cap'])]\n"
                    "best_response = max(primary + auxiliary for auxiliary in candidates)\n"
                    "solver_objective = json.loads(\n"
                    "    Path('outputs/result.json').read_text(encoding='utf-8')\n"
                    ")[\"score\"]\n"
                    "payload = {\n"
                    "    'best_response_objective': best_response,\n"
                    "    'fixed_primary_decision': primary,\n"
                    "    'repair_gain': best_response - solver_objective,\n"
                    "}\n"
                    "Path('outputs/reconciliation.json').write_text(\n"
                    "    json.dumps(payload, sort_keys=True) + '\\n', encoding='utf-8', newline='\\n'\n"
                    ")\n"
                ),
                "outputs/result.json": json.dumps(e01_main_payload, sort_keys=True)
                + "\n",
                "outputs/reconciliation.json": json.dumps(
                    e01_reconciliation_payload, sort_keys=True
                )
                + "\n",
                "paper/figures/objective.svg": e01_svg,
                "paper/main.fls": "INPUT main.tex\nINPUT figures/objective.svg\n",
                "paper/main.tex": (
                    "\\documentclass{article}\n"
                    "\\usepackage{graphicx}\n"
                    "\\begin{document}\n"
                    "% claim:c1\n"
                    "The synthetic registered objective is 1.25.\n"
                    "\\includegraphics{figures/objective.svg}\n"
                    "\\end{document}\n"
                ),
            }
        )
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
    if e01_optimization:
        problem["questions"][0].update(
            {
                "text": "Choose a primary decision and verify its auxiliary best response.",
                "task_type": "optimization",
                "evaluation_intent": (
                    "The solution must be feasible, solver-qualified, independently reconciled, "
                    "and linked to one final claim."
                ),
            }
        )
        problem["constraints"] = [
            {
                "id": "constraint:auxiliary-cap",
                "text": "The auxiliary response lies between zero and the synthetic cap.",
                "hard": True,
                "question_refs": ["question:q1"],
            }
        ]
    problem["assumptions"][0].update(
        {
            "text": "The fixture value is treated as exact test input.",
            "impact": "Only the synthetic smoke test depends on it.",
            "check": "Compare the registered result and claim numerically.",
        }
    )
    problem["data_assets"] = [
        {
            "id": "data:synthetic",
            "availability": "bundled",
            "role": "raw_data",
            "classification_basis": "content_inspected",
            "usable_for_modeling": True,
            "immutable_raw": True,
            "intended_use": "Provide the deterministic fixture score.",
            "question_refs": ["question:q1"],
            "exclusion_reason": None,
            "file": file_ref(target, "inputs/data.txt", "text/plain"),
            "source": "Synthetic fixture generator.",
            "license": "Synthetic test data.",
            "columns": [],
        }
    ]
    if e01_optimization:
        problem["data_assets"][0].update(
            {
                "intended_use": "Provide the fixed primary value and auxiliary-response cap.",
                "columns": [
                    {"name": "primary", "definition": "Fixed primary decision value.", "unit": "1"},
                    {"name": "aux_cap", "definition": "Maximum auxiliary response.", "unit": "1"},
                ],
            }
        )
    save_yaml(target, "specs/problem_spec.yaml", problem)

    model = load_yaml(target / "specs/model_spec.yaml")
    model["lifecycle_status"] = "frozen"
    model["method_selection"] = {
        "decision": "selected",
        "rationale": "A deterministic identity model is the shortest valid chain for this fixture.",
        "baseline_policy": {
            "status": "waived",
            "model_refs": [],
            "rationale": "A second identity implementation would be identical and add no discriminating evidence.",
        },
        "alternatives": [],
    }
    model["algorithm"].update(
        {
            "description": "Read the synthetic value and emit a deterministic JSON result.",
            "termination": "One deterministic pass.",
            "complexity_note": "Constant time and space for the fixture.",
        }
    )
    model["data_bindings"] = [
        {
            "symbol_ref": "symbol:x",
            "data_ref": "data:synthetic",
            "question_refs": ["question:q1"],
            "field": "scalar value",
            "transformation": "Identity transformation after parsing the registered text scalar.",
        }
    ]
    if e01_optimization:
        model["model_family"] = "optimization"
        model["method_selection"]["rationale"] = (
            "A two-variable exhaustive synthetic optimization exposes feasibility, solver quality, "
            "and fixed-decision best-response reconciliation without contest content."
        )
        model["symbols"][0].update(
            {
                "name": "synthetic_input",
                "definition": "The fixed primary value and auxiliary cap read from the synthetic input.",
            }
        )
        model["symbols"].extend(
            [
                {
                    "id": "symbol:primary-decision",
                    "name": "primary_decision",
                    "role": "decision",
                    "domain": "real",
                    "shape": "scalar",
                    "unit": "1",
                    "definition": "Primary decision held fixed during objective reconciliation.",
                },
                {
                    "id": "symbol:auxiliary-response",
                    "name": "auxiliary_response",
                    "role": "state",
                    "domain": "real",
                    "shape": "scalar",
                    "unit": "1",
                    "definition": "Auxiliary response independently reoptimized after fixing the primary decision.",
                },
            ]
        )
        model["constraint_refs"] = ["constraint:auxiliary-cap"]
        model["formulation"] = {
            "equations": [],
            "objectives": [
                {
                    "id": "formula:objective",
                    "expression": "primary_decision + auxiliary_response",
                    "format": "plain",
                    "defines": [],
                    "uses": ["symbol:primary-decision", "symbol:auxiliary-response"],
                    "source_constraint_refs": [],
                    "interpretation": "Maximize the synthetic objective.",
                }
            ],
            "constraints": [
                {
                    "id": "formula:auxiliary-cap",
                    "expression": "0 <= auxiliary_response <= 0.25",
                    "format": "plain",
                    "defines": [],
                    "uses": ["symbol:auxiliary-response"],
                    "source_constraint_refs": ["constraint:auxiliary-cap"],
                    "interpretation": "Bound the auxiliary response by the registered cap.",
                }
            ],
        }
        model["algorithm"].update(
            {
                "description": "Enumerate the bounded synthetic auxiliary response for one fixed primary decision.",
                "termination": "All two auxiliary candidates have been evaluated.",
                "complexity_note": "Constant time and space for the two-candidate micro-problem.",
            }
        )
        model["data_bindings"][0].update(
            {
                "field": "primary and aux_cap",
                "transformation": "Parse the two registered JSON scalars without changing units.",
            }
        )
    model["validation_plan"] = {
        "checks": [
            {
                "id": "check:input-integrity",
                "check_type": "input_integrity",
                "applicability": "required",
                "activation_condition": None,
                "criticality": "blocking",
                "rationale": "The fixture must consume the inspected immutable input.",
                "procedure": "Compare the registered scalar with its deterministic source.",
                "pass_rule": "The observed score equals 1.25.",
                "threshold": {"operator": "==", "value": 1.25, "unit": "1"},
                "failure_response": "block_result",
            }
        ],
        "human_review_required": True,
    }
    if e01_optimization:
        model["validation_plan"]["checks"] = [
            validation_check(
                "input_integrity",
                applicability="required",
                threshold={"operator": "==", "value": 1.25, "unit": "1"},
            ),
            validation_check(
                "constraint_feasibility",
                applicability="required",
                threshold={"operator": "<=", "value": 0.0, "unit": "1"},
            ),
            validation_check(
                "solver_optimality",
                applicability="required",
                threshold={"operator": ">=", "value": 0.9, "unit": "1"},
            ),
            validation_check("objective_reconciliation", applicability="required"),
            validation_check("baseline_comparison"),
            validation_check("holdout_leakage"),
            validation_check("sensitivity"),
            validation_check("dimensional_consistency"),
            validation_check("domain_validity"),
            validation_check("formula_back_substitution"),
        ]
    model["applicability"] = "Only this synthetic integration fixture."
    model["failure_modes"] = ["A mutated file or mismatched value must fail the audit."]
    save_yaml(target, "specs/model_spec.yaml", model)

    experiment = load_yaml(target / "experiments/experiment.yaml")
    experiment["lifecycle_status"] = "frozen"
    experiment["mode"] = "confirmatory"
    experiment["decision_timing"] = "here_and_now"
    experiment["purpose"] = "Confirm the deterministic fixture output and evidence links."
    experiment["hypothesis"] = "The generated score equals 1.25."
    experiment["data_refs"] = ["data:synthetic"]
    experiment["code_files"] = [file_ref(target, "code/main.py", "text/x-python")]
    experiment["environment"] = file_ref(target, "requirements.txt", "text/plain")
    experiment["split_strategy"] = "Not applicable: deterministic synthetic value."
    experiment["metrics"][0]["source_output_ref"] = "output:primary"
    experiment["metrics"][0]["extractor"] = {"type": "json_pointer", "pointer": "/score"}
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
    if e01_optimization:
        experiment["purpose"] = (
            "Confirm feasibility, solver quality, reproducibility, and the fixed-decision auxiliary best response."
        )
        experiment["hypothesis"] = (
            "The independent auxiliary best response does not improve the registered objective beyond tolerance."
        )
        experiment["code_files"] = [
            file_ref(target, "code/main.py", "text/x-python"),
            file_ref(target, "code/reconcile_objective.py", "text/x-python"),
        ]
        experiment["metrics"][0].update(
            {
                "name": "synthetic_objective",
                "direction": "maximize",
                "aggregation": "Single deterministic objective value.",
            }
        )
        experiment["outputs"] = [
            {
                "id": "output:primary",
                "path": "outputs/result.json",
                "required": True,
                "comparator": {
                    "type": "json_numeric",
                    "expected_sha256": None,
                    "reference_file": file_ref(
                        target, "inputs/expected-main.json", "application/json"
                    ),
                    "absolute_tolerance": 1e-12,
                    "relative_tolerance": 1e-12,
                },
            },
            {
                "id": "output:reconciliation",
                "path": "outputs/reconciliation.json",
                "required": True,
                "comparator": {
                    "type": "json_numeric",
                    "expected_sha256": None,
                    "reference_file": file_ref(
                        target,
                        "inputs/expected-reconciliation.json",
                        "application/json",
                    ),
                    "absolute_tolerance": 1e-12,
                    "relative_tolerance": 1e-12,
                },
            },
        ]
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
        "repetitions_completed": 1,
        "platform": "synthetic-fixture",
        "git_commit": None,
        "git_dirty": None,
        "environment_note": "Synthetic release-fixture run.",
    }
    result["inputs"] = [file_ref(target, "inputs/data.txt", "text/plain")]
    result["outputs"] = [
        {
            "output_ref": "output:primary",
            "file": file_ref(target, "outputs/result.json", "application/json"),
            "comparison_status": "PASS",
            "comparison_note": "Exact synthetic JSON value matched.",
        }
    ]
    if e01_optimization:
        result["outputs"] = [
            {
                "output_ref": "output:primary",
                "file": file_ref(target, "outputs/result.json", "application/json"),
                "comparison_status": "PASS",
                "comparison_note": "The clean rerun matches the preregistered JSON tolerance.",
            },
            {
                "output_ref": "output:reconciliation",
                "file": file_ref(
                    target, "outputs/reconciliation.json", "application/json"
                ),
                "comparison_status": "PASS",
                "comparison_note": "The independent reconciliation output matches its preregistered tolerance.",
            },
        ]
    result["metrics"] = [
        {
            "metric_ref": "metric:primary",
            "measurement": {"value": 1.25, "unit": "1"},
            "sample_size": 1,
            "uncertainty": None,
        }
    ]
    result["diagnostics"] = [
        {
            "id": "diagnostic:input-integrity",
            "check_ref": "check:input-integrity",
            "check_type": "input_integrity",
            "status": "PASS",
            "condition_met": None,
            "condition_evidence": None,
            "severity": "critical",
            "procedure": "Compared the registered scalar with the deterministic source.",
            "observation": "The source and result both contain 1.25.",
            "observed": {"value": 1.25, "unit": "1"},
            "source_file": file_ref(target, "outputs/result.json", "application/json"),
            "extractor": {"type": "json_pointer", "pointer": "/score"},
            "conclusion": "The planned input-integrity check passed.",
            "evidence_files": [file_ref(target, "inputs/data.txt", "text/plain")],
            "comparison_bindings": [],
        }
    ]
    if e01_optimization:
        input_integrity = result_diagnostic(
            target,
            "input_integrity",
            observed_value=1.25,
            source_path="outputs/result.json",
            pointer="/score",
            evidence_paths=["inputs/data.txt"],
        )
        constraint_feasibility = result_diagnostic(
            target,
            "constraint_feasibility",
            observed_value=0.0,
            source_path="outputs/result.json",
            pointer="/max_constraint_violation",
            evidence_paths=["outputs/result.json"],
        )
        solver_optimality = result_diagnostic(
            target,
            "solver_optimality",
            observed_value=0.95,
            source_path="outputs/result.json",
            pointer="/solver_quality",
            evidence_paths=["outputs/result.json"],
        )
        solver_optimality.update(
            {
                "objective_incumbent": {"value": 1.25, "unit": "1"},
                "objective_bound": {"value": 1.3215, "unit": "1"},
            }
        )
        objective_reconciliation = result_diagnostic(
            target,
            "objective_reconciliation",
            observed_value=0.0,
            source_path="outputs/reconciliation.json",
            pointer="/repair_gain",
            evidence_paths=["outputs/reconciliation.json"],
        )
        objective_reconciliation.update(
            {
                "procedure": (
                    "Fix the registered primary decision and independently enumerate every auxiliary response."
                ),
                "observation": "The independent best response preserves the registered objective.",
                "objective_reconciliation": {
                    "objective_metric_ref": "metric:primary",
                    "fixed_primary_decisions": ["symbol:primary-decision"],
                    "reoptimized_auxiliary_variables": ["symbol:auxiliary-response"],
                    "solver_objective": {"value": 1.25, "unit": "1"},
                    "best_response_objective": {"value": 1.25, "unit": "1"},
                    "repair_gain": {"value": 0.0, "unit": "1"},
                    "absolute_tolerance": 1e-6,
                    "relative_tolerance": 0.0,
                    "registration_timing": "pre_result",
                    "reconciliation_code_file": file_ref(
                        target, "code/reconcile_objective.py", "text/x-python"
                    ),
                    "reconciliation_method": (
                        "Independent exhaustive enumeration of the bounded auxiliary response"
                    ),
                },
                "conclusion": "The fixed-decision objective reconciliation passed.",
            }
        )
        result["diagnostics"] = [
            input_integrity,
            constraint_feasibility,
            solver_optimality,
            objective_reconciliation,
        ]
    result["warnings"] = []
    result["failure_reason"] = None
    result["logs"] = [file_ref(target, "logs/run.log", "text/plain")]
    save_yaml(target, "results/results.yaml", result)

    claims = load_yaml(target / "claims/claims.yaml")
    claims["lifecycle_status"] = "frozen"
    claims["depends_on"] = ["problem:main", "result:main"]
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
                    "source_token": str(args.reported_value),
                    "rendered_token": str(args.reported_value),
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
    if e01_optimization:
        figures["figures"] = [
            {
                "id": "figure:objective",
                "publication_status": "final",
                "provenance_type": "derived",
                "source_result_refs": ["result:main"],
                "source_files": [
                    file_ref(target, "outputs/result.json", "application/json")
                ],
                "generator_files": [
                    file_ref(target, "code/main.py", "text/x-python")
                ],
                "generator_argv": ["python", "code/main.py"],
                "output": file_ref(
                    target, "paper/figures/objective.svg", "image/svg+xml"
                ),
                "panels": ["Synthetic objective"],
                "encodings": [
                    {"field": "score", "aesthetic": "bar height", "unit": "1"}
                ],
                "axes": [
                    {
                        "name": "y",
                        "label": "Synthetic objective",
                        "unit": "1",
                        "scale": "linear",
                    }
                ],
                "caption": "Synthetic fixed-decision objective used by the E01 evidence-chain test.",
                "alt_text": "One bar at the registered synthetic objective value 1.25.",
                "claim_refs": ["claim:c1"],
                "postprocess": [],
                "external_source": None,
                "license": None,
            }
        ]
    save_yaml(target, "figures/figures.yaml", figures)

    paper_build = {
        "schema_version": "2.3.0",
        "kind": "paper_build",
        "id": "build:paper",
        "revision": 1,
        "lifecycle_status": "frozen",
        "depends_on": ["claims:main", "figures:main"],
        "provenance": {"author_type": "script"},
        "extensions": {},
        "source_entrypoint": file_ref(target, "paper/main.tex", "application/x-tex"),
        "fingerprints": {
            "claims:main": sha256_file(target / "claims/claims.yaml"),
            "figures:main": sha256_file(target / "figures/figures.yaml"),
            "result:main": sha256_file(target / "results/results.yaml"),
            "experiment:main": sha256_file(target / "experiments/experiment.yaml"),
            "model:main": sha256_file(target / "specs/model_spec.yaml"),
            "problem:main": sha256_file(target / "specs/problem_spec.yaml"),
        },
        "source_files": [file_ref(target, "paper/main.tex", "application/x-tex")],
        "resource_files": (
            [file_ref(target, "paper/figures/objective.svg", "image/svg+xml")]
            if e01_optimization
            else []
        ),
        "competition_profile": None,
        "engine": "latex",
        "compiler": {"name": "latexmk", "version": "synthetic-1"},
        "command": {"argv": ["latexmk", "-recorder", "main.tex"], "cwd": "paper", "output_path": "paper/main.pdf"},
        "started_at": "2026-01-01T00:00:01Z",
        "finished_at": "2026-01-01T00:00:02Z",
        "exit_code": 0,
        "log": file_ref(target, "paper/build.log", "text/plain"),
        "dependency_log": file_ref(target, "paper/main.fls", "text/plain"),
        "pdf": file_ref(target, "paper/main.pdf", "application/pdf"),
    }
    save_yaml(target, "paper/paper-build.yaml", paper_build)

    # Finalize the release selection before collecting signatures.  The G7
    # snapshot elides only the gate-review artifact hash, so saving the signed
    # review log and refreshing that one manifest row cannot create a hash
    # cycle or change the snapshot that the three members approved.
    manifest = load_yaml(target / "manifest.yaml")
    manifest["manifest_type"] = "release"
    manifest["lifecycle_status"] = "frozen"
    manifest["revision"] = 2
    manifest["entrypoints"] = {"run": "code/main.py", "paper": "paper/main.tex", "pdf": "paper/main.pdf"}
    manifest["environment_files"] = [
        {"id": "environment:python", **file_ref(target, "requirements.txt", "text/plain")}
    ]
    manifest["artifacts"].append(
        {
            "id": "build:paper",
            "kind": "paper_build",
            "path": "paper/paper-build.yaml",
            "sha256": sha256_file(target / "paper/paper-build.yaml"),
            "required": True,
            "depends_on": ["claims:main", "figures:main"],
        }
    )
    for artifact in manifest["artifacts"]:
        if artifact["id"] == "claims:main":
            artifact["depends_on"] = ["problem:main", "result:main"]
    manifest["deliverables"] = [
        {"id": "deliverable:paper", **file_ref(target, "paper/main.tex"), "required": True, "role": "paper_source", "media_type": "application/x-tex"},
        {"id": "deliverable:pdf", **file_ref(target, "paper/main.pdf"), "required": True, "role": "paper_pdf", "media_type": "application/pdf"},
        {"id": "deliverable:code", **file_ref(target, "code/main.py"), "required": True, "role": "code", "media_type": "text/x-python"},
        {"id": "deliverable:result", **file_ref(target, "outputs/result.json"), "required": True, "role": "result", "media_type": "application/json"},
    ]
    if e01_optimization:
        manifest["deliverables"].extend(
            [
                {
                    "id": "deliverable:objective-reconciliation-code",
                    **file_ref(target, "code/reconcile_objective.py"),
                    "required": True,
                    "role": "code",
                    "media_type": "text/x-python",
                },
                {
                    "id": "deliverable:objective-reconciliation-result",
                    **file_ref(target, "outputs/reconciliation.json"),
                    "required": True,
                    "role": "result",
                    "media_type": "application/json",
                },
            ]
        )
    manifest["notes"] = ["Synthetic release fixture; no real contest content or performance claim."]
    for artifact in manifest["artifacts"]:
        artifact["sha256"] = sha256_file(target / artifact["path"])
    snapshot_digest = release_snapshot_sha256(target, manifest)

    # Bind every gate to the exact evidence class it approves.  G1/G2/G5/G6/G7
    # require the full three-person team; G3/G4 require independent modeling
    # and computation signers.  Each member signs the complete evidence set,
    # so one person's stale fingerprint invalidates that approval set.
    gate_artifacts = {
        "G0": ["problem:main"],
        "G1": ["problem:main"],
        "G2": ["problem:main", "model:main"],
        "G3": ["problem:main", "model:main", "experiment:main", "environment:python"],
        "G4": ["problem:main", "model:main", "experiment:main", "environment:python", "result:main"],
        "G5": ["problem:main", "model:main", "experiment:main", "environment:python", "result:main", "claims:main", "figures:main"],
        "G6": ["problem:main", "model:main", "experiment:main", "environment:python", "result:main", "claims:main", "figures:main", "build:paper", "entrypoint:paper", "entrypoint:pdf", "deliverable:paper", "deliverable:pdf"],
        "G7": ["deliverable:paper", "deliverable:pdf", "deliverable:code", "deliverable:result", "snapshot:release"],
    }
    if e01_optimization:
        gate_artifacts["G7"] = [
            "deliverable:paper",
            "deliverable:pdf",
            "deliverable:code",
            "deliverable:objective-reconciliation-code",
            "deliverable:result",
            "deliverable:objective-reconciliation-result",
            "snapshot:release",
        ]
    artifact_paths = {
        "problem:main": "specs/problem_spec.yaml",
        "model:main": "specs/model_spec.yaml",
        "experiment:main": "experiments/experiment.yaml",
        "result:main": "results/results.yaml",
        "claims:main": "claims/claims.yaml",
        "figures:main": "figures/figures.yaml",
        "build:paper": "paper/paper-build.yaml",
        "environment:python": "requirements.txt",
        "entrypoint:paper": "paper/main.tex",
        "entrypoint:pdf": "paper/main.pdf",
        "deliverable:paper": "paper/main.tex",
        "deliverable:pdf": "paper/main.pdf",
        "deliverable:code": "code/main.py",
        "deliverable:result": "outputs/result.json",
    }
    if e01_optimization:
        artifact_paths.update(
            {
                "deliverable:objective-reconciliation-code": "code/reconcile_objective.py",
                "deliverable:objective-reconciliation-result": "outputs/reconciliation.json",
            }
        )
    review_log = load_yaml(target / "reviews/gate-reviews.yaml")
    review_log["lifecycle_status"] = "frozen"
    review_log["revision"] = 2
    review_log["team_members"] = [
        {"id": "member:modeler", "display_name": "Synthetic Modeler", "primary_role": "modeling"},
        {"id": "member:coder", "display_name": "Synthetic Computation Reviewer", "primary_role": "computation"},
        {"id": "member:writer", "display_name": "Synthetic Writing Reviewer", "primary_role": "writing"},
    ]
    team_names = {item["id"]: item["display_name"] for item in review_log["team_members"]}
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
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    review_log["reviews"] = []
    for index, (gate, artifact_ids) in enumerate(gate_artifacts.items()):
        approval_set_id = f"approval:{gate.lower()}-fixture"
        for signer_index, member_id in enumerate(signers_by_gate[gate]):
            review_log["reviews"].append(
                {
                    "id": f"review:{gate.lower()}-{member_id.split(':', 1)[1]}-fixture",
                    "gate": gate,
                    "decision": "PASS",
                    "basis": "human",
                    "approval_set_id": approval_set_id,
                    "member_id": member_id,
                    "reviewer": team_names[member_id],
                    "reviewed_at": (
                        base_time + timedelta(seconds=index * 10 + signer_index)
                    ).isoformat().replace("+00:00", "Z"),
                    "rationale": "Synthetic integration evidence was independently checked for this gate.",
                    "evidence_refs": artifact_ids,
                    "artifact_fingerprints": {
                        artifact_id: (
                            snapshot_digest
                            if artifact_id == "snapshot:release"
                            else sha256_file(target / artifact_paths[artifact_id])
                        )
                        for artifact_id in artifact_ids
                    },
                    "conditions": [],
                }
            )
    save_yaml(target, "reviews/gate-reviews.yaml", review_log)
    for artifact in manifest["artifacts"]:
        artifact["sha256"] = sha256_file(target / artifact["path"])
    save_yaml(target, "manifest.yaml", manifest)

    print(json.dumps({"status": "PASS", "target": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
