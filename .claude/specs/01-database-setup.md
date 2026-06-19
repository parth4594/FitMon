# Spec: Database Setup

## 1. Overview

Implement the database foundation for FitMon.

This spec covers three things:
1. The raw schema SQL — three tables created once at project setup
2. `src/db/postgres.py` — `get_connection()` function
3. Project dependencies and environment variable scaffolding

All future ingestion scripts, dbt models, and pipeline runs depend on this
being correctly implemented first.

---

## 2. Depends on

Nothing — this is the first step.

---

## 3. Entry points

No CLI commands are added in this step. The schema SQL is applied once manually
at project setup:

```bash
# Option A — via psql
psql $DATABASE_URL -f utils/create_raw_tables.sql

# Option B — paste into Supabase dashboard SQL editor
```

---

## 4. Database schema

All tables live in the `raw` schema. This schema holds untouched source data
written by ingestion scripts. No cleaning, renaming, or aggregation happens
here — that is dbt's responsibility.

**Rules that apply to every table:**
- No foreign key constraints — referential integrity is enforced in dbt tests only
- All timestamps use `TIMESTAMPTZ` (timezone-aware)
- All ingestion inserts must use `ON CONFLICT DO UPDATE` — never blind insert
- `created_at` is set automatically by the DB for audit purposes

---

### A. `raw.workout_sessions`

One row per workout session. The top-level metadata record.

| Column             | Type        | Constraints                    |
|--------------------|-------------|--------------------------------|
| workout_session_id | UUID        | PK, default gen_random_uuid()  |
| started_at         | TIMESTAMPTZ | Not null                       |
| ended_at           | TIMESTAMPTZ | Nullable                       |
| duration_seconds   | INTEGER     | Nullable                       |
| notes              | TEXT        | Nullable                       |
| created_at         | TIMESTAMPTZ | Default now()                  |

---

### B. `raw.exercises`

One row per unique exercise. Raw source list only — canonical naming and
muscle group mapping happens downstream in `dbt/seeds/dim_exercises.csv`.

| Column        | Type        | Constraints                   |
|---------------|-------------|-------------------------------|
| exercise_id   | UUID        | PK, default gen_random_uuid() |
| exercise_name | TEXT        | Not null, unique              |
| source        | TEXT        | Not null — e.g. 'hevy', 'manual' |
| created_at    | TIMESTAMPTZ | Default now()                 |

---

### C. `raw.sets`

One row per set performed. The core fact table.

| Column             | Type         | Constraints                                          |
|--------------------|--------------|------------------------------------------------------|
| set_id             | UUID         | PK, default gen_random_uuid()                        |
| workout_session_id | UUID         | Not null — joins to raw.workout_sessions (no FK constraint) |
| exercise_id        | UUID         | Not null — joins to raw.exercises (no FK constraint) |
| set_number         | INTEGER      | Not null                                             |
| set_type           | TEXT         | Nullable — e.g. 'warmup', 'working', 'failure'       |
| weight_kg          | NUMERIC(6,2) | Nullable                                             |
| reps               | INTEGER      | Nullable                                             |
| rpe                | NUMERIC(3,1) | Nullable — range 0.0–10.0, not enforced at raw layer |
| rest_seconds       | INTEGER      | Nullable                                             |
| notes              | TEXT         | Nullable                                             |
| created_at         | TIMESTAMPTZ  | Default now()                                        |

---

## 5. Functions to implement

### `src/db/postgres.py` — `get_connection()`

- Reads the following variables from `.env` via `pydantic-settings`
  (loaded through `src/config/settings.py`):
  - `SUPABASE_DB_HOST`
  - `SUPABASE_DB_PORT`
  - `SUPABASE_DB_NAME`
  - `SUPABASE_DB_USER`
  - `SUPABASE_DB_PASSWORD`
- Opens and returns a `psycopg2` connection
- Does not set a default schema — all callers use fully qualified table
  names (e.g. `raw.sets`)
- Raises a clear error if any required env variable is missing

```python
# Expected signature
def get_connection() -> psycopg2.extensions.connection:
    ...
```

**Do not implement `upsert_rows()` or any other helper in this step —
those belong to the ingestion spec.**

---

## 6. Dependencies

Two new packages are required. Add both via `uv` and confirm they appear
in `pyproject.toml`:

```bash
uv add psycopg2-binary
uv add pydantic-settings
```

`pydantic-settings` is already planned in `src/config/settings.py` — adding
it here makes `get_connection()` consistent with the rest of the config layer.

Do not use `pip`. Do not add any other packages in this step.

---

## 7. Files to change

| File | Action |
|------|--------|
| `utils/create_raw_tables.sql` | Create — three `CREATE TABLE` statements |
| `src/db/postgres.py` | Implement — `get_connection()` only |
| `src/config/settings.py` | Implement — Supabase connection settings block |
| `.env.example` | Create — five Supabase env variable placeholders |
| `pyproject.toml` | Update — add `psycopg2-binary` and `pydantic-settings` |

---

## 8. `.env.example` contents

```dotenv
# Supabase Postgres connection
SUPABASE_DB_HOST=your-project.supabase.co
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=postgres
SUPABASE_DB_USER=postgres
SUPABASE_DB_PASSWORD=your-password-here
```

The real `.env` is gitignored. Never commit credentials.

---

## 9. Rules for implementation

- No ORM — psycopg2 only
- All queries parameterized — never f-strings in SQL
- No FK constraints in the `raw` schema
- Credentials loaded from `.env` only via `pydantic-settings` — never hardcoded
- SQL must be idempotent — use `CREATE SCHEMA IF NOT EXISTS raw` and
  `CREATE TABLE IF NOT EXISTS`
- `src/db/postgres.py` is the only file allowed to contain DB connection logic
- Dates must follow YYYY-MM-DD format consistently

---

## 10. Definition of done

- [ ] `raw` schema exists in Supabase
- [ ] All three tables exist with correct columns and types
- [ ] SQL is idempotent — safe to run multiple times without error
- [ ] `get_connection()` returns a working psycopg2 connection
- [ ] `get_connection()` raises a clear error if any env variable is missing
- [ ] `.env.example` documents all five required variables
- [ ] `psycopg2-binary` and `pydantic-settings` are in `pyproject.toml`
- [ ] No credentials appear anywhere in committed files
- [ ] No FK constraints exist in the `raw` schema