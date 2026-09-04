"""Runnable minimum example for continuous black-box global optimization."""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path


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


def main() -> int:
    instance = ProblemInstance(
        name="bounded-black-box-demo",
        direction="maximize",
        unit="score",
        objective=lambda x, p: p["height"] - (x[0] - 0.25) ** 2 - (x[1] + 0.5) ** 2,
        bounds=((-1.0, 1.0), (-1.0, 1.0)),
        constraints=(
            ConstraintSpec("shared-budget", lambda x, _p: x[0] + x[1], upper=1.0),
        ),
        baseline_builder=lambda _instance: (0.0, 0.0),
        parameters={"height": 1.0},
        enumeration_oracle=EnumerationOracle(
            lambda _instance: itertools.chain(
                [(0.25, -0.5)],
                itertools.product((-1.0, 0.0, 1.0), repeat=2),
            )
        ),
        sensitivity_plan=SensitivityPlan(
            parameter_sweeps=(
                ParameterSweep(
                    "height",
                    (0.9, 1.0, 1.1),
                    lambda _i, result, height: height - (result.raw_solution[0] - 0.25) ** 2 - (result.raw_solution[1] + 0.5) ** 2,
                    "score",
                ),
            ),
            seed_sweep=SeedSweep(
                (101, 202, 303),
                lambda i, _r, seed: solve(
                    i,
                    SolveConfig(
                        method="differential_evolution",
                        seeds=(seed,),
                        repetitions=1,
                        maxiter=20,
                        popsize=5,
                    ),
                ).incumbent,
                "score",
            ),
            refinement_sweep=RefinementSweep(
                (0.2, 0.1, 0.05),
                lambda _i, _r, step: step**2,
                level_name="surrogate_grid_step",
                error_unit="score",
                theoretical_order=2.0,
            ),
        ),
        oracle_tolerance=1e-6,
    )
    baseline = build_baseline(instance)
    result = solve(
        instance,
        SolveConfig(
            method="differential_evolution",
            seeds=(17, 29),
            repetitions=2,
            maxiter=35,
            popsize=7,
        ),
    )
    diagnostics = validate_solution(instance, result)
    certificate = verify_small_instance(instance, result)
    sensitivity = run_sensitivity(instance, result)
    if any(row.status != "PASS" for row in diagnostics):
        raise AssertionError("continuous solution is infeasible")
    if certificate.status != "exact_match":
        raise AssertionError(certificate.as_dict())
    print(
        json.dumps(
            {
                "baseline": baseline.contract_fields("metric:black-box"),
                "result": result.contract_fields("metric:black-box"),
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
