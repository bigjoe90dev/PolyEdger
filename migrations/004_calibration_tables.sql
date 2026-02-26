-- 004_calibration_tables.sql
-- Calibration and trust control tables for Phase 6 (spec §14)

-- Resolution outcomes (historical)
CREATE TABLE IF NOT EXISTS resolution_outcomes (
    market_id       TEXT NOT NULL,
    resolved_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    actual_outcome  INT NOT NULL CHECK (actual_outcome IN (0, 1)),
    p_ai_raw        NUMERIC(6, 4),
    p_market_at_decision NUMERIC(6, 4),
    p_eff           NUMERIC(6, 4),
    category        TEXT,
    PRIMARY KEY (market_id)
);

CREATE INDEX IF NOT EXISTS idx_resolution_category
    ON resolution_outcomes(category);

-- Calibration bins (per category, updated periodically)
CREATE TABLE IF NOT EXISTS calibration_bins (
    category        TEXT NOT NULL,
    bin_lo          NUMERIC(4, 2) NOT NULL,
    bin_hi          NUMERIC(4, 2) NOT NULL,
    predicted_mean  NUMERIC(6, 4),
    observed_fraction NUMERIC(6, 4),
    sample_count    INT NOT NULL DEFAULT 0,
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (category, bin_lo, bin_hi)
);

-- Calibration stats (aggregate metrics)
CREATE TABLE IF NOT EXISTS calibration_stats (
    category          TEXT PRIMARY KEY,
    n_resolved        INT NOT NULL DEFAULT 0,
    brier_ai          NUMERIC(8, 6),
    brier_baseline    NUMERIC(8, 6),
    w_ai_current      NUMERIC(6, 4) NOT NULL DEFAULT 0,
    updated_at_utc    TIMESTAMPTZ NOT NULL DEFAULT now()
);
