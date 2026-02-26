"""Calibration + Trust Control — Brier score + w_ai (spec §14).

Implements:
- Brier score computation and calibration bins
- AI influence control law: w_ai = 0 until N_RESOLVED >= 50
- p_eff = p_market + w_ai × (p_ai_cal - p_market) with hard bounds
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from polyedge.constants import (
    DELTA_MAX_DEFAULT,
    DELTA_MAX_HIGH_DISPUTE,
    N_RESOLVED_MIN,
    P_EFF_OUTLIER_THRESHOLD,
    W_AI_MAX,
)

logger = logging.getLogger(__name__)

# Reason codes
REASON_P_EFF_OUTLIER = "P_EFF_OUTLIER"

# Calibration bin boundaries
DEFAULT_BINS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def brier_score(predictions: List[float], outcomes: List[int]) -> float:
    """Compute Brier score.

    Lower is better. Range [0, 1].
    predictions: list of probabilities [0,1]
    outcomes: list of binary outcomes (0 or 1)
    """
    if not predictions or len(predictions) != len(outcomes):
        return 1.0  # Worst possible

    n = len(predictions)
    return sum((p - o) ** 2 for p, o in zip(predictions, outcomes)) / n


def calibration_bins(
    predictions: List[float],
    outcomes: List[int],
    bin_edges: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    """Compute calibration bins.

    Returns list of bins with predicted_mean, observed_fraction, count.
    """
    edges = bin_edges or DEFAULT_BINS
    bins = []  # type: List[Dict[str, Any]]

    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]

        bin_preds = []
        bin_outcomes = []
        for p, o in zip(predictions, outcomes):
            if lo <= p < hi or (i == len(edges) - 2 and p == hi):
                bin_preds.append(p)
                bin_outcomes.append(o)

        if bin_preds:
            bins.append({
                "bin_lo": lo,
                "bin_hi": hi,
                "predicted_mean": sum(bin_preds) / len(bin_preds),
                "observed_fraction": sum(bin_outcomes) / len(bin_outcomes),
                "count": len(bin_preds),
            })
        else:
            bins.append({
                "bin_lo": lo,
                "bin_hi": hi,
                "predicted_mean": (lo + hi) / 2,
                "observed_fraction": None,
                "count": 0,
            })

    return bins


def compute_w_ai(
    n_resolved: int,
    category_brier_ai: Optional[float] = None,
    category_brier_baseline: Optional[float] = None,
    disagreement: float = 0.0,
    dispute_risk: float = 0.0,
    evidence_tier_mix: Optional[Dict[str, int]] = None,
) -> float:
    """Compute AI influence weight per spec §14.2.

    Returns w_ai in [0, W_AI_MAX].
    """
    # Hard gate: w_ai = 0 until enough resolved
    if n_resolved < N_RESOLVED_MIN:
        return 0.0

    w = W_AI_MAX  # Start at max and reduce

    # Reduce if AI calibration is worse than baseline
    if category_brier_ai is not None and category_brier_baseline is not None:
        if category_brier_ai > category_brier_baseline:
            # AI is worse → reduce proportionally
            ratio = category_brier_baseline / max(category_brier_ai, 0.001)
            w *= ratio

    # Reduce for high disagreement
    if disagreement > 0:
        w *= max(0.0, 1.0 - disagreement * 3)

    # Reduce for high dispute risk
    if dispute_risk > 0.5:
        w *= max(0.0, 1.0 - (dispute_risk - 0.5) * 2)

    # Reduce if evidence tier mix is weak
    if evidence_tier_mix:
        tier1_count = evidence_tier_mix.get("tier1", 0)
        if tier1_count == 0:
            w *= 0.5

    return max(0.0, min(w, W_AI_MAX))


def compute_p_eff(
    p_market: float,
    p_ai_cal: float,
    w_ai: float,
    dispute_risk: float = 0.0,
) -> Tuple[float, Optional[str]]:
    """Compute effective probability per spec §14.2.

    p_eff = p_market + w_ai × (p_ai_cal - p_market)

    Returns (p_eff, reason_code_if_rejected).
    """
    p_eff = p_market + w_ai * (p_ai_cal - p_market)

    # Hard bounds
    delta_max = DELTA_MAX_DEFAULT
    if dispute_risk >= 0.7:
        delta_max = DELTA_MAX_HIGH_DISPUTE

    # Clamp to delta_max
    delta = p_eff - p_market
    if abs(delta) > delta_max:
        if delta > 0:
            p_eff = p_market + delta_max
        else:
            p_eff = p_market - delta_max

    # Outlier check
    if abs(p_eff - p_market) > P_EFF_OUTLIER_THRESHOLD:
        return p_eff, REASON_P_EFF_OUTLIER

    # Clamp to [0, 1]
    p_eff = max(0.0, min(1.0, p_eff))

    return p_eff, None
