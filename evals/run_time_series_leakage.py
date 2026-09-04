#!/usr/bin/env python3
"""Execute E02's independent temporal-leakage positive/negative benchmark."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from _forward_scenario_support import (
    load_scenario,
    parse_target_args,
    prepare_target,
    print_summary,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = REPO_ROOT / "cumcm-modeling" / "assets" / "predict-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from predict_toolkit import (  # noqa: E402
    BacktestProtocol,
    ModelSpec,
    PredictionDataset,
    TemporalLeakageError,
    backtest,
)


SCENARIO_PATH = Path(__file__).with_name("e02_time_series_leakage_scenario.json")
SCENARIO_ID = "E02-time-series-leakage"


def build_series(sample_count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    values = np.zeros(sample_count, dtype=float)
    for index in range(1, sample_count):
        values[index] = (
            0.76 * values[index - 1]
            + 0.18 * np.sin(index / 4.0)
            + rng.normal(scale=0.04)
        )
    return values


def feature_tables(
    values: np.ndarray, future_offset: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    count = len(values)
    time = np.arange(count)
    lag_one = np.concatenate(([values[0]], values[:-1]))
    past_mean = np.array(
        [np.mean(values[max(0, index - 3):index]) if index else values[0] for index in time]
    )
    honest = pd.DataFrame({"lag_one": lag_one, "window_mean": past_mean})
    honest_availability = pd.DataFrame(
        {"lag_one": time - 1, "window_mean": time - 1}
    )

    future_mean = np.array(
        [
            np.mean(values[index + 1:min(count, index + future_offset + 1)])
            if index + 1 < count
            else values[index]
            for index in time
        ]
    )
    leaked = honest.copy()
    leaked["window_mean"] = future_mean
    leaked_availability = honest_availability.copy()
    leaked_availability["window_mean"] = np.minimum(
        time + future_offset, count - 1
    )
    return honest, honest_availability, leaked, leaked_availability


def main() -> int:
    args = parse_target_args("Run E02's non-overwriting temporal-leakage benchmark.")
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e02-time-series-leakage")
    values = build_series(int(scenario["sample_count"]), int(scenario["seed"]))
    time = np.arange(len(values))
    honest, honest_times, leaked, leaked_times = feature_tables(
        values, int(scenario["future_offset"])
    )
    protocol = BacktestProtocol(
        outer_strategy="time_series",
        outer_splits=int(scenario["outer_splits"]),
        inner_splits=int(scenario["inner_splits"]),
        random_state=int(scenario["seed"]),
        bootstrap_samples=40,
    )
    model = ModelSpec(
        Ridge(),
        task="regression",
        search_space={"alpha": [0.01, 0.1, 1.0]},
        scoring="neg_root_mean_squared_error",
        parameter_provenance="outer_train_inner_cv",
    )

    leak_code = None
    leak_message = None
    try:
        backtest(
            PredictionDataset(leaked, values, sample_times=time, feature_available_times=leaked_times),
            model,
            protocol,
        )
    except TemporalLeakageError as exc:
        leak_code = exc.code
        leak_message = str(exc)
    if leak_code != scenario["expected_leak_code"]:
        raise AssertionError(
            f"known lookahead must raise {scenario['expected_leak_code']}, got {leak_code}"
        )

    positive = backtest(
        PredictionDataset(honest, values, sample_times=time, feature_available_times=honest_times),
        model,
        protocol,
    )
    if not positive.temporal_order_preserved or not positive.feature_availability_checked:
        raise AssertionError("honest backtest did not preserve and verify temporal order")
    if positive.tuning_scope != scenario["expected_tuning_scope"]:
        raise AssertionError("hyperparameter tuning escaped the outer training folds")
    if any(
        row.train_index_max >= row.test_index_min for row in positive.fold_records
    ):
        raise AssertionError("an outer fold used observations at or after its test window")
    mask = positive.evaluated_mask
    naive = honest["lag_one"].to_numpy(dtype=float)[mask]
    naive_rmse = float(np.sqrt(np.mean((values[mask] - naive) ** 2)))
    if not np.isfinite(naive_rmse):
        raise AssertionError("naive forecast baseline was not computed")

    payload = {
        "status": "PASS",
        "scenario": SCENARIO_ID,
        "truth_source": "synthetic feature-availability timestamps",
        "negative": {
            "finding": leak_code,
            "message": leak_message,
            "future_offset": int(scenario["future_offset"]),
        },
        "positive": {
            "metric_scope": positive.metric_scope,
            "tuning_scope": positive.tuning_scope,
            "feature_availability_checked": positive.feature_availability_checked,
            "temporal_order_preserved": positive.temporal_order_preserved,
            "model_rmse": positive.metrics["rmse"],
            "naive_lag_one_rmse": naive_rmse,
            "evaluated_count": int(np.sum(mask)),
        },
        "preserved_bundle": str(target),
    }
    result_path = target / "e02_result.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
