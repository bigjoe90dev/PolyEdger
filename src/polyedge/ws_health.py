"""WS health predicates (spec §7.3).

Two predicates with different staleness thresholds:
- WS_HEALTHY_DECISION: for decision-making (6s window)
- WS_HEALTHY_EXEC: for execution (3s window, stricter)

Both return (healthy: bool, reasons: list[str]).
"""

from __future__ import annotations

import time
from typing import List, Tuple

from polyedge.constants import (
    MAX_MARKET_SNAPSHOT_AGE_DECISION_SEC,
    MAX_MARKET_SNAPSHOT_AGE_EXEC_SEC,
    WS_HEARTBEAT_SEC,
)
from polyedge.snapshots import Snapshot
from polyedge.ws_client import WSState


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ws_healthy(
    market_id: str,
    snapshot: Snapshot,
    ws_state: WSState,
    max_age_sec: int,
) -> Tuple[bool, List[str]]:
    """Core health check used by both decision and exec predicates.

    Returns (healthy, list_of_failure_reasons).
    """
    reasons = []  # type: List[str]
    now = _now_ms()

    # ws_connected == true
    if not ws_state.ws_connected:
        reasons.append("ws_connected is false")

    # now - ws_last_message_unix_ms <= WS_HEARTBEAT_SEC * 1000
    if now - ws_state.ws_last_message_unix_ms > WS_HEARTBEAT_SEC * 1000:
        reasons.append(
            "ws_last_message stale: {}ms > {}ms".format(
                now - ws_state.ws_last_message_unix_ms,
                WS_HEARTBEAT_SEC * 1000,
            )
        )

    # snapshot.snapshot_source == "WS"
    if snapshot.snapshot_source != "WS":
        reasons.append("snapshot_source is '{}', not 'WS'".format(snapshot.snapshot_source))

    # snapshot.snapshot_ws_epoch == current_ws_epoch
    if snapshot.snapshot_ws_epoch != ws_state.current_ws_epoch:
        reasons.append(
            "epoch mismatch: snapshot={}, current={}".format(
                snapshot.snapshot_ws_epoch, ws_state.current_ws_epoch,
            )
        )

    # snapshot.market_id == market_id
    if snapshot.market_id != market_id:
        reasons.append(
            "market_id mismatch: snapshot={}, expected={}".format(
                snapshot.market_id, market_id,
            )
        )

    # market_last_ws_update_unix_ms not null and > 0
    if snapshot.market_last_ws_update_unix_ms is None or snapshot.market_last_ws_update_unix_ms <= 0:
        reasons.append("market_last_ws_update_unix_ms is null or <= 0")
    else:
        # now - market_last_ws_update_unix_ms <= max_age_sec * 1000
        age_ms = now - snapshot.market_last_ws_update_unix_ms
        if age_ms > max_age_sec * 1000:
            reasons.append(
                "market_last_ws_update stale: {}ms > {}ms".format(age_ms, max_age_sec * 1000)
            )

    # orderbook_last_change_unix_ms not null and > 0
    if snapshot.orderbook_last_change_unix_ms is None or snapshot.orderbook_last_change_unix_ms <= 0:
        reasons.append("orderbook_last_change_unix_ms is null or <= 0")
    else:
        # now - orderbook_last_change_unix_ms <= max_age_sec * 1000
        age_ms = now - snapshot.orderbook_last_change_unix_ms
        if age_ms > max_age_sec * 1000:
            reasons.append(
                "orderbook_last_change stale: {}ms > {}ms".format(age_ms, max_age_sec * 1000)
            )

    # ws_last_message_unix_ms >= snapshot_at_unix_ms
    if snapshot.ws_last_message_unix_ms < snapshot.snapshot_at_unix_ms:
        reasons.append(
            "ws_last_message_unix_ms ({}) < snapshot_at_unix_ms ({})".format(
                snapshot.ws_last_message_unix_ms, snapshot.snapshot_at_unix_ms,
            )
        )

    healthy = len(reasons) == 0
    return healthy, reasons


def ws_healthy_decision(
    market_id: str,
    snapshot: Snapshot,
    ws_state: WSState,
) -> Tuple[bool, List[str]]:
    """WS_HEALTHY_DECISION predicate (spec §7.3).

    Uses MAX_MARKET_SNAPSHOT_AGE_DECISION_SEC (6s).
    """
    return _ws_healthy(
        market_id, snapshot, ws_state,
        MAX_MARKET_SNAPSHOT_AGE_DECISION_SEC,
    )


def ws_healthy_exec(
    market_id: str,
    snapshot: Snapshot,
    ws_state: WSState,
) -> Tuple[bool, List[str]]:
    """WS_HEALTHY_EXEC predicate (spec §7.3).

    Uses MAX_MARKET_SNAPSHOT_AGE_EXEC_SEC (3s).
    Identical checks but stricter timing.
    """
    return _ws_healthy(
        market_id, snapshot, ws_state,
        MAX_MARKET_SNAPSHOT_AGE_EXEC_SEC,
    )
