"""Snapshot creation, hashing, and storage (spec §7.2).

Builds immutable snapshot records from WS book data.
Implements:
- Canonical orderbook JSON for deterministic hashing
- SHA-256 orderbook_hash
- ask_sum_anomaly and invalid_book_anomaly flags
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, List, Optional

from polyedge.constants import ASK_SUM_HIGH, ASK_SUM_LOW, BOOK_LEVELS_REQUIRED

logger = logging.getLogger(__name__)


class Snapshot:
    """Immutable orderbook snapshot per spec §7.2."""

    def __init__(
        self,
        snapshot_id: str,
        market_id: str,
        snapshot_at_unix_ms: int,
        snapshot_source: str,
        snapshot_ws_epoch: int,
        ws_last_message_unix_ms: int,
        market_last_ws_update_unix_ms: Optional[int],
        orderbook_last_change_unix_ms: Optional[int],
        best_bid_yes: Optional[float],
        best_ask_yes: Optional[float],
        best_bid_no: Optional[float],
        best_ask_no: Optional[float],
        depth_yes: List[List[float]],
        depth_no: List[List[float]],
        orderbook_hash: bytes,
        ask_sum_anomaly: bool,
        invalid_book_anomaly: bool,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.market_id = market_id
        self.snapshot_at_unix_ms = snapshot_at_unix_ms
        self.snapshot_source = snapshot_source
        self.snapshot_ws_epoch = snapshot_ws_epoch
        self.ws_last_message_unix_ms = ws_last_message_unix_ms
        self.market_last_ws_update_unix_ms = market_last_ws_update_unix_ms
        self.orderbook_last_change_unix_ms = orderbook_last_change_unix_ms
        self.best_bid_yes = best_bid_yes
        self.best_ask_yes = best_ask_yes
        self.best_bid_no = best_bid_no
        self.best_ask_no = best_ask_no
        self.depth_yes = depth_yes
        self.depth_no = depth_no
        self.orderbook_hash = orderbook_hash
        self.ask_sum_anomaly = ask_sum_anomaly
        self.invalid_book_anomaly = invalid_book_anomaly


def canonical_orderbook_json(
    best_bid_yes: Optional[float],
    best_ask_yes: Optional[float],
    best_bid_no: Optional[float],
    best_ask_no: Optional[float],
    depth_yes: List[List[float]],
    depth_no: List[List[float]],
) -> str:
    """Build deterministic canonical JSON for orderbook hashing.

    Keys sorted, ASCII-safe, no trailing whitespace.
    Floats serialised to 6 decimal places for determinism.
    """

    def _fmt(v: Optional[float]) -> Optional[str]:
        if v is None:
            return None
        return "{:.6f}".format(v)

    def _fmt_levels(levels: List[List[float]]) -> List[List[str]]:
        return [["{:.6f}".format(p), "{:.2f}".format(s)] for p, s in levels]

    obj = {
        "best_ask_no": _fmt(best_ask_no),
        "best_ask_yes": _fmt(best_ask_yes),
        "best_bid_no": _fmt(best_bid_no),
        "best_bid_yes": _fmt(best_bid_yes),
        "depth_no": _fmt_levels(depth_no),
        "depth_yes": _fmt_levels(depth_yes),
    }

    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def compute_orderbook_hash(canonical_json: str) -> bytes:
    """SHA-256 hash of canonical orderbook JSON."""
    return hashlib.sha256(canonical_json.encode("utf-8")).digest()


def detect_ask_sum_anomaly(
    best_ask_yes: Optional[float],
    best_ask_no: Optional[float],
) -> bool:
    """Check binary consistency anomaly per spec §7.2.

    True if (best_ask_yes + best_ask_no) < ASK_SUM_LOW or > ASK_SUM_HIGH.
    """
    if best_ask_yes is None or best_ask_no is None:
        return True  # Missing asks is anomalous
    ask_sum = best_ask_yes + best_ask_no
    return ask_sum < ASK_SUM_LOW or ask_sum > ASK_SUM_HIGH


def detect_invalid_book_anomaly(
    best_bid_yes: Optional[float],
    best_ask_yes: Optional[float],
    best_bid_no: Optional[float],
    best_ask_no: Optional[float],
) -> bool:
    """Check invalid book conditions per spec §7.2.

    True if any:
    - any price <= 0 or >= 1
    - bid > ask on any side
    - missing best bid or best ask on either token
    """
    prices = [best_bid_yes, best_ask_yes, best_bid_no, best_ask_no]

    # Missing best bid or best ask on either token
    if any(p is None for p in prices):
        return True

    # Type narrowing after None check
    bb_y = best_bid_yes  # type: float  # type: ignore[assignment]
    ba_y = best_ask_yes  # type: float  # type: ignore[assignment]
    bb_n = best_bid_no  # type: float  # type: ignore[assignment]
    ba_n = best_ask_no  # type: float  # type: ignore[assignment]

    # Any price <= 0 or >= 1
    for p in [bb_y, ba_y, bb_n, ba_n]:
        if p <= 0 or p >= 1:
            return True

    # Bid > ask on any side
    if bb_y > ba_y:
        return True
    if bb_n > ba_n:
        return True

    return False


def create_snapshot(
    market_id: str,
    book_data: dict,
    snapshot_source: str = "WS",
) -> Snapshot:
    """Create an immutable snapshot from WS book data.

    book_data must contain keys matching the output of
    OrderbookWSClient.process_book_message().
    """
    best_bid_yes = book_data.get("best_bid_yes")
    best_ask_yes = book_data.get("best_ask_yes")
    best_bid_no = book_data.get("best_bid_no")
    best_ask_no = book_data.get("best_ask_no")
    depth_yes = book_data.get("depth_yes", [])
    depth_no = book_data.get("depth_no", [])

    canonical = canonical_orderbook_json(
        best_bid_yes, best_ask_yes, best_bid_no, best_ask_no,
        depth_yes, depth_no,
    )
    ob_hash = compute_orderbook_hash(canonical)

    ask_anomaly = detect_ask_sum_anomaly(best_ask_yes, best_ask_no)
    book_anomaly = detect_invalid_book_anomaly(
        best_bid_yes, best_ask_yes, best_bid_no, best_ask_no,
    )

    return Snapshot(
        snapshot_id=str(uuid.uuid4()),
        market_id=market_id,
        snapshot_at_unix_ms=int(time.time() * 1000),
        snapshot_source=snapshot_source,
        snapshot_ws_epoch=book_data.get("snapshot_ws_epoch", 0),
        ws_last_message_unix_ms=book_data.get("ws_last_message_unix_ms", 0),
        market_last_ws_update_unix_ms=book_data.get("market_last_ws_update_unix_ms"),
        orderbook_last_change_unix_ms=book_data.get("orderbook_last_change_unix_ms"),
        best_bid_yes=best_bid_yes,
        best_ask_yes=best_ask_yes,
        best_bid_no=best_bid_no,
        best_ask_no=best_ask_no,
        depth_yes=depth_yes,
        depth_no=depth_no,
        orderbook_hash=ob_hash,
        ask_sum_anomaly=ask_anomaly,
        invalid_book_anomaly=book_anomaly,
    )


async def store_snapshot(pool: Any, snap: Snapshot) -> None:
    """Insert a snapshot into the DB."""
    await pool.execute(
        """
        INSERT INTO snapshots (
            snapshot_id, market_id, snapshot_at_unix_ms, snapshot_source,
            snapshot_ws_epoch, ws_last_message_unix_ms,
            market_last_ws_update_unix_ms, orderbook_last_change_unix_ms,
            best_bid_yes, best_ask_yes, best_bid_no, best_ask_no,
            depth_yes, depth_no, orderbook_hash,
            ask_sum_anomaly, invalid_book_anomaly
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        """,
        uuid.UUID(snap.snapshot_id),
        snap.market_id,
        snap.snapshot_at_unix_ms,
        snap.snapshot_source,
        snap.snapshot_ws_epoch,
        snap.ws_last_message_unix_ms,
        snap.market_last_ws_update_unix_ms,
        snap.orderbook_last_change_unix_ms,
        snap.best_bid_yes,
        snap.best_ask_yes,
        snap.best_bid_no,
        snap.best_ask_no,
        json.dumps(snap.depth_yes),
        json.dumps(snap.depth_no),
        snap.orderbook_hash,
        snap.ask_sum_anomaly,
        snap.invalid_book_anomaly,
    )
