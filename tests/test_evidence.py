"""Tests for Evidence Service (spec §10)."""

from datetime import datetime, timedelta, timezone

from polyedge.evidence import (
    EvidenceFetchRateLimiter,
    EvidenceItem,
    build_evidence_bundle,
    compute_bundle_hash,
    detect_conflict,
    is_evidence_ttl_valid,
    is_high_stakes,
    is_thesis_required,
    resolve_conflict,
)


def _make_item(
    source_id: str = "src-001",
    text: str = "Sample evidence text",
    tier: int = 1,
    age_seconds: int = 300,
) -> EvidenceItem:
    now = datetime.now(timezone.utc)
    return EvidenceItem(
        source_id=source_id,
        url="https://example.com/article",
        title="Test Article",
        text=text,
        published_at_utc=now - timedelta(seconds=age_seconds),
        reliability_tier=tier,
        parser_name="test_parser",
        parser_version="1.0",
    )


# ── Thesis detection ──────────────────────────────────────────────────────────

def test_thesis_required_mid_move_geopolitics() -> None:
    """Geopolitics + mid_move trigger requires thesis."""
    c = {"trigger_reasons": ["mid_move"]}
    m = {"category": "geopolitics", "resolution_source": ""}
    assert is_thesis_required(c, m) is True


def test_thesis_required_approaching_resolution() -> None:
    """Economics + approaching_resolution requires thesis."""
    c = {"trigger_reasons": ["approaching_resolution"]}
    m = {"category": "economics", "resolution_source": ""}
    assert is_thesis_required(c, m) is True


def test_thesis_not_required_spread_change_only() -> None:
    """Spread change alone doesn't require thesis."""
    c = {"trigger_reasons": ["spread_change"]}
    m = {"category": "geopolitics", "resolution_source": "Official results"}
    assert is_thesis_required(c, m) is False


def test_thesis_required_large_size() -> None:
    """Large order size (>=0.5% of wallet) requires thesis."""
    c = {"trigger_reasons": ["spread_change"], "intended_order_size_usd": 1.0}
    m = {"category": "other", "resolution_source": ""}
    assert is_thesis_required(c, m, wallet_usd=100.0) is True


def test_thesis_required_subjective_text() -> None:
    """Resolution text with subjective terms requires thesis."""
    c = {"trigger_reasons": ["spread_change"]}
    m = {"category": "other", "resolution_source": "This outcome is debatable and uncertain"}
    assert is_thesis_required(c, m) is True


# ── High stakes ───────────────────────────────────────────────────────────────

def test_high_stakes_large_size() -> None:
    c = {"intended_order_size_usd": 2.0}
    m = {"end_date_utc": None}
    assert is_high_stakes(c, m, wallet_usd=100.0) is True


def test_high_stakes_near_resolution() -> None:
    now = datetime.now(timezone.utc)
    c = {"intended_order_size_usd": 0}
    m = {"end_date_utc": (now + timedelta(hours=3)).isoformat()}
    assert is_high_stakes(c, m) is True


def test_high_stakes_dispute_risk() -> None:
    c = {"intended_order_size_usd": 0}
    m = {"end_date_utc": None}
    assert is_high_stakes(c, m, dispute_risk=0.8) is True


def test_not_high_stakes() -> None:
    now = datetime.now(timezone.utc)
    c = {"intended_order_size_usd": 0}
    m = {"end_date_utc": (now + timedelta(days=7)).isoformat()}
    assert is_high_stakes(c, m, dispute_risk=0.3) is False


# ── TTL validation ────────────────────────────────────────────────────────────

def test_ttl_valid() -> None:
    item = _make_item(age_seconds=300)
    assert is_evidence_ttl_valid(item, source_ttl_sec=3600) is True


def test_ttl_expired() -> None:
    item = _make_item(age_seconds=7200)
    assert is_evidence_ttl_valid(item, source_ttl_sec=3600) is False


def test_ttl_category_override_stricter() -> None:
    item = _make_item(age_seconds=1800)
    assert is_evidence_ttl_valid(item, source_ttl_sec=3600, category_ttl_override_sec=1200) is False


def test_ttl_no_published_at() -> None:
    item = _make_item()
    item.published_at_utc = None
    assert is_evidence_ttl_valid(item, source_ttl_sec=3600) is False


# ── Bundle building ───────────────────────────────────────────────────────────

def test_bundle_respects_max_items() -> None:
    items = [_make_item(source_id="src-{}".format(i)) for i in range(10)]
    bundle, _ = build_evidence_bundle(items, source_ttls={"src-{}".format(i): 3600 for i in range(10)})
    assert len(bundle) <= 6


def test_bundle_tier_sorted() -> None:
    items = [
        _make_item(source_id="t3", tier=3, age_seconds=100),
        _make_item(source_id="t1", tier=1, age_seconds=100),
        _make_item(source_id="t2", tier=2, age_seconds=100),
    ]
    ttls = {"t1": 3600, "t2": 3600, "t3": 3600}
    bundle, _ = build_evidence_bundle(items, source_ttls=ttls)
    assert bundle[0].reliability_tier == 1
    assert bundle[1].reliability_tier == 2
    assert bundle[2].reliability_tier == 3


def test_bundle_hash_deterministic() -> None:
    items = [_make_item(source_id="src-1", text="Same text")]
    h1 = compute_bundle_hash(items)
    h2 = compute_bundle_hash(items)
    assert h1 == h2


def test_bundle_excludes_expired() -> None:
    items = [_make_item(source_id="old", age_seconds=7200)]
    bundle, _ = build_evidence_bundle(items, source_ttls={"old": 3600})
    assert len(bundle) == 0


# ── Conflict detection ────────────────────────────────────────────────────────

def test_no_conflict_single_item() -> None:
    items = [_make_item(text="The vote will pass, confirms majority")]
    has_conflict, _ = detect_conflict(items)
    assert has_conflict is False


def test_conflict_detected() -> None:
    items = [
        _make_item(source_id="a", text="The measure will pass, approved by committee", tier=1),
        _make_item(source_id="b", text="The measure won't pass, rejected by senate", tier=1),
    ]
    has_conflict, desc = detect_conflict(items)
    assert has_conflict is True
    assert desc is not None


def test_resolve_conflict_high_stakes_insufficient_tier1() -> None:
    items = [
        _make_item(source_id="a", text="Will pass", tier=2),
        _make_item(source_id="b", text="Won't pass", tier=2),
    ]
    action, reason = resolve_conflict(items, high_stakes=True)
    assert action == "NO_TRADE"
    assert reason == "EVIDENCE_TIER1_INSUFFICIENT"


# ── Rate limiting ─────────────────────────────────────────────────────────────

def test_rate_limiter_allows_first() -> None:
    rl = EvidenceFetchRateLimiter()
    assert rl.can_fetch() is True


def test_rate_limiter_blocks_at_cap() -> None:
    rl = EvidenceFetchRateLimiter()
    for _ in range(60):
        rl.record_fetch()
    assert rl.can_fetch() is False
