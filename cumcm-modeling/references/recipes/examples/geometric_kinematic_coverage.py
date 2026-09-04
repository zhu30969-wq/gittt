"""Runnable minimum example for geometric/kinematic coverage."""

from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np


TOOLKIT_ROOT = Path(__file__).resolve().parents[3] / "assets" / "solver-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from solver_toolkit import (  # noqa: E402
    ConstraintSpec,
    EnumerationOracle,
    ParameterSweep,
    ProblemInstance,
    RefinementSweep,
    SeedSweep,
    SensitivityPlan,
    SolveConfig,
    build_baseline,
    run_sensitivity,
    solve,
    validate_solution,
    verify_small_instance,
)


def coverage_duration(solution: tuple[float, ...] | np.ndarray, parameters: dict[str, float]) -> float:
    heading_deg, speed, release_time = map(float, solution)
    step = float(parameters["time_step"])
    horizon = float(parameters["horizon"])
    radius = float(parameters["radius"])
    times = np.arange(0.0, horizon + 0.5 * step, step)
    active = times >= release_time
    target = np.column_stack((1.0 + times, np.zeros_like(times)))
    angle = math.radians(heading_deg % 360.0)
    direction = np.array([math.cos(angle), math.sin(angle)])
    elapsed = np.maximum(0.0, times - release_time)
    sensor = elapsed[:, None] * speed * direction[None, :]
    covered = active & (np.linalg.norm(sensor - target, axis=1) <= radius)
    trapezoid = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
    return float(trapezoid(covered.astype(float), dx=step))


def main() -> int:
    parameters = {"time_step": 0.01, "horizon": 4.0, "radius": 0.5}
    candidates = tuple(
        itertools.product((0.0, 90.0, 180.0, 270.0), (0.5, 1.0, 1.5, 2.0), (0.0, 0.5, 1.0))
    )
    instance = ProblemInstance(
        name="moving-disc-coverage",
        direction="maximize",
        unit="s",
        objective=lambda x, p: coverage_duration(x, dict(p)),
        bounds=((0.0, 359.999999), (0.5, 2.0), (0.0, 1.0)),
        constraints=(
            ConstraintSpec("speed", lambda x, _p: x[1], lower=0.5, upper=2.0, unit="m/s"),
            ConstraintSpec("release-time", lambda x, _p: x[2], lower=0.0, upper=1.0, unit="s"),
        ),
        baseline_builder=lambda _instance: (180.0, 1.0, 0.0),
        parameters=parameters,
        enumeration_oracle=EnumerationOracle(lambda _instance: candidates),
        sensitivity_plan=SensitivityPlan(
            parameter_sweeps=(
                ParameterSweep(
                    "coverage-radius",
                    (0.4, 0.5, 0.6),
                    lambda _i, result, radius: coverage_duration(
                        result.raw_solution,
                        {**parameters, "radius": radius},
                    ),
                    "s",
                ),
            ),
            seed_sweep=SeedSweep(
                (1, 2, 3),
                lambda _i, result, _seed: result.incumbent,
                "s",
            ),
            refinement_sweep=RefinementSweep(
                (0.04, 0.02, 0.01),
                lambda _i, result, step: abs(
                    coverage_duration(result.raw_solution, {**parameters, "time_step": step})
                    - coverage_duration(result.raw_solution, {**parameters, "time_step": 0.001})
                ),
                level_name="time_step",
                error_unit="s",
            ),
        ),
    )
    baseline = build_baseline(instance)
    result = solve(instance, SolveConfig(method="enumeration"))
    diagnostics = validate_solution(instance, result)
    certificate = verify_small_instance(instance, result)
    sensitivity = run_sensitivity(instance, result)
    if any(row.status != "PASS" for row in diagnostics):
        raise AssertionError("coverage candidate is infeasible")
    if certificate.status != "exact_match":
        raise AssertionError(certificate.as_dict())
    print(
        json.dumps(
            {
                "baseline": baseline.contract_fields("metric:covered-time"),
                "result": result.contract_fields("metric:covered-time"),
                "diagnostics": [row.as_dict() for row in diagnostics],
                "certificate": certificate.as_dict(),
                "sensitivity": sensitivity.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
