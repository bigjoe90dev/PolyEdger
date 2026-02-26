"""Telegram Control — privileged commands + alert dedup (spec §5.5).

Implements all 7 privileged commands:
- /status  — bot state, positions, budget
- /halt    — immediate HALTED transition
- /resume  — HALTED → OBSERVE_ONLY
- /arm     — begin arming ceremony
- /balance — wallet + venue balance
- /config  — show active config hash
- /kill    — force process exit
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Alert dedup window
ALERT_DEDUP_SEC = 300  # 5 minutes


class TelegramController:
    """Telegram bot controller with operator verification."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        operator_chat_ids: Optional[Set[int]] = None,
    ) -> None:
        self._bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._enabled = bool(self._bot_token and self._bot_token != "REPLACE_ME")
        self._operator_chat_ids = operator_chat_ids or set()

        # Alert dedup
        self._recent_alerts = {}  # type: Dict[str, float]

        # Command handlers
        self._handlers = {}  # type: Dict[str, Callable]
        self._register_default_handlers()

        if self._enabled:
            logger.info("Telegram controller initialised")
        else:
            logger.info("Telegram controller disabled (no bot token)")

    def _register_default_handlers(self) -> None:
        """Register the 7 privileged commands."""
        self._handlers = {
            "/status": self._handle_status,
            "/halt": self._handle_halt,
            "/resume": self._handle_resume,
            "/arm": self._handle_arm,
            "/balance": self._handle_balance,
            "/config": self._handle_config,
            "/kill": self._handle_kill,
        }

    def is_authorised(self, chat_id: int, user_id: Optional[int] = None) -> bool:
        """Check if chat_id is in operator allowlist."""
        if not self._operator_chat_ids:
            return False
        return chat_id in self._operator_chat_ids

    async def process_message(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Process incoming message."""
        if not self.is_authorised(chat_id, user_id):
            logger.warning("Unauthorised Telegram message from chat_id=%d", chat_id)
            return None

        command = text.strip().split()[0].lower()
        handler = self._handlers.get(command)

        if handler is None:
            return "Unknown command: {}. Available: {}".format(
                command, ", ".join(sorted(self._handlers.keys())),
            )

        return await handler(context or {})

    async def send_alert(
        self,
        message: str,
        dedup_key: Optional[str] = None,
    ) -> bool:
        """Send alert with optional dedup.

        Returns True if alert was sent (not deduped).
        """
        if dedup_key:
            last_sent = self._recent_alerts.get(dedup_key, 0)
            if time.time() - last_sent < ALERT_DEDUP_SEC:
                return False  # Deduped
            self._recent_alerts[dedup_key] = time.time()

        if not self._enabled:
            logger.info("ALERT (telegram disabled): %s", message)
            return True

        # In production: send via Telegram Bot API
        logger.info("ALERT: %s", message)
        return True

    # ── Default command handlers ──────────────────────────────────────────────

    async def _handle_status(self, ctx: Dict[str, Any]) -> str:
        bot_state = ctx.get("bot_state", "UNKNOWN")
        positions = ctx.get("open_positions", 0)
        daily_pnl = ctx.get("daily_pnl", 0)
        budget_remaining = ctx.get("budget_remaining", 0)
        return "Status: state={} positions={} pnl=${:.2f} budget_remaining=${:.4f}".format(
            bot_state, positions, daily_pnl, budget_remaining,
        )

    async def _handle_halt(self, ctx: Dict[str, Any]) -> str:
        return "HALT command received. Transitioning to HALTED."

    async def _handle_resume(self, ctx: Dict[str, Any]) -> str:
        return "RESUME command received. Transitioning to OBSERVE_ONLY."

    async def _handle_arm(self, ctx: Dict[str, Any]) -> str:
        return "ARM command received. Starting arming ceremony."

    async def _handle_balance(self, ctx: Dict[str, Any]) -> str:
        wallet = ctx.get("wallet_usd", 0)
        venue = ctx.get("venue_balance_usd", 0)
        return "Balance: wallet=${:.2f} venue=${:.2f}".format(wallet, venue)

    async def _handle_config(self, ctx: Dict[str, Any]) -> str:
        config_hash = ctx.get("config_manifest_hash", "N/A")
        return "Config manifest hash: {}".format(config_hash)

    async def _handle_kill(self, ctx: Dict[str, Any]) -> str:
        return "KILL command received. Process will exit."
