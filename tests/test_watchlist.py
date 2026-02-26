"""Tests for watchlist scoring and management."""

from datetime import datetime, timedelta, timezone

from polyedge.watchlist import score_market


def _make_market(**kwargs):
    now = datetime.now(timezone.utc)
    defaults = {
        "market_id": "mkt-001",
        "end_date_utc": (now + timedelta(days=7)).isoformat() + "Z",
        "volume_24h_usd": 5000,
        "liquidity_usd": 10000,
        "spread": 0.02,
        "orderbook_last_change_unix_ms": None,
    }
    defaults.update(kwargs)
    return defaults


# ── Market scoring ────────────────────────────────────────────────────────────

def test_score_basic() -> None:
    """Basic market gets a positive score."""
    m = _make_market()
    s = score_market(m)
    assert s > 0


def test_score_higher_for_tighter_spread() -> None:
    """Tighter spread scores higher."""
    m_tight = _make_market(spread=0.005)
    m_wide = _make_market(spread=0.025)
    assert score_market(m_tight) > score_market(m_wide)


def test_score_higher_for_more_volume() -> None:
    """Higher volume scores higher."""
    m_high = _make_market(volume_24h_usd=100000)
    m_low = _make_market(volume_24h_usd=600)
    assert score_market(m_high) > score_market(m_low)


def test_score_higher_for_more_liquidity() -> None:
    """Higher liquidity scores higher."""
    m_high = _make_market(liquidity_usd=500000)
    m_low = _make_market(liquidity_usd=1100)
    assert score_market(m_high) > score_market(m_low)


def test_score_nearing_resolution_higher() -> None:
    """Markets nearing resolution score higher."""
    now = datetime.now(timezone.utc)
    m_near = _make_market(end_date_utc=(now + timedelta(hours=6)).isoformat())
    m_far = _make_market(end_date_utc=(now + timedelta(days=60)).isoformat())
    assert score_market(m_near) > score_market(m_far)


def test_score_below_volume_min_penalised() -> None:
    """Volume below minimum gets penalty."""
    m_ok = _make_market(volume_24h_usd=5000)
    m_low = _make_market(volume_24h_usd=100)
    assert score_market(m_ok) > score_market(m_low)


def test_score_no_end_date() -> None:
    """Missing end date doesn't crash, just scores lower."""
    m = _make_market(end_date_utc=None)
    s = score_market(m)
    # Should still produce a valid number
    assert isinstance(s, float)
