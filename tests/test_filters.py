"""Tests for coarse deterministic filters (spec §9.3)."""

import time
import uuid
from datetime import datetime, timedelta, timezone

from polyedge.filters import (
    REASON_CANDIDATE_EXPIRED,
    REASON_DEPTH_TOO_THIN,
    REASON_LIQUIDITY_TOO_LOW,
    REASON_MARKET_NOT_ELIGIBLE,
    REASON_SNAPSHOT_ASK_SUM_ANOMALY,
    REASON_SNAPSHOT_INVALID_BOOK,
    REASON_SPREAD_TOO_WIDE,
    REASON_TIME_TO_RESOLUTION_OUT_OF_RANGE,
    REASON_VOLUME_TOO_LOW,
    filter_candidate_age,
    filter_depth,
    filter_invalid_book,
    filter_liquidity,
    filter_market_eligible,
    filter_spread,
    filter_time_to_resolution,
    filter_volume,
    run_all_filters,
)
from polyedge.snapshots import Snapshot


def _make_snapshot(**kwargs) -> Snapshot:
    """Create a snapshot for filter testing."""
    defaults = {
        "snapshot_id": str(uuid.uuid4()),
        "market_id": "mkt-001",
        "snapshot_at_unix_ms": int(time.time() * 1000),
        "snapshot_source": "WS",
        "snapshot_ws_epoch": 1,
        "ws_last_message_unix_ms": int(time.time() * 1000),
        "market_last_ws_update_unix_ms": int(time.time() * 1000),
        "orderbook_last_change_unix_ms": int(time.time() * 1000),
        "best_bid_yes": 0.45,
        "best_ask_yes": 0.47,
        "best_bid_no": 0.53,
        "best_ask_no": 0.55,
        "depth_yes": [[0.44, 100], [0.43, 200], [0.42, 300]],
        "depth_no": [[0.52, 150], [0.51, 250], [0.50, 350]],
        "orderbook_hash": b"\x00" * 32,
        "ask_sum_anomaly": False,
        "invalid_book_anomaly": False,
    }
    defaults.update(kwargs)
    return Snapshot(**defaults)


def _make_candidate(**kwargs):
    now = datetime.now(timezone.utc)
    defaults = {
        "candidate_id": str(uuid.uuid4()),
        "market_id": "mkt-001",
        "snapshot_id": str(uuid.uuid4()),
        "created_at_utc": now,
        "trigger_reasons": ["mid_move"],
        "status": "NEW",
    }
    defaults.update(kwargs)
    return defaults


def _make_market(**kwargs):
    defaults = {
        "market_id": "mkt-001",
        "is_binary_eligible": True,
        "end_date_utc": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "volume_24h_usd": 5000,
        "liquidity_usd": 10000,
    }
    defaults.update(kwargs)
    return defaults


# ── Individual filters ────────────────────────────────────────────────────────

def test_filter_candidate_age_fresh() -> None:
    assert filter_candidate_age(_make_candidate()) is None


def test_filter_candidate_age_expired() -> None:
    c = _make_candidate(
        created_at_utc=datetime.now(timezone.utc) - timedelta(seconds=200),
    )
    assert filter_candidate_age(c) == REASON_CANDIDATE_EXPIRED


def test_filter_market_eligible_yes() -> None:
    assert filter_market_eligible({"is_binary_eligible": True}) is None


def test_filter_market_eligible_no() -> None:
    assert filter_market_eligible({"is_binary_eligible": False}) == REASON_MARKET_NOT_ELIGIBLE


def test_filter_time_to_resolution_in_range() -> None:
    m = _make_market(
        end_date_utc=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    )
    assert filter_time_to_resolution(m) is None


def test_filter_time_to_resolution_too_soon() -> None:
    m = _make_market(
        end_date_utc=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    )
    assert filter_time_to_resolution(m) == REASON_TIME_TO_RESOLUTION_OUT_OF_RANGE


def test_filter_time_to_resolution_too_far() -> None:
    m = _make_market(
        end_date_utc=(datetime.now(timezone.utc) + timedelta(days=100)).isoformat(),
    )
    assert filter_time_to_resolution(m) == REASON_TIME_TO_RESOLUTION_OUT_OF_RANGE


def test_filter_volume_sufficient() -> None:
    assert filter_volume({"volume_24h_usd": 1000}) is None


def test_filter_volume_insufficient() -> None:
    assert filter_volume({"volume_24h_usd": 100}) == REASON_VOLUME_TOO_LOW


def test_filter_liquidity_sufficient() -> None:
    assert filter_liquidity({"liquidity_usd": 5000}) is None


def test_filter_liquidity_insufficient() -> None:
    assert filter_liquidity({"liquidity_usd": 500}) == REASON_LIQUIDITY_TOO_LOW


def test_filter_invalid_book_ok() -> None:
    snap = _make_snapshot(invalid_book_anomaly=False)
    assert filter_invalid_book(snap) is None


def test_filter_invalid_book_anomaly() -> None:
    snap = _make_snapshot(invalid_book_anomaly=True)
    assert filter_invalid_book(snap) == REASON_SNAPSHOT_INVALID_BOOK


def test_filter_spread_tight() -> None:
    snap = _make_snapshot(best_bid_yes=0.45, best_ask_yes=0.47)  # spread=0.02
    assert filter_spread(snap) is None


def test_filter_spread_too_wide() -> None:
    snap = _make_snapshot(best_bid_yes=0.40, best_ask_yes=0.50)  # spread=0.10
    assert filter_spread(snap) == REASON_SPREAD_TOO_WIDE


def test_filter_depth_sufficient() -> None:
    snap = _make_snapshot(
        depth_yes=[[0.44, 30], [0.43, 30], [0.42, 30]],
        depth_no=[[0.52, 30], [0.51, 30], [0.50, 30]],
    )
    assert filter_depth(snap) is None


def test_filter_depth_too_thin() -> None:
    snap = _make_snapshot(
        depth_yes=[[0.44, 5], [0.43, 5], [0.42, 5]],  # 15 < 50
        depth_no=[[0.52, 100], [0.51, 100], [0.50, 100]],
    )
    assert filter_depth(snap) == REASON_DEPTH_TOO_THIN


# ── run_all_filters integration ──────────────────────────────────────────────

def test_all_filters_pass() -> None:
    """Fresh, eligible candidate with good snapshot passes all filters."""
    c = _make_candidate()
    m = _make_market()
    snap = _make_snapshot()
    passed, reason = run_all_filters(c, m, snap)
    assert passed is True
    assert reason is None


def test_all_filters_fail_first() -> None:
    """Expired candidate fails on the first filter."""
    c = _make_candidate(
        created_at_utc=datetime.now(timezone.utc) - timedelta(seconds=200),
    )
    m = _make_market()
    snap = _make_snapshot()
    passed, reason = run_all_filters(c, m, snap)
    assert passed is False
    assert reason == REASON_CANDIDATE_EXPIRED


def test_all_filters_fail_market() -> None:
    """Non-binary market fails on eligibility."""
    c = _make_candidate()
    m = _make_market(is_binary_eligible=False)
    snap = _make_snapshot()
    passed, reason = run_all_filters(c, m, snap)
    assert passed is False
    assert reason == REASON_MARKET_NOT_ELIGIBLE
