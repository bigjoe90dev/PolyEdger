"""Tests for WS health predicates (spec §7.3)."""

import time

from polyedge.snapshots import Snapshot
from polyedge.ws_client import WSState
from polyedge.ws_health import ws_healthy_decision, ws_healthy_exec


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_healthy_snapshot(market_id: str = "mkt-001", epoch: int = 1) -> Snapshot:
    """Create a snapshot that should pass all health checks."""
    now = _now_ms()
    return Snapshot(
        snapshot_id="snap-001",
        market_id=market_id,
        snapshot_at_unix_ms=now - 500,  # 0.5s ago
        snapshot_source="WS",
        snapshot_ws_epoch=epoch,
        ws_last_message_unix_ms=now - 200,
        market_last_ws_update_unix_ms=now - 500,
        orderbook_last_change_unix_ms=now - 500,
        best_bid_yes=0.45,
        best_ask_yes=0.48,
        best_bid_no=0.52,
        best_ask_no=0.55,
        depth_yes=[[0.44, 100]],
        depth_no=[[0.51, 200]],
        orderbook_hash=b"\x00" * 32,
        ask_sum_anomaly=False,
        invalid_book_anomaly=False,
    )


def _make_healthy_ws_state(epoch: int = 1) -> WSState:
    """Create a WS state that should pass all health checks."""
    now = _now_ms()
    state = WSState()
    state.ws_connected = True
    state.ws_last_message_unix_ms = now - 200
    state.current_ws_epoch = epoch
    return state


# ── WS_HEALTHY_DECISION ──────────────────────────────────────────────────────

def test_ws_healthy_decision_pass() -> None:
    """All conditions met → healthy."""
    snap = _make_healthy_snapshot()
    ws = _make_healthy_ws_state()
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is True
    assert reasons == []


def test_ws_healthy_decision_disconnected() -> None:
    """ws_connected=false → unhealthy."""
    snap = _make_healthy_snapshot()
    ws = _make_healthy_ws_state()
    ws.ws_connected = False
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is False
    assert any("ws_connected" in r for r in reasons)


def test_ws_healthy_decision_stale_ws_message() -> None:
    """ws_last_message too old → unhealthy."""
    snap = _make_healthy_snapshot()
    ws = _make_healthy_ws_state()
    ws.ws_last_message_unix_ms = _now_ms() - 15_000  # 15s ago > 10s heartbeat
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is False
    assert any("ws_last_message stale" in r for r in reasons)


def test_ws_healthy_decision_rest_source() -> None:
    """REST snapshot → unhealthy for trading."""
    snap = _make_healthy_snapshot()
    snap.snapshot_source = "REST"
    ws = _make_healthy_ws_state()
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is False
    assert any("REST" in r for r in reasons)


def test_ws_healthy_decision_epoch_mismatch() -> None:
    """Epoch mismatch → unhealthy."""
    snap = _make_healthy_snapshot(epoch=1)
    ws = _make_healthy_ws_state(epoch=2)
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is False
    assert any("epoch" in r for r in reasons)


def test_ws_healthy_decision_market_mismatch() -> None:
    """Wrong market_id → unhealthy."""
    snap = _make_healthy_snapshot(market_id="other-market")
    ws = _make_healthy_ws_state()
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is False
    assert any("market_id mismatch" in r for r in reasons)


def test_ws_healthy_decision_stale_market_update() -> None:
    """market_last_ws_update too old → unhealthy."""
    snap = _make_healthy_snapshot()
    snap.market_last_ws_update_unix_ms = _now_ms() - 10_000  # 10s > 6s
    ws = _make_healthy_ws_state()
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is False


def test_ws_healthy_decision_null_orderbook_change() -> None:
    """Null orderbook_last_change → unhealthy."""
    snap = _make_healthy_snapshot()
    snap.orderbook_last_change_unix_ms = None
    ws = _make_healthy_ws_state()
    healthy, reasons = ws_healthy_decision("mkt-001", snap, ws)
    assert healthy is False
    assert any("orderbook_last_change" in r for r in reasons)


# ── WS_HEALTHY_EXEC (stricter: 3s vs 6s) ─────────────────────────────────────

def test_ws_healthy_exec_pass() -> None:
    """All conditions met with fresh snapshot → healthy."""
    snap = _make_healthy_snapshot()
    ws = _make_healthy_ws_state()
    healthy, reasons = ws_healthy_exec("mkt-001", snap, ws)
    assert healthy is True


def test_ws_healthy_exec_stricter_window() -> None:
    """Snapshot passes 6s decision window but fails 3s exec window."""
    now = _now_ms()
    snap = _make_healthy_snapshot()
    # Set timestamps to 5s ago — passes decision (6s) but fails exec (3s)
    snap.market_last_ws_update_unix_ms = now - 5000
    snap.orderbook_last_change_unix_ms = now - 5000

    ws = _make_healthy_ws_state()

    dec_ok, _ = ws_healthy_decision("mkt-001", snap, ws)
    exec_ok, _ = ws_healthy_exec("mkt-001", snap, ws)

    assert dec_ok is True
    assert exec_ok is False


def test_ws_healthy_exec_disconnected() -> None:
    """Disconnected WS fails exec health too."""
    snap = _make_healthy_snapshot()
    ws = _make_healthy_ws_state()
    ws.ws_connected = False
    healthy, _ = ws_healthy_exec("mkt-001", snap, ws)
    assert healthy is False
