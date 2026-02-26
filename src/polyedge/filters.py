"""Coarse Deterministic Filters — fast rejection (spec §9.3).

Each filter returns a NO_TRADE reason code from the canonical set (§21.2).
All 10 reject conditions implemented. A candidate that fails any filter
is immediately marked FILTERED with the first failing reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from polyedge.constants import (
    BOOK_LEVELS_REQUIRED,
    CANDIDATE_MAX_AGE_SEC,
    MAX_SPREAD_ABS,
    MIN_DEPTH_USD_NEAR_TOP,
    MIN_LIQUIDITY_USD,
    MIN_VOLUME_24H_USD,
    TIME_TO_RESOLUTION_MAX_SEC,
    TIME_TO_RESOLUTION_MIN_SEC,
)

logger = logging.getLogger(__name__)

# Canonical NO_TRADE reason codes (spec §21.2)
REASON_CANDIDATE_EXPIRED = "CANDIDATE_EXPIRED"
REASON_MARKET_NOT_ELIGIBLE = "MARKET_NOT_ELIGIBLE"
REASON_TIME_TO_RESOLUTION_OUT_OF_RANGE = "TIME_TO_RESOLUTION_OUT_OF_RANGE"
REASON_VOLUME_TOO_LOW = "VOLUME_TOO_LOW"
REASON_LIQUIDITY_TOO_LOW = "LIQUIDITY_TOO_LOW"
REASON_SNAPSHOT_INVALID_BOOK = "SNAPSHOT_INVALID_BOOK"
REASON_SNAPSHOT_ASK_SUM_ANOMALY = "SNAPSHOT_ASK_SUM_ANOMALY"
REASON_SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
REASON_DEPTH_TOO_THIN = "DEPTH_TOO_THIN"
REASON_WS_UNHEALTHY_DECISION = "WS_UNHEALTHY_DECISION"


def filter_candidate_age(
    candidate: Dict[str, Any],
    now_utc: Optional[datetime] = None,
) -> Optional[str]:
    """Reject if candidate_age > CANDIDATE_MAX_AGE_SEC."""
    now = now_utc or datetime.now(timezone.utc)
    created = candidate.get("created_at_utc")
    if created is None:
        return REASON_CANDIDATE_EXPIRED

    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return REASON_CANDIDATE_EXPIRED

    age = (now - created).total_seconds()
    if age > CANDIDATE_MAX_AGE_SEC:
        return REASON_CANDIDATE_EXPIRED
    return None


def filter_market_eligible(market: Dict[str, Any]) -> Optional[str]:
    """Reject if market is not eligible (category deny, non-binary)."""
    if not market.get("is_binary_eligible", False):
        return REASON_MARKET_NOT_ELIGIBLE
    return None


def filter_time_to_resolution(
    market: Dict[str, Any],
    now_utc: Optional[datetime] = None,
) -> Optional[str]:
    """Reject if time_to_resolution outside [min, max] bounds."""
    now = now_utc or datetime.now(timezone.utc)
    end_date = market.get("end_date_utc")
    if end_date is None:
        return REASON_TIME_TO_RESOLUTION_OUT_OF_RANGE

    if isinstance(end_date, str):
        try:
            end_date = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return REASON_TIME_TO_RESOLUTION_OUT_OF_RANGE

    remaining = (end_date - now).total_seconds()
    if remaining < TIME_TO_RESOLUTION_MIN_SEC or remaining > TIME_TO_RESOLUTION_MAX_SEC:
        return REASON_TIME_TO_RESOLUTION_OUT_OF_RANGE
    return None


def filter_volume(market: Dict[str, Any]) -> Optional[str]:
    """Reject if volume_24h_usd < MIN_VOLUME_24H_USD."""
    volume = market.get("volume_24h_usd", 0) or 0
    if volume < MIN_VOLUME_24H_USD:
        return REASON_VOLUME_TOO_LOW
    return None


def filter_liquidity(market: Dict[str, Any]) -> Optional[str]:
    """Reject if liquidity_usd < MIN_LIQUIDITY_USD."""
    liquidity = market.get("liquidity_usd", 0) or 0
    if liquidity < MIN_LIQUIDITY_USD:
        return REASON_LIQUIDITY_TOO_LOW
    return None


def filter_invalid_book(snapshot: Any) -> Optional[str]:
    """Reject if invalid_book_anomaly == true."""
    if getattr(snapshot, "invalid_book_anomaly", True):
        return REASON_SNAPSHOT_INVALID_BOOK
    return None


def filter_ask_sum_anomaly(snapshot: Any) -> Optional[str]:
    """Reject if ask_sum_anomaly == true."""
    if getattr(snapshot, "ask_sum_anomaly", True):
        return REASON_SNAPSHOT_ASK_SUM_ANOMALY
    return None


def filter_spread(snapshot: Any) -> Optional[str]:
    """Reject if spread > MAX_SPREAD_ABS on either YES or NO side."""
    bid_yes = getattr(snapshot, "best_bid_yes", None)
    ask_yes = getattr(snapshot, "best_ask_yes", None)
    bid_no = getattr(snapshot, "best_bid_no", None)
    ask_no = getattr(snapshot, "best_ask_no", None)

    if bid_yes is not None and ask_yes is not None:
        spread_yes = ask_yes - bid_yes
        if spread_yes > MAX_SPREAD_ABS:
            return REASON_SPREAD_TOO_WIDE

    if bid_no is not None and ask_no is not None:
        spread_no = ask_no - bid_no
        if spread_no > MAX_SPREAD_ABS:
            return REASON_SPREAD_TOO_WIDE

    return None


def filter_depth(snapshot: Any) -> Optional[str]:
    """Reject if depth_top_levels < MIN_DEPTH_USD_NEAR_TOP on either side.

    Sum size_usd of top BOOK_LEVELS_REQUIRED bids for each token.
    """
    depth_yes = getattr(snapshot, "depth_yes", None) or []
    depth_no = getattr(snapshot, "depth_no", None) or []

    # Sum depth of top levels
    yes_depth = sum(
        level[1] for level in depth_yes[:BOOK_LEVELS_REQUIRED]
    ) if depth_yes else 0

    no_depth = sum(
        level[1] for level in depth_no[:BOOK_LEVELS_REQUIRED]
    ) if depth_no else 0

    if yes_depth < MIN_DEPTH_USD_NEAR_TOP or no_depth < MIN_DEPTH_USD_NEAR_TOP:
        return REASON_DEPTH_TOO_THIN

    return None


def filter_ws_health(
    market_id: str,
    snapshot: Any,
    ws_state: Any,
) -> Optional[str]:
    """Reject if WS_HEALTHY_DECISION == false."""
    from polyedge.ws_health import ws_healthy_decision

    healthy, reasons = ws_healthy_decision(market_id, snapshot, ws_state)
    if not healthy:
        return REASON_WS_UNHEALTHY_DECISION
    return None


def run_all_filters(
    candidate: Dict[str, Any],
    market: Dict[str, Any],
    snapshot: Any,
    ws_state: Optional[Any] = None,
    now_utc: Optional[datetime] = None,
) -> Tuple[bool, Optional[str]]:
    """Run all coarse deterministic filters in spec §9.3 order.

    Returns (passed, reason_code_if_failed).
    Fails on the first failing filter.
    """
    # 1. Candidate age
    reason = filter_candidate_age(candidate, now_utc)
    if reason:
        return False, reason

    # 2. Market eligible
    reason = filter_market_eligible(market)
    if reason:
        return False, reason

    # 3. Time to resolution
    reason = filter_time_to_resolution(market, now_utc)
    if reason:
        return False, reason

    # 4. Volume
    reason = filter_volume(market)
    if reason:
        return False, reason

    # 5. Liquidity
    reason = filter_liquidity(market)
    if reason:
        return False, reason

    # 6. Invalid book anomaly
    reason = filter_invalid_book(snapshot)
    if reason:
        return False, reason

    # 7. Ask sum anomaly
    reason = filter_ask_sum_anomaly(snapshot)
    if reason:
        return False, reason

    # 8. Spread
    reason = filter_spread(snapshot)
    if reason:
        return False, reason

    # 9. Depth
    reason = filter_depth(snapshot)
    if reason:
        return False, reason

    # 10. WS health (only if ws_state provided)
    if ws_state is not None:
        market_id = candidate.get("market_id", "")
        reason = filter_ws_health(market_id, snapshot, ws_state)
        if reason:
            return False, reason

    return True, None
