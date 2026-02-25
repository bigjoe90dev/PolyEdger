-- PolyEdge Automator v2.5 — Initial schema (spec §25)
-- Migration: 001_initial_schema
-- Idempotent: uses IF NOT EXISTS throughout

-- Bot state (singleton row)
CREATE TABLE IF NOT EXISTS bot_state (
    id           BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    state        TEXT NOT NULL,
    counter      BIGINT NOT NULL,
    ts_utc       TIMESTAMPTZ NOT NULL,
    armed_until_utc   TIMESTAMPTZ,
    halt_until_utc    TIMESTAMPTZ,
    halt_resume_state TEXT,
    state_signature   BYTEA NOT NULL
);

-- Market locks (concurrency control §20)
CREATE TABLE IF NOT EXISTS market_locks (
    market_id           TEXT PRIMARY KEY,
    owner_instance_id   TEXT NOT NULL,
    owner_worker_id     TEXT NOT NULL,
    lock_version        BIGINT NOT NULL,
    owner_heartbeat_utc TIMESTAMPTZ NOT NULL,
    expires_at_utc      TIMESTAMPTZ NOT NULL,
    last_renewed_utc    TIMESTAMPTZ NOT NULL
);

-- Snapshots (price + orderbook §7.2)
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id                  UUID PRIMARY KEY,
    market_id                    TEXT NOT NULL,
    snapshot_at_unix_ms          BIGINT NOT NULL,
    snapshot_source              TEXT NOT NULL CHECK (snapshot_source IN ('WS', 'REST')),
    snapshot_ws_epoch            BIGINT NOT NULL,
    ws_last_message_unix_ms      BIGINT NOT NULL,
    market_last_ws_update_unix_ms  BIGINT,
    orderbook_last_change_unix_ms  BIGINT,
    best_bid_yes                 NUMERIC(10, 6),
    best_ask_yes                 NUMERIC(10, 6),
    best_bid_no                  NUMERIC(10, 6),
    best_ask_no                  NUMERIC(10, 6),
    depth_yes                    JSONB NOT NULL,
    depth_no                     JSONB NOT NULL,
    orderbook_hash               BYTEA NOT NULL,
    ask_sum_anomaly              BOOLEAN NOT NULL,
    invalid_book_anomaly         BOOLEAN NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_market_id
    ON snapshots (market_id, snapshot_at_unix_ms DESC);

-- Decisions (§15)
CREATE TABLE IF NOT EXISTS decisions (
    decision_id_hex  TEXT PRIMARY KEY,
    market_id        TEXT NOT NULL,
    candidate_id     UUID NOT NULL,
    created_at_utc   TIMESTAMPTZ NOT NULL,
    side             TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
    size_usd_cents   BIGINT NOT NULL,
    entry_price      NUMERIC(10, 6) NOT NULL,
    p_market         NUMERIC(10, 6) NOT NULL,
    p_eff            NUMERIC(10, 6) NOT NULL,
    required_edge    NUMERIC(10, 6) NOT NULL,
    ev               NUMERIC(10, 6) NOT NULL,
    reason_code      TEXT NOT NULL,
    gates            JSONB NOT NULL,
    client_order_id  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_market_id
    ON decisions (market_id, created_at_utc DESC);

-- Orders (§17)
CREATE TABLE IF NOT EXISTS orders (
    local_order_id              UUID PRIMARY KEY,
    decision_id_hex             TEXT NOT NULL REFERENCES decisions (decision_id_hex),
    market_id                   TEXT NOT NULL,
    token_side                  TEXT NOT NULL CHECK (token_side IN ('YES', 'NO')),
    status                      TEXT NOT NULL,
    client_order_id             TEXT NOT NULL,
    exchange_order_id           TEXT,
    price                       NUMERIC(10, 6) NOT NULL,
    size_usd_cents              BIGINT NOT NULL,
    filled_usd_cents            BIGINT NOT NULL DEFAULT 0,
    residual_usd_cents          BIGINT NOT NULL,
    pending_unknown_since_utc   TIMESTAMPTZ,
    created_at_utc              TIMESTAMPTZ NOT NULL,
    updated_at_utc              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_market_id
    ON orders (market_id, created_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders (status) WHERE status IN ('PENDING_UNKNOWN', 'OPEN', 'CANCEL_REQUESTED');

-- Reconcile mismatches (§19)
CREATE TABLE IF NOT EXISTS reconcile_mismatches (
    mismatch_id    UUID PRIMARY KEY,
    market_id      TEXT,
    level          INT NOT NULL CHECK (level IN (1, 2, 3)),
    status         TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RESOLVED')),
    first_seen_utc TIMESTAMPTZ NOT NULL,
    last_seen_utc  TIMESTAMPTZ NOT NULL,
    details        JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reconcile_active
    ON reconcile_mismatches (status) WHERE status = 'ACTIVE';

-- AI budget (§13)
CREATE TABLE IF NOT EXISTS ai_budget_day (
    day_utc       DATE PRIMARY KEY,
    spent_usd     NUMERIC(12, 6) NOT NULL,
    in_flight_usd NUMERIC(12, 6) NOT NULL,
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_reservations (
    reservation_id UUID PRIMARY KEY,
    day_utc        DATE NOT NULL REFERENCES ai_budget_day (day_utc),
    ts_utc_db      TIMESTAMPTZ NOT NULL,
    model_key      TEXT NOT NULL,
    reserved_usd   NUMERIC(12, 6) NOT NULL,
    actual_usd     NUMERIC(12, 6),
    status         TEXT NOT NULL CHECK (status IN ('RESERVED', 'SETTLED', 'FORCE_SETTLED', 'RELEASED')),
    correlation_id UUID NOT NULL,
    expires_at_utc TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_reservations_day_status
    ON ai_reservations (day_utc, status);

-- Event log (§21)
CREATE TABLE IF NOT EXISTS event_log (
    event_id        UUID PRIMARY KEY,
    ts_utc          TIMESTAMPTZ NOT NULL,
    type            TEXT NOT NULL,
    correlation_ids JSONB NOT NULL,
    payload         JSONB NOT NULL,
    payload_hash    BYTEA NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_event_log_type
    ON event_log (type, ts_utc DESC);

-- Migration tracking
CREATE TABLE IF NOT EXISTS _migrations (
    name       TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO _migrations (name)
VALUES ('001_initial_schema')
ON CONFLICT (name) DO NOTHING;
