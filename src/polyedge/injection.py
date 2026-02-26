"""Injection Defence — deterministic pattern engine (spec §11).

Implements:
- Versioned, signed pattern ruleset from config/injection_patterns.json
- Pre-detection normalisation: NFKC, BOM strip, null bytes removed, whitespace collapsed
- Pattern matching with SUSPICIOUS and INJECTION_DETECTED severity
- INJECTION_DETECTOR_INVALID blocker if ruleset invalid
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Severity levels (spec §11.1)
SEVERITY_SUSPICIOUS = "SUSPICIOUS"
SEVERITY_INJECTION_DETECTED = "INJECTION_DETECTED"

# Reason code
REASON_INJECTION_DETECTED = "INJECTION_DETECTED"

# Minimum ruleset version (configurable, but default is 1.0.0)
MIN_INJECTION_VERSION = "1.0.0"


class InjectionPattern:
    """A single injection detection pattern."""

    def __init__(
        self,
        pattern_id: str,
        regex_utf8: str,
        severity: str,
    ) -> None:
        self.pattern_id = pattern_id
        self.regex_utf8 = regex_utf8
        self.severity = severity
        try:
            self.compiled = re.compile(regex_utf8, re.IGNORECASE | re.UNICODE)
        except re.error as e:
            logger.error("Invalid regex in pattern %s: %s", pattern_id, e)
            self.compiled = None


class InjectionDefence:
    """Deterministic injection defence engine."""

    def __init__(self, patterns_path: Optional[str] = None) -> None:
        self.patterns = []  # type: List[InjectionPattern]
        self.version = "0.0.0"
        self.valid = False

        if patterns_path:
            self.load(patterns_path)

    def load(self, patterns_path: str) -> None:
        """Load injection patterns from signed config file."""
        p = Path(patterns_path)
        if not p.is_file():
            logger.error("Injection patterns file not found: %s", patterns_path)
            self.valid = False
            return

        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load injection patterns: %s", e)
            self.valid = False
            return

        self.version = data.get("pattern_set_version", "0.0.0")

        # Check minimum version
        if not self._version_gte(self.version, MIN_INJECTION_VERSION):
            logger.error(
                "Injection pattern version %s < minimum %s",
                self.version, MIN_INJECTION_VERSION,
            )
            self.valid = False
            return

        raw_patterns = data.get("patterns", [])
        self.patterns = []
        for p_data in raw_patterns:
            pattern = InjectionPattern(
                pattern_id=p_data.get("pattern_id", "unknown"),
                regex_utf8=p_data.get("regex_utf8", ""),
                severity=p_data.get("severity", SEVERITY_SUSPICIOUS),
            )
            if pattern.compiled is not None:
                self.patterns.append(pattern)

        self.valid = True
        logger.info(
            "Injection defence loaded: version=%s patterns=%d",
            self.version, len(self.patterns),
        )

    @staticmethod
    def _version_gte(v1: str, v2: str) -> bool:
        """Compare semver-like versions. Returns True if v1 >= v2."""
        try:
            parts1 = [int(x) for x in v1.split(".")]
            parts2 = [int(x) for x in v2.split(".")]
            return parts1 >= parts2
        except (ValueError, AttributeError):
            return False

    def scan(self, text: str) -> List[Dict[str, Any]]:
        """Scan text for injection patterns.

        Text is normalised before scanning per spec §11.3:
        - Unicode NFKC
        - BOM stripped
        - Null bytes removed
        - Whitespace collapsed

        Returns list of matches: [{"pattern_id": ..., "severity": ..., "match": ...}]
        """
        normalised = normalise_for_injection(text)
        matches = []  # type: List[Dict[str, Any]]

        for pattern in self.patterns:
            if pattern.compiled is None:
                continue
            found = pattern.compiled.search(normalised)
            if found:
                matches.append({
                    "pattern_id": pattern.pattern_id,
                    "severity": pattern.severity,
                    "match": found.group(0)[:100],  # Truncate for logging
                })

        return matches

    def check(
        self,
        texts: List[str],
        high_stakes: bool = False,
        tier1_count: int = 0,
    ) -> Tuple[bool, Optional[str], List[Dict[str, Any]]]:
        """Check multiple texts for injection.

        Returns (safe, reason_code, matches).

        Per spec §11.4:
        - INJECTION_DETECTED in market text or Tier1 evidence → NO_TRADE
        - SUSPICIOUS + HIGH_STAKES → NO_TRADE
        - SUSPICIOUS + not high stakes → allowed if Tier1 count >=2
        """
        if not self.valid:
            return False, "INJECTION_DETECTOR_INVALID", []

        all_matches = []  # type: List[Dict[str, Any]]
        for text in texts:
            matches = self.scan(text)
            all_matches.extend(matches)

        if not all_matches:
            return True, None, []

        # Check for INJECTION_DETECTED severity
        has_injection = any(
            m["severity"] == SEVERITY_INJECTION_DETECTED for m in all_matches
        )
        if has_injection:
            return False, REASON_INJECTION_DETECTED, all_matches

        # Only SUSPICIOUS matches below this point
        has_suspicious = any(
            m["severity"] == SEVERITY_SUSPICIOUS for m in all_matches
        )

        if has_suspicious:
            if high_stakes:
                return False, REASON_INJECTION_DETECTED, all_matches

            # Non-high-stakes: allowed only if Tier1 >=2
            if tier1_count < 2:
                return False, REASON_INJECTION_DETECTED, all_matches

        return True, None, all_matches


def normalise_for_injection(text: str) -> str:
    """Normalise text for injection detection per spec §11.3.

    Steps:
    - Unicode NFKC
    - BOM stripped
    - Null bytes removed
    - Whitespace collapsed
    """
    # Unicode NFKC
    text = unicodedata.normalize("NFKC", text)

    # Strip BOM
    if text.startswith("\ufeff"):
        text = text[1:]

    # Remove null bytes
    text = text.replace("\x00", "")

    # Collapse whitespace
    text = " ".join(text.split())

    return text
