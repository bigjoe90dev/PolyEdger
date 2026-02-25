"""Async Postgres connection pool and migration runner.

All DB access goes through this module. Uses asyncpg for async operations.
Migrations are applied in filename order, tracked in the _migrations table.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, List, Optional

import asyncpg  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

_pool = None  # type: Optional[asyncpg.Pool]

DEFAULT_DSN = "postgresql://polyedge:polyedge_dev@localhost:5432/polyedge"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def get_dsn() -> str:
    """Return the Postgres DSN from env or default."""
    return os.environ.get("POLYEDGE_DATABASE_URL", DEFAULT_DSN)


async def get_pool() -> asyncpg.Pool:
    """Return or create the global connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=get_dsn(), min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    """Close the global connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def execute(query: str, *args: Any) -> str:
    """Execute a query and return the status string."""
    pool = await get_pool()
    return await pool.execute(query, *args)


async def fetch_one(query: str, *args: Any) -> Optional[asyncpg.Record]:
    """Fetch a single row."""
    pool = await get_pool()
    return await pool.fetchrow(query, *args)


async def fetch_all(query: str, *args: Any) -> List[asyncpg.Record]:
    """Fetch all matching rows."""
    pool = await get_pool()
    return await pool.fetch(query, *args)


async def run_migrations(migrations_dir: Optional[Path] = None) -> List[str]:
    """Apply pending SQL migrations in filename order.

    Returns list of newly applied migration names.
    """
    mdir = migrations_dir or MIGRATIONS_DIR
    if not mdir.is_dir():
        raise FileNotFoundError("Migrations directory not found: {}".format(mdir))

    pool = await get_pool()

    # Ensure _migrations table exists (bootstrap)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            name       TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Get already-applied migrations
    rows = await pool.fetch("SELECT name FROM _migrations ORDER BY name")
    applied = {r["name"] for r in rows}

    # Discover and sort migration files
    sql_files = sorted(mdir.glob("*.sql"))
    newly_applied = []  # type: List[str]

    for sql_file in sql_files:
        migration_name = sql_file.stem
        if migration_name in applied:
            logger.debug("Migration already applied: %s", migration_name)
            continue

        logger.info("Applying migration: %s", migration_name)
        sql = sql_file.read_text(encoding="utf-8")

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(sql)

        newly_applied.append(migration_name)
        logger.info("Migration applied: %s", migration_name)

    return newly_applied


def run_migrations_sync(migrations_dir: Optional[Path] = None) -> List[str]:
    """Synchronous wrapper for run_migrations."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(run_migrations(migrations_dir))
    finally:
        loop.close()
