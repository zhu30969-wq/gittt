"""Unit tests for the standalone mechanism-modeling toolkit."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT_ROOT))

from mechanism_toolkit import (  # noqa: E402
    BoundaryCondition,
    ConvergenceCertificate,
    DistributionSpec,
    Heat1DConfig,
    IdentificationConfig,
    IdentificationData,
    IdentificationResult,
    MechanismResult,
    OdeConfig,
    OdeEvent,
    UncertaintyConfig,
    identify_parameters,
    integrate_ode,
    propagate_uncertainty,
    solve_heat_1d,
    verify_analytic_oracle,
)


class MechanismToolkitTests(unittest.TestCase):
    def test_integrate_ode_records_method_steps_and_event(self) -> None:
        result = integrate_ode(
            lambda _t, state, _parameters: [-state[0]],
            OdeConfig(
                (0.0, 2.0),
                (1.0,),
                method="DOP853",
                events=(
                    OdeEvent(
                        "half-life",
                        lambda _t, state, _parameters: [state[0] - 0.5],
                        terminal=True,
                        direction=-1.0,
                    ),
                ),
            ),
        )
        self.assertTrue(result.success)
        self.assertEqual("DOP853", result.method)
        self.assertGreater(result.step_count, 0)
        self.assertEqual(("half-life",), result.triggered_events)
        self.assertAlmostEqual(math.log(2.0), result.event_times["half-life"][0], places=7)

    def test_nonstiff_step_inflation_is_flagged_without_method_switch(self) -> None:
        result = integrate_ode(
            lambda _t, state, _parameters: [-state[0]],
            OdeConfig(
                (0.0, 2.0),
                (1.0,),
                method="RK45",
                rtol=1e-10,
                atol=1e-12,
                stiffness_step_threshold=1,
            ),
        )
        self.assertEqual("RK45", result.method)
        self.assertTrue(result.stiffness_suspected)

    def test_convergence_guard_erases_value_when_observed_order_mismatches(self) -> None:
        certificate = ConvergenceCertificate(
            levels=(0.2, 0.1, 0.05),
            solutions=(1.2, 1.1, 1.05),
            theoretical_order=2.0,
            order_tolerance=0.1,
        )
        result = MechanismResult(
            value_at_finest_level=1.05,
            certificate=certificate,
            converged_value=123.0,
        )
        self.assertEqual("order_mismatch", result.convergence_status)
        self.assertIsNone(result.converged_value)
        self.assertIsNotNone(result.value_at_finest_level)

    def test_convergence_guard_accepts_three_level_second_order_sequence(self) -> None:
        certificate = ConvergenceCertificate(
            levels=(0.2, 0.1, 0.05),
            solutions=(2.04, 2.01, 2.0025),
            theoretical_order=2.0,
            order_tolerance=1e-10,
        )
        result = MechanismResult(2.0025, certificate)
        self.assertEqual("converged", result.convergence_status)
        self.assertAlmostEqual(2.0, result.converged_value, places=12)

    def test_convergence_guard_requires_three_levels(self) -> None:
        certificate = ConvergenceCertificate(
            levels=(0.1, 0.05),
            solutions=(2.01, 2.0025),
            theoretical_order=2.0,
            order_tolerance=0.1,
        )
        result = MechanismResult(2.0025, certificate, converged_value=2.0)
        self.assertEqual("insufficient_levels", result.convergence_status)
        self.assertIsNone(result.converged_value)

    def test_identification_result_erases_unidentifiable_point_estimate(self) -> None:
        guarded = IdentificationResult(
            parameter_names=("k1", "k2"),
            point_estimate={"k1": 2.0, "k2": 3.0},
            parameter_intervals={"k1": (0.0, math.inf), "k2": (0.0, math.inf)},
            identifiable_combinations=("k1*k2",),
            fit_residual=0.0,
            holdout_residual=0.0,
            jacobian_condition_number=1e12,
            column_correlation_matrix=((1.0, 1.0), (1.0, 1.0)),
            condition_number_threshold=1e8,
            column_correlation_threshold=0.995,
        )
        self.assertFalse(guarded.identifiable)
        self.assertIsNone(guarded.point_estimate)
        self.assertTrue(guarded.identifiable_combinations)

    def test_product_only_model_is_structurally_unidentifiable(self) -> None:
        x = np.linspace(0.5, 5.0, 20)
        y = 6.0 * x
        result = identify_parameters(
            lambda parameters, values: parameters["k1"] * parameters["k2"] * values,
            IdentificationData(
                x=x,
                y=y,
                fit_indices=tuple(range(0, 20, 2)),
                holdout_indices=tuple(range(1, 20, 2)),
            ),
            IdentificationConfig(
                parameter_bounds={"k1": (1.0, 4.0), "k2": (1.5, 6.0)},
                starts=6,
                seed=31,
                identifiable_combinations=("k1*k2",),
            ),
        )
        self.assertFalse(result.identifiable)
        self.assertIsNone(result.point_estimate)
        self.assertEqual(("k1*k2",), tuple(result.identifiable_combinations))
        self.assertLess(result.fit_residual, 1e-8)
        self.assertLess(result.holdout_residual, 1e-8)
        self.assertEqual(6, len(result.starts))

    def test_heat_solver_supports_time_varying_robin_environment(self) -> None:
        result = solve_heat_1d(
            Heat1DConfig(
                length=0.01,
                diffusivity=1e-5,
                conductivity=20.0,
                spatial_points=15,
                t_span=(0.0, 2.0),
                initial_temperature=20.0,
                left_boundary=BoundaryCondition(
                    "robin", heat_transfer_coefficient=50.0
                ),
                right_boundary=BoundaryCondition(
                    "robin", heat_transfer_coefficient=50.0
                ),
                environment_temperature=lambda position, time: 80.0 + 5.0 * position + time,
                t_eval=(0.0, 2.0),
            )
        )
        self.assertTrue(result.ode_result.success)
        self.assertGreater(result.temperature[0, -1], 20.0)
        self.assertGreater(result.temperature[-1, -1], 20.0)
        self.assertEqual("insufficient_levels", result.mechanism_result.convergence_status)

    def test_heat_grid_refinement_issues_second_order_certificate(self) -> None:
        result = solve_heat_1d(
            Heat1DConfig(
                length=1.0,
                diffusivity=0.2,
                conductivity=1.0,
                spatial_points=81,
                refinement_points=(21, 41, 81),
                t_span=(0.0, 0.3),
                initial_temperature=lambda position, _time: math.sin(
                    math.pi * position
                ),
                left_boundary=BoundaryCondition("dirichlet", value=0.0),
                right_boundary=BoundaryCondition("dirichlet", value=0.0),
                method="BDF",
                rtol=1e-9,
                atol=1e-11,
            )
        )
        self.assertEqual("converged", result.mechanism_result.convergence_status)
        self.assertAlmostEqual(
            2.0,
            result.mechanism_result.certificate.observed_order,
            delta=0.01,
        )
        self.assertIsNotNone(result.mechanism_result.converged_value)

    def test_uncertainty_guard_erases_underpowered_estimate(self) -> None:
        result = propagate_uncertainty(
            lambda samples: samples["x"] ** 2,
            {"x": DistributionSpec("uniform", (0.0, 1.0))},
            UncertaintyConfig(
                sample_size=32,
                method="sobol",
                seed=9,
                absolute_precision=0.0,
                relative_precision=0.0,
            ),
        )
        self.assertFalse(result.sample_size_sufficient)
        self.assertIsNone(result.reportable_estimate)
        self.assertGreater(result.mc_standard_error, 0.0)

    def test_uncertainty_reports_estimate_when_mcse_is_within_budget(self) -> None:
        result = propagate_uncertainty(
            lambda samples: samples["x"] + samples["y"],
            {
                "x": DistributionSpec("normal", (1.0, 0.1)),
                "y": DistributionSpec("uniform", (0.0, 1.0)),
            },
            UncertaintyConfig(
                sample_size=128,
                method="halton",
                seed=4,
                absolute_precision=0.1,
                relative_precision=0.0,
            ),
        )
        self.assertTrue(result.sample_size_sufficient)
        self.assertIsNotNone(result.reportable_estimate)

    def test_three_builtin_oracles_match_independent_truth_sources(self) -> None:
        for case in ("exponential_decay", "robertson", "heat_dirichlet"):
            with self.subTest(case=case):
                result = verify_analytic_oracle(case)
                self.assertIn(result.status, {"exact_match", "within_tolerance"})
                self.assertLessEqual(result.maximum_absolute_error, result.tolerance)


if __name__ == "__main__":
    unittest.main()
