"""Unit tests for the standalone prediction and evaluation toolkit."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.model_selection import GridSearchCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLKIT_ROOT))

from predict_toolkit import (  # noqa: E402
    BacktestProtocol,
    KnownLimitation,
    ModelSpec,
    PredictConfig,
    PredictionDataset,
    RankPerturbation,
    TemporalLeakageError,
    TemporalMetadataError,
    TuningLeakageError,
    backtest,
    calibrate,
    drift_diagnostic,
    fit_predict,
    rank_stability,
)


def classification_data() -> PredictionDataset:
    rng = np.random.default_rng(1234)
    features = pd.DataFrame(
        rng.normal(size=(90, 3)), columns=["cash", "volatility", "tenure"]
    )
    logit = 1.3 * features["cash"] - 0.8 * features["volatility"]
    labels = (logit + rng.normal(scale=0.6, size=len(features)) > 0).astype(int)
    return PredictionDataset(features=features, labels=labels)


def classification_model(**overrides: object) -> ModelSpec:
    values: dict[str, object] = {
        "estimator": Pipeline(
            [
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=2_000, random_state=19)),
            ]
        ),
        "task": "binary_classification",
        "search_space": {"model__C": [0.2, 1.0]},
        "scoring": "neg_brier_score",
        "parameter_provenance": "outer_train_inner_cv",
    }
    values.update(overrides)
    return ModelSpec(**values)


def classification_protocol(**overrides: object) -> BacktestProtocol:
    values: dict[str, object] = {
        "outer_strategy": "stratified",
        "outer_splits": 3,
        "inner_splits": 2,
        "repeats": 2,
        "random_state": 41,
        "bootstrap_samples": 20,
    }
    values.update(overrides)
    return BacktestProtocol(**values)


def temporal_data(*, leak: bool) -> PredictionDataset:
    time = np.arange(60)
    signal = np.sin(time / 5.0) + 0.02 * time
    lag = np.concatenate(([signal[0]], signal[:-1]))
    features = pd.DataFrame({"lag_one": lag, "time": time})
    availability = pd.DataFrame(
        {
            "lag_one": time + 1 if leak else time - 1,
            "time": time,
        }
    )
    return PredictionDataset(
        features=features,
        labels=signal,
        sample_times=time,
        feature_available_times=availability,
    )


class PredictToolkitTests(unittest.TestCase):
    def test_fit_predict_carries_scope_label_status_and_structured_limits(self) -> None:
        result = fit_predict(
            classification_data(),
            PredictConfig(
                model=classification_model(),
                protocol=classification_protocol(),
                metric_scope="synthetic labeled source sample, nested OOF only",
                label_availability="proxy",
                causal_design=True,
                known_limitations=(
                    KnownLimitation(
                        "SYNTHETIC_SCOPE",
                        "The toy population is not an external application cohort.",
                        "example only",
                    ),
                ),
            ),
        )
        self.assertEqual("proxy", result.label_availability)
        self.assertFalse(result.causal_claim_allowed)
        self.assertFalse(result.probability_calibrated)
        self.assertIn("not_validated_as_probability", result.score_semantics)
        limitation_codes = {row.code for row in result.known_limitations}
        self.assertIn("PROXY_LABEL_NO_CAUSAL_CLAIM", limitation_codes)
        self.assertIn("UNCALIBRATED_SCORE_NOT_PROBABILITY", limitation_codes)
        self.assertIn("SYNTHETIC_SCOPE", limitation_codes)
        self.assertIn("roc_auc", result.metrics)
        self.assertEqual(6, len(result.oof.fold_records))

    def test_missing_application_labels_add_transfer_limit(self) -> None:
        source = classification_data()
        application = pd.DataFrame(
            np.zeros((4, 3)), columns=source.features.columns
        )
        result = fit_predict(
            PredictionDataset(source.features, source.labels, application),
            PredictConfig(
                model=classification_model(),
                protocol=classification_protocol(bootstrap_samples=0),
                metric_scope="source-sample nested OOF; application labels missing",
                label_availability="missing",
            ),
        )
        self.assertEqual(4, len(result.scores))
        self.assertFalse(result.causal_claim_allowed)
        self.assertIn(
            "APPLICATION_LABELS_MISSING",
            {row.code for row in result.known_limitations},
        )

    def test_backtest_runs_search_inside_each_outer_training_fold(self) -> None:
        result = backtest(
            classification_data(), classification_model(), classification_protocol()
        )
        self.assertEqual("outer_train_only", result.tuning_scope)
        self.assertEqual(6, len(result.fold_records))
        self.assertTrue(all(row.best_params for row in result.fold_records))
        self.assertTrue(np.all(result.evaluated_mask))

    def test_backtest_rejects_full_dataset_tuning_scope(self) -> None:
        protocol = classification_protocol(tuning_scope="full_dataset_then_cv")
        with self.assertRaisesRegex(TuningLeakageError, "outer_train_only"):
            backtest(classification_data(), classification_model(), protocol)

    def test_backtest_rejects_declared_full_data_selected_parameters(self) -> None:
        model = classification_model(
            search_space={}, parameter_provenance="full_data_tuned"
        )
        with self.assertRaisesRegex(TuningLeakageError, "full dataset"):
            backtest(classification_data(), model, classification_protocol())

    def test_backtest_rejects_a_prefitted_search_object(self) -> None:
        data = classification_data()
        search = GridSearchCV(
            LogisticRegression(max_iter=1_000), {"C": [0.5, 1.0]}, cv=2
        ).fit(data.features, data.labels)
        model = ModelSpec(search, task="binary_classification")
        with self.assertRaisesRegex(TuningLeakageError, "fitted search object"):
            backtest(data, model, classification_protocol())

    def test_temporal_backtest_rejects_future_feature_availability(self) -> None:
        protocol = BacktestProtocol(
            outer_strategy="time_series",
            outer_splits=4,
            inner_splits=2,
            bootstrap_samples=0,
        )
        model = ModelSpec(Ridge(alpha=0.1), task="regression")
        with self.assertRaisesRegex(TemporalLeakageError, "after their prediction origin"):
            backtest(temporal_data(leak=True), model, protocol)

    def test_temporal_backtest_accepts_lagged_features_and_preserves_order(self) -> None:
        protocol = BacktestProtocol(
            outer_strategy="time_series",
            outer_splits=4,
            inner_splits=2,
            bootstrap_samples=10,
        )
        result = backtest(
            temporal_data(leak=False),
            ModelSpec(Ridge(alpha=0.1), task="regression"),
            protocol,
        )
        self.assertTrue(result.temporal_order_preserved)
        self.assertTrue(result.feature_availability_checked)
        self.assertTrue(
            all(row.train_index_max < row.test_index_min for row in result.fold_records)
        )
        self.assertIn("rmse", result.metrics)

    def test_temporal_backtest_requires_availability_metadata(self) -> None:
        data = temporal_data(leak=False)
        incomplete = PredictionDataset(data.features, data.labels)
        protocol = BacktestProtocol(
            outer_strategy="time_series", outer_splits=3, inner_splits=2
        )
        with self.assertRaises(TemporalMetadataError):
            backtest(incomplete, ModelSpec(Ridge(), task="regression"), protocol)

    def test_calibrate_returns_curves_brier_comparison_and_semantics(self) -> None:
        labels = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1] * 4)
        scores = np.array([0.05, 0.10, 0.15, 0.20, 0.45, 0.55, 0.80, 0.85, 0.90, 0.95] * 4)
        report = calibrate(scores, labels)
        self.assertTrue(report.cross_fitted)
        self.assertGreater(len(report.curve_before), 1)
        self.assertGreater(len(report.curve_after), 1)
        self.assertTrue(np.isfinite(report.brier_before))
        self.assertTrue(np.isfinite(report.brier_after))
        self.assertFalse(report.raw_score_probability_claim_allowed)
        self.assertTrue(report.calibrated_probability_claim_allowed)

    def test_rank_stability_reports_known_pair_flip_and_displacement(self) -> None:
        report = rank_stability(
            np.array([0.68, 0.70, 0.52]),
            RankPerturbation(
                np.array([[0.84, 0.70, 0.36]]), labels=("weight_shift",)
            ),
        )
        self.assertTrue(report.any_rank_reversal)
        self.assertEqual((1,), tuple(report.flip_pair_counts))
        self.assertEqual(((0, 1),), tuple(report.flipped_pairs))
        self.assertEqual(1, report.max_rank_displacement)

    def test_rank_stability_does_not_invent_a_flip(self) -> None:
        report = rank_stability(
            np.array([0.9, 0.5, 0.1]), np.array([[0.8, 0.6, 0.2]])
        )
        self.assertFalse(report.any_rank_reversal)
        self.assertEqual(0, report.total_flip_pair_count)
        self.assertEqual(0, report.max_rank_displacement)
        self.assertAlmostEqual(1.0, report.minimum_spearman)

    def test_drift_diagnostic_is_descriptive_and_not_causal(self) -> None:
        rng = np.random.default_rng(8)
        train = pd.DataFrame(
            {"stable": rng.normal(size=300), "shifted": rng.normal(size=300)}
        )
        apply = pd.DataFrame(
            {"stable": train["stable"].copy(), "shifted": rng.normal(2.5, 1.0, 300)}
        )
        report = drift_diagnostic(train, apply)
        by_name = {row.name: row.psi for row in report.features}
        self.assertGreater(by_name["shifted"], by_name["stable"])
        self.assertFalse(report.causal_claim_allowed)
        self.assertEqual("descriptive_distribution_shift_proxy", report.evidence_type)
        self.assertEqual("PSI_DESCRIPTIVE_NOT_CAUSAL", report.known_limitations[0].code)


if __name__ == "__main__":
    unittest.main()
