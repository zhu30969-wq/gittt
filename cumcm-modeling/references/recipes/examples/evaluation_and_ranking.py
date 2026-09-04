"""Runnable minimum example for evaluation and ranking stability."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


TOOLKIT_ROOT = Path(__file__).resolve().parents[3] / "assets" / "predict-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from predict_toolkit import RankPerturbation, drift_diagnostic, rank_stability  # noqa: E402


def weighted_scores(matrix: np.ndarray, weights: np.ndarray) -> np.ndarray:
    if not np.isclose(np.sum(weights), 1.0):
        raise ValueError("weights must sum to one")
    return matrix @ weights


def main() -> int:
    # All columns are preregistered benefit indicators already normalized to [0, 1].
    matrix = np.array(
        [
            [1.00, 0.20],
            [0.70, 0.70],
            [0.20, 1.00],
        ]
    )
    base_weights = np.array([0.60, 0.40])
    perturbed_weights = np.array([[0.80, 0.20], [0.55, 0.45]])
    base = weighted_scores(matrix, base_weights)
    alternatives = np.vstack(
        [weighted_scores(matrix, weights) for weights in perturbed_weights]
    )
    stability = rank_stability(
        base,
        RankPerturbation(alternatives, labels=("emphasize_criterion_1", "small_shift")),
    )

    train = pd.DataFrame(matrix, columns=["benefit_1", "benefit_2"])
    application = pd.DataFrame(
        [[0.95, 0.25], [0.68, 0.72], [0.18, 0.98], [0.50, 0.50], [0.45, 0.55]],
        columns=train.columns,
    )
    # Duplicate rows only to meet the descriptive PSI minimum sample size.
    drift = drift_diagnostic(pd.concat([train] * 3, ignore_index=True), application)
    if not stability.any_rank_reversal or stability.max_rank_displacement < 1:
        raise AssertionError("the preregistered weight perturbation should reverse a pair")
    if drift.causal_claim_allowed:
        raise AssertionError("PSI must remain descriptive rather than causal evidence")
    print(
        json.dumps(
            {
                "indicator_direction": ["benefit", "benefit"],
                "normalization": "already normalized to [0, 1] for the synthetic example",
                "base_weights": base_weights.tolist(),
                "perturbed_weights": perturbed_weights.tolist(),
                "base_scores": base.tolist(),
                "perturbed_scores": alternatives.tolist(),
                "stability": stability.as_dict(),
                "drift": drift.as_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
