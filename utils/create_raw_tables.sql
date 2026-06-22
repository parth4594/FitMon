-- FitMon: raw schema setup
-- Idempotent — safe to run multiple times.
-- Apply once at project setup:
--   psql $DATABASE_URL -f utils/create_raw_tables.sql

CREATE SCHEMA IF NOT EXISTS raw;

-- ---------------------------------------------------------------------------
-- raw.workout_sessions
-- One row per workout session (top-level metadata).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.workout_sessions (
    workout_session_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_name       TEXT    NULL,
    started_at         TIMESTAMPTZ NOT NULL,
    ended_at           TIMESTAMPTZ,
    duration_seconds   INTEGER,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- raw.exercises
-- One row per unique exercise name as it appears in the source system.
-- Canonical naming and muscle-group mapping are handled downstream in dbt.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.exercises (
    exercise_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    exercise_name TEXT        NOT NULL UNIQUE,
    source        TEXT        NOT NULL, -- e.g. 'hevy', 'manual'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- raw.sets
-- One row per set performed. Core fact table.
-- No FK constraints — referential integrity is enforced in dbt tests only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.sets (
    set_id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_session_id UUID         NOT NULL, -- logical FK → raw.workout_sessions
    exercise_id        UUID         NOT NULL, -- logical FK → raw.exercises
    set_number         INTEGER      NULL,
    set_type           TEXT,                  -- e.g. 'warmup', 'working', 'failure'
    weight_kg          NUMERIC(6,2),
    reps               INTEGER,
    rpe                NUMERIC(3,1),          -- range 0.0–10.0, not enforced at raw layer
    rest_seconds       INTEGER,
    notes              TEXT,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);
