"""Market Registry — Gamma sync skeleton + eligibility (spec §6).

Handles:
- Label normalisation (Unicode NFKC, trim, collapse, uppercase)
- Binary YES/NO detection
- Category allow/deny filtering
- critical_field_hash (SHA-256)
- Market upsert to DB
"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from polyedge.constants import ALLOWLIST_CATEGORIES, DENYLIST_CATEGORIES

logger = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


def normalise_label(label: str) -> str:
    """Normalise an outcome label per spec §6.3.

    Steps: Unicode NFKC -> trim -> collapse whitespace -> uppercase.
    """
    text = unicodedata.normalize("NFKC", label)
    text = text.strip()
    text = " ".join(text.split())
    return text.upper()


def is_binary_eligible(outcomes: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    """Check if market outcomes map to binary YES/NO.

    Returns (eligible, reason_if_not).
    Per spec §6.3: exactly two outcomes, labels normalise to YES and NO.
    """
    if len(outcomes) != 2:
        return False, "NON_BINARY: {} outcomes (need exactly 2)".format(len(outcomes))

    labels = {normalise_label(o.get("value", o.get("label", ""))) for o in outcomes}

    if labels != {"YES", "NO"}:
        return False, "NON_BINARY: labels={} (need exactly YES and NO)".format(labels)

    return True, None


def classify_category(category: str) -> Tuple[bool, Optional[str]]:
    """Check if market category is allowed.

    Returns (allowed, reason_if_not).
    """
    cat_lower = category.lower().strip()

    if cat_lower in {c.lower() for c in DENYLIST_CATEGORIES}:
        return False, "MARKET_NOT_ELIGIBLE: category '{}' is in denylist".format(category)

    if cat_lower in {c.lower() for c in ALLOWLIST_CATEGORIES}:
        return True, None

    return False, "MARKET_NOT_ELIGIBLE: category '{}' not in allowlist".format(category)


def compute_critical_field_hash(
    title: str,
    description: str,
    resolution_source: str,
    end_date: str,
    yes_token_id: str,
    no_token_id: str,
    category: str,
) -> str:
    """SHA-256 of canonical critical fields per spec §6.2.

    Hash = SHA256(title|description|resolutionSource|endDate|token_ids|category)
    Uses pipe separator, deterministic ordering.
    """
    canonical = "|".join([
        title,
        description,
        resolution_source,
        end_date,
        yes_token_id,
        no_token_id,
        category,
    ])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_token_ids(outcomes: List[Dict[str, Any]]) -> Tuple[str, str]:
    """Extract YES and NO token IDs from outcomes list."""
    yes_id = ""
    no_id = ""
    for o in outcomes:
        label = normalise_label(o.get("value", o.get("label", "")))
        token_id = o.get("asset_id", o.get("token_id", ""))
        if label == "YES":
            yes_id = str(token_id)
        elif label == "NO":
            no_id = str(token_id)
    return yes_id, no_id


async def fetch_markets_from_gamma(
    limit: int = 100,
    offset: int = 0,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    """Fetch markets from Gamma API.

    In production this performs full/delta sync (§6.1).
    """
    params = {
        "limit": limit,
        "offset": offset,
    }  # type: Dict[str, Any]
    if active_only:
        params["active"] = True
        params["closed"] = False

    async with aiohttp.ClientSession() as session:
        url = "{}/markets".format(GAMMA_API_BASE)
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.json()


def generate_mock_markets() -> List[Dict[str, Any]]:
    """Return mock market data for testing without network calls."""
    return [
        {
            "id": "mock-market-001",
            "condition_id": "cond-001",
            "question": "Will AI surpass human-level on ARC-AGI by end of 2026?",
            "description": "Resolves YES if AI achieves >95% on ARC-AGI benchmark.",
            "category": "tech/AI",
            "tags": ["AI", "benchmarks"],
            "resolutionSource": "ARC Prize Foundation official results",
            "endDate": "2026-12-31T23:59:59Z",
            "outcomes": [
                {"value": "Yes", "asset_id": "tok-yes-001"},
                {"value": "No", "asset_id": "tok-no-001"},
            ],
            "volume24hr": 12500.0,
            "liquidityClob": 45000.0,
        },
        {
            "id": "mock-market-002",
            "condition_id": "cond-002",
            "question": "Will the Fed cut rates in March 2026?",
            "description": "Resolves YES if Federal Reserve announces rate cut.",
            "category": "economics",
            "tags": ["fed", "interest-rates"],
            "resolutionSource": "Federal Reserve official statement",
            "endDate": "2026-03-20T18:00:00Z",
            "outcomes": [
                {"value": "Yes", "asset_id": "tok-yes-002"},
                {"value": "No", "asset_id": "tok-no-002"},
            ],
            "volume24hr": 85000.0,
            "liquidityClob": 120000.0,
        },
        {
            "id": "mock-market-003",
            "condition_id": "cond-003",
            "question": "Which team wins Super Bowl 2027?",
            "description": "Pick the winning team.",
            "category": "sports",
            "tags": ["NFL"],
            "resolutionSource": "NFL official results",
            "endDate": "2027-02-14T23:59:59Z",
            "outcomes": [
                {"value": "Team A", "asset_id": "tok-a-003"},
                {"value": "Team B", "asset_id": "tok-b-003"},
                {"value": "Team C", "asset_id": "tok-c-003"},
            ],
            "volume24hr": 500000.0,
            "liquidityClob": 900000.0,
        },
        {
            "id": "mock-market-004",
            "condition_id": "cond-004",
            "question": "Will there be a ceasefire in Ukraine by June 2026?",
            "description": "Resolves YES if an official ceasefire is declared.",
            "category": "geopolitics",
            "tags": ["ukraine", "conflict"],
            "resolutionSource": "United Nations Security Council official communication",
            "endDate": "2026-06-30T23:59:59Z",
            "outcomes": [
                {"value": "YES", "asset_id": "tok-yes-004"},
                {"value": "NO", "asset_id": "tok-no-004"},
            ],
            "volume24hr": 200000.0,
            "liquidityClob": 350000.0,
        },
    ]


def parse_gamma_market(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a raw Gamma API market into our internal format.

    Returns None if the market cannot be parsed (missing critical fields).
    """
    market_id = raw.get("id", raw.get("condition_id", ""))
    if not market_id:
        return None

    outcomes = raw.get("outcomes", raw.get("tokens", []))
    if not outcomes:
        return None

    eligible, reason = is_binary_eligible(outcomes)
    cat_allowed, cat_reason = classify_category(raw.get("category", ""))

    if not cat_allowed:
        eligible = False
        reason = cat_reason

    yes_id, no_id = _extract_token_ids(outcomes)

    title = raw.get("question", raw.get("title", ""))
    description = raw.get("description", "")
    resolution_source = raw.get("resolutionSource", "")
    end_date = raw.get("endDate", "")
    category = raw.get("category", "")

    cfh = compute_critical_field_hash(
        title, description, resolution_source, end_date, yes_id, no_id, category,
    )

    return {
        "market_id": str(market_id),
        "condition_id": raw.get("condition_id", str(market_id)),
        "event_id": raw.get("event_id"),
        "category": category,
        "tags": json.dumps(raw.get("tags", [])),
        "title": title,
        "description": description,
        "resolution_source": resolution_source,
        "end_date_utc": end_date or None,
        "yes_token_id": yes_id,
        "no_token_id": no_id,
        "volume_24h_usd": raw.get("volume24hr"),
        "liquidity_usd": raw.get("liquidityClob"),
        "critical_field_hash": cfh,
        "is_binary_eligible": eligible,
        "eligibility_reason": reason,
    }


async def sync_markets(pool: Any, markets: List[Dict[str, Any]]) -> Dict[str, int]:
    """Upsert parsed markets into DB.

    Returns stats: {"inserted": N, "updated": N, "skipped": N}.
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0}

    for m in markets:
        parsed = parse_gamma_market(m)
        if parsed is None:
            stats["skipped"] += 1
            continue

        # Check for critical_field_hash change (§6.4)
        existing = await pool.fetchrow(
            "SELECT critical_field_hash, frozen FROM markets WHERE market_id = $1",
            parsed["market_id"],
        )

        if existing is not None:
            if existing["critical_field_hash"] != parsed["critical_field_hash"]:
                logger.warning(
                    "Market %s critical_field_hash changed — freezing",
                    parsed["market_id"],
                )
                await pool.execute(
                    "UPDATE markets SET frozen = TRUE, last_synced_utc = now() WHERE market_id = $1",
                    parsed["market_id"],
                )
                stats["updated"] += 1
                continue

        end_date = None
        if parsed["end_date_utc"]:
            try:
                end_date = datetime.fromisoformat(
                    parsed["end_date_utc"].replace("Z", "+00:00")
                )
            except ValueError:
                end_date = None

        result = await pool.execute(
            """
            INSERT INTO markets (
                market_id, condition_id, event_id, category, tags,
                title, description, resolution_source, end_date_utc,
                yes_token_id, no_token_id, volume_24h_usd, liquidity_usd,
                critical_field_hash, is_binary_eligible, eligibility_reason,
                last_synced_utc
            ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,now())
            ON CONFLICT (market_id) DO UPDATE SET
                category = EXCLUDED.category,
                tags = EXCLUDED.tags,
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                resolution_source = EXCLUDED.resolution_source,
                end_date_utc = EXCLUDED.end_date_utc,
                volume_24h_usd = EXCLUDED.volume_24h_usd,
                liquidity_usd = EXCLUDED.liquidity_usd,
                critical_field_hash = EXCLUDED.critical_field_hash,
                is_binary_eligible = EXCLUDED.is_binary_eligible,
                eligibility_reason = EXCLUDED.eligibility_reason,
                last_synced_utc = now()
            """,
            parsed["market_id"],
            parsed["condition_id"],
            parsed["event_id"],
            parsed["category"],
            parsed["tags"],
            parsed["title"],
            parsed["description"],
            parsed["resolution_source"],
            end_date,
            parsed["yes_token_id"],
            parsed["no_token_id"],
            parsed["volume_24h_usd"],
            parsed["liquidity_usd"],
            parsed["critical_field_hash"],
            parsed["is_binary_eligible"],
            parsed["eligibility_reason"],
        )

        if "INSERT 0 1" in result:
            stats["inserted"] += 1
        else:
            stats["updated"] += 1

    logger.info(
        "Market sync: inserted=%d updated=%d skipped=%d",
        stats["inserted"],
        stats["updated"],
        stats["skipped"],
    )
    return stats


async def get_registry_stats(pool: Any) -> Dict[str, Any]:
    """Get summary statistics for the market registry."""
    total = await pool.fetchval("SELECT COUNT(*) FROM markets")
    eligible = await pool.fetchval("SELECT COUNT(*) FROM markets WHERE is_binary_eligible = TRUE")
    frozen = await pool.fetchval("SELECT COUNT(*) FROM markets WHERE frozen = TRUE")

    cats = await pool.fetch(
        "SELECT category, COUNT(*) as cnt FROM markets GROUP BY category ORDER BY cnt DESC"
    )

    return {
        "total_markets": total,
        "binary_eligible": eligible,
        "frozen": frozen,
        "by_category": {r["category"]: r["cnt"] for r in cats},
    }
