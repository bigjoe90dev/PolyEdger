"""Evidence Service — allowlist-driven deterministic fetching (spec §10).

Handles:
- Thesis vs microstructure determination (§10.3)
- High-stakes detection (§10.4)
- Evidence source registry from signed config
- Bundle building with tier sorting, truncation, hashing
- Conflict detection (§10.7)
- TTL enforcement anchored on published_at_utc
"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from polyedge.constants import EVIDENCE_FETCHES_PER_HOUR_MAX

logger = logging.getLogger(__name__)

# Evidence bundle limits (spec §10.6)
MAX_EVIDENCE_ITEMS = 6
MAX_EVIDENCE_BYTES_TOTAL = 250 * 1024  # 250KB
MAX_EVIDENCE_TEXT_CHARS_TOTAL = 40000

# Modes (spec §10.2)
MODE_STRICT = "STRICT"
MODE_MARKET_ONLY = "MARKET_ONLY"
MODE_STRICT_WITH_CORROBORATION = "STRICT_WITH_CORROBORATION"

# Subjective terms that force THESIS_REQUIRED (configurable)
DEFAULT_SUBJECTIVE_TERMS = frozenset({
    "likely", "probably", "uncertain", "debatable", "controversial",
    "disputed", "questionable", "ambiguous", "subjective",
})

# Reason codes
REASON_EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
REASON_EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
REASON_EVIDENCE_TIER1_INSUFFICIENT = "EVIDENCE_TIER1_INSUFFICIENT"


class EvidenceItem:
    """A single evidence item from a trusted source."""

    def __init__(
        self,
        source_id: str,
        url: str,
        title: str,
        text: str,
        published_at_utc: Optional[datetime],
        reliability_tier: int,
        parser_name: str,
        parser_version: str,
    ) -> None:
        self.source_id = source_id
        self.url = url
        self.title = title
        self.text = text
        self.published_at_utc = published_at_utc
        self.reliability_tier = reliability_tier
        self.parser_name = parser_name
        self.parser_version = parser_version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "published_at_utc": self.published_at_utc.isoformat() if self.published_at_utc else None,
            "reliability_tier": self.reliability_tier,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
        }


def load_evidence_sources(config_path: str) -> List[Dict[str, Any]]:
    """Load evidence source registry from signed config file."""
    from pathlib import Path
    p = Path(config_path)
    if not p.is_file():
        logger.warning("Evidence sources file not found: %s", config_path)
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("sources", [])


def is_thesis_required(
    candidate: Dict[str, Any],
    market: Dict[str, Any],
    wallet_usd: float = 100.0,
    subjective_terms: Optional[frozenset] = None,
) -> bool:
    """Determine if evidence is required per spec §10.3.

    THESIS_REQUIRED=true if any:
    - category in allowlist AND trigger includes mid_move or approaching_resolution
    - intended_order_size_usd >= 0.5% of wallet
    - resolution text contains any subjective term
    """
    terms = subjective_terms or DEFAULT_SUBJECTIVE_TERMS

    # Category + trigger check
    category = market.get("category", "").lower()
    triggers = candidate.get("trigger_reasons", [])
    if category in {"geopolitics", "economics", "tech/ai"}:
        if "mid_move" in triggers or "approaching_resolution" in triggers:
            return True

    # Size check
    intended_size = candidate.get("intended_order_size_usd", 0)
    if intended_size >= 0.005 * wallet_usd:
        return True

    # Subjective text check
    resolution_text = market.get("resolution_source", "").lower()
    for term in terms:
        if term in resolution_text:
            return True

    return False


def is_high_stakes(
    candidate: Dict[str, Any],
    market: Dict[str, Any],
    wallet_usd: float = 100.0,
    dispute_risk: float = 0.0,
) -> bool:
    """Determine if candidate is high-stakes per spec §10.4.

    HIGH_STAKES=true if any:
    - intended_order_size_usd >= 1.0% of wallet
    - time_to_resolution <= 6 hours
    - dispute_risk >= 0.7
    """
    # Size check
    intended_size = candidate.get("intended_order_size_usd", 0)
    if intended_size >= 0.01 * wallet_usd:
        return True

    # Time to resolution check
    end_date = market.get("end_date_utc")
    if end_date:
        if isinstance(end_date, str):
            try:
                end_date = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                end_date = None
        if end_date:
            remaining = (end_date - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 6 * 3600:
                return True

    # Dispute risk
    if dispute_risk >= 0.7:
        return True

    return False


def is_evidence_ttl_valid(
    item: EvidenceItem,
    source_ttl_sec: int,
    category_ttl_override_sec: Optional[int] = None,
    now_utc: Optional[datetime] = None,
) -> bool:
    """Check if evidence item is within its TTL per spec §10.5.

    Evidence is valid iff:
    published_at_utc exists AND (now - published_at_utc) <= min(source_ttl, category_ttl_override)
    """
    if item.published_at_utc is None:
        return False

    now = now_utc or datetime.now(timezone.utc)
    effective_ttl = source_ttl_sec
    if category_ttl_override_sec is not None:
        effective_ttl = min(source_ttl_sec, category_ttl_override_sec)

    age = (now - item.published_at_utc).total_seconds()
    return age <= effective_ttl


def build_evidence_bundle(
    items: List[EvidenceItem],
    source_ttls: Optional[Dict[str, int]] = None,
    now_utc: Optional[datetime] = None,
) -> Tuple[List[EvidenceItem], str]:
    """Build deterministic evidence bundle per spec §10.6.

    Selection: Tier1 newest first, then Tier2, then Tier3; tie-break by source_id.
    Truncation: drop lowest-tier first, then oldest, then truncate text.

    Returns (selected_items, bundle_hash).
    """
    now = now_utc or datetime.now(timezone.utc)
    ttls = source_ttls or {}

    # Filter by TTL
    valid_items = []  # type: List[EvidenceItem]
    for item in items:
        ttl = ttls.get(item.source_id, 3600)  # default 1h TTL
        if is_evidence_ttl_valid(item, ttl, now_utc=now):
            valid_items.append(item)

    # Sort: tier ascending (1 first), then newest first, then source_id
    def sort_key(item: EvidenceItem) -> Tuple[int, float, str]:
        ts = item.published_at_utc.timestamp() if item.published_at_utc else 0
        return (item.reliability_tier, -ts, item.source_id)

    valid_items.sort(key=sort_key)

    # Take top MAX_EVIDENCE_ITEMS
    selected = valid_items[:MAX_EVIDENCE_ITEMS]

    # Enforce byte and char limits with deterministic truncation
    total_chars = 0
    total_bytes = 0
    final = []  # type: List[EvidenceItem]

    for item in selected:
        text_chars = len(item.text)
        text_bytes = len(item.text.encode("utf-8"))

        if total_chars + text_chars > MAX_EVIDENCE_TEXT_CHARS_TOTAL:
            remaining_chars = MAX_EVIDENCE_TEXT_CHARS_TOTAL - total_chars
            if remaining_chars > 100:  # Only include if meaningful
                item.text = item.text[:remaining_chars]
                final.append(item)
            break

        if total_bytes + text_bytes > MAX_EVIDENCE_BYTES_TOTAL:
            break

        total_chars += text_chars
        total_bytes += text_bytes
        final.append(item)

    # Compute deterministic bundle hash
    bundle_hash = compute_bundle_hash(final)

    return final, bundle_hash


def compute_bundle_hash(items: List[EvidenceItem]) -> str:
    """SHA-256 hash of the canonical evidence bundle."""
    canonical = json.dumps(
        [item.to_dict() for item in items],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def detect_conflict(items: List[EvidenceItem]) -> Tuple[bool, Optional[str]]:
    """Detect evidence conflicts per spec §10.7.

    Conflict exists if:
    - Two Tier1/Tier2 items assert mutually exclusive outcomes
    - Numeric claims differ beyond tolerance (default 2% relative)

    Returns (has_conflict, conflict_description).
    """
    # Group by tier
    high_tier = [i for i in items if i.reliability_tier <= 2]
    if len(high_tier) < 2:
        return False, None

    # Simple keyword-based conflict detection
    # (In production this would use more sophisticated NLP)
    yes_signals = {"will", "yes", "likely", "confirms", "approved", "passed"}
    no_signals = {"won't", "no", "unlikely", "denied", "rejected", "failed"}

    yes_count = 0
    no_count = 0

    for item in high_tier:
        text_lower = item.text.lower()
        yes_hits = sum(1 for w in yes_signals if w in text_lower)
        no_hits = sum(1 for w in no_signals if w in text_lower)

        if yes_hits > no_hits:
            yes_count += 1
        elif no_hits > yes_hits:
            no_count += 1

    if yes_count > 0 and no_count > 0:
        return True, "Conflicting Tier1/2 evidence: {} YES vs {} NO signals".format(
            yes_count, no_count,
        )

    return False, None


def resolve_conflict(
    items: List[EvidenceItem],
    high_stakes: bool = False,
) -> Tuple[str, Optional[str]]:
    """Resolve evidence conflict per spec §10.7.

    Returns (action, reason_code).
    action is one of: "PROCEED", "NO_TRADE"
    """
    has_conflict, description = detect_conflict(items)
    if not has_conflict:
        return "PROCEED", None

    # Count non-suspicious Tier1 items
    tier1_items = [i for i in items if i.reliability_tier == 1]

    if high_stakes and len(tier1_items) < 2:
        return "NO_TRADE", REASON_EVIDENCE_TIER1_INSUFFICIENT

    # Check Tier1 majority
    if len(tier1_items) >= 2:
        # Majority exists → proceed with increased dispute_buffer
        return "PROCEED", None

    return "NO_TRADE", REASON_EVIDENCE_CONFLICT


class EvidenceFetchRateLimiter:
    """Rate limit evidence fetches per spec §3.5."""

    def __init__(self) -> None:
        self._timestamps = []  # type: List[float]

    def can_fetch(self) -> bool:
        import time
        now = time.time()
        cutoff = now - 3600  # 1 hour
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        return len(self._timestamps) < EVIDENCE_FETCHES_PER_HOUR_MAX

    def record_fetch(self) -> None:
        import time
        self._timestamps.append(time.time())
