# Spec: Strong App CSV Ingestion (`ingest_strong_data.py`)

## 1. Overview

Historical import of Strong app workout data into the `raw` schema established
by `01-database-setup.md`. The script parses Strong's German-locale CSV export,
resolves column headers through a pluggable YAML language map, and writes
sessions, exercises, and sets into Supabase. It is safely re-runnable without
duplicating data.

Two supporting capabilities are introduced alongside the ingestion script:

1. A language-agnostic column-mapping system driven by per-language YAML files
   under `src/ingestion/strong_column_maps/`. Adding support for a new language
   requires only a new YAML file — no code changes.
2. Three helper functions in `src/db/postgres.py` that write to the
   `meta.ingestion_log` table (created by `02-db-cli-setup.md`), giving
   a persistent audit trail of what ran, when, in what language, and how many
   rows landed. The same helpers are reused by future ingestion scripts
   (Hevy API, Apple Health) with no redesign.

---

## 2. Depends on

- `01-database-setup.md` — `raw.workout_sessions`, `raw.exercises`, and `raw.sets`
  must exist in Supabase before this script runs.
- `02-db-cli-setup.md` — `setup-db` must have been run so `meta.ingestion_log` exists.
- Two schema amendments to `scripts/create_raw_tables.sql` (§5.A and §5.B).

---

## 3. Entry point

```bash
# Explicit language (recommended)
uv run python -m src.ingestion.ingest_strong_data --file path/to/export.csv --lang de

# Auto-detect language from header row
uv run python -m src.ingestion.ingest_strong_data --file path/to/export.csv
```

Lives at `src/ingestion/ingest_strong_data.py`, alongside `ingest_hevy_api.py` and
`ingest_apple_health_data.py`, per the layout in CLAUDE.md.

---

## 4. Source file profile

Verified against the actual uploaded export — not assumed from documentation.

| Property | Value |
|---|---|
| Encoding | UTF-8 |
| Delimiter | `,` |
| Total data rows | 3,667 |
| Distinct sessions (`Datum`) | 76 |
| Distinct exercise names | 65 (5 with trailing-whitespace variants — see §8.4) |
| Decimal separator | Period throughout — German-locale commas never observed in `Gewicht` or `Wiederh.` across all 3,667 rows |
| `RPE` column | Empty in 100% of rows |
| `Workout-Notizen` column | Empty in 100% of rows |
| `Entfernung` (distance) | Non-zero in exactly 1 row (treadmill run, `0.52`) |

**Critical structural finding — `Reihenfolge festlegen` is not purely numeric.**
Strong interleaves a rest-timer row after every real set, where this column
contains the literal string `Ruhezeit` instead of a number:

```
"Pull Up", 1,    0, 3.0, 0,   0.0, ...   ← real set
"Pull Up", Ruhezeit, 0, 0.0, 0, 120.0, ... ← rest-timer row
```

1,833 of the 3,667 rows are `Ruhezeit` rows — not performed sets. This
directly conflicts with `raw.sets.set_number INTEGER NOT NULL` (see §5.A).
The `rest_marker` string (`Ruhezeit` in German) is localized — a Spanish or
French export will use a different string — so it is stored per-language in
the YAML configs rather than hardcoded in the script.

---

## 5. Required schema amendments

**A. `raw.sets.set_number` → nullable.**
Rest rows have no set number. Forcing a value (e.g. `0`) would misrepresent
the source. Per CLAUDE.md, the raw layer is a literal mirror of the source —
nullable is the honest representation.

**B. `raw.workout_sessions` → add `workout_name TEXT NULL`.**
The `Workout-Name` column (e.g. `"Day 1"`, `"Abend-Workout"`) has no target
column in the current schema. Dropping it silently would lose meaningful
training-split metadata. Adding the column is the correct fix.

**C. New table: `meta.ingestion_log`.**
Pipeline-generated operational metadata belongs in a separate schema from
source-mirroring raw data. Defined and created in `02-db-cli-setup.md`.

---

## 6. Language mapping design

### 6.1 Canonical column vocabulary

All pipeline code refers exclusively to these 12 canonical names, regardless
of the source language. No German or English strings appear anywhere in
`ingest_strong_data.py`:

```
date, workout_name, duration, exercise_name, set_order,
weight, reps, distance, seconds, notes, workout_notes, rpe
```

Plus one special value — `rest_marker` — the localized cell value (not a
column header) that identifies a rest-timer row.

### 6.2 File layout

```
src/ingestion/strong_column_maps/
    de.yaml
    en.yaml
    # add further languages by dropping a file here — no code changes needed
```

### 6.3 `de.yaml` — verified against the actual uploaded file

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

### 6.4 `en.yaml` — scaffold, not verified against a real English export

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

These are Strong's conventional English labels based on general knowledge —
they have not been byte-inspected the way the German file was. `rest_marker`
in particular is unconfirmed. Treat this as a provisional starting point to
be tightened the moment a real English export is available.

### 6.5 Language resolution order

1. If `--lang` is passed, load that language's YAML directly. If no matching
   file exists, fail immediately and list the available language codes.
2. If `--lang` is not passed, read the CSV header row and compare it against
   every loaded YAML's `headers` values (order-insensitive, exact match).
   Strong's headers are a closed set of app-localization strings — exact match
   is the correct bar; fuzzy or semantic matching adds complexity with no
   benefit here.
3. If exactly one config matches 100%, use it.
4. If zero or more than one match, **fail loudly** — never silently default to
   a language. Print which headers were unmatched so the error points directly
   at a missing YAML or a header typo.

### 6.6 YAML loader validation

On startup, before touching any data rows, the loader must:

- Parse every `*.yaml` file in `strong_column_maps/`
- Confirm each has all 12 canonical keys under `headers` — no missing keys,
  no unrecognized extras
- Confirm `rest_marker` is present and non-empty
- Fail immediately with a specific error (which file, which key) if any
  validation fails

This is what makes "add a language by dropping in a YAML file" actually safe.
Without this check, a typo in a new file silently breaks the wrong-language
detection path without any obvious error.

### 6.7 Dependency: `pyyaml`

YAML parsing requires `pyyaml` — not in the standard library.

```bash
uv add pyyaml
```

If you'd rather avoid a new dependency, TOML is a direct substitute and is
parseable via `tomllib` (stdlib in Python 3.11+). The YAML structure above
translates directly to TOML with no semantic changes. Flag your preference
and the language map files will be updated accordingly.

---

## 7. Column mapping (canonical → target tables)

| Canonical column | Target | Type cast | Notes |
|---|---|---|---|
| `date` | `raw.workout_sessions.started_at` | `TIMESTAMPTZ` | §8.1 |
| *(derived)* | `raw.workout_sessions.ended_at` | `TIMESTAMPTZ` | `started_at + duration_seconds` |
| `duration` | `raw.workout_sessions.duration_seconds` | `INTEGER` | §8.2 |
| `workout_name` | `raw.workout_sessions.workout_name` | `TEXT` | Requires §5.B |
| `workout_notes` | `raw.workout_sessions.notes` | `TEXT` | Empty in this file; still mapped (no target column skipped unless it doesn't exist) |
| `exercise_name` | `raw.exercises.exercise_name` | `TEXT` | Not trimmed — §8.4 |
| *(constant)* | `raw.exercises.source` | `TEXT` | `'strong'` |
| `set_order` | `raw.sets.set_number` / `set_type` | `INTEGER` / `TEXT` | §8.3 |
| `weight` | `raw.sets.weight_kg` | `NUMERIC(6,2)` | §8.5 |
| `reps` | `raw.sets.reps` | `INTEGER` | `"3.0"` → `int(float(x))` |
| `seconds` | `raw.sets.rest_seconds` | `NUMERIC` | §8.6 |
| `notes` | `raw.sets.notes` | `TEXT` | §8.7 |
| `rpe` | `raw.sets.rpe` | `NUMERIC(3,1)` | Empty in this file; still mapped |
| `distance` | — | **Dropped** | No target column in `raw.sets`; only 1 of 3,667 rows non-zero. Allowed per the spec rule: skip if the column doesn't exist in the target table. |

---

## 8. Transformation rules

### 8.1 Timestamp and timezone (unconfirmed — confirm before running)
`date` values are naive (`2025-08-01 20:00:50`, no offset). Assumption:
session **start** time, local to `Europe/Berlin`. The script localizes to
`Europe/Berlin` then converts to UTC before inserting into `TIMESTAMPTZ`.
If `Datum` actually records session finish time, derive `started_at` by
subtracting `duration_seconds` instead, and insert `Datum` as `ended_at`.

### 8.2 Duration parsing
Two formats observed: `"<N>h <M>min"` (e.g. `"1h 24min"`) and `"<M>min"`
(e.g. `"58min"`, seen in 2 sessions). No `"<N>h"`-only form observed, but
parse defensively for it anyway. All formats convert to total integer seconds.

### 8.3 Rest-row handling
Compare `set_order` against the active language map's `rest_marker` — never
a hardcoded string. If matched:
- `set_number = NULL`
- `set_type = 'rest'`
- `rest_seconds = seconds` (actual rest duration, e.g. `120.0`)

Otherwise:
- `set_number = int(value)`
- `set_type = 'working'`

Strong's export does not distinguish warmup, working, or failure sets in this
column — all non-rest rows land as `'working'` since there is no other
information to go on.

### 8.4 Exercise name — no trimming
Five exercise names have trailing-whitespace variants in the source
(e.g. `"Ankle Touch"` and `"Ankle Touch "` — genuinely different strings,
each appearing multiple times). These are preserved as-is and produce
distinct rows in `raw.exercises` because `exercise_name` is `UNIQUE`.
Deduplication and canonicalization belong in `dbt/seeds/dim_exercises.csv`,
which is exactly what that seed file exists to do. Trimming here would be
cleaning data in the raw layer, which CLAUDE.md explicitly prohibits.

### 8.5 Weight unit (unconfirmed — confirm before running)
The export has no unit column. The target column is named `weight_kg`.
Assumption: the Strong app was configured in kg for your entire export
history. If the unit setting was changed at any point, affected rows will
be off by a 2.2× factor with no way to detect this from the file alone.

### 8.6 `seconds` is semantically overloaded
For rest rows, `seconds` is the rest duration between sets. For timed
working sets (e.g. `Plank`, `Running (Treadmill)`), `seconds` is the
active hold or movement duration — not rest. Both map literally to
`raw.sets.rest_seconds` since that is the only `NUMERIC` seconds column
in the current schema. The semantic disambiguation (which rows are actual
rest durations vs. working-set durations) belongs in `stg_sets.sql`, not
here. The raw layer stays a literal mirror.

### 8.7 Notes — literal backslash-n, not real newlines
Verified at the byte level: multi-line notes in the source (e.g.
`"First 3 : Right\nLast 3 : Left"`) contain the two-character sequence
`\` + `n`, not an actual newline character. Pass through as-is — no
unescaping in ingestion.

### 8.8 Decimal parsing — built defensively
This file uses periods throughout (no German-locale commas observed). The
parser is still built to handle both `"82,5"` and `"82.5"`: if the value
contains a comma but no period, treat the comma as the decimal separator;
otherwise parse as-is. This costs nothing and makes the script reusable
against other exports that may behave differently.

---

## 9. Idempotency

Strong's CSV has no native row IDs. Deterministic UUIDv5s are derived from
a fixed namespace + composite natural key and used as the actual PK values
(not `gen_random_uuid()` defaults), so re-running against the same file
produces identical IDs and triggers the conflict paths rather than inserting
duplicates.

| Table | Natural key input | Conflict behaviour |
|---|---|---|
| `raw.workout_sessions` | `"strong\|{date}"` — confirmed 1:1 with workout name across all 76 sessions | `ON CONFLICT (workout_session_id) DO UPDATE` |
| `raw.exercises` | `"strong\|{exercise_name raw string}"` | `ON CONFLICT (exercise_name) DO NOTHING` — first-source-wins; avoids fighting over `source` attribution when Hevy later writes the same exercise name |
| `raw.sets` | `"strong\|{date}\|{exercise_name raw string}\|{row counter}"` — counter assigned in original file order per `(date, exercise_name)` pair, including rest rows | `ON CONFLICT (set_id) DO UPDATE` |

---

## 10. Ingestion log

The `meta.ingestion_log` table schema and `scripts/create_meta_tables.sql`
are defined in `02-db-cli-setup.md`. That spec must be completed and
`setup-db` run before this script executes.

### 10.1 Helper functions in `src/db/postgres.py`

Three small helpers, placed in `src/db/postgres.py` per CLAUDE.md
(all DB logic lives there):

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

`ingest_strong_data.py` calls `start_ingestion_log` before any processing,
`finish_ingestion_log` on clean exit, and `fail_ingestion_log` in the
exception handler. The same three functions are what `hevy_api.py` and
`apple_health.py` will call — no redesign required when those are built.

### 10.4 Re-runs and this table

Each run of the script writes a new `ingestion_log` row regardless of
whether any data changed — it is an audit trail of attempts, not a dedup
mechanism. Data-level idempotency is handled separately by the UUIDv5
keys in §9.

---

## 11. Open decisions — confirm before implementation begins

1. **Schema amendments** — add `workout_name` to `raw.workout_sessions`
   and make `set_number` nullable in `raw.sets`? (§5.A / §5.B) — spec assumes yes.
2. **`date` = start time or finish time?** — spec assumes start (§8.1).
3. **Weight unit** — kg throughout your entire Strong history? (§8.5)
4. **Drop `distance`?** — only 1 of 3,667 rows is non-zero (§7).
5. **`set_type = 'working'` as the default for all non-rest sets?** — no
   warmup/failure distinction is available in this export (§8.3).
6. **YAML or TOML for column maps?** — `pyyaml` (new dependency) vs
   `tomllib` (stdlib, no install needed) (§6.7).
7. **`en.yaml` as an unverified scaffold** — acceptable as a provisional
   starting point, to be validated against a real English export later? (§6.4)

---

## 12. Files to change

| File | Action |
|---|---|
| `src/ingestion/ingest_strong_data.py` | Create |
| `src/ingestion/strong_column_maps/de.yaml` | Create |
| `src/ingestion/strong_column_maps/en.yaml` | Create |
| `scripts/create_raw_tables.sql` | Amend — §5.A and §5.B |
| `src/db/postgres.py` | Add — `start_/finish_/fail_ingestion_log()` |
| `pyproject.toml` | Add `pyyaml` (subject to §11.6) |
| `CLAUDE.md` — implementation status table | Add `ingest_strong_data.py` as active |
| `CLAUDE.md` — data layer section | Add `meta` schema |

---

## 13. Dependencies

| Package | Source | Reason |
|---|---|---|
| `pyyaml` | `uv add pyyaml` | YAML column-map loading (see §6.7 for TOML alternative) |
| `psycopg2-binary` | Already in `pyproject.toml` | DB writes |
| `pydantic-settings` | Already in `pyproject.toml` | Env var loading |
| `csv`, `decimal`, `datetime`, `zoneinfo`, `uuid`, `re`, `argparse` | stdlib | All standard library, no install needed |

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

- [ ] `scripts/create_raw_tables.sql` amended per §5.A and §5.B, applied to Supabase
- [ ] `02-db-cli-setup.md` completed and `setup-db` run so `meta.ingestion_log` exists
- [ ] `de.yaml` and `en.yaml` exist and both pass loader validation (§6.6)
- [ ] Running with `--lang de` ingests all 3,667 rows without error
- [ ] Running without `--lang` auto-detects German correctly from the header row
- [ ] A deliberately malformed YAML (missing a required key) fails at startup, not mid-run
- [ ] An unrecognized header row (no matching YAML) fails with a clear message, not a silent wrong mapping
- [ ] 76 rows in `raw.workout_sessions`, each with `workout_name` populated
- [ ] 65 rows in `raw.exercises`, whitespace variants preserved as distinct rows
- [ ] 1,834 `raw.sets` rows with `set_type = 'working'`, 1,833 with `set_type = 'rest'`
- [ ] Every `'rest'` row has `set_number = NULL`
- [ ] `RPE` and `workout_notes` are mapped as `NULL` — not skipped (confirms the mapping ran)
- [ ] `ingestion_log` has a `'success'` row with accurate row counts after a clean run
- [ ] Forcing a failure produces a `'failed'` row with `error_message` populated
- [ ] Re-running the same file twice produces 2 `ingestion_log` rows and zero duplicate `raw.sets` rows