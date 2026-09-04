#!/usr/bin/env python3
"""Execute E04's independent rank-reversal positive/negative benchmark."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from _forward_scenario_support import (
    load_scenario,
    parse_target_args,
    prepare_target,
    print_summary,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = REPO_ROOT / "cumcm-modeling" / "assets" / "predict-toolkit"
sys.path.insert(0, str(TOOLKIT_ROOT))

from predict_toolkit import RankPerturbation, rank_stability  # noqa: E402


SCENARIO_PATH = Path(__file__).with_name("e04_ranking_stability_scenario.json")
SCENARIO_ID = "E04-ranking-stability"


def weighted_scores(matrix: np.ndarray, weights: np.ndarray) -> np.ndarray:
    if matrix.ndim != 2 or weights.ndim != 1 or matrix.shape[1] != weights.size:
        raise ValueError("evaluation matrix and weight vector dimensions do not match")
    if np.any(weights < 0.0) or not np.isclose(np.sum(weights), 1.0):
        raise ValueError("predeclared weights must be non-negative and sum to one")
    return matrix @ weights


def perturbation_scores(matrix: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return np.vstack([weighted_scores(matrix, row) for row in weights])


def main() -> int:
    args = parse_target_args("Run E04's non-overwriting ranking-stability benchmark.")
    scenario = load_scenario(SCENARIO_PATH, SCENARIO_ID)
    target = prepare_target(args.target, "cumcm-e04-ranking-stability")
    base_weights = np.asarray(scenario["base_weights"], dtype=float)
    perturbed_weights = np.asarray(scenario["perturbed_weights"], dtype=float)

    flip_matrix = np.asarray(scenario["flip_matrix"], dtype=float)
    flip_report = rank_stability(
        weighted_scores(flip_matrix, base_weights),
        RankPerturbation(
            perturbation_scores(flip_matrix, perturbed_weights),
            labels=("predeclared_weight_shift",),
        ),
    )
    expected_flips = int(scenario["expected_flip_pair_count"])
    expected_displacement = int(scenario["expected_max_rank_displacement"])
    if flip_report.total_flip_pair_count != expected_flips:
        raise AssertionError(
            f"expected {expected_flips} flipped pair, got {flip_report.total_flip_pair_count}"
        )
    if flip_report.max_rank_displacement != expected_displacement:
        raise AssertionError(
            f"expected displacement {expected_displacement}, got {flip_report.max_rank_displacement}"
        )
    if flip_report.minimum_spearman < float(scenario["minimum_expected_spearman"]):
        raise AssertionError(
            "the synthetic local flip should coexist with a high global Spearman value"
        )

    stable_matrix = np.asarray(scenario["stable_matrix"], dtype=float)
    stable_report = rank_stability(
        weighted_scores(stable_matrix, base_weights),
        RankPerturbation(
            perturbation_scores(stable_matrix, perturbed_weights),
            labels=("same_predeclared_weight_shift",),
        ),
    )
    if stable_report.any_rank_reversal:
        raise AssertionError("strictly separated stable ranking produced a false reversal")
    if stable_report.total_flip_pair_count != 0 or stable_report.max_rank_displacement != 0:
        raise AssertionError("stable ranking reported a false flip or displacement")

    payload = {
        "status": "PASS",
        "scenario": SCENARIO_ID,
        "truth_source": "synthetic normalized matrix with analytically known order",
        "indicator_directions": scenario["indicator_directions"],
        "normalization": scenario["normalization"],
        "predeclared_weights": {
            "base": scenario["base_weights"],
            "perturbations": scenario["perturbed_weights"],
        },
        "negative_known_flip": flip_report.as_dict(),
        "positive_stable_ranking": stable_report.as_dict(),
        "preserved_bundle": str(target),
    }
    result_path = target / "e04_result.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
