#!/usr/bin/env python3
"""Execute E05's analytic mechanism-convergence benchmark."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from _forward_scenario_support import (
    finding_rows,
    load_scenario,
    parse_target_args,
    prepare_target,
    print_summary,
    require_code,
    require_pass,
    run_audit,
)
from test_audit_regressions import (
    build_release_project,
    file_ref,
    load_yaml,
    resign_release_project,
    run_audit as run_in_process_audit,
    write_text,
    write_yaml,
)


SCENARIO_PATH = Path(__file__).with_name("e05_mechanism_convergence_scenario.json")
SCENARIO_ID = "E05-mechanism-convergence"


MECHANISM_PROGRAM = r'''"""Synthetic harmonic oscillator with an analytic reference solution."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


THEORETICAL_ORDER = 4.0
OMEGA = 1.0
FINAL_TIME = 2.0
POSITIVE_STEPS = (0.2, 0.1, 0.05, 0.025)
COARSE_STEPS = (1.0, 0.5)


def rhs(x, velocity, omega=OMEGA):
    return velocity, -(omega ** 2) * x


def analytic(time, omega=OMEGA):
    return math.cos(omega * time), -omega * math.sin(omega * time)


def rk4(step, *, broken_conservation=False):
    count = round(FINAL_TIME / step)
    x, velocity = 1.0, 0.0
    invariant0 = velocity * velocity + OMEGA * OMEGA * x * x
    maximum_drift = 0.0
    maximum_scaled_state = 1.0
    for _index in range(count):
        k1x, k1v = rhs(x, velocity)
        k2x, k2v = rhs(x + 0.5 * step * k1x, velocity + 0.5 * step * k1v)
        k3x, k3v = rhs(x + 0.5 * step * k2x, velocity + 0.5 * step * k2v)
        k4x, k4v = rhs(x + step * k3x, velocity + step * k3v)
        x += step * (k1x + 2.0 * k2x + 2.0 * k3x + k4x) / 6.0
        velocity += step * (k1v + 2.0 * k2v + 2.0 * k3v + k4v) / 6.0
        if broken_conservation:
            velocity += 0.05 * step
        invariant = velocity * velocity + OMEGA * OMEGA * x * x
        maximum_drift = max(maximum_drift, abs(invariant - invariant0))
        maximum_scaled_state = max(
            maximum_scaled_state,
            math.hypot(x, velocity / OMEGA),
        )
    exact_x, exact_velocity = analytic(FINAL_TIME)
    error_m = max(abs(x - exact_x), abs(velocity - exact_velocity) / OMEGA)
    return {
        "step_s": step,
        "x_m": x,
        "velocity_m_s": velocity,
        "error_m": error_m,
        "max_invariant_drift_m2_s2": maximum_drift,
        "max_scaled_state": maximum_scaled_state,
    }


def dimension_mismatch_count():
    # Dimension vectors are (length exponent, time exponent).
    displacement = (1, 0)
    velocity = (1, -1)
    time = (0, 1)
    omega = (0, -1)
    subtract = lambda left, right: (left[0] - right[0], left[1] - right[1])
    add = lambda left, right: (left[0] + right[0], left[1] + right[1])
    double = lambda value: (2 * value[0], 2 * value[1])
    checks = [
        subtract(displacement, time) == velocity,
        subtract(velocity, time) == add(double(omega), displacement),
        double(velocity) == add(double(omega), double(displacement)),
    ]
    return sum(not matched for matched in checks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("positive", "broken-conservation", "coarse-step"),
        default="positive",
    )
    args = parser.parse_args()
    steps = COARSE_STEPS if args.mode == "coarse-step" else POSITIVE_STEPS
    rows = [
        rk4(step, broken_conservation=args.mode == "broken-conservation")
        for step in steps
    ]
    measured_orders = [
        math.log(rows[index - 1]["error_m"] / rows[index]["error_m"])
        / math.log(rows[index - 1]["step_s"] / rows[index]["step_s"])
        for index in range(1, len(rows))
    ]
    analytic_residuals = []
    for index in range(21):
        time = FINAL_TIME * index / 20.0
        x, velocity = analytic(time)
        dxdt = velocity
        dvdt = -(OMEGA ** 2) * x
        analytic_residuals.extend((abs(dxdt - velocity), abs(dvdt + OMEGA ** 2 * x)))
    sensitivity_values = [analytic(FINAL_TIME, omega)[0] for omega in (0.95, 1.05)]
    finest = rows[-1]
    payload = {
        "score": 0.95,
        "mode": args.mode,
        "system": {
            "equations": ["x' = v", "v' = -omega^2 x"],
            "initial_state": {"x_m": 1.0, "velocity_m_s": 0.0},
            "omega_s_inverse": OMEGA,
            "final_time_s": FINAL_TIME,
            "analytic_solution": ["x(t)=cos(omega t)", "v(t)=-omega sin(omega t)"],
        },
        "refinement": rows,
        "measured_orders": measured_orders,
        "theoretical_order": THEORETICAL_ORDER,
        "checks": {
            "convergence": {"finest_error_m": finest["error_m"]},
            "conservation_balance": {
                "max_invariant_drift_m2_s2": finest["max_invariant_drift_m2_s2"]
            },
            "numerical_stability": {"max_scaled_state": finest["max_scaled_state"]},
            "boundary_case": {"initial_condition_residual_m": 0.0},
            "dimensional_consistency": {
                "mismatch_count": dimension_mismatch_count()
            },
            "sensitivity": {
                "omega_perturbation_span_m": max(sensitivity_values) - min(sensitivity_values)
            },
            "domain_validity": {"out_of_domain_count": 0},
            "formula_back_substitution": {
                "max_analytic_equation_residual": max(analytic_residuals)
            },
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


CHECK_SPECS = {
    "convergence": ("<=", "convergence_tolerance_m", "m", "/checks/convergence/finest_error_m"),
    "conservation_balance": (
        "<=",
        "conservation_tolerance_m2_s2",
        "m^2/s^2",
        "/checks/conservation_balance/max_invariant_drift_m2_s2",
    ),
    "numerical_stability": ("<=", 1.01, "1", "/checks/numerical_stability/max_scaled_state"),
    "boundary_case": ("<=", 1e-12, "m", "/checks/boundary_case/initial_condition_residual_m"),
    "dimensional_consistency": (
        "==",
        "dimension_mismatch_limit",
        "count",
        "/checks/dimensional_consistency/mismatch_count",
    ),
    "sensitivity": ("<=", 0.2, "m", "/checks/sensitivity/omega_perturbation_span_m"),
    "domain_validity": ("==", 0.0, "count", "/checks/domain_validity/out_of_domain_count"),
    "formula_back_substitution": (
        "<=",
        1e-12,
        "m/s^2",
        "/checks/formula_back_substitution/max_analytic_equation_residual",
    ),
}


def threshold_value(scenario: dict[str, Any], token: str | float) -> float:
    return float(scenario[token]) if isinstance(token, str) else float(token)


def compare(value: float, operator: str, threshold: float) -> bool:
    return {
        "==": value == threshold,
        "<=": value <= threshold,
        ">=": value >= threshold,
    }[operator]


def run_mechanism_program(root: Path, mode: str) -> dict[str, Any]:
    output = root / "outputs/result.json"
    command = [
        sys.executable,
        "-X",
        "utf8",
        "code/main.py",
        "--output",
        "outputs/result.json",
        "--mode",
        mode,
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"mechanism program returned {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return json.loads(output.read_text(encoding="utf-8"))


def build_mechanism_release(
    root: Path,
    scenario: dict[str, Any],
    mode: str,
) -> tuple[Path, dict[str, Any]]:
    root = build_release_project(root)
    write_text(root, "code/main.py", MECHANISM_PROGRAM)
    payload = run_mechanism_program(root, mode)

    problem = load_yaml(root / "specs/problem_spec.yaml")
    question = problem["questions"][0]
    question.update(
        {
            "text": "Integrate a harmonic oscillator and verify its numerical mechanism.",
            "task_type": "mechanism",
            "evaluation_intent": (
                "Compare RK4 with the analytic solution, its invariant, dimensions, and refinement behavior."
            ),
        }
    )
    problem["assumptions"][0].update(
        {
            "text": "The undamped unit-frequency oscillator is the complete synthetic mechanism.",
            "basis": "simplifying",
            "impact": "It supplies an analytic solution and conserved invariant.",
            "check": "Compare RK4 states with the closed-form solution and invariant.",
        }
    )
    write_yaml(root, "specs/problem_spec.yaml", problem)

    model = load_yaml(root / "specs/model_spec.yaml")
    model["model_family"] = "simulation"
    model["method_selection"]["rationale"] = (
        "Classical RK4 is tested on a mechanism with a closed-form reference and a conserved invariant."
    )
    model["method_selection"]["baseline_policy"] = {
        "status": "waived",
        "model_refs": [],
        "rationale": (
            "The analytic solution is a stronger independent reference than a second numerical baseline."
        ),
    }
    model["symbols"] = [
        {"id": "symbol:time", "name": "t", "role": "input", "domain": "real", "shape": "scalar", "unit": "s", "definition": "Time."},
        {"id": "symbol:displacement", "name": "x", "role": "state", "domain": "real", "shape": "scalar", "unit": "m", "definition": "Oscillator displacement."},
        {"id": "symbol:velocity", "name": "v", "role": "state", "domain": "real", "shape": "scalar", "unit": "m/s", "definition": "Oscillator velocity."},
        {"id": "symbol:omega", "name": "omega", "role": "parameter", "domain": "positive real", "shape": "scalar", "unit": "1/s", "definition": "Angular frequency."},
    ]
    model["formulation"] = {
        "equations": [
            {"id": "formula:position-rate", "expression": "dx/dt = v", "format": "plain", "defines": ["symbol:displacement"], "uses": ["symbol:time", "symbol:velocity"], "source_constraint_refs": [], "interpretation": "Kinematic state equation."},
            {"id": "formula:velocity-rate", "expression": "dv/dt = -omega^2 x", "format": "plain", "defines": ["symbol:velocity"], "uses": ["symbol:time", "symbol:omega", "symbol:displacement"], "source_constraint_refs": [], "interpretation": "Undamped restoring acceleration."},
        ],
        "objectives": [],
        "constraints": [],
    }
    model["algorithm"].update(
        {
            "description": "Integrate the oscillator with RK4 at four registered step sizes.",
            "entrypoint": "code/main.py",
            "termination": "Reach exactly two seconds at every registered step size.",
            "complexity_note": "Linear in the number of time steps.",
        }
    )
    model["validation_plan"]["checks"] = []
    for check_type, (operator, threshold_token, unit, _pointer) in CHECK_SPECS.items():
        threshold = threshold_value(scenario, threshold_token)
        model["validation_plan"]["checks"].append(
            {
                "id": f"check:{check_type.replace('_', '-')}",
                "check_type": check_type,
                "applicability": "required",
                "activation_condition": None,
                "criticality": "blocking",
                "rationale": f"The analytic mechanism exposes a direct {check_type} invariant.",
                "procedure": f"Extract and independently compare the registered {check_type} measure.",
                "pass_rule": f"Observed value must be {operator} {threshold} {unit}.",
                "threshold": {"operator": operator, "value": threshold, "unit": unit},
                "failure_response": "block_result",
            }
        )
    model["sensitivity_plan"] = [
        "Perturb omega by plus/minus five percent.",
        "Refine RK4 step sizes from 0.2 s to 0.025 s.",
    ]
    model["applicability"] = "Synthetic smooth initial-value mechanisms with an analytic reference."
    model["failure_modes"] = [
        "A coarse step exceeds the registered absolute-error tolerance.",
        "A state perturbation breaks the conserved quadratic invariant.",
        "Equation terms use inconsistent length/time dimensions.",
    ]
    write_yaml(root, "specs/model_spec.yaml", model)

    experiment = load_yaml(root / "experiments/experiment.yaml")
    experiment.update(
        {
            "purpose": "Benchmark mechanism convergence against a closed-form truth source.",
            "hypothesis": "RK4 reaches fourth-order convergence while preserving the oscillator invariant within tolerance.",
            "code_files": [file_ref(root, "code/main.py")],
            "command": {
                "argv": ["python", "-X", "utf8", "code/main.py", "--output", "outputs/result.json", "--mode", mode],
                "cwd": ".",
                "environment_allowlist": [],
                "network_access": "not_needed",
            },
            "parameters": {
                "theoretical_order": float(scenario["theoretical_order"]),
                "step_sizes_s": scenario["coarse_steps"] if mode == "coarse-step" else scenario["positive_steps"],
                "final_time_s": float(scenario["final_time_s"]),
            },
            "split_strategy": "No data split; the analytic solution is evaluated at the same final time.",
        }
    )
    experiment["outputs"][0]["comparator"]["expected_sha256"] = file_ref(root, "outputs/result.json")["sha256"]
    write_yaml(root, "experiments/experiment.yaml", experiment)

    result = load_yaml(root / "results/results.yaml")
    result["run"].update(
        {
            "argv": ["python", "-X", "utf8", "code/main.py", "--output", "outputs/result.json", "--mode", mode],
            "environment_note": "Synthetic analytic RK4 mechanism benchmark.",
        }
    )
    result["outputs"][0]["file"] = file_ref(root, "outputs/result.json")
    result["diagnostics"] = []
    for check_type, (operator, threshold_token, unit, pointer) in CHECK_SPECS.items():
        threshold = threshold_value(scenario, threshold_token)
        tokens = [token.replace("~1", "-").replace("~0", "~") for token in pointer.lstrip("/").split("/")]
        observed: Any = payload
        for token in tokens:
            observed = observed[token]
        value = float(observed)
        passed = compare(value, operator, threshold)
        result["diagnostics"].append(
            {
                "id": f"diagnostic:{check_type.replace('_', '-')}",
                "check_ref": f"check:{check_type.replace('_', '-')}",
                "check_type": check_type,
                "status": "PASS" if passed else "BLOCK",
                "condition_met": None,
                "condition_evidence": None,
                "severity": "critical",
                "procedure": f"Extracted {pointer} from the hashed mechanism output.",
                "observation": f"Observed {value} {unit}; registered rule is {operator} {threshold} {unit}.",
                "observed": {"value": value, "unit": unit},
                "source_file": file_ref(root, "outputs/result.json"),
                "extractor": {"type": "json_pointer", "pointer": pointer},
                "conclusion": f"The {check_type} check {'passed' if passed else 'failed'}.",
                "evidence_files": [],
                "comparison_bindings": [],
            }
        )
    write_yaml(root, "results/results.yaml", result)
    resign_release_project(root)
    return root, payload


def assert_threshold_pass(report: dict[str, Any], check_ref: str) -> None:
    matches = [
        row
        for row in finding_rows(report, "DIAGNOSTIC_THRESHOLD_PASS")
        if check_ref in str(row.get("message"))
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one threshold PASS for {check_ref}, got {matches}")


def assert_result_ineligible(root: Path, context: str) -> None:
    _report, audit = run_in_process_audit(root)
    if audit.result_eligibility.get("result:main") is not False:
        raise AssertionError(f"{context} did not make result:main ineligible")


def main() -> int:
    args = parse_target_args(
        "Run E05's non-overwriting analytic mechanism-convergence benchmark."
    )
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e05-mechanism-convergence")
    report_root = target / "reports"

    positive_root, positive_payload = build_mechanism_release(
        target / "positive", scenario, "positive"
    )
    positive_report = run_audit(positive_root, report_root / "positive.json", {0})
    require_pass(positive_report)
    orders = [float(value) for value in positive_payload["measured_orders"]]
    theoretical = float(scenario["theoretical_order"])
    order_tolerance = float(scenario["order_absolute_tolerance"])
    if not orders or any(abs(value - theoretical) > order_tolerance for value in orders):
        raise AssertionError(
            f"measured RK4 orders {orders} do not match theory {theoretical}"
        )
    assert_threshold_pass(positive_report, "check:convergence")
    assert_threshold_pass(positive_report, "check:conservation-balance")
    assert_threshold_pass(positive_report, "check:dimensional-consistency")

    broken_root, broken_payload = build_mechanism_release(
        target / "broken-conservation", scenario, "broken-conservation"
    )
    broken_report = run_audit(
        broken_root, report_root / "broken-conservation.json", {10, 12}
    )
    require_code(broken_report, str(scenario["threshold_failure_code"]))
    broken_drift = float(
        broken_payload["checks"]["conservation_balance"]["max_invariant_drift_m2_s2"]
    )
    if broken_drift <= float(scenario["conservation_tolerance_m2_s2"]):
        raise AssertionError("conservation perturbation did not exceed its tolerance")
    assert_result_ineligible(broken_root, "broken conservation")

    coarse_root, coarse_payload = build_mechanism_release(
        target / "coarse-step", scenario, "coarse-step"
    )
    coarse_report = run_audit(coarse_root, report_root / "coarse-step.json", {10, 12})
    require_code(coarse_report, str(scenario["threshold_failure_code"]))
    coarse_error = float(coarse_payload["checks"]["convergence"]["finest_error_m"])
    if coarse_error <= float(scenario["convergence_tolerance_m"]):
        raise AssertionError("coarse step did not exceed its convergence tolerance")
    assert_result_ineligible(coarse_root, "coarse step")

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "analytic_truth": "x(t)=cos(t), v(t)=-sin(t)",
            "theoretical_order": theoretical,
            "measured_orders": orders,
            "positive_finest_error_m": positive_payload["checks"]["convergence"]["finest_error_m"],
            "positive_invariant_drift_m2_s2": positive_payload["checks"]["conservation_balance"]["max_invariant_drift_m2_s2"],
            "dimension_mismatch_count": positive_payload["checks"]["dimensional_consistency"]["mismatch_count"],
            "negative": {
                "broken_conservation_drift_m2_s2": broken_drift,
                "coarse_step_error_m": coarse_error,
                "finding": scenario["threshold_failure_code"],
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
