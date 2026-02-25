"""Tests for snapshot creation, canonical hashing, and anomaly detection."""

import json

from polyedge.snapshots import (
    canonical_orderbook_json,
    compute_orderbook_hash,
    create_snapshot,
    detect_ask_sum_anomaly,
    detect_invalid_book_anomaly,
)


# ── Canonical orderbook JSON ─────────────────────────────────────────────────

def test_canonical_orderbook_json_deterministic() -> None:
    """Same inputs produce identical canonical JSON."""
    j1 = canonical_orderbook_json(0.45, 0.48, 0.52, 0.55, [[0.44, 100]], [[0.51, 200]])
    j2 = canonical_orderbook_json(0.45, 0.48, 0.52, 0.55, [[0.44, 100]], [[0.51, 200]])
    assert j1 == j2


def test_canonical_orderbook_json_sorted_keys() -> None:
    """Keys are sorted in canonical JSON."""
    j = canonical_orderbook_json(0.45, 0.48, 0.52, 0.55, [], [])
    parsed = json.loads(j)
    keys = list(parsed.keys())
    assert keys == sorted(keys)


def test_canonical_orderbook_json_no_whitespace() -> None:
    """Canonical JSON has no extra whitespace (compact separators)."""
    j = canonical_orderbook_json(0.45, 0.48, 0.52, 0.55, [], [])
    assert " " not in j


def test_orderbook_hash_deterministic() -> None:
    """Same canonical JSON produces same hash."""
    j = canonical_orderbook_json(0.45, 0.48, 0.52, 0.55, [], [])
    h1 = compute_orderbook_hash(j)
    h2 = compute_orderbook_hash(j)
    assert h1 == h2
    assert len(h1) == 32  # SHA-256 is 32 bytes


def test_orderbook_hash_changes_on_different_input() -> None:
    """Different inputs produce different hashes."""
    j1 = canonical_orderbook_json(0.45, 0.48, 0.52, 0.55, [], [])
    j2 = canonical_orderbook_json(0.46, 0.48, 0.52, 0.55, [], [])
    h1 = compute_orderbook_hash(j1)
    h2 = compute_orderbook_hash(j2)
    assert h1 != h2


# ── Ask sum anomaly ──────────────────────────────────────────────────────────

def test_ask_sum_anomaly_normal() -> None:
    """Normal ask sum (e.g. 1.00) → no anomaly."""
    assert detect_ask_sum_anomaly(0.55, 0.55) is False


def test_ask_sum_anomaly_low() -> None:
    """Sum below ASK_SUM_LOW (0.98) → anomaly."""
    assert detect_ask_sum_anomaly(0.45, 0.50) is True  # 0.95


def test_ask_sum_anomaly_high() -> None:
    """Sum above ASK_SUM_HIGH (2.00) → anomaly."""
    assert detect_ask_sum_anomaly(0.99, 1.02) is True  # 2.01


def test_ask_sum_anomaly_boundary_low() -> None:
    """Sum exactly at ASK_SUM_LOW (0.98) → not anomaly."""
    assert detect_ask_sum_anomaly(0.49, 0.49) is False  # 0.98


def test_ask_sum_anomaly_none() -> None:
    """Missing ask → anomaly."""
    assert detect_ask_sum_anomaly(None, 0.5) is True
    assert detect_ask_sum_anomaly(0.5, None) is True


# ── Invalid book anomaly ─────────────────────────────────────────────────────

def test_invalid_book_normal() -> None:
    """Normal book → no anomaly."""
    assert detect_invalid_book_anomaly(0.45, 0.48, 0.52, 0.55) is False


def test_invalid_book_price_zero() -> None:
    """Price <= 0 → anomaly."""
    assert detect_invalid_book_anomaly(0.0, 0.48, 0.52, 0.55) is True


def test_invalid_book_price_one() -> None:
    """Price >= 1 → anomaly."""
    assert detect_invalid_book_anomaly(0.45, 1.0, 0.52, 0.55) is True


def test_invalid_book_bid_greater_ask_yes() -> None:
    """bid_yes > ask_yes → anomaly."""
    assert detect_invalid_book_anomaly(0.50, 0.48, 0.52, 0.55) is True


def test_invalid_book_bid_greater_ask_no() -> None:
    """bid_no > ask_no → anomaly."""
    assert detect_invalid_book_anomaly(0.45, 0.48, 0.56, 0.55) is True


def test_invalid_book_missing_prices() -> None:
    """Missing any price → anomaly."""
    assert detect_invalid_book_anomaly(None, 0.48, 0.52, 0.55) is True
    assert detect_invalid_book_anomaly(0.45, None, 0.52, 0.55) is True
    assert detect_invalid_book_anomaly(0.45, 0.48, None, 0.55) is True
    assert detect_invalid_book_anomaly(0.45, 0.48, 0.52, None) is True


# ── Snapshot creation ─────────────────────────────────────────────────────────

def test_create_snapshot_fields() -> None:
    """Snapshot has all required fields."""
    book = {
        "best_bid_yes": 0.45,
        "best_ask_yes": 0.48,
        "best_bid_no": 0.52,
        "best_ask_no": 0.55,
        "depth_yes": [[0.44, 100], [0.43, 200], [0.42, 300]],
        "depth_no": [[0.51, 150], [0.50, 250], [0.49, 350]],
        "ws_last_message_unix_ms": 1000000,
        "market_last_ws_update_unix_ms": 1000000,
        "orderbook_last_change_unix_ms": 999000,
        "snapshot_ws_epoch": 1,
    }
    snap = create_snapshot("mkt-001", book)
    assert snap.market_id == "mkt-001"
    assert snap.snapshot_source == "WS"
    assert snap.snapshot_ws_epoch == 1
    assert snap.orderbook_hash is not None
    assert len(snap.orderbook_hash) == 32
    assert snap.ask_sum_anomaly is False  # 0.48 + 0.55 = 1.03
    assert snap.invalid_book_anomaly is False
