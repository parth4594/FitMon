# Spec: Hevy CSV Ingestion

## 1. Overview

Extend the existing app-agnostic CSV ingestion pipeline
(`src/ingestion/ingest_workout_csv.py`) to support the Hevy app CSV export
format.

This spec covers:
1. A new column map YAML for Hevy English exports —
   `src/ingestion/column_maps/hevy/en.yaml`
2. Schema amendments to `raw.workout_sessions` and `raw.sets` to accommodate
   Hevy-specific fields
3. A new table `raw.cardio_sets` for cardio-specific columns
4. The mapping and parsing rules that differ from the Strong ingestion path

The existing script logic is reused without modification where possible.
Hevy-specific behaviour is encoded entirely in the column map YAML and the
schema additions below.

---

## 2. Depends on

- `01-database-setup.md` — `raw.workout_sessions`, `raw.exercises`, and
  `raw.sets` must exist in Supabase.
- `02-db-cli-setup.md` — `setup-db` must have been run so
  `meta.ingestion_log` exists.
- `03-ingest-workout-csv.md` — the generic ingestion script must be
  implemented and working for the Strong source before this spec is applied.

---

## 3. Entry point

```bash
uv run python -m src.cli ingest-csv \
  --file path/to/hevy_export.csv \
  --source hevy \
  --lang en
```

`--source hevy` selects `src/ingestion/column_maps/hevy/en.yaml`.
`--lang en` is always explicit for Hevy — there is no German locale variant.

---

## 4. Source file profile

Verified against the sample rows provided. All assumptions below are
grounded in the actual CSV format, not Hevy documentation.

| Property | Value |
|---|---|
| Encoding | UTF-8 |
| Delimiter | `,` |
| Decimal separator | Period (`.`) throughout |
| `start_time` / `end_time` format | `DD.MM.YYYY, HH:MM` (e.g. `10.07.2026, 20:20`) |
| Rest marker rows | None — Hevy has no rest-timer rows |
| `superset_id` column | Present in CSV — not ingested |
| `set_index` | Zero-based integer (0, 1, 2 …) — maps to `set_number`, not transformed |
| Cardio columns | `distance_km`, `duration_seconds` — present but NULL for non-cardio sets |
| `description` | Per-session notes field (same value repeated across all rows for a session) |
| `exercise_notes` | Per-set notes field |

---

## 5. Schema amendments

Apply these changes to `utils/create_raw_tables.sql` and run against
Supabase before implementing.

### 5.A `raw.workout_sessions` — add `ended_at`

The Strong ingestion path did not populate `ended_at` because the Strong CSV
has no end-time column. Hevy provides `end_time` directly.

The column already exists in the table definition from `spec_db_setup.md`
(`ended_at TIMESTAMPTZ Nullable`) — no schema change required for this
column. Confirm it is present before proceeding.

### 5.B `raw.sets` — add `exercise_notes`

The Strong CSV has no per-set notes column. Hevy provides `exercise_notes`.

```sql
ALTER TABLE raw.sets
  ADD COLUMN IF NOT EXISTS exercise_notes TEXT;
```

Add this line to `utils/create_raw_tables.sql` so `setup-db` remains
idempotent for future runs.

### 5.C New table — `raw.cardio_sets`

One row per set that contains non-null cardio data (`distance_km` or
`duration_seconds`). Linked to the parent session by `workout_session_id`
and to the exercise by `exercise_id`.

```sql
CREATE TABLE IF NOT EXISTS raw.cardio_sets (
    cardio_set_id       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    workout_session_id  UUID          NOT NULL,   -- joins raw.workout_sessions (no FK constraint)
    exercise_id         UUID          NOT NULL,   -- joins raw.exercises (no FK constraint)
    set_index           INTEGER       NOT NULL,
    exercise_title      TEXT          NOT NULL,
    distance_km         NUMERIC(8,3)  NULLABLE,
    duration_seconds    INTEGER       NULLABLE,
    created_at          TIMESTAMPTZ   DEFAULT now()
);
```

**No FK constraints** — consistent with the raw schema rules.

---

## 6. Column map YAML

File path: `src/ingestion/column_maps/hevy/en.yaml`

```yaml
language: en
headers:
  workout_name:     title
  started_at:       start_time
  ended_at:         end_time
  notes:            description      # "description" in Hevy → "notes" in raw.workout_sessions
  exercise_name:    exercise_title
  set_number:       set_index        # zero-based; not transformed here
  set_type:         set_type
  weight_kg:        weight_kg
  reps:             reps
  rpe:              rpe
  exercise_notes:   exercise_notes   # per-set notes; absent in Strong
  distance_km:      distance_km      # cardio; routed to raw.cardio_sets if non-null
  duration_seconds: duration_seconds # cardio; routed to raw.cardio_sets if non-null

# Columns present in the CSV that must be silently ignored
# NOTE: ignored_columns is a Hevy-specific key. The ingestion script must
# read this list and skip matching source columns before mapping.
ignored_columns:
  - superset_id
```

---

## 7. Parsing rules

### 7.1 Timestamp parsing

Both `start_time` and `end_time` arrive in the format `DD.MM.YYYY, HH:MM`
(e.g. `10.07.2026, 20:20`). Parse with:

```python
from datetime import datetime, timezone

def parse_hevy_timestamp(raw: str) -> datetime:
    dt = datetime.strptime(raw.strip(), "%d.%m.%Y, %H:%M")
    return dt.replace(tzinfo=timezone.utc)
```

Store as `TIMESTAMPTZ`. Assume UTC until the user adds timezone handling in
the transformation layer.

Trigger this parser when `source == 'hevy'` and the column map marks a field
as a timestamp. The column map YAML does not encode timestamp hints — the
dispatch is handled by the ingestion script's source-aware timestamp parser.

### 7.2 Session deduplication

Session identity key: `(title, started_at)` — same as Strong.

Use `ON CONFLICT (workout_session_id) DO UPDATE` on upsert.
`workout_session_id` is a UUIDv5 derived from `(title, started_at)`.

`ended_at` is written on every upsert so that a re-run with the same file
updates it if the source changes.

### 7.3 `set_number` (zero-based)

`set_index` in the Hevy CSV starts from `0`. Write the raw value directly to
`raw.sets.set_number`. **Do not add 1.** The transformation to 1-based
indexing is deferred to `stg_sets.sql`.

### 7.4 No rest marker

The Strong path checks each row against `rest_marker` in the YAML to detect
rest-timer rows and set `set_type = 'rest'` / `set_number = NULL`.

Hevy has no rest-timer rows. If `rest_marker` is absent from the column map
YAML (as it is here), the rest-detection block is skipped entirely — the
existing `if rest_marker and …` guard in `ingest_workout_csv.py` handles
this without code changes.

### 7.5 Cardio row detection and routing

A row is treated as a cardio row if **either** `distance_km` or
`duration_seconds` is non-null and non-empty in the source CSV.

For cardio rows:
- Write a record to `raw.cardio_sets` using the cardio fields from §6.
- Also write a record to `raw.sets` with cardio columns set to `NULL` — the
  set still represents a performed set and belongs in the main fact table.

For non-cardio rows (the majority):
- Write to `raw.sets` only. Do not write to `raw.cardio_sets`.

Cardio set identity key for upsert:
`UUIDv5(namespace=NAMESPACE_DNS, name=f"{workout_session_id}:{exercise_name}:{set_index}")`

### 7.6 `superset_id` — ignored

`superset_id` is listed under `ignored_columns` in the YAML. The ingestion
script reads this top-level list at startup and silently skips any source
CSV column whose name appears in it — before the `headers:` mapping is
applied. This requires a small extension to `ingest_workout_csv.py` (see
§10): load `config.get("ignored_columns", [])` and filter those keys out of
each row dict before processing.

### 7.7 Decimal parsing

`weight_kg` uses a period decimal separator in Hevy exports. No special
handling needed beyond the standard `float()` cast already used for Strong.

### 7.8 `exercise_notes`

Map `exercise_notes` (Hevy CSV) → `exercise_notes` (raw.sets). Write `NULL`
if the cell is empty.

---

## 8. UUIDv5 keying

Consistent with the Strong ingestion path:

| Entity | Namespace | Name components |
|---|---|---|
| `workout_session_id` | `NAMESPACE_DNS` | `f"{title}:{started_at.isoformat()}"` |
| `exercise_id` | `NAMESPACE_DNS` | `f"hevy:{exercise_title}"` |
| `set_id` | `NAMESPACE_DNS` | `f"{workout_session_id}:{exercise_name}:{row_counter}"` |
| `cardio_set_id` | `NAMESPACE_DNS` | `f"{workout_session_id}:{exercise_name}:{set_index}"` |

`row_counter` is a `defaultdict(int)` keyed on `(started_at, exercise_name)`,
incremented per row — same pattern as Strong. Hevy has no rest rows where
`set_index` could be NULL, but the `row_counter` key is kept for consistency
across sources.

The `exercise_id` namespace prefix `hevy:` ensures exercise UUIDs from Hevy
and Strong are distinct even if the exercise names collide (e.g. both apps
may have `"Bench Press"`).

---

## 9. `ingestion_log` entry

Written to `meta.ingestion_log` on every run — same as Strong.

| Field | Value |
|---|---|
| `source` | `'hevy'` |
| `file_path` | Absolute path of the ingested file |
| `rows_read` | Total CSV rows parsed |
| `rows_inserted` | Rows written to `raw.sets` |
| `status` | `'success'` or `'failed'` |
| `error_message` | `NULL` on success, exception message on failure |

---

## 10. Files to change

| File | Action |
|---|---|
| `utils/create_raw_tables.sql` | Add `ALTER TABLE raw.sets ADD COLUMN IF NOT EXISTS exercise_notes TEXT` and `CREATE TABLE IF NOT EXISTS raw.cardio_sets (…)` |
| `src/ingestion/column_maps/hevy/en.yaml` | Create — full column map for Hevy English export |
| `src/ingestion/ingest_workout_csv.py` | Extend — add Hevy timestamp parser dispatch, cardio row routing, and `ignored_columns` filtering |

## 10.1 Files NOT to change

| File | Reason |
|---|---|
| `src/db/postgres.py` | No new DB connection logic required |
| `src/ingestion/column_maps/strong/` | Strong maps are unaffected |
| `src/cli.py` | `ingest-csv` command already accepts `--source` and `--lang` |

---

## 11. Unit tests to add

Add to `tests/unit/test_ingest_workout_csv.py`.

### 11.1 Hevy timestamp parsing

```python
def test_parse_hevy_timestamp_roundtrip():
    raw = "10.07.2026, 20:20"
    dt = parse_hevy_timestamp(raw)
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.day == 10
    assert dt.hour == 20
    assert dt.minute == 20
    assert dt.tzinfo is not None
```

### 11.2 Cardio row detection

```python
@pytest.mark.parametrize("distance_km,duration_seconds,expected", [
    ("1.5", "",    True),
    ("",    "300", True),
    ("",    "",    False),
    (None,  None,  False),
])
def test_is_cardio_row(distance_km, duration_seconds, expected):
    assert is_cardio_row(distance_km, duration_seconds) == expected
```

---

## 12. Definition of done

- [ ] `raw.sets` has an `exercise_notes TEXT` column in Supabase
- [ ] `raw.cardio_sets` table exists with correct schema
- [ ] `src/ingestion/column_maps/hevy/en.yaml` exists and loads without error
- [ ] Running `--source hevy --lang en` ingests the sample rows without error
- [ ] `raw.workout_sessions` row for the sample session has `started_at = 2026-07-10 20:20 UTC`
  and `ended_at = 2026-07-10 21:26 UTC`
- [ ] `notes` in `raw.workout_sessions` is `"Post workout: banana coffee and cola zero"`
- [ ] `raw.sets` rows for `Chest Fly (Machine)` have `set_number` values `0, 1, 2`
  (not `1, 2, 3`)
- [ ] `superset_id` does not appear in any raw table
- [ ] Cardio rows (non-null `distance_km` or `duration_seconds`) produce a record
  in both `raw.sets` and `raw.cardio_sets`
- [ ] Non-cardio rows produce a record in `raw.sets` only
- [ ] Re-running the same file twice produces zero duplicate rows in any raw table
- [ ] `meta.ingestion_log` has a `'success'` row after a clean run with `source = 'hevy'`
- [ ] All four unit tests in §11 pass
- [ ] No FK constraints added to any raw table