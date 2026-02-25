"""Tests for WAL writer, reader, and replay logic."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polyedge.wal import (
    VALID_RECORD_TYPES,
    WALReader,
    WALSyncError,
    WALWriter,
    replay_wal,
)


@pytest.fixture
def wal_path(tmp_path: Path) -> Path:
    return tmp_path / "test_wal.jsonl"


def test_wal_write_read(wal_path: Path) -> None:
    """Write records, read back in order."""
    with WALWriter(wal_path) as writer:
        r1 = writer.write("STATE_CHANGED", {"state": "OBSERVE_ONLY"})
        r2 = writer.write("STATE_CHANGED", {"state": "PAPER_TRADING"})

    reader = WALReader(wal_path)
    records = reader.read_all()
    assert len(records) == 2
    assert records[0]["record_type"] == "STATE_CHANGED"
    assert records[0]["payload"]["state"] == "OBSERVE_ONLY"
    assert records[1]["payload"]["state"] == "PAPER_TRADING"


def test_wal_record_has_required_fields(wal_path: Path) -> None:
    """Each WAL record has event_id, record_type, ts_utc, payload."""
    with WALWriter(wal_path) as writer:
        record = writer.write("STATE_CHANGED", {"state": "OBSERVE_ONLY"})

    assert "event_id" in record
    assert "record_type" in record
    assert "ts_utc" in record
    assert "payload" in record
    assert "payload_hash" in record


def test_wal_canonical_json(wal_path: Path) -> None:
    """WAL records are canonical JSON with sorted keys."""
    with WALWriter(wal_path) as writer:
        writer.write("STATE_CHANGED", {"z_key": 1, "a_key": 2})

    raw = wal_path.read_text()
    line = raw.strip()
    parsed = json.loads(line)
    # Re-serialize with sorted keys should match
    canonical = json.dumps(parsed, sort_keys=True, ensure_ascii=True)
    assert line == canonical


def test_wal_invalid_record_type(wal_path: Path) -> None:
    """Invalid record type raises ValueError."""
    with WALWriter(wal_path) as writer:
        with pytest.raises(ValueError, match="Invalid WAL record type"):
            writer.write("INVALID_TYPE", {})


def test_wal_write_not_opened() -> None:
    """Writing to unopened WAL raises WALSyncError."""
    writer = WALWriter("/tmp/not_opened.jsonl")
    with pytest.raises(WALSyncError, match="not opened"):
        writer.write("STATE_CHANGED", {})


def test_wal_reader_empty(tmp_path: Path) -> None:
    """Reading non-existent WAL returns empty list."""
    reader = WALReader(tmp_path / "nonexistent.jsonl")
    assert reader.read_all() == []


def test_wal_append_only(wal_path: Path) -> None:
    """Multiple write sessions append, not overwrite."""
    with WALWriter(wal_path) as writer:
        writer.write("STATE_CHANGED", {"n": 1})

    with WALWriter(wal_path) as writer:
        writer.write("STATE_CHANGED", {"n": 2})

    reader = WALReader(wal_path)
    records = reader.read_all()
    assert len(records) == 2


@pytest.mark.asyncio
async def test_wal_replay_inserts_missing(wal_path: Path) -> None:
    """WAL replay inserts records not yet in event_log."""
    # Write some WAL records
    with WALWriter(wal_path) as writer:
        writer.write("STATE_CHANGED", {"state": "OBSERVE_ONLY"})
        writer.write("STATE_CHANGED", {"state": "PAPER_TRADING"})

    # Mock the pool
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = await replay_wal(wal_path, pool)
    assert stats["inserted"] == 2
    assert stats["skipped"] == 0
    assert stats["orphans_adopted"] == 0


@pytest.mark.asyncio
async def test_wal_replay_idempotent(wal_path: Path) -> None:
    """Replaying twice skips already-inserted records (ON CONFLICT DO NOTHING)."""
    with WALWriter(wal_path) as writer:
        writer.write("STATE_CHANGED", {"state": "OBSERVE_ONLY"})

    pool = AsyncMock()
    # First replay: insert
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    stats1 = await replay_wal(wal_path, pool)
    assert stats1["inserted"] == 1

    # Second replay: conflict → skip
    pool.execute = AsyncMock(return_value="INSERT 0 0")
    stats2 = await replay_wal(wal_path, pool)
    assert stats2["skipped"] == 1


@pytest.mark.asyncio
async def test_wal_orphan_adoption(wal_path: Path) -> None:
    """ORDER_INTENT without ORDER_RESULT → adopted as PENDING_UNKNOWN."""
    with WALWriter(wal_path) as writer:
        writer.write("ORDER_INTENT", {
            "decision_id_hex": "abc123",
            "market_id": "market-001",
            "side": "YES",
            "client_order_id": "abc123",
            "price": 0.45,
            "size_usd_cents": 500,
        })

    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = await replay_wal(wal_path, pool)
    assert stats["orphans_adopted"] == 1

    # Verify the INSERT call for the orphan included PENDING_UNKNOWN
    calls = pool.execute.call_args_list
    orphan_call = [c for c in calls if "PENDING_UNKNOWN" in str(c)]
    assert len(orphan_call) == 1


@pytest.mark.asyncio
async def test_wal_resolved_intent_not_orphan(wal_path: Path) -> None:
    """ORDER_INTENT + ORDER_RESULT → not an orphan."""
    with WALWriter(wal_path) as writer:
        writer.write("ORDER_INTENT", {
            "decision_id_hex": "abc123",
            "market_id": "market-001",
            "side": "YES",
            "client_order_id": "abc123",
            "price": 0.45,
            "size_usd_cents": 500,
        })
        writer.write("ORDER_RESULT", {
            "decision_id_hex": "abc123",
            "exchange_order_id": "exch-001",
            "status": "FILLED",
        })

    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")

    stats = await replay_wal(wal_path, pool)
    assert stats["orphans_adopted"] == 0


@pytest.mark.asyncio
async def test_wal_replay_db_failure(wal_path: Path) -> None:
    """DB insert failure during replay raises WALSyncError."""
    with WALWriter(wal_path) as writer:
        writer.write("STATE_CHANGED", {"state": "OBSERVE_ONLY"})

    pool = AsyncMock()
    pool.execute = AsyncMock(side_effect=Exception("DB connection lost"))

    with pytest.raises(WALSyncError, match="DB insert failed"):
        await replay_wal(wal_path, pool)
