"""Tests for Decision Engine, Risk Manager, Locks, and PAPER Execution."""

import time
import uuid
from typing import Any

from polyedge.decision import (
    REASON_EV_TOO_LOW,
    compute_ev,
    compute_fee_cost,
    compute_spread_cost,
    make_decision,
)
from polyedge.execution import PaperExecutionEngine, PaperFillTracker
from polyedge.locks import LockManager
from polyedge.risk import RiskManager
from polyedge.snapshots import Snapshot


def _make_snapshot(**kwargs) -> Snapshot:
    defaults = {
        "snapshot_id": str(uuid.uuid4()),
        "market_id": "mkt-001",
        "snapshot_at_unix_ms": int(time.time() * 1000),
        "snapshot_source": "WS",
        "snapshot_ws_epoch": 1,
        "ws_last_message_unix_ms": int(time.time() * 1000),
        "market_last_ws_update_unix_ms": int(time.time() * 1000),
        "orderbook_last_change_unix_ms": int(time.time() * 1000),
        "best_bid_yes": 0.48,
        "best_ask_yes": 0.50,
        "best_bid_no": 0.50,
        "best_ask_no": 0.52,
        "depth_yes": [[0.47, 100], [0.46, 200], [0.45, 300]],
        "depth_no": [[0.49, 100], [0.48, 200], [0.47, 300]],
        "orderbook_hash": b"\x00" * 32,
        "ask_sum_anomaly": False,
        "invalid_book_anomaly": False,
    }
    defaults.update(kwargs)
    return Snapshot(**defaults)


# ── Decision Engine ───────────────────────────────────────────────────────────

def test_spread_cost() -> None:
    assert abs(compute_spread_cost(0.48, 0.50) - 0.01) < 1e-9


def test_fee_cost_paper() -> None:
    """Paper fees: max(actual, PAPER_MIN_FEE_BPS) × PAPER_FEE_MULTIPLIER."""
    fee = compute_fee_cost(fee_rate_bps=5, is_paper=True)  # max(5, 10) × 2 = 20bps
    assert abs(fee - 0.002) < 0.0001


def test_ev_positive() -> None:
    ev = compute_ev(p_eff=0.55, entry_price=0.50, required_edge=0.01, side="YES")
    assert ev > 0


def test_ev_negative() -> None:
    ev = compute_ev(p_eff=0.45, entry_price=0.50, required_edge=0.01, side="YES")
    assert ev < 0


def test_make_decision_trade() -> None:
    """High EV produces TRADE decision."""
    snap = _make_snapshot(
        best_bid_yes=0.40, best_ask_yes=0.42,
        best_bid_no=0.58, best_ask_no=0.60,
    )
    d = make_decision(
        market_id="mkt-001",
        candidate_id="cand-001",
        p_eff=0.70,  # Strong conviction
        snapshot=snap,
        order_size_usd=2.0,
        time_to_resolution_days=7,
    )
    assert d["side"] == "YES"
    assert d["reason_code"] == "TRADE"
    assert d["decision_id_hex"] is not None


def test_make_decision_no_trade() -> None:
    """Low EV produces NO_TRADE."""
    snap = _make_snapshot(
        best_bid_yes=0.49, best_ask_yes=0.51,
        best_bid_no=0.49, best_ask_no=0.51,
    )
    d = make_decision(
        market_id="mkt-001",
        candidate_id="cand-001",
        p_eff=0.50,  # No edge
        snapshot=snap,
        order_size_usd=2.0,
    )
    assert d["side"] == "NO_TRADE"
    assert d["reason_code"] == REASON_EV_TOO_LOW


def test_decision_id_deterministic() -> None:
    """Same inputs → same decision_id_hex."""
    snap = _make_snapshot()
    d1 = make_decision("mkt", "c1", 0.55, snap, 1.0)
    d2 = make_decision("mkt", "c1", 0.55, snap, 1.0)
    assert d1["decision_id_hex"] == d2["decision_id_hex"]


# ── Risk Manager ──────────────────────────────────────────────────────────────

def test_risk_order_size() -> None:
    """Order size respects per-market limit."""
    rm = RiskManager(wallet_usd=100.0)
    size = rm.compute_order_size("mkt-001")
    assert size <= 100.0 * 0.02  # MAX_PER_MARKET_PCT


def test_risk_position_limit() -> None:
    """Cannot exceed max open positions."""
    rm = RiskManager(wallet_usd=1000.0, max_open_positions=2)
    rm.add_position("mkt-1", "YES", 10, 0.5)
    rm.add_position("mkt-2", "NO", 10, 0.5)
    can_open, reason = rm.can_open_position("mkt-3")
    assert can_open is False
    assert "max positions" in reason


def test_risk_daily_stop() -> None:
    """Daily stop triggered at -3% of wallet."""
    rm = RiskManager(wallet_usd=100.0)
    rm.add_position("mkt-1", "YES", 10, 0.5)
    rm.close_position("mkt-1", 0.2)  # Loss
    assert rm.check_daily_stop()  # PnL negative enough


def test_risk_twap_no_samples() -> None:
    """Risk MTM returns None with insufficient samples."""
    rm = RiskManager()
    assert rm.risk_mtm("mkt-001") is None


def test_risk_wallet_staleness() -> None:
    """Wallet marked stale if not updated."""
    rm = RiskManager()
    rm._wallet_updated_at = time.time() - 7200  # 2 hours old
    assert rm.is_wallet_stale() is True


# ── Locks ─────────────────────────────────────────────────────────────────────

def test_lock_acquire_release() -> None:
    """Basic lock acquire and release."""
    lm = LockManager("instance-1")
    ok, version = lm.acquire("mkt-001", "worker-1")
    assert ok is True
    assert version == 1

    assert lm.release("mkt-001", "worker-1") is True
    assert "mkt-001" not in lm.held_locks


def test_lock_blocked_by_another() -> None:
    """Cannot acquire lock held by another worker."""
    lm = LockManager("instance-1")
    lm.acquire("mkt-001", "worker-1")
    ok, _ = lm.acquire("mkt-001", "worker-2")
    assert ok is False


def test_lock_steal_after_expiry() -> None:
    """Lock can be stolen after expiry + grace."""
    lm = LockManager("instance-1")
    lm.acquire("mkt-001", "worker-1")

    # Manually expire
    lm._locks["mkt-001"].expires_at = time.time() - 100
    ok, version = lm.acquire("mkt-001", "worker-2")
    assert ok is True
    assert version == 2


def test_lock_validate_success() -> None:
    """Pre-exec validation passes for valid lock."""
    lm = LockManager("instance-1")
    ok, version = lm.acquire("mkt-001", "worker-1")
    valid, _ = lm.validate_for_submit("mkt-001", "worker-1", version)
    assert valid is True


def test_lock_validate_wrong_version() -> None:
    """Pre-exec validation fails for wrong version."""
    lm = LockManager("instance-1")
    lm.acquire("mkt-001", "worker-1")
    valid, reason = lm.validate_for_submit("mkt-001", "worker-1", 999)
    assert valid is False
    assert "version" in reason


# ── PAPER Execution ───────────────────────────────────────────────────────────

def test_paper_submit_order() -> None:
    """Paper order is recorded."""
    eng = PaperExecutionEngine()
    order = eng.submit_order("oid-1", "mkt-001", "YES", 2.0, 0.50, "dec-1")
    assert order["status"] == "OPEN"
    assert len(eng.open_orders) == 1


def test_paper_cancel() -> None:
    """Paper order can be cancelled."""
    eng = PaperExecutionEngine()
    eng.submit_order("oid-1", "mkt-001", "YES", 2.0, 0.50, "dec-1")
    assert eng.cancel_order("oid-1") is True
    assert len(eng.open_orders) == 0


def test_paper_fill_not_touch() -> None:
    """Touch (price at limit) does NOT fill in paper mode."""
    tracker = PaperFillTracker()
    fill = tracker.check_fill("oid-1", "YES", limit_price=0.50, current_price=0.50)
    assert fill is None


def test_paper_fill_requires_through() -> None:
    """Fill requires trade-through by >=1 tick."""
    tracker = PaperFillTracker()
    # Price at limit — no fill
    fill = tracker.check_fill("oid-1", "YES", limit_price=0.50, current_price=0.50)
    assert fill is None
    # Price through by 1 tick (0.49) — pending but not sustained yet
    fill = tracker.check_fill("oid-1", "YES", limit_price=0.50, current_price=0.49)
    assert fill is None  # Need to sustain for 3s
