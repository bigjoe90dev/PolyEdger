"""WebSocket client for Polymarket orderbook data (spec §7).

Tracks per-connection and per-market timestamps:
- ws_connected, ws_last_message_unix_ms, current_ws_epoch (global)
- market_last_ws_update_unix_ms, orderbook_last_change_unix_ms (per market)

Supports mock mode for testing without credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class MarketWSState:
    """Per-market WebSocket tracking."""

    def __init__(self, market_id: str) -> None:
        self.market_id = market_id
        self.market_last_ws_update_unix_ms = 0
        self.orderbook_last_change_unix_ms = 0
        self.last_best_bid_yes = None  # type: Optional[float]
        self.last_best_ask_yes = None  # type: Optional[float]
        self.last_best_bid_no = None  # type: Optional[float]
        self.last_best_ask_no = None  # type: Optional[float]
        self.last_depth_yes = None  # type: Optional[List[List[float]]]
        self.last_depth_no = None  # type: Optional[List[List[float]]]


class WSState:
    """Global WebSocket connection state."""

    def __init__(self) -> None:
        self.ws_connected = False
        self.ws_last_message_unix_ms = 0
        self.current_ws_epoch = 0
        self.markets = {}  # type: Dict[str, MarketWSState]

    def ensure_market(self, market_id: str) -> MarketWSState:
        """Get or create per-market state."""
        if market_id not in self.markets:
            self.markets[market_id] = MarketWSState(market_id=market_id)
        return self.markets[market_id]


def _now_ms() -> int:
    """Current UTC time in milliseconds."""
    return int(time.time() * 1000)


def _books_changed(
    mstate: MarketWSState,
    best_bid_yes: Optional[float],
    best_ask_yes: Optional[float],
    best_bid_no: Optional[float],
    best_ask_no: Optional[float],
    depth_yes: Optional[List[List[float]]],
    depth_no: Optional[List[List[float]]],
    book_levels: int = 3,
) -> bool:
    """Check if best bid/ask or top N depth levels changed."""
    if (
        mstate.last_best_bid_yes != best_bid_yes
        or mstate.last_best_ask_yes != best_ask_yes
        or mstate.last_best_bid_no != best_bid_no
        or mstate.last_best_ask_no != best_ask_no
    ):
        return True

    # Compare top levels
    old_dy = (mstate.last_depth_yes or [])[:book_levels]
    new_dy = (depth_yes or [])[:book_levels]
    old_dn = (mstate.last_depth_no or [])[:book_levels]
    new_dn = (depth_no or [])[:book_levels]

    return old_dy != new_dy or old_dn != new_dn


class OrderbookWSClient:
    """WebSocket client for Polymarket orderbook subscriptions.

    Manages connection lifecycle, epoch tracking, and per-market state.
    """

    def __init__(
        self,
        ws_url: str = DEFAULT_WS_URL,
        mock_mode: bool = False,
        on_book_update: Optional[Callable[..., Coroutine[Any, Any, None]]] = None,
    ) -> None:
        self.ws_url = ws_url
        self.mock_mode = mock_mode
        self.state = WSState()
        self._on_book_update = on_book_update
        self._subscribed_markets = set()  # type: Set[str]
        self._running = False
        self._ws = None  # type: Any

    async def connect(self) -> None:
        """Establish WS connection."""
        if self.mock_mode:
            self.state.ws_connected = True
            self.state.current_ws_epoch += 1
            self.state.ws_last_message_unix_ms = _now_ms()
            logger.info("Mock WS connected (epoch=%d)", self.state.current_ws_epoch)
            return

        import websockets  # type: ignore[import-untyped]

        logger.info("Connecting to WS: %s", self.ws_url)
        self._ws = await websockets.connect(self.ws_url, ping_interval=10, ping_timeout=5)
        self.state.ws_connected = True
        self.state.current_ws_epoch += 1
        self.state.ws_last_message_unix_ms = _now_ms()
        logger.info("WS connected (epoch=%d)", self.state.current_ws_epoch)

    async def disconnect(self) -> None:
        """Close WS connection and increment epoch."""
        self.state.ws_connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Epoch increments on disconnect per spec
        self.state.current_ws_epoch += 1
        logger.info("WS disconnected (epoch=%d)", self.state.current_ws_epoch)

    async def subscribe(self, market_ids: List[str]) -> None:
        """Subscribe to orderbook updates for given markets."""
        for mid in market_ids:
            self.state.ensure_market(mid)
            self._subscribed_markets.add(mid)

        if self.mock_mode:
            logger.info("Mock subscribed to %d markets", len(market_ids))
            return

        if self._ws is None:
            raise RuntimeError("WS not connected")

        # Send subscription messages
        for mid in market_ids:
            msg = json.dumps({
                "type": "subscribe",
                "channel": "market",
                "market": mid,
            }, sort_keys=True)
            await self._ws.send(msg)

        logger.info("Subscribed to %d markets", len(market_ids))

    def process_book_message(
        self,
        market_id: str,
        best_bid_yes: Optional[float],
        best_ask_yes: Optional[float],
        best_bid_no: Optional[float],
        best_ask_no: Optional[float],
        depth_yes: Optional[List[List[float]]] = None,
        depth_no: Optional[List[List[float]]] = None,
    ) -> Dict[str, Any]:
        """Process an orderbook update and update tracking state.

        Returns the current market state dict for snapshot creation.
        """
        now_ms = _now_ms()
        self.state.ws_last_message_unix_ms = now_ms

        mstate = self.state.ensure_market(market_id)
        mstate.market_last_ws_update_unix_ms = now_ms

        # Check if book changed
        if _books_changed(
            mstate, best_bid_yes, best_ask_yes, best_bid_no, best_ask_no,
            depth_yes, depth_no,
        ):
            mstate.orderbook_last_change_unix_ms = now_ms

        # Update stored values
        mstate.last_best_bid_yes = best_bid_yes
        mstate.last_best_ask_yes = best_ask_yes
        mstate.last_best_bid_no = best_bid_no
        mstate.last_best_ask_no = best_ask_no
        mstate.last_depth_yes = depth_yes
        mstate.last_depth_no = depth_no

        return {
            "market_id": market_id,
            "best_bid_yes": best_bid_yes,
            "best_ask_yes": best_ask_yes,
            "best_bid_no": best_bid_no,
            "best_ask_no": best_ask_no,
            "depth_yes": depth_yes or [],
            "depth_no": depth_no or [],
            "ws_last_message_unix_ms": self.state.ws_last_message_unix_ms,
            "market_last_ws_update_unix_ms": mstate.market_last_ws_update_unix_ms,
            "orderbook_last_change_unix_ms": mstate.orderbook_last_change_unix_ms,
            "snapshot_ws_epoch": self.state.current_ws_epoch,
        }

    async def run_mock_loop(self, duration_sec: float = 10.0) -> List[Dict[str, Any]]:
        """Run a mock data loop for testing. Returns generated book updates."""
        import random

        results = []  # type: List[Dict[str, Any]]
        start = time.time()

        while time.time() - start < duration_sec:
            for mid in self._subscribed_markets:
                # Generate realistic-ish mock data
                mid_price = random.uniform(0.20, 0.80)
                spread = random.uniform(0.005, 0.02)
                bid_yes = round(mid_price - spread / 2, 4)
                ask_yes = round(mid_price + spread / 2, 4)
                bid_no = round(1.0 - ask_yes, 4)
                ask_no = round(1.0 - bid_yes, 4)

                depth_yes = [
                    [round(bid_yes - i * 0.01, 4), round(random.uniform(50, 500), 2)]
                    for i in range(3)
                ]
                depth_no = [
                    [round(bid_no - i * 0.01, 4), round(random.uniform(50, 500), 2)]
                    for i in range(3)
                ]

                book_data = self.process_book_message(
                    mid, bid_yes, ask_yes, bid_no, ask_no, depth_yes, depth_no,
                )
                results.append(book_data)

                if self._on_book_update:
                    await self._on_book_update(book_data)

            await asyncio.sleep(0.5)

        return results

    async def run(self, duration_sec: Optional[float] = None) -> None:
        """Run the WS client loop.

        In mock mode, generates synthetic data. In live mode, processes real WS messages.
        """
        self._running = True

        if self.mock_mode:
            await self.run_mock_loop(duration_sec or 10.0)
            return

        if self._ws is None:
            raise RuntimeError("WS not connected — call connect() first")

        import websockets

        start = time.time()
        try:
            async for raw_msg in self._ws:
                if not self._running:
                    break
                if duration_sec and (time.time() - start) >= duration_sec:
                    break

                try:
                    msg = json.loads(raw_msg)
                    self._handle_message(msg)
                except json.JSONDecodeError:
                    logger.warning("WS: non-JSON message received")
                except Exception as e:
                    logger.error("WS message processing error: %s", e)

        except websockets.ConnectionClosed:
            logger.warning("WS connection closed")
        finally:
            await self.disconnect()

    def _handle_message(self, msg: Dict[str, Any]) -> None:
        """Parse and route a WS message."""
        market_id = msg.get("market", msg.get("asset_id", ""))
        if not market_id:
            return

        # Extract book data from message (format depends on venue)
        bids = msg.get("bids", [])
        asks = msg.get("asks", [])

        if not bids and not asks:
            # Heartbeat or other non-book message — still update last_message
            self.state.ws_last_message_unix_ms = _now_ms()
            return

        # Parse YES side
        best_bid_yes = float(bids[0]["price"]) if bids else None
        best_ask_yes = float(asks[0]["price"]) if asks else None

        depth_yes = [
            [float(level["price"]), float(level.get("size", 0))]
            for level in (bids[:3] + asks[:3])
        ] if bids or asks else None

        # For binary markets, NO side is the complement
        best_bid_no = round(1.0 - float(asks[0]["price"]), 6) if asks else None
        best_ask_no = round(1.0 - float(bids[0]["price"]), 6) if bids else None

        depth_no = None  # Simplified; production would parse full NO book

        self.process_book_message(
            market_id, best_bid_yes, best_ask_yes, best_bid_no, best_ask_no,
            depth_yes, depth_no,
        )

    def stop(self) -> None:
        """Signal the run loop to stop."""
        self._running = False
