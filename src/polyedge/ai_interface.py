"""AI Analysis interface — Phase 3 stub (spec §12).

Defines the strict JSON schema and interface contracts for the AI swarm.
All analysis methods raise AINotImplementedError — no OpenRouter calls are made.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Strict JSON schema version per spec §12.5
SCHEMA_VERSION = "polyedge.ai.v2.5"

# Required fields in AI response
REQUIRED_FIELDS = frozenset({
    "market_id",
    "prob_yes_raw",
    "confidence_raw",
    "resolution_risk",
    "dispute_risk",
    "resolution_summary",
    "evidence_summary",
    "uncertainty_reason",
    "key_drivers",
    "disqualifiers",
    "recommended_side",
    "notes",
})

# Valid values for recommended_side
VALID_SIDES = frozenset({"YES", "NO", "NO_TRADE"})

# Swarm composition per spec §12.1 (total weight = 6)
SWARM_MODELS = {
    "deepseek/deepseek-v3.2": {"weight": 2},
    "minimax/minimax-m2.5": {"weight": 2},
    "moonshotai/kimi-k2.5": {"weight": 1},
    "z-ai/glm-5": {"weight": 1},
}


class AINotImplementedError(Exception):
    """Raised when AI analysis is attempted before Phase 5 implementation."""


def validate_ai_response(response: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate an AI response against the strict JSON schema.

    Returns (valid, list_of_errors).
    """
    errors: list[str] = []

    # Check all required fields present
    missing = REQUIRED_FIELDS - set(response.keys())
    if missing:
        errors.append(f"Missing required fields: {sorted(missing)}")

    # Range checks
    for field in ("prob_yes_raw", "confidence_raw", "resolution_risk", "dispute_risk"):
        val = response.get(field)
        if val is not None:
            if not isinstance(val, (int, float)):
                errors.append(f"{field} must be numeric, got {type(val).__name__}")
            elif val < 0 or val > 1:
                errors.append(f"{field} out of range [0,1]: {val}")

    # recommended_side check
    side = response.get("recommended_side")
    if side is not None and side not in VALID_SIDES:
        errors.append(f"recommended_side must be one of {sorted(VALID_SIDES)}, got '{side}'")

    # Type checks for arrays
    for field in ("key_drivers", "disqualifiers"):
        val = response.get(field)
        if val is not None and not isinstance(val, list):
            errors.append(f"{field} must be an array, got {type(val).__name__}")

    # Type checks for strings
    for field in ("resolution_summary", "evidence_summary", "uncertainty_reason", "notes"):
        val = response.get(field)
        if val is not None and not isinstance(val, str):
            errors.append(f"{field} must be a string, got {type(val).__name__}")

    return len(errors) == 0, errors


class AISwarm:
    """Stub AI swarm that refuses to make any analysis calls."""

    def __init__(self) -> None:
        logger.info("AISwarm initialised in DISABLED mode (Phase 3 stub)")

    async def analyze(
        self,
        market_id: str,
        candidate_id: str,
        evidence_bundle: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Refuse to perform AI analysis."""
        raise AINotImplementedError(
            f"AI analysis is disabled for market {market_id}. "
            "AI swarm is not implemented (Phase 5 required). "
            "No OpenRouter calls will be made."
        )

    @property
    def is_enabled(self) -> bool:
        """AI is never enabled in this stub."""
        return False
