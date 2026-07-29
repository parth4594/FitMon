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
    started_at         TIMESTAMPTZ NOT NULL,
    ended_at           TIMESTAMPTZ,
    duration_seconds   INTEGER,
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS workout_name TEXT NULL;

-- Hevy API ingestion (spec 05 §4.A/§4.B) — hevy_workout_id is the native
-- Hevy UUID and becomes the conflict key for API upserts, distinct from
-- workout_session_id which is DB-generated for the CSV path. deleted_at
-- stays NULL until soft-delete is implemented (deleted events are logged
-- and skipped, never applied to raw tables — see ingest_hevy_api.py).
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS hevy_workout_id TEXT UNIQUE;
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS routine_id TEXT;
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS hevy_updated_at TIMESTAMPTZ;
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS hevy_created_at TIMESTAMPTZ;
ALTER TABLE raw.workout_sessions
ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
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

-- Hevy API ingestion (spec 05 §4.C) — exercise_template_id is the stable
-- conflict key for API upserts; exercise_name can change, so it is not.
ALTER TABLE raw.exercises
ADD COLUMN IF NOT EXISTS exercise_template_id TEXT UNIQUE;
ALTER TABLE raw.exercises
ADD COLUMN IF NOT EXISTS supersets_id INTEGER;
ALTER TABLE raw.exercises
ADD COLUMN IF NOT EXISTS exercise_index INTEGER;
ALTER TABLE raw.exercises
ADD COLUMN IF NOT EXISTS notes TEXT;

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
ALTER TABLE raw.sets
ADD COLUMN IF NOT EXISTS exercise_notes TEXT;

-- Hevy API ingestion (spec 05 §4.D/§8) — no single stable conflict key
-- exists for a set from the API, so hevy_workout_id + exercise_template_id
-- + set_index (all TEXT/INTEGER, nullable for CSV-sourced rows) form a
-- composite conflict key instead. workout_session_id / exercise_id (the
-- DB-generated UUID FKs) are still populated on every API-sourced row too,
-- resolved via the RETURNING clause on the parent session/exercise upserts.
ALTER TABLE raw.sets
ADD COLUMN IF NOT EXISTS hevy_workout_id TEXT;
ALTER TABLE raw.sets
ADD COLUMN IF NOT EXISTS exercise_template_id TEXT;
ALTER TABLE raw.sets
ADD COLUMN IF NOT EXISTS set_index INTEGER;
ALTER TABLE raw.sets
ADD COLUMN IF NOT EXISTS distance_meters NUMERIC(8,2);
ALTER TABLE raw.sets
ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;
ALTER TABLE raw.sets
ADD COLUMN IF NOT EXISTS custom_metric NUMERIC(8,2);

CREATE UNIQUE INDEX IF NOT EXISTS ux_sets_hevy_conflict_key
ON raw.sets (hevy_workout_id, exercise_template_id, set_index);

-- ---------------------------------------------------------------------------
-- raw.cardio_sets
-- One row per set with non-null cardio data (distance_km or duration_seconds).
-- Hevy-specific. No FK constraints — consistent with the raw schema rules.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.cardio_sets (
    cardio_set_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_session_id  UUID          NOT NULL, -- logical FK → raw.workout_sessions
    exercise_id         UUID          NOT NULL, -- logical FK → raw.exercises
    set_index           INTEGER       NOT NULL,
    exercise_title      TEXT          NOT NULL,
    distance_km         NUMERIC(8,3),
    duration_seconds    INTEGER,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);

