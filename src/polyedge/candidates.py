"""Candidate Pipeline — trigger detection + anti-spoof (spec §9).

Fast loop runs every FAST_LOOP_SEC (2s). For each watchlist market:
- Read latest WS snapshot
- Compute triggers (spread_change, depth_drop, mid_move, approaching_resolution)
- Only enqueue if trigger persists TRIGGER_PERSIST_UPDATES updates over TRIGGER_PERSIST_MIN_SEC
- Enforce global + per-market candidate caps
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from polyedge.constants import (
    CANDIDATES_PER_MIN_MAX,
    CANDIDATE_MAX_AGE_SEC,
    MAX_SPREAD_ABS,
    MIN_DEPTH_USD_NEAR_TOP,
    PER_MARKET_CANDIDATES_PER_MIN_MAX,
    TIME_TO_RESOLUTION_MIN_SEC,
    TRIGGER_PERSIST_MIN_SEC,
    TRIGGER_PERSIST_UPDATES,
)

logger = logging.getLogger(__name__)

# Trigger types (spec §9.1)
TRIGGER_SPREAD_CHANGE = "spread_change"
TRIGGER_DEPTH_DROP = "depth_drop"
TRIGGER_MID_MOVE = "mid_move"
TRIGGER_APPROACHING_RESOLUTION = "approaching_resolution"

ALL_TRIGGER_TYPES = frozenset({
    TRIGGER_SPREAD_CHANGE,
    TRIGGER_DEPTH_DROP,
    TRIGGER_MID_MOVE,
    TRIGGER_APPROACHING_RESOLUTION,
})

# Candidate status enum (spec §9.2)
STATUS_NEW = "NEW"
STATUS_FILTERED = "FILTERED"
STATUS_EVIDENCE_DONE = "EVIDENCE_DONE"
STATUS_AI_DONE = "AI_DONE"
STATUS_DECIDED = "DECIDED"
STATUS_EXECUTED = "EXECUTED"
STATUS_DROPPED = "DROPPED"


class TriggerState:
    """In-memory trigger persistence tracker per market per trigger type.

    Per spec §9.1: trigger must persist for TRIGGER_PERSIST_UPDATES WS updates
    over TRIGGER_PERSIST_MIN_SEC elapsed before a candidate is enqueued.
    """

    def __init__(self) -> None:
        # (market_id, trigger_type) -> {"first_seen": timestamp, "count": int}
        self._state = {}  # type: Dict[Tuple[str, str], Dict[str, Any]]

    def record_trigger(
        self,
        market_id: str,
        trigger_type: str,
        snapshot_id: str,
    ) -> bool:
        """Record a trigger occurrence. Returns True if persistence threshold met."""
        key = (market_id, trigger_type)
        now = time.time()

        if key not in self._state:
            self._state[key] = {
                "first_seen": now,
                "count": 1,
                "last_snapshot_id": snapshot_id,
            }
            return False

        entry = self._state[key]

        # Don't count same snapshot twice
        if entry["last_snapshot_id"] == snapshot_id:
            return False

        entry["count"] += 1
        entry["last_snapshot_id"] = snapshot_id

        # Check persistence thresholds
        elapsed = now - entry["first_seen"]
        if entry["count"] >= TRIGGER_PERSIST_UPDATES and elapsed >= TRIGGER_PERSIST_MIN_SEC:
            return True

        return False

    def clear_trigger(self, market_id: str, trigger_type: str) -> None:
        """Clear a trigger after candidate is enqueued or trigger disappears."""
        key = (market_id, trigger_type)
        self._state.pop(key, None)

    def clear_market(self, market_id: str) -> None:
        """Clear all triggers for a market."""
        keys_to_remove = [k for k in self._state if k[0] == market_id]
        for k in keys_to_remove:
            del self._state[k]


class CandidateRateLimiter:
    """Enforce global and per-market candidate rate limits.

    Per spec §9.1:
    - CANDIDATES_PER_MIN_MAX (50) globally
    - PER_MARKET_CANDIDATES_PER_MIN_MAX (10) per market
    """

    def __init__(self) -> None:
        self._global_timestamps = []  # type: List[float]
        self._market_timestamps = defaultdict(list)  # type: Dict[str, List[float]]

    def _prune(self, timestamps: List[float], now: float) -> List[float]:
        """Remove timestamps older than 60 seconds."""
        cutoff = now - 60.0
        return [t for t in timestamps if t > cutoff]

    def can_enqueue(self, market_id: str) -> bool:
        """Check if a new candidate can be enqueued."""
        now = time.time()

        # Prune old timestamps
        self._global_timestamps = self._prune(self._global_timestamps, now)
        self._market_timestamps[market_id] = self._prune(
            self._market_timestamps[market_id], now,
        )

        # Check global cap
        if len(self._global_timestamps) >= CANDIDATES_PER_MIN_MAX:
            return False

        # Check per-market cap
        if len(self._market_timestamps[market_id]) >= PER_MARKET_CANDIDATES_PER_MIN_MAX:
            return False

        return True

    def record_enqueue(self, market_id: str) -> None:
        """Record that a candidate was enqueued."""
        now = time.time()
        self._global_timestamps.append(now)
        self._market_timestamps[market_id].append(now)


def detect_triggers(
    market_id: str,
    snapshot: Any,
    prev_snapshot: Optional[Any] = None,
    market_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Detect which triggers fire for a market given the latest snapshot.

    Returns list of trigger type strings.
    """
    triggers = []  # type: List[str]

    if snapshot is None:
        return triggers

    # Trigger: spread_change
    if hasattr(snapshot, "best_bid_yes") and hasattr(snapshot, "best_ask_yes"):
        if snapshot.best_bid_yes is not None and snapshot.best_ask_yes is not None:
            spread_yes = snapshot.best_ask_yes - snapshot.best_bid_yes
            if prev_snapshot and prev_snapshot.best_bid_yes and prev_snapshot.best_ask_yes:
                prev_spread = prev_snapshot.best_ask_yes - prev_snapshot.best_bid_yes
                spread_change = abs(spread_yes - prev_spread)
                if spread_change > 0.005:  # 0.5% spread change threshold
                    triggers.append(TRIGGER_SPREAD_CHANGE)

    # Trigger: depth_drop
    if hasattr(snapshot, "depth_yes") and prev_snapshot and hasattr(prev_snapshot, "depth_yes"):
        current_depth = sum(level[1] for level in (snapshot.depth_yes or [])[:3])
        prev_depth = sum(level[1] for level in (prev_snapshot.depth_yes or [])[:3])
        if prev_depth > 0 and current_depth < prev_depth * 0.7:  # 30% depth drop
            triggers.append(TRIGGER_DEPTH_DROP)

    # Trigger: mid_move
    if (hasattr(snapshot, "best_bid_yes") and hasattr(snapshot, "best_ask_yes")
            and snapshot.best_bid_yes is not None and snapshot.best_ask_yes is not None):
        mid = (snapshot.best_bid_yes + snapshot.best_ask_yes) / 2.0
        if prev_snapshot and prev_snapshot.best_bid_yes and prev_snapshot.best_ask_yes:
            prev_mid = (prev_snapshot.best_bid_yes + prev_snapshot.best_ask_yes) / 2.0
            if abs(mid - prev_mid) > 0.01:  # 1% mid move
                triggers.append(TRIGGER_MID_MOVE)

    # Trigger: approaching_resolution
    if market_data:
        end_date = market_data.get("end_date_utc")
        if end_date:
            if isinstance(end_date, str):
                try:
                    end_date = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    end_date = None
            if end_date:
                remaining = (end_date - datetime.now(timezone.utc)).total_seconds()
                if TIME_TO_RESOLUTION_MIN_SEC <= remaining <= 24 * 3600:  # Within 24h
                    triggers.append(TRIGGER_APPROACHING_RESOLUTION)

    return triggers


def create_candidate(
    market_id: str,
    snapshot_id: str,
    trigger_reasons: List[str],
) -> Dict[str, Any]:
    """Create a candidate record per spec §9.2."""
    now = datetime.now(timezone.utc)
    return {
        "candidate_id": str(uuid.uuid4()),
        "market_id": market_id,
        "snapshot_id": snapshot_id,
        "created_at_utc": now,
        "trigger_reasons": trigger_reasons,
        "status": STATUS_NEW,
        "filter_reason": None,
        "decided_at_utc": None,
        "decision_id_hex": None,
        "updated_at_utc": now,
    }


async def enqueue_candidate(pool: Any, candidate: Dict[str, Any]) -> None:
    """Insert a candidate into the database."""
    import json as _json

    await pool.execute(
        """
        INSERT INTO candidates (
            candidate_id, market_id, snapshot_id, created_at_utc,
            trigger_reasons, status, filter_reason,
            decided_at_utc, decision_id_hex, updated_at_utc
        ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
        """,
        uuid.UUID(candidate["candidate_id"]),
        candidate["market_id"],
        uuid.UUID(candidate["snapshot_id"]),
        candidate["created_at_utc"],
        _json.dumps(candidate["trigger_reasons"]),
        candidate["status"],
        candidate["filter_reason"],
        candidate["decided_at_utc"],
        candidate["decision_id_hex"],
        candidate["updated_at_utc"],
    )


async def update_candidate_status(
    pool: Any,
    candidate_id: str,
    status: str,
    filter_reason: Optional[str] = None,
    decision_id_hex: Optional[str] = None,
) -> None:
    """Update candidate status."""
    now = datetime.now(timezone.utc)
    decided_at = now if status == STATUS_DECIDED else None

    await pool.execute(
        """
        UPDATE candidates SET
            status = $2, filter_reason = $3,
            decided_at_utc = COALESCE($4, decided_at_utc),
            decision_id_hex = COALESCE($5, decision_id_hex),
            updated_at_utc = $6
        WHERE candidate_id = $1
        """,
        uuid.UUID(candidate_id), status, filter_reason, decided_at, decision_id_hex, now,
    )


def is_candidate_expired(candidate: Dict[str, Any]) -> bool:
    """Check if a candidate has exceeded CANDIDATE_MAX_AGE_SEC."""
    created = candidate.get("created_at_utc")
    if created is None:
        return True
    now = datetime.now(timezone.utc)
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    age = (now - created).total_seconds()
    return age > CANDIDATE_MAX_AGE_SEC
