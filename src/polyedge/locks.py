"""Market Locks — per-market concurrency control (spec §20).

Implements:
- Lock acquisition with steal-after-expiry grace
- Lock renewal with heartbeat
- Pre-exec validation (owner, TTL, version)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

from polyedge.constants import (
    LOCK_RENEW_EVERY_SEC,
    LOCK_STEAL_GRACE_AFTER_EXPIRY_SEC,
    LOCK_TTL_SEC,
    MIN_LOCK_TTL_BEFORE_SUBMIT_SEC,
)

logger = logging.getLogger(__name__)


class MarketLock:
    """In-memory market lock for a single market."""

    def __init__(
        self,
        market_id: str,
        owner_instance_id: str,
        owner_worker_id: str,
    ) -> None:
        self.market_id = market_id
        self.owner_instance_id = owner_instance_id
        self.owner_worker_id = owner_worker_id
        self.lock_version = 1
        self.owner_heartbeat = time.time()
        self.expires_at = time.time() + LOCK_TTL_SEC
        self.last_renewed = time.time()

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def is_stealable(self) -> bool:
        """Can be stolen if expired + grace period passed."""
        return time.time() > self.expires_at + LOCK_STEAL_GRACE_AFTER_EXPIRY_SEC


class LockManager:
    """Manages market locks for concurrent workers."""

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self._locks = {}  # type: Dict[str, MarketLock]

    def acquire(
        self,
        market_id: str,
        worker_id: str,
    ) -> Tuple[bool, Optional[int]]:
        """Acquire a lock on a market.

        Returns (success, lock_version).
        """
        existing = self._locks.get(market_id)

        if existing is None:
            # No lock exists → acquire
            lock = MarketLock(market_id, self.instance_id, worker_id)
            self._locks[market_id] = lock
            logger.debug("Lock acquired: market=%s worker=%s version=%d", market_id, worker_id, lock.lock_version)
            return True, lock.lock_version

        if existing.is_stealable():
            # Expired + grace → steal
            lock = MarketLock(market_id, self.instance_id, worker_id)
            lock.lock_version = existing.lock_version + 1
            self._locks[market_id] = lock
            logger.warning(
                "Lock stolen: market=%s from=%s by=%s version=%d",
                market_id, existing.owner_worker_id, worker_id, lock.lock_version,
            )
            return True, lock.lock_version

        if (existing.owner_instance_id == self.instance_id
                and existing.owner_worker_id == worker_id):
            # Already owned by same worker
            return True, existing.lock_version

        # Owned by someone else and not stealable
        return False, None

    def renew(self, market_id: str, worker_id: str) -> bool:
        """Renew a lock (heartbeat)."""
        lock = self._locks.get(market_id)
        if lock is None:
            return False

        if lock.owner_instance_id != self.instance_id or lock.owner_worker_id != worker_id:
            return False

        lock.owner_heartbeat = time.time()
        lock.expires_at = time.time() + LOCK_TTL_SEC
        lock.last_renewed = time.time()
        lock.lock_version += 1

        return True

    def release(self, market_id: str, worker_id: str) -> bool:
        """Release a lock."""
        lock = self._locks.get(market_id)
        if lock is None:
            return False

        if lock.owner_instance_id != self.instance_id or lock.owner_worker_id != worker_id:
            return False

        del self._locks[market_id]
        return True

    def validate_for_submit(
        self,
        market_id: str,
        worker_id: str,
        expected_version: int,
    ) -> Tuple[bool, str]:
        """Pre-exec lock validation per spec §20.4.

        Returns (valid, reason_if_not).
        """
        lock = self._locks.get(market_id)
        if lock is None:
            return False, "LOCK_LOST: no lock for market {}".format(market_id)

        if lock.owner_instance_id != self.instance_id:
            return False, "LOCK_LOST: owned by different instance"

        if lock.owner_worker_id != worker_id:
            return False, "LOCK_LOST: owned by different worker"

        # TTL check
        remaining = lock.expires_at - time.time()
        if remaining < MIN_LOCK_TTL_BEFORE_SUBMIT_SEC:
            return False, "LOCK_LOST: TTL too low ({:.1f}s < {}s)".format(
                remaining, MIN_LOCK_TTL_BEFORE_SUBMIT_SEC,
            )

        # Version check
        if lock.lock_version != expected_version:
            return False, "LOCK_LOST: version mismatch (expected={} actual={})".format(
                expected_version, lock.lock_version,
            )

        return True, ""

    @property
    def held_locks(self) -> Dict[str, int]:
        """Map of market_id → lock_version for held locks."""
        return {mid: l.lock_version for mid, l in self._locks.items() if not l.is_expired()}
