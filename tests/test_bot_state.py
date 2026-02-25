"""Tests for bot state management and signature verification."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from polyedge.bot_state import (
    BotState,
    StateSignatureError,
    initialise_bot_state,
    load_bot_state,
    save_bot_state,
)

SECRET = "test-state-secret-key"


def test_state_signature_roundtrip() -> None:
    """Sign and verify passes."""
    bs = BotState(state="OBSERVE_ONLY", counter=1, ts_utc=datetime.now(timezone.utc))
    bs.sign(SECRET)
    assert bs.verify_signature(SECRET) is True


def test_state_signature_tamper_detection() -> None:
    """Modifying state after signing fails verification."""
    bs = BotState(state="OBSERVE_ONLY", counter=1, ts_utc=datetime.now(timezone.utc))
    bs.sign(SECRET)
    bs.state = "PAPER_TRADING"
    assert bs.verify_signature(SECRET) is False


def test_state_signature_wrong_key() -> None:
    """Wrong secret fails verification."""
    bs = BotState(state="OBSERVE_ONLY", counter=1, ts_utc=datetime.now(timezone.utc))
    bs.sign(SECRET)
    assert bs.verify_signature("wrong-key") is False


def test_state_invalid_state() -> None:
    """Invalid state raises ValueError."""
    with pytest.raises(ValueError, match="Invalid state"):
        BotState(state="INVALID", counter=1, ts_utc=datetime.now(timezone.utc))


def test_state_to_dict() -> None:
    """to_dict includes all fields."""
    now = datetime.now(timezone.utc)
    bs = BotState(state="OBSERVE_ONLY", counter=1, ts_utc=now)
    d = bs.to_dict()
    assert d["state"] == "OBSERVE_ONLY"
    assert d["counter"] == 1
    assert d["armed_until_utc"] is None


@pytest.mark.asyncio
async def test_initialise_creates_observe_only() -> None:
    """First init creates OBSERVE_ONLY state."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()

    bs = await initialise_bot_state(pool, SECRET)
    assert bs.state == "OBSERVE_ONLY"
    assert bs.counter == 1
    assert bs.verify_signature(SECRET) is True


@pytest.mark.asyncio
async def test_startup_forces_observe_only_from_live_trading() -> None:
    """State LIVE_TRADING is force-downgraded to OBSERVE_ONLY on startup."""
    now = datetime.now(timezone.utc)
    live_bs = BotState(state="LIVE_TRADING", counter=5, ts_utc=now)
    live_bs.sign(SECRET)

    # Simulate DB returning the LIVE_TRADING row
    row = {
        "state": "LIVE_TRADING",
        "counter": 5,
        "ts_utc": now,
        "armed_until_utc": None,
        "halt_until_utc": None,
        "halt_resume_state": None,
        "state_signature": live_bs.state_signature,
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.execute = AsyncMock()

    bs = await initialise_bot_state(pool, SECRET)
    assert bs.state == "OBSERVE_ONLY"
    assert bs.counter == 6  # Incremented


@pytest.mark.asyncio
async def test_startup_forces_observe_only_from_live_armed() -> None:
    """State LIVE_ARMED is force-downgraded to OBSERVE_ONLY on startup."""
    now = datetime.now(timezone.utc)
    armed_bs = BotState(state="LIVE_ARMED", counter=3, ts_utc=now)
    armed_bs.sign(SECRET)

    row = {
        "state": "LIVE_ARMED",
        "counter": 3,
        "ts_utc": now,
        "armed_until_utc": now,
        "halt_until_utc": None,
        "halt_resume_state": None,
        "state_signature": armed_bs.state_signature,
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    pool.execute = AsyncMock()

    bs = await initialise_bot_state(pool, SECRET)
    assert bs.state == "OBSERVE_ONLY"


@pytest.mark.asyncio
async def test_startup_invalid_signature_halts() -> None:
    """Invalid state signature raises StateSignatureError."""
    now = datetime.now(timezone.utc)

    row = {
        "state": "OBSERVE_ONLY",
        "counter": 1,
        "ts_utc": now,
        "armed_until_utc": None,
        "halt_until_utc": None,
        "halt_resume_state": None,
        "state_signature": b"tampered-signature-bytes",
    }
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)

    with pytest.raises(StateSignatureError, match="signature verification failed"):
        await initialise_bot_state(pool, SECRET)
