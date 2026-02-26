"""Tests for candidate pipeline: triggers, anti-spoof persistence, rate limiting."""

import time
import uuid
from datetime import datetime, timedelta, timezone

from polyedge.candidates import (
    CandidateRateLimiter,
    TriggerState,
    create_candidate,
    detect_triggers,
    is_candidate_expired,
    STATUS_NEW,
    TRIGGER_MID_MOVE,
    TRIGGER_SPREAD_CHANGE,
)
from polyedge.snapshots import Snapshot


def _make_snapshot(
    market_id: str = "mkt-001",
    bid_yes: float = 0.45,
    ask_yes: float = 0.48,
    bid_no: float = 0.52,
    ask_no: float = 0.55,
    depth_yes: list = None,
    depth_no: list = None,
) -> Snapshot:
    """Create a minimal snapshot for trigger testing."""
    return Snapshot(
        snapshot_id=str(uuid.uuid4()),
        market_id=market_id,
        snapshot_at_unix_ms=int(time.time() * 1000),
        snapshot_source="WS",
        snapshot_ws_epoch=1,
        ws_last_message_unix_ms=int(time.time() * 1000),
        market_last_ws_update_unix_ms=int(time.time() * 1000),
        orderbook_last_change_unix_ms=int(time.time() * 1000),
        best_bid_yes=bid_yes,
        best_ask_yes=ask_yes,
        best_bid_no=bid_no,
        best_ask_no=ask_no,
        depth_yes=depth_yes or [[0.44, 100], [0.43, 200], [0.42, 300]],
        depth_no=depth_no or [[0.51, 150], [0.50, 250], [0.49, 350]],
        orderbook_hash=b"\x00" * 32,
        ask_sum_anomaly=False,
        invalid_book_anomaly=False,
    )


# ── Trigger detection ─────────────────────────────────────────────────────────

def test_detect_mid_move() -> None:
    """Mid move > 1% triggers TRIGGER_MID_MOVE."""
    prev = _make_snapshot(bid_yes=0.45, ask_yes=0.48)
    curr = _make_snapshot(bid_yes=0.50, ask_yes=0.53)
    triggers = detect_triggers("mkt-001", curr, prev)
    assert TRIGGER_MID_MOVE in triggers


def test_detect_no_mid_move() -> None:
    """Small mid change does NOT trigger."""
    prev = _make_snapshot(bid_yes=0.45, ask_yes=0.48)
    curr = _make_snapshot(bid_yes=0.455, ask_yes=0.485)
    triggers = detect_triggers("mkt-001", curr, prev)
    assert TRIGGER_MID_MOVE not in triggers


def test_detect_spread_change() -> None:
    """Spread change > 0.5% triggers TRIGGER_SPREAD_CHANGE."""
    prev = _make_snapshot(bid_yes=0.45, ask_yes=0.46)  # spread = 0.01
    curr = _make_snapshot(bid_yes=0.45, ask_yes=0.48)  # spread = 0.03
    triggers = detect_triggers("mkt-001", curr, prev)
    assert TRIGGER_SPREAD_CHANGE in triggers


def test_detect_no_triggers_without_prev() -> None:
    """First snapshot (no prev) produces no triggers."""
    curr = _make_snapshot()
    triggers = detect_triggers("mkt-001", curr, None)
    assert len(triggers) == 0


# ── Trigger persistence (anti-spoof) ─────────────────────────────────────────

def test_trigger_state_needs_persistence() -> None:
    """Single trigger occurrence does NOT meet persistence threshold."""
    ts = TriggerState()
    result = ts.record_trigger("mkt-001", TRIGGER_MID_MOVE, "snap-1")
    assert result is False


def test_trigger_state_meets_persistence() -> None:
    """After TRIGGER_PERSIST_UPDATES updates over TRIGGER_PERSIST_MIN_SEC → persists."""
    ts = TriggerState()

    # Manually set first_seen to past
    ts.record_trigger("mkt-001", TRIGGER_MID_MOVE, "snap-1")
    key = ("mkt-001", TRIGGER_MID_MOVE)
    ts._state[key]["first_seen"] = time.time() - 10  # 10s ago
    ts._state[key]["count"] = 2  # Already 2 updates

    # Third update should meet threshold (count=3, elapsed>6s)
    result = ts.record_trigger("mkt-001", TRIGGER_MID_MOVE, "snap-3")
    assert result is True


def test_trigger_state_same_snapshot_not_counted() -> None:
    """Same snapshot_id doesn't increment count."""
    ts = TriggerState()
    ts.record_trigger("mkt-001", TRIGGER_MID_MOVE, "snap-1")
    ts.record_trigger("mkt-001", TRIGGER_MID_MOVE, "snap-1")  # Same
    assert ts._state[("mkt-001", TRIGGER_MID_MOVE)]["count"] == 1


def test_trigger_state_clear() -> None:
    """Clearing a trigger removes it from state."""
    ts = TriggerState()
    ts.record_trigger("mkt-001", TRIGGER_MID_MOVE, "snap-1")
    ts.clear_trigger("mkt-001", TRIGGER_MID_MOVE)
    assert ("mkt-001", TRIGGER_MID_MOVE) not in ts._state


# ── Rate limiting ─────────────────────────────────────────────────────────────

def test_rate_limiter_allows_first() -> None:
    """First candidate is always allowed."""
    rl = CandidateRateLimiter()
    assert rl.can_enqueue("mkt-001") is True


def test_rate_limiter_per_market_cap() -> None:
    """Per-market cap of 10 is enforced."""
    rl = CandidateRateLimiter()
    for _ in range(10):
        assert rl.can_enqueue("mkt-001") is True
        rl.record_enqueue("mkt-001")

    # 11th should be blocked
    assert rl.can_enqueue("mkt-001") is False


def test_rate_limiter_different_markets() -> None:
    """Different markets have independent per-market caps."""
    rl = CandidateRateLimiter()
    for _ in range(10):
        rl.record_enqueue("mkt-001")

    # mkt-002 should still be allowed
    assert rl.can_enqueue("mkt-002") is True


# ── Candidate creation ────────────────────────────────────────────────────────

def test_create_candidate_fields() -> None:
    """Created candidate has all required fields."""
    c = create_candidate("mkt-001", "snap-001", ["mid_move"])
    assert c["market_id"] == "mkt-001"
    assert c["snapshot_id"] == "snap-001"
    assert c["trigger_reasons"] == ["mid_move"]
    assert c["status"] == STATUS_NEW
    assert c["candidate_id"] is not None


def test_candidate_expired() -> None:
    """Candidate older than CANDIDATE_MAX_AGE_SEC is expired."""
    c = create_candidate("mkt-001", "snap-001", ["mid_move"])
    c["created_at_utc"] = datetime.now(timezone.utc) - timedelta(seconds=200)
    assert is_candidate_expired(c) is True


def test_candidate_not_expired() -> None:
    """Fresh candidate is not expired."""
    c = create_candidate("mkt-001", "snap-001", ["mid_move"])
    assert is_candidate_expired(c) is False
