"""Write-Ahead Log — append-only, fsync per record (spec §18).

Each WAL record is a single canonical JSON line:
- Keys sorted deterministically
- UTC timestamps only
- No locale formatting

Record types: STATE_CHANGED, ORDER_INTENT, ORDER_INTENT_ABORTED,
ORDER_RESULT, CANCEL_INTENT, CANCEL_RESULT.

On fsync failure: raises WALSyncError — caller MUST exit non-zero.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

VALID_RECORD_TYPES = frozenset({
    "STATE_CHANGED",
    "ORDER_INTENT",
    "ORDER_INTENT_ABORTED",
    "ORDER_RESULT",
    "CANCEL_INTENT",
    "CANCEL_RESULT",
})


class WALSyncError(Exception):
    """Raised when WAL fsync fails — process must halt."""


class WALWriter:
    """Append-only WAL writer with fsync per record.

    Each write atomically appends one canonical JSON line and fsyncs.
    """

    def __init__(self, wal_path: str) -> None:
        self.path = Path(wal_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = None  # type: Optional[int]

    def open(self) -> None:
        """Open the WAL file for appending."""
        self._fd = os.open(
            str(self.path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o640,
        )

    def close(self) -> None:
        """Close the WAL file descriptor."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> WALWriter:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def write(self, record_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Append a single WAL record and fsync.

        Returns the full record dict (including generated fields).
        Raises WALSyncError on any I/O failure.
        """
        if record_type not in VALID_RECORD_TYPES:
            raise ValueError("Invalid WAL record type: {}".format(record_type))

        if self._fd is None:
            raise WALSyncError("WAL not opened — call open() first")

        record = {
            "event_id": str(uuid.uuid4()),
            "record_type": record_type,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }  # type: Dict[str, Any]

        # Canonical JSON: sorted keys, no extra whitespace, ASCII-safe
        line = json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n"
        line_bytes = line.encode("utf-8")

        # Compute payload hash for event_log dedup
        record["payload_hash"] = hashlib.sha256(line_bytes).hexdigest()

        try:
            os.write(self._fd, line_bytes)
            os.fsync(self._fd)
        except OSError as e:
            raise WALSyncError("WAL fsync failed: {}".format(e)) from e

        logger.debug("WAL record written: type=%s id=%s", record_type, record["event_id"])
        return record


class WALReader:
    """Iterate WAL records in offset order (deterministic)."""

    def __init__(self, wal_path: str) -> None:
        self.path = Path(wal_path)

    def read_all(self) -> List[Dict[str, Any]]:
        """Read all WAL records in order."""
        if not self.path.is_file():
            return []

        records = []  # type: List[Dict[str, Any]]
        with open(self.path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    record["_line_num"] = line_num
                    records.append(record)
                except json.JSONDecodeError as e:
                    logger.error("WAL parse error at line %d: %s", line_num, e)
                    raise WALSyncError(
                        "WAL corrupted at line {}: {}".format(line_num, e)
                    ) from e

        return records


async def replay_wal(
    wal_path: str,
    pool: Any,
) -> Dict[str, int]:
    """Replay WAL records into DB event_log, adopting orphan intents.

    Per spec §18.4:
    - Insert missing records into event_log (idempotent via payload_hash).
    - Orphan adoption: ORDER_INTENT without ORDER_RESULT or ORDER_INTENT_ABORTED
      creates a local order with status PENDING_UNKNOWN.

    Returns stats dict with counts of inserted, skipped, orphans_adopted.
    Raises WALSyncError if any DB insert fails during replay.
    """
    import uuid as _uuid

    reader = WALReader(str(wal_path))
    records = reader.read_all()

    stats = {"inserted": 0, "skipped": 0, "orphans_adopted": 0}
    if not records:
        logger.info("WAL replay: no records to replay")
        return stats

    # Track intents and their resolutions for orphan detection
    intent_ids = {}  # type: Dict[str, Dict[str, Any]]
    resolved_intents = set()  # type: Set[str]

    for rec in records:
        rt = rec.get("record_type", "")
        payload = rec.get("payload", {})

        if rt == "ORDER_INTENT":
            decision_id = payload.get("decision_id_hex", rec["event_id"])
            intent_ids[decision_id] = rec
        elif rt in ("ORDER_RESULT", "ORDER_INTENT_ABORTED"):
            decision_id = payload.get("decision_id_hex", "")
            if decision_id:
                resolved_intents.add(decision_id)

    # Insert records into event_log
    for rec in records:
        event_id = rec["event_id"]
        ts_utc = rec["ts_utc"]
        record_type = rec["record_type"]
        payload = rec.get("payload", {})

        # Compute canonical payload hash for dedup
        canonical = json.dumps(
            {"event_id": event_id, "record_type": record_type, "payload": payload},
            sort_keys=True,
            ensure_ascii=True,
        )
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).digest()

        try:
            result = await pool.execute(
                """
                INSERT INTO event_log (event_id, ts_utc, type, correlation_ids, payload, payload_hash)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (payload_hash) DO NOTHING
                """,
                _uuid.UUID(event_id),
                datetime.fromisoformat(ts_utc),
                record_type,
                json.dumps([event_id]),  # correlation_ids
                json.dumps(payload),  # payload
                payload_hash,
            )
            if "INSERT 0 1" in result:
                stats["inserted"] += 1
            else:
                stats["skipped"] += 1
        except Exception as e:
            raise WALSyncError(
                "WAL replay DB insert failed for event {}: {}".format(event_id, e)
            ) from e

    # Orphan adoption: ORDER_INTENT without ORDER_RESULT or ORDER_INTENT_ABORTED
    for decision_id, intent_rec in intent_ids.items():
        if decision_id not in resolved_intents:
            logger.warning(
                "WAL orphan detected: ORDER_INTENT %s without result — adopting as PENDING_UNKNOWN",
                decision_id,
            )
            payload = intent_rec.get("payload", {})
            try:
                await pool.execute(
                    """
                    INSERT INTO orders (
                        local_order_id, decision_id_hex, market_id, token_side,
                        status, client_order_id, price, size_usd_cents,
                        filled_usd_cents, residual_usd_cents,
                        pending_unknown_since_utc, created_at_utc, updated_at_utc
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    ON CONFLICT (local_order_id) DO NOTHING
                    """,
                    _uuid.uuid4(),
                    decision_id,
                    payload.get("market_id", "UNKNOWN"),
                    payload.get("side", "YES"),
                    "PENDING_UNKNOWN",
                    payload.get("client_order_id", decision_id),
                    float(payload.get("price", 0)),
                    int(payload.get("size_usd_cents", 0)),
                    0,
                    int(payload.get("size_usd_cents", 0)),
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                )
                stats["orphans_adopted"] += 1
            except Exception as e:
                raise WALSyncError(
                    "WAL orphan adoption failed for intent {}: {}".format(decision_id, e)
                ) from e

    logger.info(
        "WAL replay complete: inserted=%d skipped=%d orphans=%d",
        stats["inserted"],
        stats["skipped"],
        stats["orphans_adopted"],
    )
    return stats
