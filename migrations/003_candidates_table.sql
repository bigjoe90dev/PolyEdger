-- 003_candidates_table.sql
-- Candidate pipeline tables for Phase 3 (spec §9)

-- Candidates table
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id   UUID PRIMARY KEY,
    market_id      TEXT NOT NULL,
    snapshot_id    UUID NOT NULL REFERENCES snapshots(snapshot_id),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    status         TEXT NOT NULL DEFAULT 'NEW'
                   CHECK (status IN (
                       'NEW', 'FILTERED', 'EVIDENCE_DONE',
                       'AI_DONE', 'DECIDED', 'EXECUTED', 'DROPPED'
                   )),
    filter_reason  TEXT,
    decided_at_utc TIMESTAMPTZ,
    decision_id_hex TEXT,
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_candidates_market_id
    ON candidates(market_id);
CREATE INDEX IF NOT EXISTS idx_candidates_status
    ON candidates(status);
CREATE INDEX IF NOT EXISTS idx_candidates_created_at
    ON candidates(created_at_utc);

-- Watchlist table (bounded market selection)
CREATE TABLE IF NOT EXISTS watchlist (
    market_id       TEXT PRIMARY KEY,
    score           NUMERIC(10, 4) NOT NULL DEFAULT 0,
    added_at_utc    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_scored_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Probation table (markets excluded due to anomalies)
CREATE TABLE IF NOT EXISTS probation (
    market_id          TEXT PRIMARY KEY,
    reason             TEXT NOT NULL,
    anomaly_count      INT NOT NULL DEFAULT 1,
    probation_until_utc TIMESTAMPTZ NOT NULL,
    added_at_utc       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quarantine table (noisy markets, spec §8.2)
CREATE TABLE IF NOT EXISTS quarantine (
    market_id            TEXT PRIMARY KEY,
    trigger_count_hour   INT NOT NULL DEFAULT 0,
    no_trade_count_hour  INT NOT NULL DEFAULT 0,
    quarantine_until_utc TIMESTAMPTZ NOT NULL,
    added_at_utc         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trigger persistence tracking (anti-spoof, spec §9.1)
CREATE TABLE IF NOT EXISTS trigger_state (
    market_id          TEXT NOT NULL,
    trigger_type       TEXT NOT NULL,
    first_seen_utc     TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_count       INT NOT NULL DEFAULT 1,
    last_snapshot_id   UUID,
    PRIMARY KEY (market_id, trigger_type)
);
