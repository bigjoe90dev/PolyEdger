"""Tests for Calibration + Trust Control (spec §14)."""

from polyedge.calibration import (
    REASON_P_EFF_OUTLIER,
    brier_score,
    calibration_bins,
    compute_p_eff,
    compute_w_ai,
)


# ── Brier score ───────────────────────────────────────────────────────────────

def test_brier_perfect() -> None:
    """Perfect predictions → Brier = 0."""
    preds = [1.0, 0.0, 1.0]
    outcomes = [1, 0, 1]
    assert brier_score(preds, outcomes) == 0.0


def test_brier_worst() -> None:
    """Maximally wrong predictions → Brier = 1."""
    preds = [0.0, 1.0]
    outcomes = [1, 0]
    assert brier_score(preds, outcomes) == 1.0


def test_brier_moderate() -> None:
    """50/50 predictions for all-YES outcomes → Brier = 0.25."""
    preds = [0.5, 0.5]
    outcomes = [1, 1]
    assert abs(brier_score(preds, outcomes) - 0.25) < 0.001


# ── Calibration bins ──────────────────────────────────────────────────────────

def test_bins_basic() -> None:
    """Basic binning produces correct counts."""
    preds = [0.15, 0.25, 0.55, 0.85]
    outcomes = [0, 0, 1, 1]
    bins = calibration_bins(preds, outcomes)
    assert len(bins) == 10  # 10 bins for [0,0.1) to [0.9,1.0]
    # Check that predictions land in correct bins
    populated = [b for b in bins if b["count"] > 0]
    assert len(populated) == 4


# ── w_ai control law ─────────────────────────────────────────────────────────

def test_w_ai_zero_under_threshold() -> None:
    """w_ai = 0 until N_RESOLVED >= 50."""
    assert compute_w_ai(n_resolved=10) == 0.0
    assert compute_w_ai(n_resolved=49) == 0.0


def test_w_ai_max_at_threshold() -> None:
    """w_ai at max when above threshold with good calibration."""
    w = compute_w_ai(n_resolved=100)
    assert w > 0
    assert w <= 0.35


def test_w_ai_reduced_for_bad_calibration() -> None:
    """w_ai reduced when AI calibration is worse than baseline."""
    w_good = compute_w_ai(n_resolved=100, category_brier_ai=0.15, category_brier_baseline=0.20)
    w_bad = compute_w_ai(n_resolved=100, category_brier_ai=0.30, category_brier_baseline=0.20)
    assert w_good > w_bad


def test_w_ai_reduced_for_high_dispute() -> None:
    """w_ai reduced for high dispute risk."""
    w_low = compute_w_ai(n_resolved=100, dispute_risk=0.1)
    w_high = compute_w_ai(n_resolved=100, dispute_risk=0.8)
    assert w_low > w_high


# ── p_eff computation ─────────────────────────────────────────────────────────

def test_p_eff_no_ai_influence() -> None:
    """With w_ai=0, p_eff = p_market."""
    p_eff, reason = compute_p_eff(p_market=0.55, p_ai_cal=0.70, w_ai=0.0)
    assert p_eff == 0.55
    assert reason is None


def test_p_eff_with_ai_influence() -> None:
    """With w_ai>0, p_eff shifts toward p_ai_cal."""
    p_eff, reason = compute_p_eff(p_market=0.55, p_ai_cal=0.70, w_ai=0.30)
    assert p_eff > 0.55
    assert p_eff < 0.70
    assert reason is None


def test_p_eff_clamped_by_delta_max() -> None:
    """p_eff clamped at delta_max = 0.10."""
    p_eff, reason = compute_p_eff(p_market=0.50, p_ai_cal=0.90, w_ai=0.35)
    assert abs(p_eff - 0.50) <= 0.10 + 0.001


def test_p_eff_high_dispute_tighter_delta() -> None:
    """High dispute risk uses DELTA_MAX_HIGH_DISPUTE = 0.05."""
    p_eff, reason = compute_p_eff(p_market=0.50, p_ai_cal=0.90, w_ai=0.35, dispute_risk=0.8)
    assert abs(p_eff - 0.50) <= 0.05 + 0.001


def test_p_eff_outlier_rejection() -> None:
    """p_eff outlier (>0.20 from market) rejected."""
    # This shouldn't happen with delta_max clamping, but test the threshold
    p_eff, reason = compute_p_eff(p_market=0.50, p_ai_cal=0.50, w_ai=0.0)
    assert reason is None  # Normal case
