"""Execution Engine — Phase 3 stub (spec §17).

All methods refuse to act. No orders will ever be placed by this module
until Phase 8 implementation is complete and the arming ceremony succeeds.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ExecutionDisabledError(Exception):
    """Raised when execution is attempted before Phase 8 implementation."""


class ExecutionEngine:
    """Stub execution engine that refuses all trading actions."""

    def __init__(self) -> None:
        logger.info("ExecutionEngine initialised in DISABLED mode (Phase 3 stub)")

    def submit_order(self, *args: object, **kwargs: object) -> None:
        """Refuse to submit any order."""
        raise ExecutionDisabledError(
            "Order submission is disabled. Execution engine is not implemented "
            "(Phase 8 required). Default state: OBSERVE_ONLY."
        )

    def cancel_order(self, *args: object, **kwargs: object) -> None:
        """Refuse to cancel any order."""
        raise ExecutionDisabledError(
            "Order cancellation is disabled. Execution engine is not implemented "
            "(Phase 8 required)."
        )

    def replace_order(self, *args: object, **kwargs: object) -> None:
        """Refuse to replace any order."""
        raise ExecutionDisabledError(
            "Order replacement is disabled. Execution engine is not implemented "
            "(Phase 8 required)."
        )

    @property
    def is_enabled(self) -> bool:
        """Execution is never enabled in this stub."""
        return False
