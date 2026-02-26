"""Tests for AI Budget Manager (spec §13)."""

from polyedge.budget import (
    BudgetDeniedError,
    BudgetManager,
    compute_daily_cap,
    compute_window_cap,
)


# ── Cap computation ───────────────────────────────────────────────────────────

def test_daily_cap_user_limit() -> None:
    """User cap ($2) is binding when wallet is large."""
    assert compute_daily_cap(1000.0) == 2.00


def test_daily_cap_wallet_limit() -> None:
    """Wallet-based cap is binding when wallet is small."""
    cap = compute_daily_cap(100.0)
    assert cap == 0.50  # 100 * 0.005 = 0.50 < 2.00


def test_window_cap() -> None:
    """Window cap is 20% of daily cap."""
    assert compute_window_cap(0.50) == 0.10


# ── Reservation ───────────────────────────────────────────────────────────────

def test_reserve_success() -> None:
    """Simple reservation succeeds under cap."""
    bm = BudgetManager(wallet_usd=100.0)
    rid = bm.reserve("deepseek/deepseek-v3.2", 0.01, "corr-001")
    assert rid is not None


def test_reserve_denied_daily_cap() -> None:
    """Reservation denied when daily cap exceeded."""
    bm = BudgetManager(wallet_usd=100.0)  # daily_cap = 0.50, window_cap = 0.10

    # Use small amounts that fit window cap but exhaust daily cap
    for i in range(10):
        rid = bm.reserve("model{}".format(i), 0.04, "c{}".format(i))
        bm.settle(rid, 0.04)  # settle to free window space

    # spent=0.40, next 0.04+0.04 = 0.48, one more should exceed
    rid = bm.reserve("modelA", 0.04, "cA")
    bm.settle(rid, 0.04)  # spent=0.44
    rid = bm.reserve("modelB", 0.04, "cB")
    bm.settle(rid, 0.04)  # spent=0.48

    # This should push over 0.50
    try:
        bm.reserve("modelC", 0.04, "cC")
        assert False, "Should have raised BudgetDeniedError"
    except BudgetDeniedError:
        pass


def test_reserve_multiple_same_correlation() -> None:
    """Multiple reservations with same correlation_id share the analysis count."""
    bm = BudgetManager(wallet_usd=1000.0)  # cap = 2.00
    bm.reserve("model1", 0.01, "same-corr")
    bm.reserve("model2", 0.01, "same-corr")
    bm.reserve("model3", 0.01, "same-corr")
    # All should succeed since they share correlation_id


# ── Settlement ────────────────────────────────────────────────────────────────

def test_settle_success() -> None:
    """Settlement decreases in_flight and increases spent."""
    bm = BudgetManager(wallet_usd=1000.0)
    rid = bm.reserve("model1", 0.10, "c1")

    assert bm.stats["in_flight_usd"] == 0.10
    assert bm.stats["spent_usd"] == 0.0

    bm.settle(rid, actual_usd=0.05)

    assert bm.stats["in_flight_usd"] == 0.0
    assert bm.stats["spent_usd"] == 0.05


def test_settle_idempotent() -> None:
    """Settling twice returns False on second call (no double-decrement)."""
    bm = BudgetManager(wallet_usd=1000.0)
    rid = bm.reserve("model1", 0.10, "c1")

    assert bm.settle(rid, 0.05) is True
    assert bm.settle(rid, 0.05) is False  # Already settled

    # Spent should only be 0.05, not 0.10
    assert bm.stats["spent_usd"] == 0.05


def test_settle_uses_reserved_as_fallback() -> None:
    """If actual_usd is None, use reserved_usd."""
    bm = BudgetManager(wallet_usd=1000.0)
    rid = bm.reserve("model1", 0.10, "c1")
    bm.settle(rid)  # No actual_usd
    assert bm.stats["spent_usd"] == 0.10


# ── Release ───────────────────────────────────────────────────────────────────

def test_release_frees_in_flight() -> None:
    """Releasing a reservation frees in_flight without spending."""
    bm = BudgetManager(wallet_usd=1000.0)
    rid = bm.reserve("model1", 0.10, "c1")
    assert bm.stats["in_flight_usd"] == 0.10

    bm.release(rid)
    assert bm.stats["in_flight_usd"] == 0.0
    assert bm.stats["spent_usd"] == 0.0


# ── Reaper ────────────────────────────────────────────────────────────────────

def test_reaper_force_settles_expired() -> None:
    """Reaper force-settles expired reservations."""
    bm = BudgetManager(wallet_usd=1000.0)
    rid = bm.reserve("model1", 0.10, "c1")

    # Manually expire the reservation
    from datetime import datetime, timedelta, timezone
    bm._reservations[rid]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=30)
    )

    count = bm.reap_expired()
    assert count == 1
    assert bm.stats["spent_usd"] == 0.10
    assert bm.stats["in_flight_usd"] == 0.0


def test_reaper_force_settle_no_double_decrement() -> None:
    """Reaper + settle does not double-decrement."""
    bm = BudgetManager(wallet_usd=1000.0)
    rid = bm.reserve("model1", 0.10, "c1")

    from datetime import datetime, timedelta, timezone
    bm._reservations[rid]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=30)
    )

    bm.reap_expired()
    # Try to settle after force-settle
    result = bm.settle(rid, 0.05)
    assert result is False  # Already force-settled
    assert bm.stats["spent_usd"] == 0.10  # Not 0.15


def test_reaper_degraded_threshold() -> None:
    """>=3 force-settles triggers is_degraded."""
    bm = BudgetManager(wallet_usd=1000.0)

    from datetime import datetime, timedelta, timezone

    for i in range(3):
        rid = bm.reserve("model{}".format(i), 0.01, "c{}".format(i))
        bm._reservations[rid]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=30)
        )
        bm.reap_expired()

    assert bm.is_degraded is True


# ── Parallel safety ───────────────────────────────────────────────────────────

def test_parallel_reservations_respect_daily_cap() -> None:
    """Parallel reservations never exceed daily cap."""
    bm = BudgetManager(wallet_usd=1000.0)  # daily_cap=2.00, window_cap=0.40

    # Reserve up to near daily cap
    for i in range(8):
        rid = bm.reserve("m{}".format(i), 0.20, "c{}".format(i))
        bm.settle(rid, 0.20)  # settle to free window space
    # spent = 1.60

    bm.reserve("m8", 0.20, "c8")  # in_flight=0.20, total=1.80
    bm.reserve("m9", 0.15, "c9")  # in_flight=0.35, total=1.95

    # This should be denied (1.95 + 0.10 = 2.05 > 2.00)
    try:
        bm.reserve("m10", 0.10, "c10")
        assert False, "Should deny"
    except BudgetDeniedError:
        pass

    # Total in_flight should be 0.35
    assert abs(bm.stats["in_flight_usd"] - 0.35) < 0.001
