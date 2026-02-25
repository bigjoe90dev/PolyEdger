-- PolyEdge Automator v2.5 — Markets table (spec §6.2)
-- Migration: 002_markets_table

CREATE TABLE IF NOT EXISTS markets (
    market_id          TEXT PRIMARY KEY,
    condition_id       TEXT NOT NULL,
    event_id           TEXT,
    category           TEXT NOT NULL,
    tags               JSONB NOT NULL DEFAULT '[]'::jsonb,
    title              TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    resolution_source  TEXT NOT NULL DEFAULT '',
    end_date_utc       TIMESTAMPTZ,
    resolve_time_utc   TIMESTAMPTZ,
    yes_token_id       TEXT NOT NULL,
    no_token_id        TEXT NOT NULL,
    volume_24h_usd     NUMERIC(14, 2),
    liquidity_usd      NUMERIC(14, 2),
    critical_field_hash TEXT NOT NULL,
    is_binary_eligible BOOLEAN NOT NULL,
    eligibility_reason TEXT,
    first_seen_utc     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_synced_utc    TIMESTAMPTZ NOT NULL DEFAULT now(),
    frozen             BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_markets_category
    ON markets (category);

CREATE INDEX IF NOT EXISTS idx_markets_eligible
    ON markets (is_binary_eligible) WHERE is_binary_eligible = TRUE;

CREATE INDEX IF NOT EXISTS idx_markets_critical_hash
    ON markets (critical_field_hash);

INSERT INTO _migrations (name)
VALUES ('002_markets_table')
ON CONFLICT (name) DO NOTHING;
