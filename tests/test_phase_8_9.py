"""Tests for Phases 8-9: Reconcile, Arming, Telegram, Startup, Observability."""

import asyncio
import os
import tempfile
import time

from polyedge.arming import ArmingCeremony, ArmingError
from polyedge.observability import NO_TRADE_REASONS, EventLog
from polyedge.reconcile import LEVEL_1, LEVEL_2, LEVEL_3, ReconcileEngine, classify_mismatch
from polyedge.startup import StartupSequence
from polyedge.telegram import TelegramController


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Reconcile ─────────────────────────────────────────────────────────────────

def test_classify_mismatch_levels() -> None:
    assert classify_mismatch(0.05, 100.0) == LEVEL_1   # 0.05%
    assert classify_mismatch(0.30, 100.0) == LEVEL_2   # 0.3%
    assert classify_mismatch(2.00, 100.0) == LEVEL_3   # 2%


def test_reconcile_no_mismatches() -> None:
    eng = ReconcileEngine(wallet_usd=100.0)
    local = {"mkt-1": {"notional_usd": 10.0}}
    remote = {"mkt-1": {"notional_usd": 10.0}}
    mm = eng.reconcile_positions(local, remote)
    assert len(mm) == 0


def test_reconcile_position_missing_remote() -> None:
    eng = ReconcileEngine(wallet_usd=100.0)
    local = {"mkt-1": {"notional_usd": 10.0}}
    remote = {}
    mm = eng.reconcile_positions(local, remote)
    assert len(mm) == 1
    assert mm[0].level == LEVEL_3


def test_reconcile_green_no_data() -> None:
    eng = ReconcileEngine(wallet_usd=100.0)
    green, reasons = eng.reconcile_green()
    assert green is False
    assert any("No reconciliation" in r for r in reasons)


def test_reconcile_green_after_clean() -> None:
    eng = ReconcileEngine(wallet_usd=100.0)
    eng.reconcile_positions(
        {"mkt-1": {"notional_usd": 10}},
        {"mkt-1": {"notional_usd": 10}},
    )
    green, reasons = eng.reconcile_green()
    assert green is True


def test_reconcile_green_fails_with_level3() -> None:
    eng = ReconcileEngine(wallet_usd=100.0)
    eng.reconcile_positions(
        {"mkt-1": {"notional_usd": 10}},
        {},  # Missing remote → Level-3
    )
    green, reasons = eng.reconcile_green()
    assert green is False
    assert any("Level-3" in r for r in reasons)


# ── Arming ────────────────────────────────────────────────────────────────────

def test_arming_step1_step2() -> None:
    tmpdir = tempfile.mkdtemp()
    ac = ArmingCeremony(
        process_start_unix_ms=1000,
        local_state_secret="test-secret",
        arming_dir=tmpdir,
    )

    nonce1 = ac.step1_totp("123456")
    assert len(nonce1) == 16

    record = ac.step2_confirm(nonce1)
    assert record["process_start_unix_ms"] == 1000
    assert ac.is_armed is True


def test_arming_verify_file() -> None:
    tmpdir = tempfile.mkdtemp()
    ac = ArmingCeremony(
        process_start_unix_ms=2000,
        local_state_secret="test-secret-2",
        arming_dir=tmpdir,
    )
    nonce1 = ac.step1_totp("654321")
    ac.step2_confirm(nonce1)

    # Create new ceremony instance with same params → should verify
    ac2 = ArmingCeremony(
        process_start_unix_ms=2000,
        local_state_secret="test-secret-2",
        arming_dir=tmpdir,
    )
    valid, msg = ac2.verify_arming_file()
    assert valid is True


def test_arming_wrong_process() -> None:
    tmpdir = tempfile.mkdtemp()
    ac = ArmingCeremony(
        process_start_unix_ms=3000,
        local_state_secret="test-secret-3",
        arming_dir=tmpdir,
    )
    nonce1 = ac.step1_totp("111111")
    ac.step2_confirm(nonce1)

    # Different process_start → should fail
    ac2 = ArmingCeremony(
        process_start_unix_ms=9999,
        local_state_secret="test-secret-3",
        arming_dir=tmpdir,
    )
    valid, msg = ac2.verify_arming_file()
    assert valid is False
    assert "different process" in msg


def test_arming_nonce_mismatch() -> None:
    tmpdir = tempfile.mkdtemp()
    ac = ArmingCeremony(
        process_start_unix_ms=4000,
        local_state_secret="test-secret-4",
        arming_dir=tmpdir,
    )
    ac.step1_totp("222222")
    try:
        ac.step2_confirm("wrong-nonce")
        assert False, "Should fail"
    except ArmingError:
        pass


# ── Telegram ──────────────────────────────────────────────────────────────────

def test_telegram_authorised() -> None:
    tc = TelegramController(bot_token="test", operator_chat_ids={12345})
    assert tc.is_authorised(12345) is True
    assert tc.is_authorised(99999) is False


def test_telegram_process_message() -> None:
    tc = TelegramController(bot_token="test", operator_chat_ids={12345})
    result = _run(tc.process_message(12345, 1, "/status", {
        "bot_state": "OBSERVE_ONLY",
        "open_positions": 0,
        "daily_pnl": 0,
        "budget_remaining": 1.5,
    }))
    assert "OBSERVE_ONLY" in result


def test_telegram_unauthorised_ignored() -> None:
    tc = TelegramController(bot_token="test", operator_chat_ids={12345})
    result = _run(tc.process_message(99999, 1, "/status"))
    assert result is None


def test_telegram_alert_dedup() -> None:
    tc = TelegramController(bot_token="test", operator_chat_ids={12345})
    sent1 = _run(tc.send_alert("Test alert", dedup_key="test-1"))
    assert sent1 is True
    sent2 = _run(tc.send_alert("Test alert", dedup_key="test-1"))
    assert sent2 is False  # Deduped


# ── Startup ───────────────────────────────────────────────────────────────────

def test_startup_with_config() -> None:
    """Startup with config dir → at least step 1 runs."""
    tmpdir = tempfile.mkdtemp()
    ss = StartupSequence()
    ok, report = ss.run_all(config_dir=tmpdir)
    # Will fail at step 1 (no manifest)
    assert ok is False
    assert len(report["blockers"]) > 0


def test_startup_clock_drift_ok() -> None:
    """Clock drift step passes when time is reasonable."""
    ss = StartupSequence()
    result = ss._step_clock_drift()
    assert result.get("blocker") is False


# ── Observability ─────────────────────────────────────────────────────────────

def test_no_trade_reasons_count() -> None:
    """All 23 canonical reasons are defined."""
    assert len(NO_TRADE_REASONS) == 23


def test_event_log_counting() -> None:
    log = EventLog()
    log.log_event("FILTER", market_id="mkt-1", reason_code="EV_TOO_LOW")
    log.log_event("FILTER", market_id="mkt-2", reason_code="EV_TOO_LOW")
    log.log_event("FILTER", market_id="mkt-3", reason_code="SPREAD_TOO_WIDE")

    assert log.no_trade_stats["EV_TOO_LOW"] == 2
    assert log.no_trade_stats["SPREAD_TOO_WIDE"] == 1
    assert log.stats["total_events"] == 3


def test_event_log_recent() -> None:
    log = EventLog()
    for i in range(150):
        log.log_event("TEST", market_id="mkt-{}".format(i))
    assert len(log.recent_events) == 100  # Capped at 100
