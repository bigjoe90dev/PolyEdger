"""Risk Manager — limits, MTM, daily stop (spec §16).

Implements:
- Order sizing (max per market, total exposure, position count)
- Conservative and risk MTM marks
- TWAP anti-spoof (outlier rejection)
- wallet_usd_last_good tracking
- Daily stop loss → HALTED_DAILY
"""

from __future__ import annotations

import logging
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

from polyedge.constants import (
    DAILY_STOP_LOSS_PCT,
    MAX_OPEN_POSITIONS,
    MAX_PER_MARKET_PCT,
    MAX_TOTAL_EXPOSURE_PCT,
    MIN_DEPTH_USD_NEAR_TOP,
    MIN_RECONCILE_THRESHOLD_USD,
)

logger = logging.getLogger(__name__)


class RiskManager:
    """Risk management with position limits, MTM, and daily stop."""

    def __init__(
        self,
        wallet_usd: float = 100.0,
        max_per_market_pct: float = MAX_PER_MARKET_PCT,
        max_total_exposure_pct: float = MAX_TOTAL_EXPOSURE_PCT,
        max_open_positions: int = MAX_OPEN_POSITIONS,
        daily_stop_loss_pct: float = DAILY_STOP_LOSS_PCT,
    ) -> None:
        self.wallet_usd_last_good = wallet_usd
        self._wallet_updated_at = time.time()

        self.max_per_market_pct = max_per_market_pct
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_open_positions = max_open_positions
        self.daily_stop_loss_pct = daily_stop_loss_pct

        # Active positions
        self._positions = {}  # type: Dict[str, Dict[str, Any]]
        self._daily_pnl = 0.0

        # TWAP samples for risk MTM
        self._twap_samples = {}  # type: Dict[str, List[Dict[str, Any]]]

    def compute_order_size(
        self,
        market_id: str,
        venue_balance_usd: Optional[float] = None,
    ) -> float:
        """Compute intended order size per spec §16.1."""
        max_per_market = self.max_per_market_pct * self.wallet_usd_last_good
        remaining_capacity = self._remaining_exposure_capacity()

        size = min(max_per_market, remaining_capacity)
        if venue_balance_usd is not None:
            size = min(size, venue_balance_usd)

        return max(0, round(size, 2))

    def can_open_position(self, market_id: str) -> Tuple[bool, str]:
        """Check if a new position can be opened."""

        if len(self._positions) >= self.max_open_positions:
            return False, "RISK_LIMIT_HIT: max positions {} reached".format(self.max_open_positions)

        current_exposure = self._total_exposure()
        if current_exposure >= self.max_total_exposure_pct * self.wallet_usd_last_good:
            return False, "RISK_LIMIT_HIT: max total exposure reached"

        return True, ""

    def _total_exposure(self) -> float:
        """Total open notional at risk."""
        return sum(p.get("notional_usd", 0) for p in self._positions.values())

    def _remaining_exposure_capacity(self) -> float:
        """Remaining exposure before hitting max."""
        max_exposure = self.max_total_exposure_pct * self.wallet_usd_last_good
        return max(0, max_exposure - self._total_exposure())

    def add_position(
        self,
        market_id: str,
        side: str,
        size_usd: float,
        entry_price: float,
    ) -> None:
        """Track a new position."""
        self._positions[market_id] = {
            "side": side,
            "notional_usd": size_usd,
            "entry_price": entry_price,
            "opened_at": time.time(),
        }

    def close_position(self, market_id: str, exit_price: float) -> float:
        """Close a position and return PnL."""
        pos = self._positions.pop(market_id, None)
        if pos is None:
            return 0.0

        if pos["side"] == "YES":
            pnl = (exit_price - pos["entry_price"]) * pos["notional_usd"] / pos["entry_price"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["notional_usd"] / pos["entry_price"]

        self._daily_pnl += pnl
        return pnl

    def conservative_mtm(self, market_id: str, best_bid: Optional[float] = None) -> float:
        """Conservative MTM using best bid."""
        pos = self._positions.get(market_id)
        if pos is None:
            return 0.0
        bid = best_bid if best_bid is not None else 0.0
        return bid * pos["notional_usd"] / max(pos["entry_price"], 0.001)

    def add_twap_sample(
        self,
        market_id: str,
        mid: float,
        spread: float,
        depth_top: float,
    ) -> None:
        """Add a TWAP sample for risk MTM (spec §16.3)."""
        # Validity check
        if spread > 0.10 or depth_top < MIN_DEPTH_USD_NEAR_TOP:
            return  # Invalid sample

        if market_id not in self._twap_samples:
            self._twap_samples[market_id] = []

        self._twap_samples[market_id].append({
            "mid": mid,
            "ts": time.time(),
        })

        # Keep only last 300s
        cutoff = time.time() - 300
        self._twap_samples[market_id] = [
            s for s in self._twap_samples[market_id] if s["ts"] > cutoff
        ]

    def risk_mtm(self, market_id: str) -> Optional[float]:
        """Risk MTM using TWAP with anti-spoof (spec §16.3)."""
        samples = self._twap_samples.get(market_id, [])
        if len(samples) < 3:
            return None

        # Check span >=60s
        times = [s["ts"] for s in samples]
        if max(times) - min(times) < 60:
            return None

        mids = [s["mid"] for s in samples]

        # Outlier rejection if >=10 samples
        if len(mids) >= 10:
            mean = statistics.mean(mids)
            stdev = statistics.stdev(mids)
            if stdev > 0:
                mids = [m for m in mids if abs(m - mean) <= 2 * stdev]

        if not mids:
            return None

        return statistics.median(mids)

    def check_daily_stop(self) -> bool:
        """Check if daily stop loss is hit per spec §16.5.

        Returns True if HALTED_DAILY should be triggered.
        """
        threshold = -self.daily_stop_loss_pct * self.wallet_usd_last_good
        return self._daily_pnl <= threshold

    def is_wallet_stale(self, max_stale_sec: float = 3600) -> bool:
        """Check if wallet_usd_last_good is stale."""
        return (time.time() - self._wallet_updated_at) > max_stale_sec

    def update_wallet(self, wallet_usd: float) -> None:
        """Update wallet reference value."""
        self.wallet_usd_last_good = wallet_usd
        self._wallet_updated_at = time.time()

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "wallet_usd": self.wallet_usd_last_good,
            "open_positions": len(self._positions),
            "total_exposure": self._total_exposure(),
            "daily_pnl": self._daily_pnl,
            "daily_stop_threshold": -self.daily_stop_loss_pct * self.wallet_usd_last_good,
        }
