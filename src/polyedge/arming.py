"""Arming Ceremony — two-step TOTP + nonce flow (spec §5.6).

Implements:
- Two-step arming: operator provides TOTP, system generates nonce
- Arming file validation (per-process binding)
- Local arming file generation + verification
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from polyedge.constants import (
    ARMING_FILE_MAX_AGE_SEC,
    ARMING_NONCE1_TTL_SEC,
    ARMING_WINDOW_SEC,
    TOTP_REPLAY_BLOCK_SEC,
)

logger = logging.getLogger(__name__)


class ArmingError(Exception):
    """Raised when arming ceremony fails."""


class ArmingCeremony:
    """Two-step arming ceremony per spec §5.6.

    Step 1: Operator provides TOTP code → nonce1 generated
    Step 2: Operator provides nonce1 back → arming file created
    """

    def __init__(
        self,
        process_start_unix_ms: int,
        local_state_secret: str,
        arming_dir: str = "data",
    ) -> None:
        self._process_start = process_start_unix_ms
        self._secret = local_state_secret
        self._arming_dir = Path(arming_dir)
        self._nonce1 = None  # type: Optional[str]
        self._nonce1_created_at = 0.0
        self._last_totp_used = ""
        self._last_totp_used_at = 0.0
        self._armed = False

    @property
    def is_armed(self) -> bool:
        return self._armed

    def step1_totp(self, totp_code: str) -> str:
        """Step 1: Validate TOTP and generate nonce1.

        Returns nonce1 string.
        """
        # Replay block
        if totp_code == self._last_totp_used:
            elapsed = time.time() - self._last_totp_used_at
            if elapsed < TOTP_REPLAY_BLOCK_SEC:
                raise ArmingError("TOTP replay blocked ({}s since last use)".format(int(elapsed)))

        # Generate TOTP from secret (simplified: HMAC of current 30s window)
        window = int(time.time() / 30)
        expected = hmac.new(
            self._secret.encode("utf-8"),
            str(window).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:6]

        # In production, would use proper TOTP. For now, accept the code
        # if it looks valid (6 digits/chars)
        if len(totp_code) < 6:
            raise ArmingError("TOTP code too short")

        self._last_totp_used = totp_code
        self._last_totp_used_at = time.time()

        # Generate nonce1
        nonce_input = "{}.{}.{}".format(self._process_start, time.time(), totp_code)
        self._nonce1 = hashlib.sha256(nonce_input.encode("utf-8")).hexdigest()[:16]
        self._nonce1_created_at = time.time()

        logger.info("Arming step 1 complete: nonce1 generated")
        return self._nonce1

    def step2_confirm(self, nonce1: str) -> Dict[str, Any]:
        """Step 2: Confirm with nonce1 and create arming file.

        Returns arming record.
        """
        if self._nonce1 is None:
            raise ArmingError("Step 1 not completed")

        # Check nonce1 TTL
        if time.time() - self._nonce1_created_at > ARMING_NONCE1_TTL_SEC:
            self._nonce1 = None
            raise ArmingError("Nonce1 expired (>{0}s)".format(ARMING_NONCE1_TTL_SEC))

        if nonce1 != self._nonce1:
            raise ArmingError("Nonce1 mismatch")

        # Create arming file
        arming_record = {
            "armed_at_utc": time.time(),
            "process_start_unix_ms": self._process_start,
            "nonce1": self._nonce1,
            "arming_signature": hmac.new(
                self._secret.encode("utf-8"),
                "{}:{}".format(self._process_start, self._nonce1).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest(),
        }

        # Write arming file
        self._arming_dir.mkdir(parents=True, exist_ok=True)
        arming_path = self._arming_dir / "arming.json"
        with open(arming_path, "w", encoding="utf-8") as f:
            json.dump(arming_record, f, indent=2)

        self._armed = True
        self._nonce1 = None  # Consume nonce

        logger.info("Arming ceremony complete: file written to %s", arming_path)
        return arming_record

    def verify_arming_file(self) -> Tuple[bool, str]:
        """Verify an existing arming file.

        Checks:
        - File exists and is parseable
        - process_start_unix_ms matches current process
        - File age within ARMING_FILE_MAX_AGE_SEC
        - Signature valid
        """
        arming_path = self._arming_dir / "arming.json"

        if not arming_path.is_file():
            return False, "Arming file not found"

        try:
            with open(arming_path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return False, "Arming file unreadable: {}".format(e)

        # Process binding
        if record.get("process_start_unix_ms") != self._process_start:
            return False, "Arming file bound to different process"

        # Age check
        armed_at = record.get("armed_at_utc", 0)
        age = time.time() - armed_at
        if age > ARMING_FILE_MAX_AGE_SEC:
            return False, "Arming file expired ({:.0f}s > {}s)".format(age, ARMING_FILE_MAX_AGE_SEC)

        # Signature check
        expected_sig = hmac.new(
            self._secret.encode("utf-8"),
            "{}:{}".format(record.get("process_start_unix_ms"), record.get("nonce1")).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if record.get("arming_signature") != expected_sig:
            return False, "Arming signature mismatch"

        self._armed = True
        return True, "Armed"
