# Spec: Workout CSV Ingestion (`ingest_workout_csv.py`)

## 1. Overview

A source-agnostic CSV ingestion script that imports workout data from any
app that exports a structured CSV file — Strong, or any other app a future
user brings. The script is not tied to any one app: the caller specifies
which app's column map to load via a `--source` flag, and the script does
the same parsing and writing logic regardless of origin.

Your immediate use case is ingesting your own Strong app export. Any other user
running this pipeline with a different app only needs to add a column map
directory for their source — no code changes.

Two supporting capabilities ship alongside the script:

1. A pluggable YAML column-mapping system under
   `src/ingestion/column_maps/<source>/<lang>.yaml`. Adding a new app or a
   new language requires only a new YAML file — no code changes.
2. Three helper functions in `src/db/postgres.py` that write run metadata
   to `meta.ingestion_log` (table created by `02-db-cli-setup.md`),
   giving a persistent audit trail across all ingestion sources. The same
   helpers are reused by future scripts (`ingest_hevy_api.py`, `ingest_apple_health_data.py`)
   with no redesign.

---

## 2. Depends on

- `01-database-setup.md` — `raw.workout_sessions`, `raw.exercises`, and
  `raw.sets` must exist in Supabase.
- `02-db-cli-setup.md` — `setup-db` must have been run so
  `meta.ingestion_log` exists.
- Two schema amendments to `utils/create_raw_tables.sql` (§5.A and §5.B).

---

## 3. Entry point

```bash
# Explicit source and language (recommended)
uv run python -m src.cli ingest-csv --file path/to/export.csv --source strong --lang de

# Auto-detect language from header row (source still required)
uv run python -m src.cli ingest-csv --file path/to/export.csv --source strong
```

`--source` is always required. It selects the column map directory and sets
the `source` value written to `ingestion_log`. `--lang` is optional —
if omitted, the script auto-detects from the CSV header row (§7.5).

The CLI command `ingest-csv` lives in `src/cli.py` and calls
`src/ingestion/ingest_workout_csv.py`.

---

## 4. Source file profile

Verified against your actual Strong App German export — not assumed from
documentation.

| Property | Value |
|---|---|
| Encoding | UTF-8 |
| Delimiter | `,` |
| Total data rows | 3,667 |
| Distinct sessions | 76 |
| Distinct exercise names | 65 (5 with trailing-whitespace variants — see §8.4) |
| Decimal separator | Period throughout — German-locale commas never observed across all 3,667 rows |
| `RPE` column | Empty in 100% of rows |
| `Workout-Notizen` column | Empty in 100% of rows |
| `Entfernung` (distance) | Non-zero in exactly 1 row (treadmill, `0.52`) |

**Critical structural finding — `Reihenfolge festlegen` is not purely numeric.**
Strong interleaves a rest-timer row after every real set, where this column
contains the literal string `Ruhezeit` instead of a set number:

```
"Pull Up", 1,        0, 3.0, 0,   0.0, ...  ← real set
"Pull Up", Ruhezeit, 0, 0.0, 0, 120.0, ...  ← rest-timer row
```

1,833 of the 3,667 rows are rest-timer rows — not performed sets. This
directly conflicts with `raw.sets.set_number INTEGER NOT NULL` (see §5.A).
The rest-marker string (`Ruhezeit` in German) is localized per app and
language, so it lives in the YAML config rather than being hardcoded.

---

## 5. Required schema amendments

Both amendments below are already defined and applied in `02-db-cli-setup.md`.
They are listed here only so the ingestion script's dependencies are visible
in one place — do not redefine or reapply them.
 
**A. `raw.sets.set_number` → nullable.**
Required because rest-timer rows have no set number.
 
**B. `raw.workout_sessions.workout_name TEXT NULL` → added.**
Required to capture the workout name from the source CSV.

---

## 6. Column map design

### 6.1 Canonical column vocabulary

All pipeline code refers exclusively to these 12 canonical names. No
app-specific or language-specific strings appear anywhere in
`ingest_workout_csv.py`:

```
date, workout_name, duration, exercise_name, set_order,
weight, reps, distance, seconds, notes, workout_notes, rpe
```

Plus one special value — `rest_marker` — the localized cell value (not a
column header) that identifies a rest-timer row. This is app- and
language-specific, so it lives in the YAML alongside the header map.

### 6.2 Directory layout

```
src/ingestion/column_maps/
    strong/
        de.yaml       # verified against real export
        en.yaml       # provisional scaffold — see §6.4
    # another_app/
    #     en.yaml     # a future user drops this in — nothing else changes
```

The `--source` flag maps directly to a subdirectory name. Adding a new app
means creating a new subdirectory with at least one language YAML. No other
change is required.

### 6.3 `strong/de.yaml` — verified against the actual uploaded file

```yaml
language: de
headers:
  date: Datum
  workout_name: Workout-Name
  duration: Dauer
  exercise_name: Name der Übung
  set_order: Reihenfolge festlegen
  weight: Gewicht
  reps: Wiederh.
  distance: Entfernung
  seconds: Sekunden
  notes: Notizen
  workout_notes: Workout-Notizen
  rpe: RPE
rest_marker: Ruhezeit
```

### 6.4 `strong/en.yaml` — provisional scaffold, not verified

```yaml
language: en
headers:
  date: Date
  workout_name: Workout Name
  duration: Duration
  exercise_name: Exercise Name
  set_order: Set Order
  weight: Weight
  reps: Reps
  distance: Distance
  seconds: Seconds
  notes: Notes
  workout_notes: Workout Notes
  rpe: RPE
rest_marker: Rest Timer
```

These are Strong app's conventional English labels based on general knowledge —
not byte-inspected the way the German file was. `rest_marker` in particular
is unconfirmed. Treat this as a provisional starting point; tighten it the
moment a real English Strong export is available.

### 6.5 Language resolution order

1. If `--lang` is passed, load that language's YAML from the source
   directory. Fail immediately with the list of available language codes
   if no matching file exists.
2. If `--lang` is not passed, read the CSV header row and compare it
   against every YAML in the source directory (order-insensitive, exact
   match). App-exported headers are a closed set of localization strings —
   exact match is the right bar; fuzzy or semantic matching adds complexity
   with no benefit. The CSV Headers should not be considered as case-sensitive.
3. If exactly one YAML matches 100%, use it.
4. If zero or more than one match, **fail loudly** — never silently default
   to a language. Print which headers were unmatched so the error points
   directly at a missing YAML or a typo.

### 6.6 YAML loader validation

On startup, before touching any data rows, the loader must:

- Parse every `*.yaml` in the resolved source directory
- Confirm each has all 12 canonical keys under `headers` — no missing, no
  unrecognized extras
- Confirm `rest_marker` is present and non-empty
- Fail immediately with a specific error (which file, which key) if
  anything is wrong

This is what makes "add a language by dropping in a YAML" actually safe.
Without this check, a typo in a new file silently breaks auto-detection
without any obvious error. Ignore case senstivity.

### 6.7 Dependency: `pyyaml`

YAML parsing requires `pyyaml` — not in the standard library.

```bash
uv add pyyaml
```

Alternative: TOML via `tomllib` (stdlib, Python 3.11+) would eliminate
this dependency. The YAML structure above translates directly to TOML with
no semantic change. Confirm your preference before implementation.

---

## 7. Column mapping (canonical → target tables)

| Canonical column | Target | Type cast | Notes |
|---|---|---|---|
| `date` | `raw.workout_sessions.started_at` | `TIMESTAMPTZ` | §8.1 |
| *(derived)* | `raw.workout_sessions.ended_at` | `TIMESTAMPTZ` | `started_at + duration_seconds` |
| `duration` | `raw.workout_sessions.duration_seconds` | `INTEGER` | §8.2 |
| `workout_name` | `raw.workout_sessions.workout_name` | `TEXT` | Requires §5.B |
| `workout_notes` | `raw.workout_sessions.notes` | `TEXT` | Empty in this file; still mapped |
| `exercise_name` | `raw.exercises.exercise_name` | `TEXT` | Not trimmed — §8.4 |
| *(from --source flag)* | `raw.exercises.source` | `TEXT` | e.g. `'strong'` |
| `set_order` | `raw.sets.set_number` / `set_type` | `INTEGER` / `TEXT` | §8.3 |
| `weight` | `raw.sets.weight_kg` | `NUMERIC(6,2)` | §8.5 |
| `reps` | `raw.sets.reps` | `INTEGER` | `"3.0"` → `int(float(x))` |
| `seconds` | `raw.sets.rest_seconds` | `NUMERIC` | §8.6 |
| `notes` | `raw.sets.notes` | `TEXT` | §8.7 |
| `rpe` | `raw.sets.rpe` | `NUMERIC(3,1)` | Empty in this file; still mapped |
| `distance` | — | **Dropped** | No target column in `raw.sets`; 1 of 3,667 rows non-zero. Skipped per rule: drop only if the column has no target in the schema. |

---

## 8. Transformation rules

### 8.1 Timestamp and timezone (unconfirmed — confirm before running)
`date` values are naive (`2025-08-01 20:00:50`, no UTC offset). Assumption:
session **start** time, local to `Europe/Berlin`. The script localizes to
`Europe/Berlin` then converts to UTC before inserting as `TIMESTAMPTZ`.
If `date` actually records finish time, `started_at` should be derived by
subtracting `duration_seconds`, and `date` inserted as `ended_at` instead.

### 8.2 Duration parsing
Two formats observed: `"<N>h <M>min"` (e.g. `"1h 24min"`) and `"<M>min"`
(e.g. `"58min"`, seen in 2 sessions). Parse defensively for an `"<N>h"`-only
form even though none was observed. All formats convert to total integer
seconds.

### 8.3 Rest-row handling
Compare `set_order` against the active language map's `rest_marker` — never
a hardcoded string. If matched:
- `set_number = NULL`
- `set_type = 'rest'`
- `rest_seconds = seconds` (the actual rest duration, e.g. `120.0`)

Otherwise:
- `set_number = int(value)`
- `set_type = 'working'`

Strong's export does not distinguish warmup, working, or failure sets —
all non-rest rows land as `'working'` since no other information is
available in this file.

### 8.4 Exercise name — no trimming
Five exercise names have trailing-whitespace variants in the source
(e.g. `"Ankle Touch"` and `"Ankle Touch "`). These are preserved as-is
and produce distinct rows in `raw.exercises` since `exercise_name` is
`UNIQUE`. Deduplication and canonicalization belong in
`dbt/seeds/dim_exercises.csv`. Trimming here would be cleaning data in
the raw layer, which CLAUDE.md explicitly prohibits.

### 8.5 Weight unit (unconfirmed — confirm before running)
The export has no unit column. The target column is `weight_kg`. Assumption:
Strong was configured in kg for your entire export history. If the unit
setting was changed at any point, affected rows will be off by 2.2× with
no way to detect this from the file alone.

### 8.6 `seconds` is semantically overloaded
For rest rows, `seconds` is the rest duration between sets. For timed
working sets (e.g. `Plank`, treadmill runs), `seconds` is the active
duration of the set — not rest. Both map literally to `raw.sets.rest_seconds`
since that is the only seconds column in the current schema. Disambiguation
belongs in `stg_sets.sql`, not here.

### 8.7 Notes — literal backslash-n, not real newlines
Verified at the byte level: multi-line notes (e.g.
`"First 3 : Right\nLast 3 : Left"`) contain the two-character sequence
`\` + `n`, not an actual newline character. Pass through as-is.

### 8.8 Decimal parsing — built defensively
This file uses periods throughout. The parser still handles both `"82,5"`
and `"82.5"`: if the value contains a comma but no period, treat the comma
as the decimal separator; otherwise parse as-is. This makes the script
reusable against exports from other locales.

---

## 9. Idempotency

No native row IDs exist in CSV exports. Deterministic UUIDv5s are derived
from a fixed namespace + composite natural key and used as actual PK values
(not `gen_random_uuid()` defaults), so re-running the same file produces
identical IDs and triggers conflict paths rather than inserting duplicates.

The `--source` value is included in every key prefix so data from different
apps never collides in shared tables.

| Table | Natural key input | Conflict behaviour |
|---|---|---|
| `raw.workout_sessions` | `"{source}\|{date}"` | `ON CONFLICT (workout_session_id) DO UPDATE` |
| `raw.exercises` | `"{source}\|{exercise_name raw string}"` | `ON CONFLICT (exercise_name) DO NOTHING` — first-source-wins; avoids fighting over `source` attribution if two sources share an exercise name |
| `raw.sets` | `"{source}\|{date}\|{exercise_name raw string}\|{row counter}"` — counter assigned in original file order per `(date, exercise_name)` pair, including rest rows | `ON CONFLICT (set_id) DO UPDATE` |

---

## 10. Ingestion log

The `meta.ingestion_log` table schema is defined and created in
`02-db-cli-setup.md`. That spec must be completed and `setup-db` run
before this script executes.

### 10.1 Helper functions in `src/db/postgres.py`

Three small helpers, placed in `src/db/postgres.py` per CLAUDE.md:

```python
def start_ingestion_log(
    conn,
    source: str,
    language: str | None = None,
    details: dict | None = None,
) -> UUID: ...

def finish_ingestion_log(
    conn,
    log_id: UUID,
    rows_read: int,
    rows_inserted: int,
    rows_updated: int,
    rows_skipped: int,
) -> None: ...

def fail_ingestion_log(
    conn,
    log_id: UUID,
    error_message: str,
) -> None: ...
```

`ingest_workout_csv.py` calls `start_ingestion_log` before any processing,
`finish_ingestion_log` on clean exit, and `fail_ingestion_log` in the
exception handler. The same three functions are reused by `ingest_hevy_api.py`
and `ingest_apple_health_data.py` when those are built.

### 10.2 Re-runs and this table

Each run writes a new `ingestion_log` row regardless of whether data
changed — it is an audit trail of attempts, not a dedup mechanism.
Data-level idempotency is handled separately by the UUIDv5 keys in §9.

---

## 11. Open decisions — confirm before implementation begins

1. **Schema amendments** — apply §5.A and §5.B to Supabase via
   `ALTER TABLE`, and update `utils/create_raw_tables.sql` to match?
   Spec assumes yes.
2. **`date` = start time or finish time?** — spec assumes start (§8.1).
3. **Weight unit** — kg throughout your entire Strong history? (§8.5)
4. **Drop `distance`?** — affects 1 of 3,667 rows (§7).
5. **`set_type = 'working'` as default for all non-rest sets?** — no
   warmup/failure distinction available in this export (§8.3).
6. **YAML or TOML?** — `pyyaml` (new dependency) vs `tomllib` (stdlib)
   (§6.7).
7. **`strong/en.yaml` as a provisional scaffold** — acceptable starting
   point, to be validated against a real English export later? (§6.4)

---

## 12. Files to change

| File | Action |
|---|---|
| `src/ingestion/ingest_workout_csv.py` | Create |
| `src/ingestion/column_maps/strong/de.yaml` | Create |
| `src/ingestion/column_maps/strong/en.yaml` | Create |
| `src/db/postgres.py` | Add — `start_/finish_/fail_ingestion_log()` |
| `src/cli.py` | Add — `ingest-csv` command |
| `pyproject.toml` | Add `pyyaml` (subject to §11.6) |
| `CLAUDE.md` | See §13 |

---

## 13. Dependencies

| Package | Source | Reason |
|---|---|---|
| `pyyaml` | `uv add pyyaml` | YAML column-map loading (see §6.7 for TOML alternative) |
| `psycopg2-binary` | Already in `pyproject.toml` | DB writes |
| `pydantic-settings` | Already in `pyproject.toml` | Env var loading |
| `csv`, `decimal`, `datetime`, `zoneinfo`, `uuid`, `re`, `argparse` | stdlib | No install needed |

---

## 14. Implementation rules

- No ORM — `psycopg2` only, via `get_connection()` from `src/db/postgres.py`
- All queries parameterized — never f-strings in SQL
- Raw layer stays a literal mirror of source values — no trimming, no type
  cleaning, no inferred categorization beyond what §8 defines
- Language resolution never silently guesses (§6.5)
- Column-map YAML files are validated at startup before any rows are
  processed (§6.6)
- `ingestion_log` is written for every run; `fail_ingestion_log` is called
  in any exception path so a crashed run is never invisible
- Credentials via `.env` / `pydantic-settings` only — nothing hardcoded

---

## 15. Definition of done



- [ ] `utils/create_raw_tables.sql` amended per §5.A and §5.B, applied to Supabase
- [ ] `spec_setup_db_cli.md` completed and `setup-db` run so `meta.ingestion_log` exists
- [ ] `column_maps/strong/de.yaml` and `en.yaml` exist and pass loader validation
- [ ] Running `--source strong --lang de` ingests all 3,667 rows without error
- [ ] Running `--source strong` without `--lang` auto-detects German correctly
- [ ] A deliberately malformed YAML (missing a required key) fails at startup, not mid-run
- [ ] An unrecognized header row (no matching YAML) fails with a clear message
- [ ] One row in `raw.workout_sessions` per distinct `date` value in the source file, each with `workout_name` populated
- [ ] One row in `raw.exercises` per distinct `exercise_name` raw string in the source file, whitespace variants preserved as distinct rows
- [ ] Every non-rest row in the source file produces a `set_type = 'working'` row in `raw.sets`; every rest-marker row produces a `set_type = 'rest'` row with `set_number = NULL`
- [ ] Every `'rest'` row has `set_number = NULL`
- [ ] `RPE` and `workout_notes` mapped as `NULL` — not skipped
- [ ] `ingestion_log` has a `'success'` row with accurate row counts after a clean run
- [ ] Forcing a failure produces a `'failed'` row with `error_message` populated
- [ ] Re-running the same file twice produces 2 `ingestion_log` rows and zero duplicate `raw.sets` rows
- [ ] Running with `--source another_app` and a valid column map for that app works without code changes

On completion, update .claude/state.md with what was done and what are the open questions along with today's date.
