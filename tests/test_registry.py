"""Tests for market registry: normalisation, binary detection, category filtering, hashing."""

from polyedge.registry import (
    classify_category,
    compute_critical_field_hash,
    is_binary_eligible,
    normalise_label,
    parse_gamma_market,
)


# ── Label normalisation ──────────────────────────────────────────────────────

def test_normalise_label_basic() -> None:
    assert normalise_label("Yes") == "YES"
    assert normalise_label("no") == "NO"
    assert normalise_label("  yes  ") == "YES"


def test_normalise_label_unicode() -> None:
    """NFKC normalisation handles compatibility characters."""
    # ﬁ (U+FB01) decomposes to fi under NFKC
    assert "FI" in normalise_label("ﬁnal")


def test_normalise_label_whitespace_collapse() -> None:
    assert normalise_label("  y  e  s  ") == "Y E S"


def test_normalise_label_fullwidth() -> None:
    """Full-width characters normalise under NFKC."""
    # Ｙ Ｅ Ｓ -> YES
    assert normalise_label("\uff39\uff25\uff33") == "YES"


# ── Binary eligibility ───────────────────────────────────────────────────────

def test_binary_eligible_yes_no() -> None:
    outcomes = [{"value": "Yes"}, {"value": "No"}]
    eligible, reason = is_binary_eligible(outcomes)
    assert eligible is True
    assert reason is None


def test_binary_eligible_case_insensitive() -> None:
    outcomes = [{"value": "YES"}, {"value": "no"}]
    eligible, _ = is_binary_eligible(outcomes)
    assert eligible is True


def test_binary_not_eligible_three_outcomes() -> None:
    outcomes = [{"value": "A"}, {"value": "B"}, {"value": "C"}]
    eligible, reason = is_binary_eligible(outcomes)
    assert eligible is False
    assert "3 outcomes" in reason


def test_binary_not_eligible_wrong_labels() -> None:
    outcomes = [{"value": "True"}, {"value": "False"}]
    eligible, reason = is_binary_eligible(outcomes)
    assert eligible is False
    assert "NON_BINARY" in reason


def test_binary_not_eligible_one_outcome() -> None:
    outcomes = [{"value": "Yes"}]
    eligible, reason = is_binary_eligible(outcomes)
    assert eligible is False


# ── Category classification ──────────────────────────────────────────────────

def test_category_allowed_geopolitics() -> None:
    allowed, reason = classify_category("geopolitics")
    assert allowed is True


def test_category_allowed_tech_ai() -> None:
    allowed, _ = classify_category("tech/AI")
    assert allowed is True


def test_category_allowed_economics() -> None:
    allowed, _ = classify_category("economics")
    assert allowed is True


def test_category_denied_sports() -> None:
    allowed, reason = classify_category("sports")
    assert allowed is False
    assert "denylist" in reason


def test_category_denied_unknown() -> None:
    allowed, reason = classify_category("entertainment")
    assert allowed is False
    assert "not in allowlist" in reason


# ── Critical field hash ──────────────────────────────────────────────────────

def test_critical_field_hash_deterministic() -> None:
    h1 = compute_critical_field_hash("t", "d", "r", "e", "y", "n", "c")
    h2 = compute_critical_field_hash("t", "d", "r", "e", "y", "n", "c")
    assert h1 == h2
    assert len(h1) == 64


def test_critical_field_hash_differs_on_change() -> None:
    h1 = compute_critical_field_hash("title1", "d", "r", "e", "y", "n", "c")
    h2 = compute_critical_field_hash("title2", "d", "r", "e", "y", "n", "c")
    assert h1 != h2


# ── parse_gamma_market ────────────────────────────────────────────────────────

def test_parse_valid_market() -> None:
    raw = {
        "id": "mkt-001",
        "condition_id": "cond-001",
        "question": "Will X happen?",
        "description": "Resolves YES if X.",
        "category": "geopolitics",
        "tags": ["geo"],
        "resolutionSource": "Official",
        "endDate": "2026-12-31T00:00:00Z",
        "outcomes": [
            {"value": "Yes", "asset_id": "tok-y"},
            {"value": "No", "asset_id": "tok-n"},
        ],
    }
    result = parse_gamma_market(raw)
    assert result is not None
    assert result["market_id"] == "mkt-001"
    assert result["is_binary_eligible"] is True
    assert result["yes_token_id"] == "tok-y"
    assert result["no_token_id"] == "tok-n"


def test_parse_sports_market_not_eligible() -> None:
    raw = {
        "id": "mkt-sports",
        "condition_id": "cond-s",
        "question": "Who wins?",
        "category": "sports",
        "outcomes": [
            {"value": "Yes", "asset_id": "y"},
            {"value": "No", "asset_id": "n"},
        ],
    }
    result = parse_gamma_market(raw)
    assert result is not None
    assert result["is_binary_eligible"] is False


def test_parse_missing_id() -> None:
    raw = {"outcomes": []}
    assert parse_gamma_market(raw) is None


def test_parse_no_outcomes() -> None:
    raw = {"id": "mkt-001", "condition_id": "c"}
    assert parse_gamma_market(raw) is None
