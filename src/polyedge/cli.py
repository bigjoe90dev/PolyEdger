"""PolyEdge CLI entrypoint (spec §24 Phase 0).

Single command entrypoint supporting:
  config verify | config generate-manifest
  db migrate
  wal write | wal replay
  ws run | ws health
  registry sync | registry stats
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

# ─── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("polyedge")


def _run(coro: Any) -> Any:
    """Run an async coroutine from sync Click context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Root CLI ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(version="2.5.0", prog_name="polyedge")
def cli() -> None:
    """PolyEdge Automator v2.5 — autonomous prediction-market edge system."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG commands
# ═══════════════════════════════════════════════════════════════════════════════

@cli.group()
def config() -> None:
    """Config signing and verification."""
    pass


@config.command("generate-manifest")
@click.option(
    "--config-dir", default="config", help="Path to config directory",
    type=click.Path(exists=True),
)
@click.option("--key", required=True, help="Operator HMAC key for signing")
def config_generate_manifest(config_dir: str, key: str) -> None:
    """Generate a signed config manifest."""
    from polyedge.config_signing import generate_manifest

    manifest = generate_manifest(Path(config_dir), key)
    click.echo("Manifest generated with {} file hashes.".format(len(manifest["file_hashes"])))
    click.echo("Signature: {}...".format(manifest["signature"][:32]))
    click.echo("Written to: {}".format(Path(config_dir) / "manifest.json"))


@config.command("verify")
@click.option(
    "--config-dir", default="config", help="Path to config directory",
    type=click.Path(exists=True),
)
@click.option("--key", required=True, help="Operator HMAC key for verification")
def config_verify(config_dir: str, key: str) -> None:
    """Verify the signed config manifest (fail-closed)."""
    from polyedge.config_signing import ConfigTamperError, verify_manifest

    try:
        verify_manifest(Path(config_dir), key)
        click.echo("✓ Config manifest verified OK")
    except ConfigTamperError as e:
        click.echo("✗ CONFIG_TAMPER: {}".format(e), err=True)
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# DB commands
# ═══════════════════════════════════════════════════════════════════════════════

@cli.group()
def db() -> None:
    """Database operations."""
    pass


@db.command("migrate")
@click.option(
    "--migrations-dir", default=None,
    help="Path to migrations directory (default: auto-detect)",
)
def db_migrate(migrations_dir: Optional[str]) -> None:
    """Run pending database migrations."""
    from polyedge.db import close_pool, run_migrations

    mdir = Path(migrations_dir) if migrations_dir else None

    async def _run_migrate() -> List[str]:
        try:
            result = await run_migrations(mdir)
            return result
        finally:
            await close_pool()

    applied = _run(_run_migrate())
    if applied:
        click.echo("Applied {} migration(s): {}".format(len(applied), ", ".join(applied)))
    else:
        click.echo("All migrations already applied.")


# ═══════════════════════════════════════════════════════════════════════════════
# WAL commands
# ═══════════════════════════════════════════════════════════════════════════════

@cli.group()
def wal() -> None:
    """Write-Ahead Log operations."""
    pass


@wal.command("write")
@click.option("--type", "record_type", required=True, help="WAL record type")
@click.option("--payload", required=True, help="JSON payload")
@click.option("--wal-path", default="data/wal.jsonl", help="Path to WAL file")
def wal_write(record_type: str, payload: str, wal_path: str) -> None:
    """Write a single record to the WAL."""
    from polyedge.wal import WALSyncError, WALWriter

    try:
        payload_dict = json.loads(payload)
    except json.JSONDecodeError as e:
        click.echo("Invalid JSON payload: {}".format(e), err=True)
        sys.exit(1)

    try:
        with WALWriter(wal_path) as writer:
            record = writer.write(record_type, payload_dict)
        click.echo("WAL record written: type={} id={}".format(record_type, record["event_id"]))
    except (WALSyncError, ValueError) as e:
        click.echo("WAL write failed: {}".format(e), err=True)
        sys.exit(1)


@wal.command("replay")
@click.option("--wal-path", default="data/wal.jsonl", help="Path to WAL file")
def wal_replay(wal_path: str) -> None:
    """Replay WAL records into the database."""
    from polyedge.db import close_pool, get_pool
    from polyedge.wal import WALSyncError, replay_wal

    async def _run_replay() -> Dict[str, int]:
        try:
            pool = await get_pool()
            return await replay_wal(wal_path, pool)
        finally:
            await close_pool()

    try:
        stats = _run(_run_replay())
        click.echo(
            "WAL replay complete: inserted={} skipped={} orphans_adopted={}".format(
                stats["inserted"], stats["skipped"], stats["orphans_adopted"],
            )
        )
    except WALSyncError as e:
        click.echo("WAL replay failed: {}".format(e), err=True)
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# WS commands
# ═══════════════════════════════════════════════════════════════════════════════

@cli.group()
def ws() -> None:
    """WebSocket operations."""
    pass


@ws.command("run")
@click.option("--mock", is_flag=True, help="Use mock data (no real WS connection)")
@click.option("--duration", default=10, help="Run duration in seconds")
@click.option("--markets", default=None, help="Comma-separated market IDs to subscribe")
def ws_run(mock: bool, duration: int, markets: Optional[str]) -> None:
    """Connect to WS and ingest orderbook snapshots."""
    from polyedge.db import close_pool, get_pool
    from polyedge.snapshots import create_snapshot, store_snapshot
    from polyedge.ws_client import OrderbookWSClient

    market_ids = markets.split(",") if markets else ["mock-market-001", "mock-market-002"]
    snapshot_count = 0

    async def on_book_update(book_data: Dict[str, Any]) -> None:
        nonlocal snapshot_count
        snap = create_snapshot(book_data["market_id"], book_data, snapshot_source="WS")
        try:
            pool = await get_pool()
            await store_snapshot(pool, snap)
            snapshot_count += 1
        except Exception as e:
            logger.error("Failed to store snapshot: %s", e)

    async def _run_ws() -> None:
        try:
            client = OrderbookWSClient(mock_mode=mock, on_book_update=on_book_update)
            await client.connect()
            await client.subscribe(market_ids)
            await client.run(duration_sec=duration)
        finally:
            await close_pool()

    click.echo("WS {} mode — subscribing to {} markets".format(
        "mock" if mock else "live", len(market_ids),
    ))
    _run(_run_ws())
    click.echo("WS run complete: {} snapshots stored".format(snapshot_count))


@ws.command("health")
@click.option("--mock", is_flag=True, help="Use mock data for health check")
def ws_health(mock: bool) -> None:
    """Print WS health state per market."""
    from polyedge.snapshots import create_snapshot
    from polyedge.ws_client import OrderbookWSClient
    from polyedge.ws_health import ws_healthy_decision, ws_healthy_exec

    async def _run_health() -> None:
        client = OrderbookWSClient(mock_mode=True)
        await client.connect()
        await client.subscribe(["mock-market-001", "mock-market-002"])
        # Generate one round of mock data
        results = await client.run_mock_loop(duration_sec=2.0)

        for book_data in results[-2:]:  # Last snapshot per market
            mid = book_data["market_id"]
            snap = create_snapshot(mid, book_data, snapshot_source="WS")

            dec_ok, dec_reasons = ws_healthy_decision(mid, snap, client.state)
            exec_ok, exec_reasons = ws_healthy_exec(mid, snap, client.state)

            click.echo("")
            click.echo("-" * 50)
            click.echo("Market: {}".format(mid))
            click.echo("  Decision healthy: {}".format("OK" if dec_ok else "FAIL"))
            if dec_reasons:
                for r in dec_reasons:
                    click.echo("    - {}".format(r))
            click.echo("  Execution healthy: {}".format("OK" if exec_ok else "FAIL"))
            if exec_reasons:
                for r in exec_reasons:
                    click.echo("    - {}".format(r))
            click.echo("  Anomalies: ask_sum={} invalid_book={}".format(
                snap.ask_sum_anomaly, snap.invalid_book_anomaly,
            ))

    _run(_run_health())


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY commands
# ═══════════════════════════════════════════════════════════════════════════════

@cli.group()
def registry() -> None:
    """Market Registry operations."""
    pass


@registry.command("sync")
@click.option("--mock", is_flag=True, help="Use mock data (no Gamma API calls)")
def registry_sync(mock: bool) -> None:
    """Sync markets from Gamma API (or mock data)."""
    from polyedge.db import close_pool, get_pool
    from polyedge.registry import generate_mock_markets, sync_markets

    async def _run_sync() -> Dict[str, int]:
        try:
            pool = await get_pool()
            if mock:
                markets = generate_mock_markets()
            else:
                from polyedge.registry import fetch_markets_from_gamma
                markets = await fetch_markets_from_gamma()
            return await sync_markets(pool, markets)
        finally:
            await close_pool()

    click.echo("Registry sync ({} mode)...".format("mock" if mock else "live"))
    stats = _run(_run_sync())
    click.echo(
        "Sync complete: inserted={} updated={} skipped={}".format(
            stats["inserted"], stats["updated"], stats["skipped"],
        )
    )


@registry.command("stats")
def registry_stats() -> None:
    """Print market registry statistics."""
    from polyedge.db import close_pool, get_pool
    from polyedge.registry import get_registry_stats

    async def _run_stats() -> Dict[str, Any]:
        try:
            pool = await get_pool()
            return await get_registry_stats(pool)
        finally:
            await close_pool()

    stats = _run(_run_stats())
    click.echo("Total markets:     {}".format(stats["total_markets"]))
    click.echo("Binary eligible:   {}".format(stats["binary_eligible"]))
    click.echo("Frozen:            {}".format(stats["frozen"]))
    click.echo("By category:")
    for cat, count in stats["by_category"].items():
        click.echo("  {}: {}".format(cat, count))


# ═══════════════════════════════════════════════════════════════════════════════
# Snapshot commands
# ═══════════════════════════════════════════════════════════════════════════════

@cli.group()
def snapshot() -> None:
    """Snapshot operations."""
    pass


@snapshot.command("ingest")
@click.option("--market-id", required=True, help="Market ID")
@click.option("--data", required=True, help="JSON book data")
def snapshot_ingest(market_id: str, data: str) -> None:
    """Ingest a single snapshot from JSON data."""
    from polyedge.db import close_pool, get_pool
    from polyedge.snapshots import create_snapshot, store_snapshot

    try:
        book_data = json.loads(data)
    except json.JSONDecodeError as e:
        click.echo("Invalid JSON: {}".format(e), err=True)
        sys.exit(1)

    async def _run_ingest() -> None:
        try:
            pool = await get_pool()
            snap = create_snapshot(market_id, book_data, snapshot_source="REST")
            await store_snapshot(pool, snap)
            click.echo("Snapshot stored: id={} market={}".format(snap.snapshot_id, market_id))
        finally:
            await close_pool()

    _run(_run_ingest())


if __name__ == "__main__":
    cli()
