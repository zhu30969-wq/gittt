"""Runnable minimum example for a resource-and-timing MILP."""

from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path


TOOLKIT_ROOT = Path(__file__).resolve().parents[3] / "assets" / "solver-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from solver_toolkit import (  # noqa: E402
    ConstraintSpec,
    MilpOracle,
    ParameterSweep,
    ProblemInstance,
    SeedSweep,
    SensitivityPlan,
    SolveConfig,
    build_baseline,
    run_sensitivity,
    solve,
    validate_solution,
    verify_small_instance,
)


def optimum_at_capacity(capacity: float) -> float:
    feasible = [
        3.0 * x + 2.0 * y
        for x, y in itertools.product((0.0, 1.0), repeat=2)
        if 2.0 * x + y <= capacity + 1e-12
    ]
    return max(feasible)


def main() -> int:
    instance = ProblemInstance(
        name="two-job-resource-schedule",
        direction="maximize",
        unit="benefit",
        objective=lambda x, _p: 3.0 * x[0] + 2.0 * x[1],
        bounds=((0.0, 1.0), (0.0, 1.0)),
        constraints=(
            ConstraintSpec(
                "resource-capacity",
                lambda x, p: 2.0 * x[0] + x[1],
                upper=2.0,
                unit="resource",
            ),
        ),
        baseline_builder=lambda _instance: (0.0, 0.0),
        milp_oracle=MilpOracle(
            objective_coefficients=(3.0, 2.0),
            integrality=(1, 1),
            constraint_matrix=((2.0, 1.0),),
            constraint_lower=(-math.inf,),
            constraint_upper=(2.0,),
        ),
        sensitivity_plan=SensitivityPlan(
            parameter_sweeps=(
                ParameterSweep(
                    "resource-capacity",
                    (1.0, 2.0, 3.0),
                    lambda _i, _r, capacity: optimum_at_capacity(capacity),
                    "benefit",
                ),
            ),
            seed_sweep=SeedSweep(
                (11, 22, 33),
                lambda _i, result, _seed: result.incumbent,
                "benefit",
            ),
        ),
    )
    baseline = build_baseline(instance)
    result = solve(instance, SolveConfig(method="milp"))
    diagnostics = validate_solution(instance, result)
    certificate = verify_small_instance(instance, result)
    sensitivity = run_sensitivity(instance, result)
    if any(row.status != "PASS" for row in diagnostics):
        raise AssertionError("MILP result is infeasible")
    if certificate.status != "exact_match" or result.gap != 0.0:
        raise AssertionError(certificate.as_dict())
    print(
        json.dumps(
            {
                "baseline": baseline.contract_fields("metric:benefit"),
                "result": result.contract_fields("metric:benefit"),
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
