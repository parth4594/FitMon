-- FitMon: meta schema setup
-- Idempotent — safe to run multiple times.
-- Applied automatically by: uv run python -m src.cli setup-db

CREATE SCHEMA IF NOT EXISTS meta;

-- ---------------------------------------------------------------------------
-- meta.ingestion_log
-- One row per ingestion run. Records source, outcome, and row-level counts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta.ingestion_log (
    ingestion_log_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source           TEXT        NOT NULL,
    language         TEXT        NULL,
    status           TEXT        NOT NULL DEFAULT 'running',
    rows_read        INTEGER     NULL,
    rows_inserted    INTEGER     NULL,
    rows_updated     INTEGER     NULL,
    rows_skipped     INTEGER     NULL,
    error_message    TEXT        NULL,
    details          JSONB       NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
