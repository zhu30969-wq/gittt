"""Unit tests for the standalone solver toolkit."""

from __future__ import annotations

import itertools
import math
import sys
import unittest
from pathlib import Path

import numpy as np


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT_ROOT))

from solver_toolkit import (  # noqa: E402
    BaselineInfeasibleError,
    ConstraintSpec,
    EnumerationOracle,
    MilpOracle,
    ParameterSweep,
    ProblemInstance,
    RefinementSweep,
    SeedSweep,
    SensitivityPlan,
    SolveConfig,
    SolveResult,
    build_baseline,
    run_sensitivity,
    solve,
    validate_solution,
    verify_small_instance,
)


def binary_instance(*, with_oracle: bool = True) -> ProblemInstance:
    return ProblemInstance(
        name="two-item-knapsack",
        direction="maximize",
        unit="point",
        objective=lambda x, _p: 3.0 * x[0] + 2.0 * x[1],
        bounds=((0.0, 1.0), (0.0, 1.0)),
        constraints=(
            ConstraintSpec(
                "capacity",
                lambda x, _p: 2.0 * x[0] + x[1],
                upper=2.0,
                unit="slot",
            ),
        ),
        baseline_builder=lambda _instance: (0.0, 0.0),
        enumeration_oracle=(
            EnumerationOracle(
                lambda _instance: itertools.product((0.0, 1.0), repeat=2)
            )
            if with_oracle
            else None
        ),
        oracle_tolerance=1e-9,
    )


def result_for(instance: ProblemInstance, solution: tuple[float, ...]) -> SolveResult:
    value = float(instance.objective(np.asarray(solution), instance.parameters))
    return SolveResult(
        incumbent=value,
        unit=instance.unit,
        bound=None,
        gap=None,
        solver_name="test-candidate",
        seeds=(7,),
        repetitions=1,
        wall_time=0.0,
        raw_solution=solution,
        direction=instance.direction,
    )


class SolverToolkitTests(unittest.TestCase):
    def test_gap_is_forced_to_none_without_a_valid_bound(self) -> None:
        guarded = SolveResult(
            incumbent=4.0,
            unit="s",
            bound=None,
            gap=0.123,
            solver_name="heuristic",
            seeds=(1,),
            repetitions=1,
            wall_time=0.0,
            raw_solution=(0.0,),
            direction="maximize",
        )
        self.assertIsNone(guarded.gap)
        self.assertEqual({}, guarded.contract_fields("metric:test")["solver_bound_evidence"])

    def test_validate_solution_reports_every_constraint_and_violation(self) -> None:
        instance = ProblemInstance(
            name="two-constraints",
            direction="minimize",
            unit="yuan",
            objective=lambda x, _p: x[0],
            bounds=((0.0, 10.0),),
            constraints=(
                ConstraintSpec("minimum", lambda x, _p: x[0], lower=2.0),
                ConstraintSpec("maximum", lambda x, _p: x[0], upper=4.0),
            ),
            baseline_builder=lambda _instance: (2.0,),
        )
        diagnostics = validate_solution(instance, result_for(instance, (5.5,)))
        declared = [row for row in diagnostics if not row.constraint_id.startswith("bound:")]
        self.assertEqual(["minimum", "maximum"], [row.constraint_id for row in declared])
        self.assertEqual(["PASS", "BLOCK"], [row.status for row in declared])
        self.assertAlmostEqual(1.5, declared[1].violation)

    def test_validate_solution_reports_decision_bound_violations(self) -> None:
        instance = binary_instance()
        diagnostics = validate_solution(instance, result_for(instance, (1.5, 0.0)))
        bound = next(row for row in diagnostics if row.constraint_id == "bound:x0")
        self.assertEqual("BLOCK", bound.status)
        self.assertAlmostEqual(0.5, bound.violation)

    def test_build_baseline_returns_a_real_feasible_result(self) -> None:
        instance = binary_instance()
        baseline = build_baseline(instance)
        self.assertEqual(0.0, baseline.incumbent)
        self.assertTrue(all(row.status == "PASS" for row in validate_solution(instance, baseline)))

    def test_build_baseline_rejects_an_infeasible_placeholder(self) -> None:
        instance = ProblemInstance(
            name="bad-baseline",
            direction="maximize",
            unit="1",
            objective=lambda x, _p: x[0],
            bounds=((0.0, 2.0),),
            constraints=(ConstraintSpec("upper", lambda x, _p: x[0], upper=1.0),),
            baseline_builder=lambda _instance: (2.0,),
        )
        with self.assertRaises(BaselineInfeasibleError):
            build_baseline(instance)

    def test_solve_complete_enumeration(self) -> None:
        result = solve(binary_instance(), SolveConfig(method="enumeration"))
        self.assertEqual(3.0, result.incumbent)
        self.assertEqual(3.0, result.bound)
        self.assertEqual(0.0, result.gap)

    def test_solve_differential_evolution_keeps_gap_empty(self) -> None:
        instance = ProblemInstance(
            name="continuous-bowl",
            direction="maximize",
            unit="point",
            objective=lambda x, _p: 1.0 - (x[0] - 0.25) ** 2,
            bounds=((-1.0, 1.0),),
            constraints=(ConstraintSpec("domain", lambda x, _p: x[0], lower=-1.0, upper=1.0),),
            baseline_builder=lambda _instance: (0.0,),
        )
        result = solve(
            instance,
            SolveConfig(
                method="differential_evolution",
                seeds=(11, 17),
                repetitions=2,
                maxiter=20,
                popsize=5,
            ),
        )
        self.assertGreater(result.incumbent, 0.999999)
        self.assertIsNone(result.bound)
        self.assertIsNone(result.gap)

    def test_enumeration_oracle_reports_suboptimal_with_correct_gap(self) -> None:
        instance = binary_instance()
        certificate = verify_small_instance(instance, result_for(instance, (0.0, 1.0)))
        self.assertEqual("suboptimal", certificate.status)
        self.assertEqual("enumeration", certificate.oracle)
        self.assertAlmostEqual(1.0, certificate.absolute_gap)
        self.assertAlmostEqual(1.0 / 3.0, certificate.relative_gap)

    def test_enumeration_oracle_reports_exact_match(self) -> None:
        instance = binary_instance()
        certificate = verify_small_instance(instance, result_for(instance, (1.0, 0.0)))
        self.assertEqual("exact_match", certificate.status)
        self.assertAlmostEqual(0.0, certificate.absolute_gap)

    def test_missing_oracle_reports_not_reducible_with_reason(self) -> None:
        instance = binary_instance(with_oracle=False)
        certificate = verify_small_instance(instance, result_for(instance, (1.0, 0.0)))
        self.assertEqual("not_reducible", certificate.status)
        self.assertIn("no finite enumeration", certificate.reason)

    def test_exact_milp_oracle_is_supported(self) -> None:
        instance = ProblemInstance(
            name="milp-knapsack",
            direction="maximize",
            unit="point",
            objective=lambda x, _p: 3.0 * x[0] + 2.0 * x[1],
            bounds=((0.0, 1.0), (0.0, 1.0)),
            constraints=(
                ConstraintSpec("capacity", lambda x, _p: 2.0 * x[0] + x[1], upper=2.0),
            ),
            baseline_builder=lambda _instance: (0.0, 0.0),
            milp_oracle=MilpOracle(
                objective_coefficients=(3.0, 2.0),
                integrality=(1, 1),
                constraint_matrix=((2.0, 1.0),),
                constraint_lower=(-math.inf,),
                constraint_upper=(2.0,),
            ),
        )
        solved = solve(instance, SolveConfig(method="milp"))
        self.assertEqual(3.0, solved.incumbent)
        certificate = verify_small_instance(instance, result_for(instance, (1.0, 0.0)))
        self.assertEqual("exact_match", certificate.status)
        self.assertEqual("milp", certificate.oracle)

    def test_run_sensitivity_covers_parameters_seeds_and_refinement(self) -> None:
        instance = binary_instance()
        instance = ProblemInstance(
            **{
                **instance.__dict__,
                "sensitivity_plan": SensitivityPlan(
                    parameter_sweeps=(
                        ParameterSweep(
                            "capacity",
                            (1.0, 2.0, 3.0),
                            lambda _i, result, value: result.incumbent + value,
                            "point",
                        ),
                    ),
                    seed_sweep=SeedSweep(
                        (3, 5, 7),
                        lambda _i, result, seed: result.incumbent + seed * 0.01,
                        "point",
                    ),
                    refinement_sweep=RefinementSweep(
                        (0.2, 0.1, 0.05),
                        lambda _i, _r, step: step**2,
                        theoretical_order=2.0,
                    ),
                ),
            }
        )
        report = run_sensitivity(instance, result_for(instance, (1.0, 0.0)))
        self.assertEqual(3, len(report.parameter_sweeps["capacity"]))
        self.assertEqual([3, 5, 7], report.seed_distribution["seeds"])
        orders = report.refinement["measured_orders"]
        self.assertTrue(all(abs(order - 2.0) < 1e-12 for order in orders))


if __name__ == "__main__":
    unittest.main()
