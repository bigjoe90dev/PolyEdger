"""Decision Engine — EV math + friction model (spec §15).

Implements:
- Friction model: spread, fees, slippage, dispute, latency, time value
- EV calculation: EV = (p_eff × $1) - entry_price - required_edge
- Trade rule: only if max(EV_yes, EV_no) >= EV_MIN
- Deterministic decision_id from canonical hash
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple

from polyedge.constants import (
    EV_MIN,
    PAPER_FEE_MULTIPLIER,
    PAPER_MIN_FEE_BPS,
)

logger = logging.getLogger(__name__)

# Reason codes
REASON_EV_TOO_LOW = "EV_TOO_LOW"


def compute_spread_cost(bid: float, ask: float) -> float:
    """Maker-first spread cost: 0.5 × (ask - bid)."""
    return 0.5 * max(0, ask - bid)


def compute_fee_cost(
    fee_rate_bps: float = 0.0,
    is_paper: bool = True,
) -> float:
    """Fee cost per $1 payout share."""
    if is_paper:
        effective_bps = max(fee_rate_bps, PAPER_MIN_FEE_BPS)
        return (effective_bps / 10000.0) * PAPER_FEE_MULTIPLIER
    else:
        return fee_rate_bps / 10000.0


def compute_slippage_buffer(
    order_size_usd: float,
    depth_usd_top_levels: float,
) -> float:
    """Slippage buffer."""
    return max(0.005, order_size_usd / max(depth_usd_top_levels, 1) * 0.02)


def compute_dispute_buffer(
    dispute_risk: float,
    evidence_conflict_tier1: bool = False,
) -> float:
    """Dispute risk buffer."""
    buf = 0.01 + 0.02 * dispute_risk
    if evidence_conflict_tier1:
        buf *= 1.5
    return buf


def compute_latency_penalty(decision_to_exec_sec: float) -> float:
    """Latency penalty."""
    return max(0, (decision_to_exec_sec - 2)) * 0.001


def compute_time_value_penalty(time_to_resolution_days: float) -> float:
    """Time value penalty."""
    return min(0.02, time_to_resolution_days * 0.0002)


def compute_required_edge(
    spread_cost: float,
    fee_cost: float,
    slippage_buffer: float,
    dispute_buffer: float,
    latency_penalty: float,
    time_value_penalty: float,
) -> float:
    """Total required edge (sum of all friction components)."""
    return (
        spread_cost
        + fee_cost
        + slippage_buffer
        + dispute_buffer
        + latency_penalty
        + time_value_penalty
    )


def compute_ev(
    p_eff: float,
    entry_price: float,
    required_edge: float,
    side: str = "YES",
) -> float:
    """EV calculation per spec §15.4.

    EV_yes = (p_eff × $1) - entry_price - required_edge
    EV_no  = ((1 - p_eff) × $1) - entry_price - required_edge
    """
    if side == "YES":
        return p_eff * 1.0 - entry_price - required_edge
    else:
        return (1.0 - p_eff) * 1.0 - entry_price - required_edge


def make_decision(
    market_id: str,
    candidate_id: str,
    p_eff: float,
    snapshot: Any,
    order_size_usd: float,
    dispute_risk: float = 0.0,
    evidence_conflict_tier1: bool = False,
    decision_to_exec_sec: float = 0.0,
    time_to_resolution_days: float = 30.0,
    fee_rate_bps: float = 0.0,
    is_paper: bool = True,
) -> Dict[str, Any]:
    """Produce a deterministic decision with full friction model.

    Returns decision dict with side, EV, required_edge, gates, etc.
    """
    bid_yes = getattr(snapshot, "best_bid_yes", 0) or 0
    ask_yes = getattr(snapshot, "best_ask_yes", 0) or 0
    bid_no = getattr(snapshot, "best_bid_no", 0) or 0
    ask_no = getattr(snapshot, "best_ask_no", 0) or 0

    depth_yes = sum(l[1] for l in (getattr(snapshot, "depth_yes", []) or [])[:3])
    depth_no = sum(l[1] for l in (getattr(snapshot, "depth_no", []) or [])[:3])

    # Friction components
    spread_yes = compute_spread_cost(bid_yes, ask_yes)
    spread_no = compute_spread_cost(bid_no, ask_no)
    fee = compute_fee_cost(fee_rate_bps, is_paper)
    slippage_yes = compute_slippage_buffer(order_size_usd, depth_yes)
    slippage_no = compute_slippage_buffer(order_size_usd, depth_no)
    dispute = compute_dispute_buffer(dispute_risk, evidence_conflict_tier1)
    latency = compute_latency_penalty(decision_to_exec_sec)
    time_val = compute_time_value_penalty(time_to_resolution_days)

    edge_yes = compute_required_edge(spread_yes, fee, slippage_yes, dispute, latency, time_val)
    edge_no = compute_required_edge(spread_no, fee, slippage_no, dispute, latency, time_val)

    ev_yes = compute_ev(p_eff, ask_yes, edge_yes, "YES")
    ev_no = compute_ev(p_eff, ask_no, edge_no, "NO")

    # Trade rule
    if ev_yes >= EV_MIN and ev_yes >= ev_no:
        side = "YES"
        ev = ev_yes
        entry_price = ask_yes
        required_edge = edge_yes
    elif ev_no >= EV_MIN:
        side = "NO"
        ev = ev_no
        entry_price = ask_no
        required_edge = edge_no
    else:
        side = "NO_TRADE"
        ev = max(ev_yes, ev_no)
        entry_price = 0
        required_edge = max(edge_yes, edge_no)

    # Build canonical decision string for hashing
    canonical = json.dumps({
        "market_id": market_id,
        "candidate_id": candidate_id,
        "side": side,
        "p_eff": round(p_eff, 6),
        "entry_price": round(entry_price, 6),
        "ev": round(ev, 6),
        "required_edge": round(required_edge, 6),
        "order_size_usd": round(order_size_usd, 2),
    }, sort_keys=True, separators=(",", ":"))

    decision_id_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return {
        "decision_id_hex": decision_id_hex,
        "market_id": market_id,
        "candidate_id": candidate_id,
        "side": side,
        "size_usd": order_size_usd,
        "entry_price": round(entry_price, 6),
        "p_market": round(ask_yes, 6),
        "p_eff": round(p_eff, 6),
        "required_edge": round(required_edge, 6),
        "ev": round(ev, 6),
        "ev_yes": round(ev_yes, 6),
        "ev_no": round(ev_no, 6),
        "reason_code": "TRADE" if side != "NO_TRADE" else REASON_EV_TOO_LOW,
        "gates": {
            "spread_cost_yes": round(spread_yes, 6),
            "spread_cost_no": round(spread_no, 6),
            "fee_cost": round(fee, 6),
            "slippage_yes": round(slippage_yes, 6),
            "slippage_no": round(slippage_no, 6),
            "dispute_buffer": round(dispute, 6),
            "latency_penalty": round(latency, 6),
            "time_value_penalty": round(time_val, 6),
        },
        "client_order_id": decision_id_hex,
        "is_paper": is_paper,
    }
