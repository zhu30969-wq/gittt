#!/usr/bin/env python3
"""Execute E21's structural and contract-level identifiability guards."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from _forward_scenario_support import (
    load_scenario,
    parse_target_args,
    prepare_target,
    print_summary,
    require_code,
    require_pass,
    run_audit,
)
from test_audit_regressions import run_audit as run_in_process_audit
from test_identifiability_guard import build_identification_release


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = REPO_ROOT / "cumcm-modeling" / "assets" / "mechanism-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from mechanism_toolkit import (  # noqa: E402
    IdentificationConfig,
    IdentificationData,
    identify_parameters,
)


SCENARIO_PATH = Path(__file__).with_name("e21_identifiability_guard_scenario.json")
SCENARIO_ID = "E21-identifiability-guard"


def main() -> int:
    args = parse_target_args("Run E21's non-overwriting identifiability benchmark.")
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e21-identifiability-guard")
    report_root = target / "reports"

    count = int(scenario["sample_count"])
    stride = int(scenario["fit_stride"])
    x = np.linspace(0.5, 5.0, count)
    y = float(scenario["true_product"]) * x
    bounds = {
        name: (float(values[0]), float(values[1]))
        for name, values in scenario["parameter_bounds"].items()
    }
    identification = identify_parameters(
        lambda parameters, values: parameters["k1"] * parameters["k2"] * values,
        IdentificationData(
            x=x,
            y=y,
            fit_indices=tuple(range(0, count, stride)),
            holdout_indices=tuple(range(1, count, stride)),
        ),
        IdentificationConfig(
            parameter_bounds=bounds,
            starts=int(scenario["starts"]),
            seed=int(scenario["seed"]),
            identifiable_combinations=(str(scenario["identifiable_combination"]),),
        ),
    )
    if identification.identifiable:
        raise AssertionError("product-only parameters were incorrectly declared identifiable")
    if identification.point_estimate is not None:
        raise AssertionError("unidentifiable toolkit result exposed a point estimate")
    if not identification.identifiable_combinations:
        raise AssertionError("unidentifiable toolkit result lost its identifiable combination")

    positive_root = build_identification_release(expose_point_estimate=False)
    positive_report = run_audit(
        positive_root, report_root / "positive-suppressed.json", {0}
    )
    require_pass(positive_report)

    negative_root = build_identification_release(expose_point_estimate=True)
    negative_report = run_audit(
        negative_root, report_root / "negative-point-claim.json", {10, 12}
    )
    finding_code = str(scenario["expected_finding"])
    require_code(negative_report, finding_code)
    _report, negative_audit = run_in_process_audit(negative_root)
    if negative_audit.result_eligibility.get("result:main") is not False:
        raise AssertionError("unidentifiable claimed point estimate remained eligible")

    print_summary(
        {
            "status": "PASS",
            "scenario": SCENARIO_ID,
            "preserved_bundle": str(target),
            "toolkit_guard": {
                "identifiable": identification.identifiable,
                "point_estimate": identification.point_estimate,
                "identifiable_combinations": list(
                    identification.identifiable_combinations
                ),
                "jacobian_condition_number": identification.jacobian_condition_number,
                "maximum_column_correlation": identification.maximum_column_correlation,
            },
            "contract_positive_status": positive_report["status"],
            "contract_negative_finding": finding_code,
            "contract_negative_result_eligibility": False,
            "contract_fixture_roots": {
                "positive": str(positive_root),
                "negative": str(negative_root),
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
