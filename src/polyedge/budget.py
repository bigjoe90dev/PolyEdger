"""AI Budget Manager — atomic reservations, settlement, reaper (spec §13).

Implements:
- Budget reservation with SERIALIZABLE isolation semantics
- Daily and rolling-window cap enforcement
- Idempotent settlement (compare-and-swap)
- Reaper for expired reservations (force-settle)
- Analysis count cap per UTC day
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from polyedge.constants import (
    AI_ANALYSES_PER_DAY_HARD_CAP,
    AI_CAP_PCT_PER_DAY_DEFAULT,
    AI_CAP_USD_USER,
    AI_WINDOW_CAP_PCT_OF_DAILY,
    AI_WINDOW_SEC,
)

logger = logging.getLogger(__name__)

# Reservation status enum (spec §13.2)
STATUS_RESERVED = "RESERVED"
STATUS_SETTLED = "SETTLED"
STATUS_FORCE_SETTLED = "FORCE_SETTLED"
STATUS_RELEASED = "RELEASED"

# Default reservation expiry
DEFAULT_RESERVATION_EXPIRY_SEC = 120

# Force-settle threshold for COST_ACCOUNTING_DEGRADED
FORCE_SETTLE_DEGRADED_THRESHOLD = 3


def compute_daily_cap(wallet_usd: float) -> float:
    """Compute effective daily AI cap per spec §3.3.

    AI_CAP_USD_EFFECTIVE = min(AI_CAP_USD_USER, wallet_usd * AI_CAP_PCT_PER_DAY_DEFAULT)
    """
    return min(AI_CAP_USD_USER, wallet_usd * AI_CAP_PCT_PER_DAY_DEFAULT)


def compute_window_cap(daily_cap: float) -> float:
    """Compute rolling window cap.

    AI_WINDOW_CAP_USD = AI_CAP_USD_EFFECTIVE * AI_WINDOW_CAP_PCT_OF_DAILY
    """
    return daily_cap * AI_WINDOW_CAP_PCT_OF_DAILY


class BudgetDeniedError(Exception):
    """Raised when AI budget reservation is denied."""


class BudgetManager:
    """In-memory budget manager for testing.

    Production uses DB with SERIALIZABLE transactions.
    This in-memory version enforces all the same invariants.
    """

    def __init__(self, wallet_usd: float = 100.0) -> None:
        self.wallet_usd = wallet_usd
        self.daily_cap = compute_daily_cap(wallet_usd)
        self.window_cap = compute_window_cap(self.daily_cap)

        # Day tracking
        self._today = datetime.now(timezone.utc).date()
        self._spent_usd = 0.0
        self._in_flight_usd = 0.0

        # Reservations
        self._reservations = {}  # type: Dict[str, Dict[str, Any]]

        # Tracking
        self._correlation_ids_today = set()  # type: set
        self._force_settle_count_today = 0

    def _check_day_rollover(self) -> None:
        """Reset counters if day has changed."""
        today = datetime.now(timezone.utc).date()
        if today != self._today:
            self._today = today
            self._spent_usd = 0.0
            self._in_flight_usd = 0.0
            self._correlation_ids_today.clear()
            self._force_settle_count_today = 0
            # Clear settled/force-settled reservations from previous day
            to_remove = [
                rid for rid, r in self._reservations.items()
                if r["status"] != STATUS_RESERVED
            ]
            for rid in to_remove:
                del self._reservations[rid]

    def _window_sum(self) -> float:
        """Sum of in-flight (RESERVED) costs in the rolling window.

        Per spec §13.3: window check is separate from daily.
        Only RESERVED items count towards window pressure since
        settled items are already tracked in spent_usd.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=AI_WINDOW_SEC)

        total = 0.0
        for r in self._reservations.values():
            if r["status"] == STATUS_RESERVED:
                ts = r["ts_utc"]
                if ts >= cutoff:
                    total += r["reserved_usd"]
        return total

    def reserve(
        self,
        model_key: str,
        worst_case_usd: float,
        correlation_id: str,
    ) -> str:
        """Reserve budget for an AI call.

        Returns reservation_id on success.
        Raises BudgetDeniedError if any cap is exceeded.
        """
        self._check_day_rollover()
        now = datetime.now(timezone.utc)

        # Check daily cap
        if self._spent_usd + self._in_flight_usd + worst_case_usd > self.daily_cap:
            raise BudgetDeniedError(
                "Daily cap exceeded: spent={:.4f} in_flight={:.4f} requested={:.4f} cap={:.4f}".format(
                    self._spent_usd, self._in_flight_usd, worst_case_usd, self.daily_cap,
                )
            )

        # Check window cap
        window_sum = self._window_sum()
        if window_sum + worst_case_usd > self.window_cap:
            raise BudgetDeniedError(
                "Window cap exceeded: window_sum={:.4f} requested={:.4f} cap={:.4f}".format(
                    window_sum, worst_case_usd, self.window_cap,
                )
            )

        # Check analysis count cap
        if len(self._correlation_ids_today) >= AI_ANALYSES_PER_DAY_HARD_CAP:
            # Only count if this is a new correlation_id
            if correlation_id not in self._correlation_ids_today:
                raise BudgetDeniedError(
                    "Analysis count cap exceeded: {} >= {}".format(
                        len(self._correlation_ids_today), AI_ANALYSES_PER_DAY_HARD_CAP,
                    )
                )

        # Create reservation
        reservation_id = str(uuid.uuid4())
        expires_at = now + timedelta(seconds=DEFAULT_RESERVATION_EXPIRY_SEC)

        self._reservations[reservation_id] = {
            "reservation_id": reservation_id,
            "model_key": model_key,
            "reserved_usd": worst_case_usd,
            "actual_usd": None,
            "status": STATUS_RESERVED,
            "correlation_id": correlation_id,
            "ts_utc": now,
            "expires_at": expires_at,
        }

        self._in_flight_usd += worst_case_usd
        self._correlation_ids_today.add(correlation_id)

        logger.debug(
            "Budget reserved: id=%s model=%s usd=%.4f",
            reservation_id, model_key, worst_case_usd,
        )
        return reservation_id

    def settle(self, reservation_id: str, actual_usd: Optional[float] = None) -> bool:
        """Settle a reservation (idempotent compare-and-swap).

        Returns True if settlement succeeded, False if already final.
        """
        self._check_day_rollover()

        r = self._reservations.get(reservation_id)
        if r is None:
            logger.warning("Settle: reservation %s not found", reservation_id)
            return False

        if r["status"] != STATUS_RESERVED:
            logger.info("Settle: reservation %s already %s (idempotent)", reservation_id, r["status"])
            return False

        cost = actual_usd if actual_usd is not None else r["reserved_usd"]

        r["status"] = STATUS_SETTLED
        r["actual_usd"] = cost

        self._in_flight_usd -= r["reserved_usd"]
        self._spent_usd += cost

        logger.debug(
            "Budget settled: id=%s actual=%.4f",
            reservation_id, cost,
        )
        return True

    def release(self, reservation_id: str) -> bool:
        """Release a reservation without spending (cancel).

        Returns True if release succeeded.
        """
        r = self._reservations.get(reservation_id)
        if r is None or r["status"] != STATUS_RESERVED:
            return False

        r["status"] = STATUS_RELEASED
        self._in_flight_usd -= r["reserved_usd"]

        logger.debug("Budget released: id=%s", reservation_id)
        return True

    def reap_expired(self) -> int:
        """Force-settle expired reservations (reaper, spec §13.5).

        Returns count of force-settled reservations.
        """
        self._check_day_rollover()
        now = datetime.now(timezone.utc)
        grace = timedelta(seconds=5)
        count = 0

        for r in list(self._reservations.values()):
            if r["status"] == STATUS_RESERVED and r["expires_at"] < now - grace:
                r["status"] = STATUS_FORCE_SETTLED
                r["actual_usd"] = r["reserved_usd"]

                self._in_flight_usd -= r["reserved_usd"]
                self._spent_usd += r["reserved_usd"]

                count += 1
                self._force_settle_count_today += 1

                logger.warning(
                    "Budget force-settled: id=%s model=%s usd=%.4f",
                    r["reservation_id"], r["model_key"], r["reserved_usd"],
                )

        return count

    @property
    def is_degraded(self) -> bool:
        """Check if cost accounting is degraded (≥3 force-settles in LIVE/day)."""
        return self._force_settle_count_today >= FORCE_SETTLE_DEGRADED_THRESHOLD

    @property
    def stats(self) -> Dict[str, Any]:
        """Current budget stats."""
        self._check_day_rollover()
        return {
            "daily_cap": self.daily_cap,
            "window_cap": self.window_cap,
            "spent_usd": self._spent_usd,
            "in_flight_usd": self._in_flight_usd,
            "remaining_daily": self.daily_cap - self._spent_usd - self._in_flight_usd,
            "window_sum": self._window_sum(),
            "analyses_today": len(self._correlation_ids_today),
            "force_settles_today": self._force_settle_count_today,
            "is_degraded": self.is_degraded,
        }
