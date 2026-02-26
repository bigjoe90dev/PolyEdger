"""Watchlist Manager — bounded market selection (spec §8).

Maintains a scored watchlist of up to WATCHLIST_MAX eligible markets.
Markets with repeated anomalies go to probation. Noisy markets
(>10 triggers/hour yielding no trade) get quarantined for 2 hours.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from polyedge.constants import (
    BOOK_LEVELS_REQUIRED,
    MAX_SPREAD_ABS,
    MIN_DEPTH_USD_NEAR_TOP,
    MIN_LIQUIDITY_USD,
    MIN_VOLUME_24H_USD,
    PROBATION_MAX,
    TIME_TO_RESOLUTION_MAX_SEC,
    TIME_TO_RESOLUTION_MIN_SEC,
    WATCHLIST_MAX,
)

logger = logging.getLogger(__name__)

# Quarantine constants (spec §8.2)
QUARANTINE_TRIGGER_THRESHOLD = 10  # triggers/hour with no trade
QUARANTINE_DURATION_HOURS = 2


def score_market(market: Dict[str, Any], now_utc: Optional[datetime] = None) -> float:
    """Compute watchlist priority score for a market.

    Higher score = higher priority. Factors:
    - Resolution proximity (markets nearing resolution scored higher)
    - Volume/liquidity (higher = better)
    - Spread tightness (tighter = better)
    - Recent orderbook activity
    """
    now = now_utc or datetime.now(timezone.utc)
    score = 0.0

    # Resolution proximity component (0–40 points)
    end_date = market.get("end_date_utc")
    if end_date:
        if isinstance(end_date, str):
            try:
                end_date = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                end_date = None

    if end_date:
        time_remaining = (end_date - now).total_seconds()
        if TIME_TO_RESOLUTION_MIN_SEC <= time_remaining <= TIME_TO_RESOLUTION_MAX_SEC:
            # Closer to resolution = higher score (inverse relationship)
            # Normalise to 0–40 range
            fraction_remaining = time_remaining / TIME_TO_RESOLUTION_MAX_SEC
            score += 40.0 * (1.0 - fraction_remaining)
        else:
            # Out of range → low score
            score -= 10.0

    # Volume component (0–20 points)
    volume = market.get("volume_24h_usd", 0) or 0
    if volume >= MIN_VOLUME_24H_USD:
        # Log scale score, capped at 20
        import math
        score += min(20.0, math.log10(max(volume, 1)) * 4.0)
    else:
        score -= 5.0

    # Liquidity component (0–20 points)
    liquidity = market.get("liquidity_usd", 0) or 0
    if liquidity >= MIN_LIQUIDITY_USD:
        import math
        score += min(20.0, math.log10(max(liquidity, 1)) * 4.0)
    else:
        score -= 5.0

    # Spread component (0–10 points, tighter = better)
    spread = market.get("spread")
    if spread is not None and spread <= MAX_SPREAD_ABS:
        score += 10.0 * (1.0 - spread / MAX_SPREAD_ABS)

    # Recent activity component (0–10 points)
    last_update = market.get("orderbook_last_change_unix_ms")
    if last_update:
        import time
        age_sec = (time.time() * 1000 - last_update) / 1000.0
        if age_sec < 60:
            score += 10.0
        elif age_sec < 300:
            score += 5.0
        elif age_sec < 900:
            score += 2.0

    return round(score, 4)


async def refresh_watchlist(
    pool: Any,
    eligible_markets: List[Dict[str, Any]],
    now_utc: Optional[datetime] = None,
) -> Dict[str, int]:
    """Rebuild the watchlist from eligible markets.

    Excludes probation and quarantine markets.
    Returns stats: {"added": N, "removed": N, "probation": N, "quarantine": N}.
    """
    now = now_utc or datetime.now(timezone.utc)

    # Get probation and quarantine market IDs
    probation_rows = await pool.fetch(
        "SELECT market_id FROM probation WHERE probation_until_utc > $1",
        now,
    )
    probation_ids = {r["market_id"] for r in probation_rows}

    quarantine_rows = await pool.fetch(
        "SELECT market_id FROM quarantine WHERE quarantine_until_utc > $1",
        now,
    )
    quarantine_ids = {r["market_id"] for r in quarantine_rows}

    excluded = probation_ids | quarantine_ids

    # Score and sort eligible markets
    scored = []  # type: List[Tuple[float, Dict[str, Any]]]
    for m in eligible_markets:
        mid = m.get("market_id", "")
        if mid in excluded:
            continue
        s = score_market(m, now)
        scored.append((s, m))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top WATCHLIST_MAX
    selected = scored[:WATCHLIST_MAX]

    # Clear and repopulate watchlist
    await pool.execute("DELETE FROM watchlist")

    added = 0
    for s, m in selected:
        mid = m.get("market_id", "")
        await pool.execute(
            """
            INSERT INTO watchlist (market_id, score, added_at_utc, last_scored_utc)
            VALUES ($1, $2, $3, $3)
            ON CONFLICT (market_id) DO UPDATE SET
                score = EXCLUDED.score,
                last_scored_utc = EXCLUDED.last_scored_utc
            """,
            mid, s, now,
        )
        added += 1

    stats = {
        "added": added,
        "removed": max(0, len(eligible_markets) - added - len(excluded)),
        "probation": len(probation_ids),
        "quarantine": len(quarantine_ids),
    }

    logger.info(
        "Watchlist refreshed: added=%d probation=%d quarantine=%d",
        stats["added"], stats["probation"], stats["quarantine"],
    )
    return stats


async def add_to_probation(
    pool: Any,
    market_id: str,
    reason: str,
    duration_hours: int = 2,
) -> None:
    """Add a market to probation."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=duration_hours)

    # Check probation count limit
    count = await pool.fetchval("SELECT COUNT(*) FROM probation WHERE probation_until_utc > $1", now)
    if count >= PROBATION_MAX:
        logger.warning("Probation list full (%d/%d), cannot add %s", count, PROBATION_MAX, market_id)
        return

    await pool.execute(
        """
        INSERT INTO probation (market_id, reason, anomaly_count, probation_until_utc, added_at_utc)
        VALUES ($1, $2, 1, $3, $4)
        ON CONFLICT (market_id) DO UPDATE SET
            reason = EXCLUDED.reason,
            anomaly_count = probation.anomaly_count + 1,
            probation_until_utc = EXCLUDED.probation_until_utc
        """,
        market_id, reason, until, now,
    )
    logger.info("Market %s added to probation until %s: %s", market_id, until.isoformat(), reason)


async def check_quarantine(
    pool: Any,
    market_id: str,
    no_trade: bool = True,
) -> bool:
    """Track trigger counts and quarantine noisy markets.

    Returns True if market is now quarantined.
    """
    now = datetime.now(timezone.utc)

    # Check if already quarantined
    row = await pool.fetchrow(
        "SELECT quarantine_until_utc FROM quarantine WHERE market_id = $1 AND quarantine_until_utc > $2",
        market_id, now,
    )
    if row:
        return True

    # Track trigger count for this hour
    hour_start = now.replace(minute=0, second=0, microsecond=0)

    existing = await pool.fetchrow(
        "SELECT trigger_count_hour, no_trade_count_hour FROM quarantine WHERE market_id = $1",
        market_id,
    )

    trigger_count = 1
    no_trade_count = 1 if no_trade else 0

    if existing:
        trigger_count = existing["trigger_count_hour"] + 1
        no_trade_count = existing["no_trade_count_hour"] + (1 if no_trade else 0)

    # Check quarantine threshold: >10 triggers/hour and ALL yielded no trade
    should_quarantine = (
        trigger_count > QUARANTINE_TRIGGER_THRESHOLD
        and no_trade_count >= trigger_count
    )

    quarantine_until = now + timedelta(hours=QUARANTINE_DURATION_HOURS) if should_quarantine else now

    await pool.execute(
        """
        INSERT INTO quarantine (market_id, trigger_count_hour, no_trade_count_hour, quarantine_until_utc, added_at_utc)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (market_id) DO UPDATE SET
            trigger_count_hour = $2,
            no_trade_count_hour = $3,
            quarantine_until_utc = $4
        """,
        market_id, trigger_count, no_trade_count, quarantine_until, now,
    )

    if should_quarantine:
        logger.warning(
            "Market %s quarantined for %dh: %d triggers, %d no-trades",
            market_id, QUARANTINE_DURATION_HOURS, trigger_count, no_trade_count,
        )

    return should_quarantine


async def get_watchlist(pool: Any) -> List[Dict[str, Any]]:
    """Get current watchlist ordered by score descending."""
    rows = await pool.fetch(
        "SELECT market_id, score, added_at_utc, last_scored_utc FROM watchlist ORDER BY score DESC"
    )
    return [dict(r) for r in rows]


async def cleanup_expired(pool: Any) -> Dict[str, int]:
    """Remove expired probation and quarantine entries."""
    now = datetime.now(timezone.utc)

    p_result = await pool.execute(
        "DELETE FROM probation WHERE probation_until_utc <= $1", now,
    )
    q_result = await pool.execute(
        "DELETE FROM quarantine WHERE quarantine_until_utc <= $1", now,
    )

    p_count = int(p_result.split()[-1]) if p_result else 0
    q_count = int(q_result.split()[-1]) if q_result else 0

    if p_count or q_count:
        logger.info("Cleanup: %d probation, %d quarantine entries expired", p_count, q_count)

    return {"probation_expired": p_count, "quarantine_expired": q_count}
