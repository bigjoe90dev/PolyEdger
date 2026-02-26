"""Tests for AI Swarm interface (spec §12)."""

from polyedge.ai_interface import (
    build_analysis_prompt,
    check_quorum,
    compute_prompt_hash,
    compute_weighted_disagreement,
    validate_ai_response,
    DISAGREE_THRESHOLD,
)


# ── JSON schema validation ────────────────────────────────────────────────────

def test_validate_valid_response() -> None:
    """Complete valid response passes."""
    resp = {
        "market_id": "mkt-001",
        "prob_yes_raw": 0.55,
        "confidence_raw": 0.7,
        "resolution_risk": 0.1,
        "dispute_risk": 0.05,
        "resolution_summary": "Strong evidence for YES",
        "evidence_summary": "Multiple Tier1 sources agree",
        "uncertainty_reason": "Limited historical data",
        "key_drivers": ["GDP growth", "Fed policy"],
        "disqualifiers": [],
        "recommended_side": "YES",
        "notes": "High confidence",
    }
    valid, errors = validate_ai_response(resp)
    assert valid is True
    assert errors == []


def test_validate_missing_fields() -> None:
    """Missing required fields detected."""
    resp = {"market_id": "mkt-001", "prob_yes_raw": 0.5}
    valid, errors = validate_ai_response(resp)
    assert valid is False
    assert any("Missing" in e for e in errors)


def test_validate_out_of_range() -> None:
    """Out-of-range numeric values detected."""
    resp = {
        "market_id": "mkt-001",
        "prob_yes_raw": 1.5,  # > 1
        "confidence_raw": -0.1,  # < 0
        "resolution_risk": 0.5,
        "dispute_risk": 0.1,
        "resolution_summary": "test",
        "evidence_summary": "test",
        "uncertainty_reason": "test",
        "key_drivers": [],
        "disqualifiers": [],
        "recommended_side": "YES",
        "notes": "test",
    }
    valid, errors = validate_ai_response(resp)
    assert valid is False
    assert len(errors) == 2


def test_validate_invalid_side() -> None:
    """Invalid recommended_side detected."""
    resp = {
        "market_id": "mkt-001",
        "prob_yes_raw": 0.5,
        "confidence_raw": 0.5,
        "resolution_risk": 0.1,
        "dispute_risk": 0.1,
        "resolution_summary": "test",
        "evidence_summary": "test",
        "uncertainty_reason": "test",
        "key_drivers": [],
        "disqualifiers": [],
        "recommended_side": "MAYBE",
        "notes": "test",
    }
    valid, errors = validate_ai_response(resp)
    assert valid is False
    assert any("recommended_side" in e for e in errors)


def test_validate_wrong_types() -> None:
    """Wrong types for fields detected."""
    resp = {
        "market_id": "mkt-001",
        "prob_yes_raw": "not a number",
        "confidence_raw": 0.5,
        "resolution_risk": 0.1,
        "dispute_risk": 0.1,
        "resolution_summary": 123,  # Should be string
        "evidence_summary": "ok",
        "uncertainty_reason": "ok",
        "key_drivers": "not an array",  # Should be list
        "disqualifiers": [],
        "recommended_side": "YES",
        "notes": "ok",
    }
    valid, errors = validate_ai_response(resp)
    assert valid is False


# ── Quorum ────────────────────────────────────────────────────────────────────

def test_quorum_met() -> None:
    """Quorum met with 4 models, weight=6, low disagreement."""
    results = [
        {"parse_ok": True, "weight": 2, "prob_yes_raw": 0.55},
        {"parse_ok": True, "weight": 2, "prob_yes_raw": 0.57},
        {"parse_ok": True, "weight": 1, "prob_yes_raw": 0.53},
        {"parse_ok": True, "weight": 1, "prob_yes_raw": 0.56},
    ]
    met, reason = check_quorum(results)
    assert met is True


def test_quorum_failed_insufficient_models() -> None:
    """Quorum fails with only 2 valid models."""
    results = [
        {"parse_ok": True, "weight": 2, "prob_yes_raw": 0.55},
        {"parse_ok": True, "weight": 2, "prob_yes_raw": 0.57},
        {"parse_ok": False, "weight": 1, "error": "timeout"},
        {"parse_ok": False, "weight": 1, "error": "timeout"},
    ]
    met, reason = check_quorum(results)
    assert met is False
    assert "QUORUM" in reason


def test_quorum_failed_insufficient_weight() -> None:
    """Quorum fails with enough models but not enough weight."""
    results = [
        {"parse_ok": True, "weight": 1, "prob_yes_raw": 0.55},
        {"parse_ok": True, "weight": 1, "prob_yes_raw": 0.57},
        {"parse_ok": True, "weight": 1, "prob_yes_raw": 0.53},
        {"parse_ok": False, "weight": 2, "error": "timeout"},
    ]
    met, reason = check_quorum(results)
    assert met is False
    assert "weight" in reason


def test_quorum_failed_high_disagreement() -> None:
    """Quorum fails when disagreement exceeds threshold."""
    results = [
        {"parse_ok": True, "weight": 2, "prob_yes_raw": 0.20},
        {"parse_ok": True, "weight": 2, "prob_yes_raw": 0.80},
        {"parse_ok": True, "weight": 1, "prob_yes_raw": 0.50},
        {"parse_ok": True, "weight": 1, "prob_yes_raw": 0.70},
    ]
    met, reason = check_quorum(results)
    assert met is False
    assert "DISAGREEMENT" in reason


# ── Disagreement calculation ──────────────────────────────────────────────────

def test_disagreement_zero_same_values() -> None:
    """Identical probabilities → zero disagreement."""
    results = [
        {"weight": 2, "prob_yes_raw": 0.55},
        {"weight": 2, "prob_yes_raw": 0.55},
    ]
    assert compute_weighted_disagreement(results) == 0.0


def test_disagreement_high_divergent() -> None:
    """Very different probabilities → high disagreement."""
    results = [
        {"weight": 1, "prob_yes_raw": 0.10},
        {"weight": 1, "prob_yes_raw": 0.90},
    ]
    d = compute_weighted_disagreement(results)
    assert d > DISAGREE_THRESHOLD


def test_disagreement_single_value() -> None:
    """Single value → zero disagreement."""
    results = [{"weight": 1, "prob_yes_raw": 0.5}]
    assert compute_weighted_disagreement(results) == 0.0


# ── Prompt hashing ────────────────────────────────────────────────────────────

def test_prompt_hash_deterministic() -> None:
    """Same prompt → same hash."""
    p = "Analyze this market"
    assert compute_prompt_hash(p) == compute_prompt_hash(p)


def test_prompt_hash_changes() -> None:
    """Different prompt → different hash."""
    assert compute_prompt_hash("prompt A") != compute_prompt_hash("prompt B")


# ── Prompt building ───────────────────────────────────────────────────────────

def test_build_prompt_includes_market() -> None:
    """Prompt includes market title and category."""
    market = {
        "title": "Will AI pass ARC-AGI?",
        "description": "Resolves YES if >95%",
        "category": "tech/AI",
        "resolution_source": "ARC Prize Foundation",
        "end_date_utc": "2026-12-31",
    }
    prompt = build_analysis_prompt(market)
    assert "ARC-AGI" in prompt
    assert "tech/AI" in prompt


def test_build_prompt_includes_evidence() -> None:
    """Prompt includes evidence bundle."""
    market = {"title": "Test", "description": "", "category": "", "resolution_source": "", "end_date_utc": ""}
    evidence = {
        "items": [
            {"title": "Fed cuts rates", "text": "The Federal Reserve...", "reliability_tier": 1, "source_id": "reuters"},
        ],
    }
    prompt = build_analysis_prompt(market, evidence_bundle=evidence)
    assert "Fed cuts rates" in prompt
    assert "Tier 1" in prompt
