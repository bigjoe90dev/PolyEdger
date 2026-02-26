"""Observability — canonical event log + NO_TRADE reasons (spec §21, §24).

Implements:
- All 23 canonical NO_TRADE reason codes
- Canonical event log format
- Event counting + statistics
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── All 23 canonical NO_TRADE reason codes (spec §21.2) ──────────────────────

NO_TRADE_REASONS = frozenset({
    # Coarse filters (Phase 3)
    "CANDIDATE_EXPIRED",
    "MARKET_NOT_ELIGIBLE",
    "TIME_TO_RESOLUTION_OUT_OF_RANGE",
    "VOLUME_TOO_LOW",
    "LIQUIDITY_TOO_LOW",
    "SNAPSHOT_INVALID_BOOK",
    "SNAPSHOT_ASK_SUM_ANOMALY",
    "SPREAD_TOO_WIDE",
    "DEPTH_TOO_THIN",
    "WS_UNHEALTHY_DECISION",
    # Evidence (Phase 4)
    "EVIDENCE_REQUIRED",
    "EVIDENCE_CONFLICT",
    "EVIDENCE_TIER1_INSUFFICIENT",
    # Injection (Phase 4)
    "INJECTION_DETECTED",
    "INJECTION_DETECTOR_INVALID",
    # AI (Phase 5)
    "AI_QUORUM_FAILED",
    "AI_DISAGREEMENT",
    "AI_BUDGET_EXCEEDED",
    # Calibration (Phase 6)
    "P_EFF_OUTLIER",
    # Decision (Phase 7)
    "EV_TOO_LOW",
    # Risk (Phase 7)
    "RISK_LIMIT_HIT",
    # Execution (Phase 8)
    "LOCK_LOST",
    # Reconciliation (Phase 8)
    "RECONCILE_RED",
})


class EventLog:
    """Canonical event log."""

    def __init__(self) -> None:
        self._events = []  # type: List[Dict[str, Any]]
        self._no_trade_counts = {}  # type: Dict[str, int]

    def log_event(
        self,
        event_type: str,
        market_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        reason_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a canonical event."""
        event = {
            "ts": time.time(),
            "event_type": event_type,
            "market_id": market_id,
            "candidate_id": candidate_id,
            "reason_code": reason_code,
            "details": details or {},
        }
        self._events.append(event)

        if reason_code and reason_code in NO_TRADE_REASONS:
            self._no_trade_counts[reason_code] = self._no_trade_counts.get(reason_code, 0) + 1

        logger.info(
            "Event: type=%s market=%s reason=%s",
            event_type, market_id or "-", reason_code or "-",
        )

    @property
    def no_trade_stats(self) -> Dict[str, int]:
        """Count of each NO_TRADE reason."""
        return dict(self._no_trade_counts)

    @property
    def recent_events(self) -> List[Dict[str, Any]]:
        """Last 100 events."""
        return self._events[-100:]

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_events": len(self._events),
            "no_trade_breakdown": self.no_trade_stats,
            "unique_reasons": len(self._no_trade_counts),
        }
