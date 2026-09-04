"""Runnable minimum example for scoped prediction and classification."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


TOOLKIT_ROOT = Path(__file__).resolve().parents[3] / "assets" / "predict-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from predict_toolkit import (  # noqa: E402
    BacktestProtocol,
    KnownLimitation,
    ModelSpec,
    PredictConfig,
    PredictionDataset,
    calibrate,
    drift_diagnostic,
    fit_predict,
)


def main() -> int:
    rng = np.random.default_rng(2020)
    source = pd.DataFrame(
        rng.normal(size=(120, 3)),
        columns=["cash_flow", "volatility", "relationship_length"],
    )
    latent = 1.1 * source["cash_flow"] - 0.9 * source["volatility"]
    labels = (latent + rng.normal(scale=0.8, size=len(source)) > 0.0).astype(int)
    application = pd.DataFrame(
        rng.normal(loc=(0.25, 0.0, 0.0), size=(24, 3)), columns=source.columns
    )

    model = ModelSpec(
        estimator=Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=2_000, random_state=2020)),
            ]
        ),
        task="binary_classification",
        search_space={"model__C": [0.2, 1.0, 5.0]},
        scoring="neg_brier_score",
        parameter_provenance="outer_train_inner_cv",
    )
    protocol = BacktestProtocol(
        outer_strategy="stratified",
        outer_splits=3,
        inner_splits=2,
        repeats=2,
        random_state=2020,
        bootstrap_samples=50,
    )
    result = fit_predict(
        PredictionDataset(source, labels, prediction_features=application),
        PredictConfig(
            model=model,
            protocol=protocol,
            metric_scope="synthetic source sample, repeated nested outer folds only",
            label_availability="missing",
            known_limitations=(
                KnownLimitation(
                    code="SYNTHETIC_EXAMPLE_ONLY",
                    message="Synthetic coefficients are not an empirical domain model.",
                    scope="this runnable recipe",
                ),
            ),
        ),
    )
    evaluated = result.oof.evaluated_mask
    calibration = calibrate(result.oof.predictions[evaluated], labels[evaluated])
    drift = drift_diagnostic(source, application)

    if result.causal_claim_allowed:
        raise AssertionError("missing application labels cannot support a causal claim")
    if result.probability_calibrated:
        raise AssertionError("fit_predict must not silently mark scores as calibrated")
    if result.oof.tuning_scope != "outer_train_only":
        raise AssertionError("hyperparameter search escaped the outer training folds")
    print(
        json.dumps(
            {
                "prediction": result.as_dict(),
                "calibration": calibration.as_dict(),
                "drift": drift.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
