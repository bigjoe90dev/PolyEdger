"""Execution Engine — PAPER mode implementation (spec §17).

Phase 7: PAPER execution with pessimistic fills.
Phase 8: LIVE execution will be added later.

PAPER mode per spec §17.3:
- No "touch = fill": trade-through by >=1 tick for >=3s
- Fees: max(actual_fee_bps, PAPER_MIN_FEE_BPS) × PAPER_FEE_MULTIPLIER
- Pessimistic fill simulation
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from polyedge.constants import (
    MAX_DECISION_TO_EXEC_DELAY_SEC,
    PAPER_FEE_MULTIPLIER,
    PAPER_MIN_FEE_BPS,
)

logger = logging.getLogger(__name__)


class ExecutionDisabledError(Exception):
    """Raised when execution is attempted in a disabled mode."""


class PaperFillTracker:
    """Track potential fills for PAPER mode pessimistic fill logic.

    A PAPER fill is only triggered when price trades THROUGH the
    limit by >= 1 tick and sustains for >= 3 seconds.
    """

    TICK_SIZE = 0.01  # Polymarket tick
    SUSTAIN_SEC = 3.0

    def __init__(self) -> None:
        self._pending = {}  # type: Dict[str, Dict[str, Any]]

    def check_fill(
        self,
        order_id: str,
        side: str,
        limit_price: float,
        current_price: float,
    ) -> Optional[Dict[str, Any]]:
        """Check if a pending order should be paper-filled.

        Returns fill dict if fill occurs, None otherwise.
        """
        through = False
        if side == "YES":
            # Buy YES at limit_price: fill when best_ask drops through
            through = current_price <= limit_price - self.TICK_SIZE
        else:
            # Buy NO at limit_price: fill when best_ask drops through
            through = current_price <= limit_price - self.TICK_SIZE

        key = order_id

        if through:
            if key not in self._pending:
                self._pending[key] = {
                    "first_through_at": time.time(),
                    "through_price": current_price,
                }

            entry = self._pending[key]
            elapsed = time.time() - entry["first_through_at"]

            if elapsed >= self.SUSTAIN_SEC:
                # Fill confirmed
                self._pending.pop(key, None)
                return {
                    "order_id": order_id,
                    "fill_price": limit_price,  # Pessimistic: fill at limit
                    "fill_time": time.time(),
                    "through_price": entry["through_price"],
                }
        else:
            # Price pulled back above limit: reset
            self._pending.pop(key, None)

        return None


class PaperExecutionEngine:
    """PAPER execution engine (spec §17.3)."""

    def __init__(self) -> None:
        self._fill_tracker = PaperFillTracker()
        self._orders = {}  # type: Dict[str, Dict[str, Any]]
        self._fills = []  # type: List[Dict[str, Any]]
        self._total_fees_usd = 0.0

    def submit_order(
        self,
        client_order_id: str,
        market_id: str,
        side: str,
        size_usd: float,
        limit_price: float,
        decision_id_hex: str,
    ) -> Dict[str, Any]:
        """Submit a PAPER order."""
        order = {
            "client_order_id": client_order_id,
            "market_id": market_id,
            "side": side,
            "size_usd": size_usd,
            "limit_price": limit_price,
            "decision_id_hex": decision_id_hex,
            "status": "OPEN",
            "submitted_at": time.time(),
            "fills": [],
        }

        self._orders[client_order_id] = order
        logger.info(
            "PAPER order submitted: id=%s market=%s side=%s size=%.2f price=%.4f",
            client_order_id, market_id, side, size_usd, limit_price,
        )
        return order

    def process_book_update(
        self,
        market_id: str,
        best_ask: float,
        best_bid: float,
    ) -> List[Dict[str, Any]]:
        """Process a book update; check for PAPER fills.

        Returns list of fills that occurred.
        """
        new_fills = []  # type: List[Dict[str, Any]]

        for order_id, order in list(self._orders.items()):
            if order["market_id"] != market_id or order["status"] != "OPEN":
                continue

            current_price = best_ask  # Use ask for buys

            fill = self._fill_tracker.check_fill(
                order_id, order["side"], order["limit_price"], current_price,
            )

            if fill:
                # Apply pessimistic fees
                fee_bps = max(PAPER_MIN_FEE_BPS, 0) * PAPER_FEE_MULTIPLIER
                fee_usd = order["size_usd"] * (fee_bps / 10000.0)
                fill["fee_usd"] = fee_usd

                order["fills"].append(fill)
                order["status"] = "FILLED"
                self._fills.append(fill)
                self._total_fees_usd += fee_usd
                new_fills.append(fill)

                logger.info(
                    "PAPER fill: order=%s price=%.4f fee=%.4f",
                    order_id, fill["fill_price"], fee_usd,
                )

        return new_fills

    def cancel_order(self, client_order_id: str) -> bool:
        """Cancel a PAPER order."""
        order = self._orders.get(client_order_id)
        if order and order["status"] == "OPEN":
            order["status"] = "CANCELLED"
            return True
        return False

    @property
    def open_orders(self) -> List[Dict[str, Any]]:
        return [o for o in self._orders.values() if o["status"] == "OPEN"]

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_orders": len(self._orders),
            "open_orders": len(self.open_orders),
            "total_fills": len(self._fills),
            "total_fees_usd": self._total_fees_usd,
        }
