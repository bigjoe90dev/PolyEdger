"""Bot durable state management (spec §5.1, §5.2, §5.4).

Manages the singleton bot_state row with HMAC signature verification.
On startup, forces OBSERVE_ONLY if state was LIVE_ARMED or LIVE_TRADING.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from polyedge.constants import VALID_STATES

logger = logging.getLogger(__name__)


class StateSignatureError(Exception):
    """Raised when bot_state signature verification fails."""


class InvalidStateTransition(Exception):
    """Raised on an illegal state transition."""


def _compute_state_signature(
    state: str,
    counter: int,
    ts_utc: str,
    secret: str,
) -> bytes:
    """HMAC-SHA256 signature over canonical state fields."""
    canonical = "state={}|counter={}|ts_utc={}".format(state, counter, ts_utc)
    return hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).digest()


class BotState:
    """Manages the singleton durable bot state."""

    def __init__(
        self,
        state: str,
        counter: int,
        ts_utc: datetime,
        armed_until_utc: Optional[datetime] = None,
        halt_until_utc: Optional[datetime] = None,
        halt_resume_state: Optional[str] = None,
        state_signature: bytes = b"",
    ) -> None:
        if state not in VALID_STATES:
            raise ValueError("Invalid state: {}".format(state))
        self.state = state
        self.counter = counter
        self.ts_utc = ts_utc
        self.armed_until_utc = armed_until_utc
        self.halt_until_utc = halt_until_utc
        self.halt_resume_state = halt_resume_state
        self.state_signature = state_signature

    def verify_signature(self, secret: str) -> bool:
        """Verify the HMAC signature. Returns True if valid."""
        expected = _compute_state_signature(
            self.state,
            self.counter,
            self.ts_utc.isoformat(),
            secret,
        )
        return hmac.compare_digest(self.state_signature, expected)

    def sign(self, secret: str) -> None:
        """Compute and set the HMAC signature."""
        self.state_signature = _compute_state_signature(
            self.state,
            self.counter,
            self.ts_utc.isoformat(),
            secret,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for logging."""
        return {
            "state": self.state,
            "counter": self.counter,
            "ts_utc": self.ts_utc.isoformat(),
            "armed_until_utc": self.armed_until_utc.isoformat() if self.armed_until_utc else None,
            "halt_until_utc": self.halt_until_utc.isoformat() if self.halt_until_utc else None,
            "halt_resume_state": self.halt_resume_state,
        }


async def load_bot_state(pool: Any) -> Optional[BotState]:
    """Load the bot_state singleton row from DB.  Returns None if not yet initialised."""
    row = await pool.fetchrow("SELECT * FROM bot_state WHERE id = TRUE")
    if row is None:
        return None
    return BotState(
        state=row["state"],
        counter=row["counter"],
        ts_utc=row["ts_utc"],
        armed_until_utc=row["armed_until_utc"],
        halt_until_utc=row["halt_until_utc"],
        halt_resume_state=row["halt_resume_state"],
        state_signature=bytes(row["state_signature"]),
    )


async def save_bot_state(pool: Any, bs: BotState) -> None:
    """Upsert the bot_state singleton row."""
    await pool.execute(
        """
        INSERT INTO bot_state (id, state, counter, ts_utc, armed_until_utc,
                               halt_until_utc, halt_resume_state, state_signature)
        VALUES (TRUE, $1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (id) DO UPDATE SET
            state = EXCLUDED.state,
            counter = EXCLUDED.counter,
            ts_utc = EXCLUDED.ts_utc,
            armed_until_utc = EXCLUDED.armed_until_utc,
            halt_until_utc = EXCLUDED.halt_until_utc,
            halt_resume_state = EXCLUDED.halt_resume_state,
            state_signature = EXCLUDED.state_signature
        """,
        bs.state,
        bs.counter,
        bs.ts_utc,
        bs.armed_until_utc,
        bs.halt_until_utc,
        bs.halt_resume_state,
        bs.state_signature,
    )


async def initialise_bot_state(pool: Any, secret: str) -> BotState:
    """Load existing state or create initial OBSERVE_ONLY state.

    Returns the (possibly force-downgraded) BotState.
    """
    bs = await load_bot_state(pool)

    if bs is None:
        # First run: create OBSERVE_ONLY
        now = datetime.now(timezone.utc)
        bs = BotState(state="OBSERVE_ONLY", counter=1, ts_utc=now)
        bs.sign(secret)
        await save_bot_state(pool, bs)
        logger.info("Bot state initialised: OBSERVE_ONLY")
        return bs

    # Verify signature (spec §5.4 step 4)
    if not bs.verify_signature(secret):
        raise StateSignatureError(
            "Bot state signature verification failed — possible tampering. HALTED."
        )

    # Force downgrade on startup (spec §5.4 step 5)
    if bs.state in ("LIVE_ARMED", "LIVE_TRADING"):
        old_state = bs.state
        bs.state = "OBSERVE_ONLY"
        bs.counter += 1
        bs.ts_utc = datetime.now(timezone.utc)
        bs.armed_until_utc = None
        bs.sign(secret)
        await save_bot_state(pool, bs)
        logger.warning(
            "Startup force-downgrade: %s -> OBSERVE_ONLY (spec §5.4 step 5)",
            old_state,
        )

    return bs
