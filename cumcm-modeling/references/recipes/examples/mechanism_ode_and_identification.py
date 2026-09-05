"""Runnable minimum example for ODE integration and parameter identification."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np


TOOLKIT_ROOT = Path(__file__).resolve().parents[3] / "assets" / "mechanism-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from mechanism_toolkit import (  # noqa: E402
    ConvergenceCertificate,
    DistributionSpec,
    IdentificationConfig,
    IdentificationData,
    MechanismResult,
    OdeConfig,
    OdeEvent,
    UncertaintyConfig,
    identify_parameters,
    integrate_ode,
    propagate_uncertainty,
    verify_analytic_oracle,
)


def main() -> int:
    ode = integrate_ode(
        lambda _time, state, parameters: [-parameters["rate"] * state[0]],
        OdeConfig(
            t_span=(0.0, 2.0),
            y0=(1.0,),
            method="DOP853",
            rtol=1e-10,
            atol=1e-12,
            parameters={"rate": 1.7},
            events=(
                OdeEvent(
                    "half-life",
                    lambda _time, state, _parameters: [state[0] - 0.5],
                    terminal=False,
                    direction=-1.0,
                ),
            ),
        ),
    )
    convergence = MechanismResult(
        value_at_finest_level=2.0025,
        certificate=ConvergenceCertificate(
            levels=(0.2, 0.1, 0.05),
            solutions=(2.04, 2.01, 2.0025),
            theoretical_order=2.0,
            order_tolerance=0.1,
        ),
    )

    x = np.linspace(0.5, 5.0, 20)
    identification = identify_parameters(
        lambda parameters, values: parameters["k1"] * parameters["k2"] * values,
        IdentificationData(
            x=x,
            y=6.0 * x,
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
    uncertainty = propagate_uncertainty(
        lambda samples: np.exp(-samples["rate"] * 2.0),
        {"rate": DistributionSpec("normal", (1.7, 0.05))},
        UncertaintyConfig(
            sample_size=128,
            method="sobol",
            seed=31,
            absolute_precision=0.01,
            relative_precision=0.0,
        ),
    )
    oracles = {
        name: verify_analytic_oracle(name).status
        for name in ("exponential_decay", "robertson", "heat_dirichlet")
    }
    payload = {
        "ode": {
            **ode.as_dict(),
            "final_value": float(ode.y[0, -1]),
            "analytic_final_value": math.exp(-3.4),
        },
        "convergence": {
            "status": convergence.convergence_status,
            "value_at_finest_level": convergence.value_at_finest_level,
            "converged_value": convergence.converged_value,
        },
        "identification": identification.contract_fields(),
        "uncertainty": {
            "raw_estimate": uncertainty.raw_estimate,
            "reportable_estimate": uncertainty.reportable_estimate,
            "mc_standard_error": uncertainty.mc_standard_error,
            "sample_size_sufficient": uncertainty.sample_size_sufficient,
        },
        "analytic_oracles": oracles,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
