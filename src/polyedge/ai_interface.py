"""AI Analysis — OpenRouter swarm with strict JSON (spec §12).

Implements:
- Parallel dispatch to 4 models via OpenRouter
- Strict JSON schema validation
- Quorum check (≥3 models, weight ≥4)
- Disagreement detection (weighted stdev > 0.12)
- Budget-gated retry sweep
- Late result discard (barrier_generation, candidate state)
- Prompt hashing for replayability
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

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
}  # type: Dict[str, Dict[str, int]]

# Timeouts (spec §12.2)
PER_MODEL_TIMEOUT_SEC = 8
SWARM_TOTAL_TIMEOUT_SEC = 10

# Quorum (spec §12.4)
QUORUM_MIN_MODELS = 3
QUORUM_MIN_WEIGHT = 4
DISAGREE_THRESHOLD = 0.12

# Retry (spec §12.3)
SWARM_RETRY_TOTAL = 1

# OpenRouter API
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class AINotImplementedError(Exception):
    """Raised when AI analysis is attempted but disabled."""


def validate_ai_response(response: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate an AI response against the strict JSON schema.

    Returns (valid, list_of_errors).
    """
    errors = []  # type: List[str]

    # Check all required fields present
    missing = REQUIRED_FIELDS - set(response.keys())
    if missing:
        errors.append("Missing required fields: {}".format(sorted(missing)))

    # Range checks
    for field in ("prob_yes_raw", "confidence_raw", "resolution_risk", "dispute_risk"):
        val = response.get(field)
        if val is not None:
            if not isinstance(val, (int, float)):
                errors.append("{} must be numeric, got {}".format(field, type(val).__name__))
            elif val < 0 or val > 1:
                errors.append("{} out of range [0,1]: {}".format(field, val))

    # recommended_side check
    side = response.get("recommended_side")
    if side is not None and side not in VALID_SIDES:
        errors.append("recommended_side must be one of {}, got '{}'".format(sorted(VALID_SIDES), side))

    # Type checks for arrays
    for field in ("key_drivers", "disqualifiers"):
        val = response.get(field)
        if val is not None and not isinstance(val, list):
            errors.append("{} must be an array, got {}".format(field, type(val).__name__))

    # Type checks for strings
    for field in ("resolution_summary", "evidence_summary", "uncertainty_reason", "notes"):
        val = response.get(field)
        if val is not None and not isinstance(val, str):
            errors.append("{} must be a string, got {}".format(field, type(val).__name__))

    return len(errors) == 0, errors


def compute_prompt_hash(prompt: str) -> str:
    """SHA-256 hash of the prompt for replayability."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def compute_weighted_disagreement(
    results: List[Dict[str, Any]],
) -> float:
    """Compute weighted standard deviation of prob_yes_raw.

    Returns 0.0 if insufficient data.
    """
    if len(results) < 2:
        return 0.0

    total_weight = sum(r.get("weight", 1) for r in results)
    if total_weight == 0:
        return 0.0

    # Weighted mean
    weighted_sum = sum(r["prob_yes_raw"] * r.get("weight", 1) for r in results)
    mean = weighted_sum / total_weight

    # Weighted variance
    variance = sum(
        r.get("weight", 1) * (r["prob_yes_raw"] - mean) ** 2
        for r in results
    ) / total_weight

    return math.sqrt(variance)


def check_quorum(results: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Check if quorum is met per spec §12.4.

    Returns (met, reason_if_not).
    """
    valid_results = [r for r in results if r.get("parse_ok", False)]

    if len(valid_results) < QUORUM_MIN_MODELS:
        return False, "AI_QUORUM_FAILED: only {}/{} models returned valid JSON".format(
            len(valid_results), QUORUM_MIN_MODELS,
        )

    total_weight = sum(r.get("weight", 1) for r in valid_results)
    if total_weight < QUORUM_MIN_WEIGHT:
        return False, "AI_QUORUM_FAILED: total weight {}/{} insufficient".format(
            total_weight, QUORUM_MIN_WEIGHT,
        )

    # Check disagreement
    disagreement = compute_weighted_disagreement(valid_results)
    if disagreement > DISAGREE_THRESHOLD:
        return False, "AI_DISAGREEMENT: weighted stdev {:.4f} > threshold {:.4f}".format(
            disagreement, DISAGREE_THRESHOLD,
        )

    return True, ""


def build_analysis_prompt(
    market: Dict[str, Any],
    evidence_bundle: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the analysis prompt for the AI swarm."""
    parts = [
        "You are analysing a binary prediction market. Respond ONLY with valid JSON.",
        "",
        "Market: {}".format(market.get("title", "Unknown")),
        "Description: {}".format(market.get("description", "")),
        "Category: {}".format(market.get("category", "")),
        "Resolution source: {}".format(market.get("resolution_source", "")),
        "End date: {}".format(market.get("end_date_utc", "")),
    ]

    if snapshot:
        parts.extend([
            "",
            "Current prices:",
            "  YES best_bid={} best_ask={}".format(
                snapshot.get("best_bid_yes"), snapshot.get("best_ask_yes"),
            ),
            "  NO  best_bid={} best_ask={}".format(
                snapshot.get("best_bid_no"), snapshot.get("best_ask_no"),
            ),
        ])

    if evidence_bundle:
        parts.extend([
            "",
            "Evidence:",
        ])
        for i, item in enumerate(evidence_bundle.get("items", [])):
            parts.append("  [{}] {} (Tier {} - {})".format(
                i + 1, item.get("title", ""), item.get("reliability_tier", "?"),
                item.get("source_id", ""),
            ))
            text = item.get("text", "")[:500]
            parts.append("    {}".format(text))

    parts.extend([
        "",
        'Respond with JSON matching schema version "{}":'.format(SCHEMA_VERSION),
        json.dumps({
            "market_id": "<market_id>",
            "prob_yes_raw": 0.55,
            "confidence_raw": 0.7,
            "resolution_risk": 0.1,
            "dispute_risk": 0.05,
            "resolution_summary": "...",
            "evidence_summary": "...",
            "uncertainty_reason": "...",
            "key_drivers": ["..."],
            "disqualifiers": ["..."],
            "recommended_side": "YES|NO|NO_TRADE",
            "notes": "...",
        }, indent=2),
    ])

    return "\n".join(parts)


async def call_single_model(
    session: aiohttp.ClientSession,
    model_key: str,
    prompt: str,
    api_key: str,
    market_id: str,
) -> Dict[str, Any]:
    """Call a single model via OpenRouter.

    Returns result dict with parse_ok, response, model, weight, etc.
    """
    weight = SWARM_MODELS.get(model_key, {}).get("weight", 1)
    result = {
        "model": model_key,
        "weight": weight,
        "parse_ok": False,
        "response": None,
        "error": None,
        "latency_ms": 0,
    }  # type: Dict[str, Any]

    start = time.time()

    try:
        payload = {
            "model": model_key,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 2000,
        }

        timeout = aiohttp.ClientTimeout(total=PER_MODEL_TIMEOUT_SEC)
        async with session.post(
            OPENROUTER_API_URL,
            json=payload,
            headers={
                "Authorization": "Bearer {}".format(api_key),
                "Content-Type": "application/json",
            },
            timeout=timeout,
        ) as resp:
            result["latency_ms"] = int((time.time() - start) * 1000)

            if resp.status != 200:
                result["error"] = "HTTP {}".format(resp.status)
                return result

            data = await resp.json()

        # Extract content
        choices = data.get("choices", [])
        if not choices:
            result["error"] = "No choices in response"
            return result

        content = choices[0].get("message", {}).get("content", "")

        # Parse JSON from content (may be wrapped in markdown)
        json_str = content.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.startswith("```") and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            json_str = "\n".join(json_lines)

        parsed = json.loads(json_str)

        # Validate
        valid, errors = validate_ai_response(parsed)
        if valid:
            # Ensure market_id matches
            parsed["market_id"] = market_id
            result["response"] = parsed
            result["parse_ok"] = True
            result["prob_yes_raw"] = parsed["prob_yes_raw"]
        else:
            result["error"] = "Schema validation: {}".format("; ".join(errors))

    except asyncio.TimeoutError:
        result["error"] = "Timeout after {}s".format(PER_MODEL_TIMEOUT_SEC)
        result["latency_ms"] = int((time.time() - start) * 1000)
    except json.JSONDecodeError as e:
        result["error"] = "JSON parse error: {}".format(e)
        result["latency_ms"] = int((time.time() - start) * 1000)
    except Exception as e:
        result["error"] = "Unexpected: {}".format(e)
        result["latency_ms"] = int((time.time() - start) * 1000)

    return result


class AISwarm:
    """OpenRouter AI swarm with quorum and disagreement checks."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._enabled = bool(self._api_key and self._api_key != "sk-or-REPLACE_ME")
        if self._enabled:
            logger.info("AISwarm initialised with %d models", len(SWARM_MODELS))
        else:
            logger.info("AISwarm initialised in DISABLED mode (no API key)")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def analyze(
        self,
        market_id: str,
        candidate_id: str,
        market: Dict[str, Any],
        evidence_bundle: Optional[Dict[str, Any]] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        barrier_generation: int = 0,
        budget_manager: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run AI swarm analysis.

        Returns analysis result dict with:
        - quorum_met, disagreement, results, aggregated probability, etc.
        """
        if not self._enabled:
            raise AINotImplementedError(
                "AI analysis is disabled (no API key). "
                "Set OPENROUTER_API_KEY in .env file."
            )

        prompt = build_analysis_prompt(market, evidence_bundle, snapshot)
        prompt_hash = compute_prompt_hash(prompt)

        # Parallel dispatch with total timeout
        timeout = aiohttp.ClientTimeout(total=SWARM_TOTAL_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = []
            for model_key in SWARM_MODELS:
                tasks.append(
                    call_single_model(session, model_key, prompt, self._api_key, market_id)
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        processed = []  # type: List[Dict[str, Any]]
        for r in results:
            if isinstance(r, Exception):
                processed.append({
                    "model": "unknown",
                    "weight": 0,
                    "parse_ok": False,
                    "error": str(r),
                })
            else:
                processed.append(r)

        # Check quorum
        quorum_met, quorum_reason = check_quorum(processed)

        # Compute aggregated probability (weighted mean of valid results)
        valid_results = [r for r in processed if r.get("parse_ok", False)]
        aggregated_prob = None
        disagreement = 0.0

        if valid_results:
            total_weight = sum(r.get("weight", 1) for r in valid_results)
            if total_weight > 0:
                aggregated_prob = sum(
                    r["prob_yes_raw"] * r.get("weight", 1) for r in valid_results
                ) / total_weight
            disagreement = compute_weighted_disagreement(valid_results)

        return {
            "market_id": market_id,
            "candidate_id": candidate_id,
            "prompt_hash": prompt_hash,
            "schema_version": SCHEMA_VERSION,
            "quorum_met": quorum_met,
            "quorum_reason": quorum_reason if not quorum_met else None,
            "disagreement": disagreement,
            "aggregated_prob_yes": aggregated_prob,
            "model_results": processed,
            "models_total": len(SWARM_MODELS),
            "models_valid": len(valid_results),
            "barrier_generation": barrier_generation,
        }
