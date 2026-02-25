"""Telegram control plane — Phase 3 stub (spec §5.5).

Logs all commands but refuses to send any messages or process commands.
Full implementation deferred to Phase 8+.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TelegramDisabledError(Exception):
    """Raised when Telegram operations are attempted before implementation."""


class TelegramBot:
    """Stub Telegram bot that refuses to act."""

    def __init__(self) -> None:
        logger.info("TelegramBot initialised in DISABLED mode (Phase 3 stub)")

    def send_alert(self, message: str, dedup_key: str | None = None) -> None:
        """Log alert but refuse to send."""
        logger.warning(
            "Telegram DISABLED — alert not sent: %s (dedup_key=%s)",
            message[:100],
            dedup_key,
        )

    def start_polling(self) -> None:
        """Refuse to start polling."""
        raise TelegramDisabledError(
            "Telegram polling is disabled. Not implemented (Phase 8+ required)."
        )

    async def handle_command(self, command: str, args: list[str], **kwargs: Any) -> None:
        """Refuse to handle commands."""
        raise TelegramDisabledError(
            f"Telegram command handling is disabled. Command: /{command}"
        )
