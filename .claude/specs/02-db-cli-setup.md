# Spec: Database Setup CLI (`setup-db`)

## 1. Overview

Add a `setup-db` command to `src/cli.py` that executes all database setup
SQL scripts in the correct order against Supabase. Anyone cloning the repo
runs this once after filling in `.env` — no manual steps in the Supabase
SQL editor, no `psql` binary required.

---

## 2. Depends on

- `01-database-setup.md` — `get_connection()` must exist in `src/db/postgres.py`
- `scripts/create_raw_tables.sql` must exist (created in `spec_db_setup.md`)
- `scripts/create_meta_tables.sql` must exist (created in this spec)

---

## 3. Entry point

```bash
uv run python -m src.cli setup-db
```

---

## 4. SQL scripts and execution order

Two scripts, run in order within a single connection:

| Order | File | Creates |
|---|---|---|
| 1 | `scripts/create_raw_tables.sql` | `raw` schema + `raw.workout_sessions`, `raw.exercises`, `raw.sets` |
| 2 | `scripts/create_meta_tables.sql` | `meta` schema + `meta.ingestion_log` |

All statements use `CREATE SCHEMA IF NOT EXISTS` and `CREATE TABLE IF NOT
EXISTS` — safe to run multiple times without error.

---

## 5. `scripts/create_meta_tables.sql`

Create this file:

```sql
CREATE SCHEMA IF NOT EXISTS meta;

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
```

---

## 6. Implementation — `src/cli.py`

```python
@cli.command("setup-db")
def setup_db():
    """Create all schemas and tables in Supabase."""
```

The command must:

1. Resolve the two SQL file paths relative to the project root — not
   hardcoded absolute paths, so it works on any machine.
2. Open one `psycopg2` connection via `get_connection()`.
3. For each script in order: read the file, execute it, print a confirmation
   line (`"✓ create_raw_tables.sql"`, `"✓ create_meta_tables.sql"`).
4. Call `conn.commit()` once after both scripts succeed, finalising the transaction against Supabase.
5. On any error: print a clear message identifying which script failed and
   why, roll back, and exit with a non-zero code.

---

## 7. Files to change

| File | Action |
|---|---|
| `scripts/create_meta_tables.sql` | Create — contents in §5 |
| `src/cli.py` | Add `setup-db` command |
| `CLAUDE.md` | Add `uv run python -m src.cli setup-db` to the commands section |

---

## 8. Definition of done

- [ ] `uv run python -m src.cli setup-db` runs without error on a clean Supabase instance
- [ ] Re-running the command on an already-set-up instance produces no errors
- [ ] All four tables exist after running: `raw.workout_sessions`, `raw.exercises`, `raw.sets`, `meta.ingestion_log`
- [ ] A failure in the second script rolls back and does not leave a partial state
- [ ] No SQL editor steps required anywhere in the setup process