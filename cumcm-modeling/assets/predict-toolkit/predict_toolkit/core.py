"""Reusable prediction, validation, ranking, and drift interfaces for CUMCM.

The toolkit is evidence-first: metrics carry their scope and label status,
hyperparameter search is nested inside each outer training fold, calibration is
evaluated out of fold, and distribution drift is explicitly descriptive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.base import clone
from sklearn.calibration import calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    log_loss,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.model_selection import GridSearchCV, KFold, StratifiedKFold, TimeSeriesSplit


TaskKind = Literal["binary_classification", "regression"]
LabelAvailability = Literal["real", "proxy", "missing"]
OuterStrategy = Literal["stratified", "kfold", "time_series"]


class PredictionToolkitError(ValueError):
    """Base class for rejected prediction workflows."""

    code = "PREDICTION_TOOLKIT_ERROR"


class TuningLeakageError(PredictionToolkitError):
    """Raised when an API call would tune on outer validation observations."""

    code = "FULL_DATA_TUNING_FORBIDDEN"


class TemporalLeakageError(PredictionToolkitError):
    """Raised when a feature uses information unavailable at prediction time."""

    code = "TEMPORAL_FEATURE_LOOKAHEAD"


class TemporalMetadataError(PredictionToolkitError):
    """Raised when chronological validation lacks feature-availability metadata."""

    code = "TEMPORAL_AVAILABILITY_UNDECLARED"


@dataclass(frozen=True)
class KnownLimitation:
    """One machine-readable limitation attached to a result."""

    code: str
    message: str
    scope: str

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip() or not self.scope.strip():
            raise ValueError("limitation code, message, and scope must be non-empty")

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "scope": self.scope}


@dataclass(frozen=True)
class PredictionDataset:
    """Labeled fitting data plus optional application data and time metadata.

    ``feature_available_times`` has the same row count as ``features``. Each
    cell records the latest time used to construct that feature value. For a
    temporal backtest it must be no later than the corresponding prediction
    origin in ``sample_times``.
    """

    features: Any
    labels: Any
    prediction_features: Any | None = None
    sample_times: Any | None = None
    feature_available_times: Any | None = None


@dataclass(frozen=True)
class ModelSpec:
    """An unfitted estimator and, optionally, a nested search space."""

    estimator: Any
    task: TaskKind
    search_space: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    scoring: str | None = None
    positive_label: Any = 1
    parameter_provenance: str = "predeclared"

    def __post_init__(self) -> None:
        if self.task not in {"binary_classification", "regression"}:
            raise ValueError(f"unsupported prediction task: {self.task!r}")
        allowed = {"predeclared", "outer_train_inner_cv", "full_data_tuned"}
        if self.parameter_provenance not in allowed:
            raise ValueError(f"parameter_provenance must be one of {sorted(allowed)}")


@dataclass(frozen=True)
class BacktestProtocol:
    """Nested validation protocol with an explicit tuning boundary."""

    outer_strategy: OuterStrategy
    outer_splits: int = 5
    inner_splits: int = 3
    repeats: int = 1
    random_state: int = 2020
    tuning_scope: str = "outer_train_only"
    bootstrap_samples: int = 200
    confidence_level: float = 0.95
    require_feature_availability: bool = True

    def __post_init__(self) -> None:
        if self.outer_strategy not in {"stratified", "kfold", "time_series"}:
            raise ValueError(f"unsupported outer strategy: {self.outer_strategy!r}")
        if self.outer_splits < 2 or self.inner_splits < 2:
            raise ValueError("outer_splits and inner_splits must both be at least 2")
        if self.repeats < 1:
            raise ValueError("repeats must be positive")
        if self.outer_strategy == "time_series" and self.repeats != 1:
            raise ValueError("time_series backtests use one chronological repeat")
        if self.bootstrap_samples < 0:
            raise ValueError("bootstrap_samples must be non-negative")
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError("confidence_level must lie strictly between 0 and 1")


@dataclass(frozen=True)
class PredictConfig:
    """Configuration for fitting without overstating the evidence."""

    model: ModelSpec
    protocol: BacktestProtocol
    metric_scope: str
    label_availability: LabelAvailability
    causal_design: bool = False
    known_limitations: Sequence[KnownLimitation] = ()

    def __post_init__(self) -> None:
        if not self.metric_scope.strip():
            raise ValueError("metric_scope must be non-empty")
        if self.label_availability not in {"real", "proxy", "missing"}:
            raise ValueError("label_availability must be real, proxy, or missing")
        if any(not isinstance(item, KnownLimitation) for item in self.known_limitations):
            raise TypeError("known_limitations must contain KnownLimitation values")


@dataclass(frozen=True)
class FoldRecord:
    repeat: int
    fold: int
    train_size: int
    test_size: int
    train_index_min: int
    train_index_max: int
    test_index_min: int
    test_index_max: int
    best_params: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repeat": self.repeat,
            "fold": self.fold,
            "train_size": self.train_size,
            "test_size": self.test_size,
            "train_index_min": self.train_index_min,
            "train_index_max": self.train_index_max,
            "test_index_min": self.test_index_min,
            "test_index_max": self.test_index_max,
            "best_params": dict(self.best_params),
        }


@dataclass(frozen=True)
class OOFResult:
    """Repeated out-of-fold predictions and their scoped evidence."""

    predictions: np.ndarray
    repeat_predictions: np.ndarray
    evaluated_mask: np.ndarray
    metrics: Mapping[str, float]
    metric_intervals: Mapping[str, tuple[float, float]]
    fold_records: Sequence[FoldRecord]
    metric_scope: str
    outer_strategy: OuterStrategy
    tuning_scope: str
    temporal_order_preserved: bool
    feature_availability_checked: bool
    probability_calibrated: bool
    score_semantics: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "predictions": _nullable_list(self.predictions),
            "repeat_predictions": [
                _nullable_list(row) for row in np.asarray(self.repeat_predictions)
            ],
            "evaluated_count": int(np.sum(self.evaluated_mask)),
            "metrics": {key: float(value) for key, value in self.metrics.items()},
            "metric_intervals": {
                key: [float(bounds[0]), float(bounds[1])]
                for key, bounds in self.metric_intervals.items()
            },
            "fold_records": [row.as_dict() for row in self.fold_records],
            "metric_scope": self.metric_scope,
            "outer_strategy": self.outer_strategy,
            "tuning_scope": self.tuning_scope,
            "temporal_order_preserved": self.temporal_order_preserved,
            "feature_availability_checked": self.feature_availability_checked,
            "probability_calibrated": self.probability_calibrated,
            "score_semantics": self.score_semantics,
        }


@dataclass(frozen=True)
class PredictResult:
    """Final predictions plus explicit evidence boundaries."""

    scores: np.ndarray
    predictions: np.ndarray
    metrics: Mapping[str, float]
    metric_intervals: Mapping[str, tuple[float, float]]
    metric_scope: str
    label_availability: LabelAvailability
    causal_claim_allowed: bool
    known_limitations: Sequence[KnownLimitation]
    probability_calibrated: bool
    score_semantics: str
    oof: OOFResult
    fitted_model: Any = field(repr=False, compare=False)
    selected_parameters: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scores": _nullable_list(self.scores),
            "predictions": _plain_list(self.predictions),
            "metrics": {key: float(value) for key, value in self.metrics.items()},
            "metric_intervals": {
                key: [float(bounds[0]), float(bounds[1])]
                for key, bounds in self.metric_intervals.items()
            },
            "metric_scope": self.metric_scope,
            "label_availability": self.label_availability,
            "causal_claim_allowed": self.causal_claim_allowed,
            "known_limitations": [row.as_dict() for row in self.known_limitations],
            "probability_calibrated": self.probability_calibrated,
            "score_semantics": self.score_semantics,
            "selected_parameters": dict(self.selected_parameters),
            "oof": self.oof.as_dict(),
        }


@dataclass(frozen=True)
class Calibration:
    """Cross-fitted Platt calibration evidence."""

    raw_scores: np.ndarray
    calibrated_scores: np.ndarray
    curve_before: Sequence[Mapping[str, float]]
    curve_after: Sequence[Mapping[str, float]]
    brier_before: float
    brier_after: float
    method: str
    cross_fitted: bool
    raw_score_probability_claim_allowed: bool
    calibrated_probability_claim_allowed: bool
    interpretation: str
    fitted_calibrator: Any = field(repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "curve_before": [dict(point) for point in self.curve_before],
            "curve_after": [dict(point) for point in self.curve_after],
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "method": self.method,
            "cross_fitted": self.cross_fitted,
            "raw_score_probability_claim_allowed": self.raw_score_probability_claim_allowed,
            "calibrated_probability_claim_allowed": self.calibrated_probability_claim_allowed,
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True)
class RankPerturbation:
    """Predeclared alternative score vectors for a ranking."""

    scores: Any
    labels: Sequence[str] = ()


@dataclass(frozen=True)
class StabilityReport:
    """Rank correlation and explicit local reversal evidence."""

    spearman_values: Sequence[float]
    flip_pair_counts: Sequence[int]
    flipped_pairs: Sequence[tuple[int, int]]
    max_displacements: Sequence[int]
    median_spearman: float
    minimum_spearman: float
    total_flip_pair_count: int
    unique_flip_pair_count: int
    max_rank_displacement: int
    any_rank_reversal: bool
    perturbation_labels: Sequence[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "spearman_values": [float(value) for value in self.spearman_values],
            "flip_pair_counts": [int(value) for value in self.flip_pair_counts],
            "flipped_pairs": [list(pair) for pair in self.flipped_pairs],
            "max_displacements": [int(value) for value in self.max_displacements],
            "median_spearman": float(self.median_spearman),
            "minimum_spearman": float(self.minimum_spearman),
            "total_flip_pair_count": int(self.total_flip_pair_count),
            "unique_flip_pair_count": int(self.unique_flip_pair_count),
            "max_rank_displacement": int(self.max_rank_displacement),
            "any_rank_reversal": self.any_rank_reversal,
            "perturbation_labels": list(self.perturbation_labels),
        }


@dataclass(frozen=True)
class DriftFeature:
    name: str
    psi: float | None
    train_count: int
    apply_count: int
    train_missing_rate: float
    apply_missing_rate: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "psi": self.psi,
            "train_count": self.train_count,
            "apply_count": self.apply_count,
            "train_missing_rate": self.train_missing_rate,
            "apply_missing_rate": self.apply_missing_rate,
        }


@dataclass(frozen=True)
class DriftReport:
    """Descriptive population-shift diagnostics, never causal evidence."""

    features: Sequence[DriftFeature]
    median_psi: float | None
    maximum_psi: float | None
    features_above_0_10: int
    features_above_0_25: int
    metric_scope: str
    evidence_type: str
    causal_claim_allowed: bool
    known_limitations: Sequence[KnownLimitation]

    def as_dict(self) -> dict[str, Any]:
        return {
            "features": [row.as_dict() for row in self.features],
            "median_psi": self.median_psi,
            "maximum_psi": self.maximum_psi,
            "features_above_0_10": self.features_above_0_10,
            "features_above_0_25": self.features_above_0_25,
            "metric_scope": self.metric_scope,
            "evidence_type": self.evidence_type,
            "causal_claim_allowed": self.causal_claim_allowed,
            "known_limitations": [row.as_dict() for row in self.known_limitations],
        }


def _to_frame(values: Any, name: str) -> pd.DataFrame:
    if isinstance(values, pd.DataFrame):
        frame = values.copy()
    else:
        array = np.asarray(values)
        if array.ndim == 1:
            array = array[:, None]
        if array.ndim != 2:
            raise ValueError(f"{name} must be a 1D or 2D table")
        frame = pd.DataFrame(
            array, columns=[f"feature_{index}" for index in range(array.shape[1])]
        )
    if frame.empty or frame.shape[1] == 0:
        raise ValueError(f"{name} must contain at least one row and one feature")
    return frame.reset_index(drop=True)


def _labels(values: Any, expected: int) -> np.ndarray:
    labels = np.asarray(values)
    if labels.ndim != 1 or labels.size != expected:
        raise ValueError("labels must be one-dimensional and match feature rows")
    return labels


def _plain_list(values: Any) -> list[Any]:
    result: list[Any] = []
    for value in np.asarray(values).tolist():
        result.append(value.item() if hasattr(value, "item") else value)
    return result


def _nullable_list(values: Any) -> list[float | None]:
    return [None if not np.isfinite(value) else float(value) for value in np.asarray(values)]


def _validate_tuning_contract(model: ModelSpec, protocol: BacktestProtocol) -> None:
    if protocol.tuning_scope != "outer_train_only":
        raise TuningLeakageError(
            "hyperparameter selection must use tuning_scope='outer_train_only'; "
            "full-data tuning cannot become out-of-fold evidence"
        )
    if model.parameter_provenance == "full_data_tuned":
        raise TuningLeakageError(
            "a model whose parameters were selected on the full dataset is rejected; "
            "pass the unfitted estimator and search_space for nested tuning"
        )
    if hasattr(model.estimator, "best_params_") or hasattr(model.estimator, "cv_results_"):
        raise TuningLeakageError(
            "a fitted search object is rejected; pass its base estimator and "
            "search_space so tuning occurs inside each outer training fold"
        )


def _validate_temporal_metadata(
    dataset: PredictionDataset,
    features: pd.DataFrame,
    protocol: BacktestProtocol,
) -> bool:
    if protocol.outer_strategy != "time_series":
        return False
    if dataset.sample_times is None or dataset.feature_available_times is None:
        if protocol.require_feature_availability:
            raise TemporalMetadataError(
                "time-series backtesting requires sample_times and "
                "feature_available_times to verify information availability"
            )
        return False
    sample_times = np.asarray(dataset.sample_times)
    if sample_times.ndim != 1 or sample_times.size != len(features):
        raise ValueError("sample_times must be one-dimensional and match feature rows")
    availability = _to_frame(dataset.feature_available_times, "feature_available_times")
    if availability.shape != features.shape:
        raise ValueError("feature_available_times must have the same shape as features")
    availability.columns = features.columns
    leaks: list[str] = []
    for row_index in range(len(features)):
        for column_index, column in enumerate(features.columns):
            try:
                is_future = availability.iat[row_index, column_index] > sample_times[row_index]
            except TypeError as exc:
                raise ValueError(
                    "sample_times and feature_available_times must be comparable"
                ) from exc
            if bool(is_future):
                leaks.append(f"row={row_index}, feature={column}")
    if leaks:
        preview = "; ".join(leaks[:5])
        suffix = "" if len(leaks) <= 5 else f"; ... ({len(leaks)} total)"
        raise TemporalLeakageError(
            f"feature values use information after their prediction origin: {preview}{suffix}"
        )
    return True


def _outer_splits(
    features: pd.DataFrame,
    labels: np.ndarray,
    protocol: BacktestProtocol,
) -> list[tuple[int, int, np.ndarray, np.ndarray]]:
    rows: list[tuple[int, int, np.ndarray, np.ndarray]] = []
    if protocol.outer_strategy == "time_series":
        splitter = TimeSeriesSplit(n_splits=protocol.outer_splits)
        for fold, (train, test) in enumerate(splitter.split(features)):
            rows.append((0, fold, train, test))
        return rows
    for repeat in range(protocol.repeats):
        seed = protocol.random_state + repeat
        if protocol.outer_strategy == "stratified":
            splitter = StratifiedKFold(
                n_splits=protocol.outer_splits,
                shuffle=True,
                random_state=seed,
            )
            split_iterator = splitter.split(features, labels)
        else:
            splitter = KFold(
                n_splits=protocol.outer_splits,
                shuffle=True,
                random_state=seed,
            )
            split_iterator = splitter.split(features)
        for fold, (train, test) in enumerate(split_iterator):
            rows.append((repeat, fold, train, test))
    return rows


def _inner_splitter(model: ModelSpec, protocol: BacktestProtocol, seed: int) -> Any:
    if protocol.outer_strategy == "time_series":
        return TimeSeriesSplit(n_splits=protocol.inner_splits)
    if model.task == "binary_classification":
        return StratifiedKFold(
            n_splits=protocol.inner_splits,
            shuffle=True,
            random_state=seed,
        )
    return KFold(n_splits=protocol.inner_splits, shuffle=True, random_state=seed)


def _fit_one(
    model: ModelSpec,
    protocol: BacktestProtocol,
    features: pd.DataFrame,
    labels: np.ndarray,
    seed: int,
) -> tuple[Any, dict[str, Any]]:
    estimator = clone(model.estimator)
    if not model.search_space:
        estimator.fit(features, labels)
        return estimator, {}
    search = GridSearchCV(
        estimator,
        {key: list(values) for key, values in model.search_space.items()},
        scoring=model.scoring,
        cv=_inner_splitter(model, protocol, seed),
        n_jobs=1,
        refit=True,
    )
    search.fit(features, labels)
    return search.best_estimator_, dict(search.best_params_)


def _score_estimator(model: ModelSpec, estimator: Any, features: pd.DataFrame) -> np.ndarray:
    if model.task == "regression":
        return np.asarray(estimator.predict(features), dtype=float)
    if not hasattr(estimator, "predict_proba"):
        raise ValueError("binary classification estimators must implement predict_proba")
    probabilities = np.asarray(estimator.predict_proba(features), dtype=float)
    classes = list(estimator.classes_)
    if model.positive_label not in classes:
        raise ValueError(f"positive label {model.positive_label!r} was not fitted")
    return probabilities[:, classes.index(model.positive_label)]


def _metric_values(model: ModelSpec, labels: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    if model.task == "regression":
        residual = np.asarray(labels, dtype=float) - scores
        return {
            "rmse": float(np.sqrt(np.mean(residual**2))),
            "mae": float(mean_absolute_error(labels, scores)),
            "r2": float(r2_score(labels, scores)),
        }
    binary = (labels == model.positive_label).astype(int)
    clipped = np.clip(scores, 1e-8, 1.0 - 1e-8)
    predicted = (clipped >= 0.5).astype(int)
    return {
        "roc_auc": float(roc_auc_score(binary, clipped)),
        "pr_auc": float(average_precision_score(binary, clipped)),
        "brier": float(brier_score_loss(binary, clipped)),
        "log_loss": float(log_loss(binary, clipped, labels=[0, 1])),
        "balanced_accuracy": float(balanced_accuracy_score(binary, predicted)),
    }


def _bootstrap_intervals(
    model: ModelSpec,
    labels: np.ndarray,
    scores: np.ndarray,
    protocol: BacktestProtocol,
) -> dict[str, tuple[float, float]]:
    if protocol.bootstrap_samples == 0:
        return {}
    rng = np.random.default_rng(protocol.random_state + 70_000)
    values: dict[str, list[float]] = {}
    for _iteration in range(protocol.bootstrap_samples):
        if model.task == "binary_classification":
            pieces: list[np.ndarray] = []
            binary = (labels == model.positive_label).astype(int)
            for label in np.unique(binary):
                positions = np.flatnonzero(binary == label)
                pieces.append(rng.choice(positions, size=len(positions), replace=True))
            sample = np.concatenate(pieces)
            rng.shuffle(sample)
        else:
            sample = rng.choice(np.arange(len(labels)), size=len(labels), replace=True)
        sampled_metrics = _metric_values(model, labels[sample], scores[sample])
        for name, value in sampled_metrics.items():
            if math.isfinite(value):
                values.setdefault(name, []).append(value)
    alpha = (1.0 - protocol.confidence_level) / 2.0
    return {
        name: (
            float(np.quantile(metric_values, alpha)),
            float(np.quantile(metric_values, 1.0 - alpha)),
        )
        for name, metric_values in values.items()
        if metric_values
    }


def backtest(
    dataset: PredictionDataset,
    model: ModelSpec,
    protocol: BacktestProtocol,
) -> OOFResult:
    """Run nested out-of-fold validation under an explicit split protocol."""

    _validate_tuning_contract(model, protocol)
    features = _to_frame(dataset.features, "features")
    labels = _labels(dataset.labels, len(features))
    availability_checked = _validate_temporal_metadata(dataset, features, protocol)
    repeat_count = 1 if protocol.outer_strategy == "time_series" else protocol.repeats
    predictions = np.full((repeat_count, len(features)), np.nan, dtype=float)
    fold_records: list[FoldRecord] = []
    for repeat, fold, train, test in _outer_splits(features, labels, protocol):
        if protocol.outer_strategy == "time_series" and int(np.max(train)) >= int(np.min(test)):
            raise TemporalLeakageError("outer time-series split is not chronological")
        estimator, best_params = _fit_one(
            model,
            protocol,
            features.iloc[train],
            labels[train],
            protocol.random_state + repeat * 1_000 + fold,
        )
        predictions[repeat, test] = _score_estimator(model, estimator, features.iloc[test])
        fold_records.append(
            FoldRecord(
                repeat=repeat,
                fold=fold,
                train_size=len(train),
                test_size=len(test),
                train_index_min=int(np.min(train)),
                train_index_max=int(np.max(train)),
                test_index_min=int(np.min(test)),
                test_index_max=int(np.max(test)),
                best_params=best_params,
            )
        )
    count = np.sum(np.isfinite(predictions), axis=0)
    consensus = np.divide(
        np.nansum(predictions, axis=0),
        count,
        out=np.full(len(features), np.nan, dtype=float),
        where=count > 0,
    )
    evaluated = np.isfinite(consensus)
    if not np.any(evaluated):
        raise ValueError("backtest did not produce any out-of-fold predictions")
    scoped_labels = labels[evaluated]
    scoped_scores = consensus[evaluated]
    metric_scope = (
        "chronological outer-test observations only"
        if protocol.outer_strategy == "time_series"
        else "repeated outer validation folds only"
    )
    return OOFResult(
        predictions=consensus,
        repeat_predictions=predictions,
        evaluated_mask=evaluated,
        metrics=_metric_values(model, scoped_labels, scoped_scores),
        metric_intervals=_bootstrap_intervals(
            model, scoped_labels, scoped_scores, protocol
        ),
        fold_records=tuple(fold_records),
        metric_scope=metric_scope,
        outer_strategy=protocol.outer_strategy,
        tuning_scope=protocol.tuning_scope,
        temporal_order_preserved=(protocol.outer_strategy == "time_series"),
        feature_availability_checked=availability_checked,
        probability_calibrated=False,
        score_semantics=(
            "uncalibrated_score_not_validated_as_probability"
            if model.task == "binary_classification"
            else "point_prediction"
        ),
    )


def _merge_limitations(
    supplied: Sequence[KnownLimitation], additions: Sequence[KnownLimitation]
) -> tuple[KnownLimitation, ...]:
    by_code: dict[str, KnownLimitation] = {}
    for limitation in (*supplied, *additions):
        by_code.setdefault(limitation.code, limitation)
    return tuple(by_code.values())


def fit_predict(dataset: PredictionDataset, config: PredictConfig) -> PredictResult:
    """Backtest, fit, and predict while preserving evidence limitations."""

    features = _to_frame(dataset.features, "features")
    labels = _labels(dataset.labels, len(features))
    oof = backtest(dataset, config.model, config.protocol)
    final_model, selected = _fit_one(
        config.model,
        config.protocol,
        features,
        labels,
        config.protocol.random_state + 900_000,
    )
    application = (
        features
        if dataset.prediction_features is None
        else _to_frame(dataset.prediction_features, "prediction_features")
    )
    if list(application.columns) != list(features.columns):
        raise ValueError("prediction_features must use the same columns and order as features")
    scores = _score_estimator(config.model, final_model, application)
    if config.model.task == "binary_classification":
        negative_labels = [value for value in np.unique(labels) if value != config.model.positive_label]
        negative_label = negative_labels[0] if negative_labels else 0
        predictions = np.where(scores >= 0.5, config.model.positive_label, negative_label)
    else:
        predictions = scores.copy()

    additions: list[KnownLimitation] = []
    if config.label_availability == "proxy":
        additions.append(
            KnownLimitation(
                code="PROXY_LABEL_NO_CAUSAL_CLAIM",
                message="The fitted target is a proxy label and cannot identify a causal effect.",
                scope=config.metric_scope,
            )
        )
    elif config.label_availability == "missing":
        additions.append(
            KnownLimitation(
                code="APPLICATION_LABELS_MISSING",
                message=(
                    "Application-cohort outcomes are unavailable, so source-sample "
                    "metrics do not establish target-cohort performance."
                ),
                scope=config.metric_scope,
            )
        )
    if not config.causal_design:
        additions.append(
            KnownLimitation(
                code="PREDICTION_NOT_CAUSAL",
                message="Predictive association alone does not identify an intervention effect.",
                scope=config.metric_scope,
            )
        )
    if config.model.task == "binary_classification":
        additions.append(
            KnownLimitation(
                code="UNCALIBRATED_SCORE_NOT_PROBABILITY",
                message=(
                    "The returned score has not been validated as a calibrated probability; "
                    "call calibrate before using probability language."
                ),
                scope=config.metric_scope,
            )
        )
    causal_allowed = bool(
        config.causal_design and config.label_availability == "real"
    )
    return PredictResult(
        scores=scores,
        predictions=np.asarray(predictions),
        metrics=oof.metrics,
        metric_intervals=oof.metric_intervals,
        metric_scope=config.metric_scope,
        label_availability=config.label_availability,
        causal_claim_allowed=causal_allowed,
        known_limitations=_merge_limitations(config.known_limitations, additions),
        probability_calibrated=False,
        score_semantics=oof.score_semantics,
        oof=oof,
        fitted_model=final_model,
        selected_parameters=selected,
    )


def _curve_points(labels: np.ndarray, scores: np.ndarray) -> list[dict[str, float]]:
    observed, predicted = calibration_curve(labels, scores, n_bins=10, strategy="quantile")
    return [
        {"mean_predicted": float(x_value), "observed_frequency": float(y_value)}
        for x_value, y_value in zip(predicted, observed)
    ]


def calibrate(scores: Any, labels: Any) -> Calibration:
    """Evaluate Platt calibration with cross-fitted calibrated probabilities."""

    raw = np.asarray(scores, dtype=float)
    truth = np.asarray(labels)
    if raw.ndim != 1 or truth.ndim != 1 or raw.size != truth.size:
        raise ValueError("scores and labels must be matching one-dimensional arrays")
    if raw.size < 4 or not np.all(np.isfinite(raw)):
        raise ValueError("calibration requires at least four finite scores")
    if np.any((raw < 0.0) | (raw > 1.0)):
        raise ValueError("Brier calibration comparison requires scores in [0, 1]")
    classes = np.unique(truth)
    if classes.size != 2:
        raise ValueError("calibrate requires exactly two label classes")
    binary = (truth == classes[-1]).astype(int)
    smallest_class = int(np.min(np.bincount(binary)))
    folds = min(5, smallest_class)
    if folds < 2:
        raise ValueError("calibration requires at least two observations in each class")
    splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=73_021)
    calibrated = np.full(raw.size, np.nan, dtype=float)
    for train, test in splitter.split(raw[:, None], binary):
        fold_model = LogisticRegression(solver="lbfgs")
        fold_model.fit(raw[train, None], binary[train])
        calibrated[test] = fold_model.predict_proba(raw[test, None])[:, 1]
    final_model = LogisticRegression(solver="lbfgs")
    final_model.fit(raw[:, None], binary)
    clipped = np.clip(raw, 1e-8, 1.0 - 1e-8)
    return Calibration(
        raw_scores=raw,
        calibrated_scores=calibrated,
        curve_before=tuple(_curve_points(binary, clipped)),
        curve_after=tuple(_curve_points(binary, calibrated)),
        brier_before=float(brier_score_loss(binary, clipped)),
        brier_after=float(brier_score_loss(binary, calibrated)),
        method="cross_fitted_platt",
        cross_fitted=True,
        raw_score_probability_claim_allowed=False,
        calibrated_probability_claim_allowed=True,
        interpretation=(
            "Raw scores are not treated as probabilities. Calibrated values are "
            "cross-fitted probability estimates for this registered sample scope."
        ),
        fitted_calibrator=final_model,
    )


def _rank_positions(values: np.ndarray) -> np.ndarray:
    order = np.argsort(-values, kind="stable")
    positions = np.empty(values.size, dtype=int)
    positions[order] = np.arange(1, values.size + 1)
    return positions


def rank_stability(scores: Any, perturbation: Any) -> StabilityReport:
    """Compare a base ranking with predeclared perturbed score vectors."""

    baseline = np.asarray(scores, dtype=float)
    if baseline.ndim != 1 or baseline.size < 2 or not np.all(np.isfinite(baseline)):
        raise ValueError("scores must contain at least two finite values")
    if isinstance(perturbation, RankPerturbation):
        alternatives = np.asarray(perturbation.scores, dtype=float)
        labels = tuple(perturbation.labels)
    else:
        alternatives = np.asarray(perturbation, dtype=float)
        labels = ()
    if alternatives.ndim == 1:
        alternatives = alternatives[None, :]
    if alternatives.ndim != 2 or alternatives.shape[1] != baseline.size:
        raise ValueError("perturbation must have one column per baseline score")
    if not np.all(np.isfinite(alternatives)):
        raise ValueError("perturbed scores must be finite")
    if labels and len(labels) != alternatives.shape[0]:
        raise ValueError("perturbation labels must match the number of score rows")
    if not labels:
        labels = tuple(f"perturbation_{index}" for index in range(len(alternatives)))

    base_positions = _rank_positions(baseline)
    correlations: list[float] = []
    flip_counts: list[int] = []
    displacements: list[int] = []
    unique_flips: set[tuple[int, int]] = set()
    for row in alternatives:
        correlations.append(float(spearmanr(baseline, row).statistic))
        row_flips = 0
        for left in range(baseline.size):
            for right in range(left + 1, baseline.size):
                before = baseline[left] - baseline[right]
                after = row[left] - row[right]
                if before * after < 0.0:
                    row_flips += 1
                    unique_flips.add((left, right))
        flip_counts.append(row_flips)
        positions = _rank_positions(row)
        displacements.append(int(np.max(np.abs(positions - base_positions))))
    return StabilityReport(
        spearman_values=tuple(correlations),
        flip_pair_counts=tuple(flip_counts),
        flipped_pairs=tuple(sorted(unique_flips)),
        max_displacements=tuple(displacements),
        median_spearman=float(np.nanmedian(correlations)),
        minimum_spearman=float(np.nanmin(correlations)),
        total_flip_pair_count=int(sum(flip_counts)),
        unique_flip_pair_count=len(unique_flips),
        max_rank_displacement=max(displacements),
        any_rank_reversal=bool(any(flip_counts)),
        perturbation_labels=labels,
    )


def _numeric_frame(values: Any, name: str) -> pd.DataFrame:
    return _to_frame(values, name).apply(pd.to_numeric, errors="coerce")


def _psi(left: pd.Series, right: pd.Series, bins: int = 10) -> float | None:
    left_values = left.dropna().to_numpy(dtype=float)
    right_values = right.dropna().to_numpy(dtype=float)
    if left_values.size < 5 or right_values.size < 5:
        return None
    edges = np.unique(np.quantile(left_values, np.linspace(0.0, 1.0, bins + 1)))
    if edges.size < 3:
        return 0.0
    edges[0] = -np.inf
    edges[-1] = np.inf
    left_share = np.histogram(left_values, bins=edges)[0] / left_values.size
    right_share = np.histogram(right_values, bins=edges)[0] / right_values.size
    left_share = np.clip(left_share, 1e-6, None)
    right_share = np.clip(right_share, 1e-6, None)
    return float(np.sum((right_share - left_share) * np.log(right_share / left_share)))


def drift_diagnostic(train: Any, apply: Any) -> DriftReport:
    """Compute feature-wise PSI as descriptive shift evidence only."""

    training = _numeric_frame(train, "train")
    application = _numeric_frame(apply, "apply")
    if list(training.columns) != list(application.columns):
        raise ValueError("train and apply must have identical feature columns and order")
    rows: list[DriftFeature] = []
    for column in training.columns:
        left = training[column]
        right = application[column]
        rows.append(
            DriftFeature(
                name=str(column),
                psi=_psi(left, right),
                train_count=int(left.notna().sum()),
                apply_count=int(right.notna().sum()),
                train_missing_rate=float(left.isna().mean()),
                apply_missing_rate=float(right.isna().mean()),
            )
        )
    finite = [row.psi for row in rows if row.psi is not None and math.isfinite(row.psi)]
    limitation = KnownLimitation(
        code="PSI_DESCRIPTIVE_NOT_CAUSAL",
        message=(
            "PSI describes marginal distribution difference; it neither identifies "
            "a cause nor predicts an intervention effect."
        ),
        scope="registered train-versus-application feature distributions",
    )
    return DriftReport(
        features=tuple(rows),
        median_psi=float(np.median(finite)) if finite else None,
        maximum_psi=float(np.max(finite)) if finite else None,
        features_above_0_10=sum(value > 0.10 for value in finite),
        features_above_0_25=sum(value > 0.25 for value in finite),
        metric_scope="registered train-versus-application feature distributions",
        evidence_type="descriptive_distribution_shift_proxy",
        causal_claim_allowed=False,
        known_limitations=(limitation,),
    )
