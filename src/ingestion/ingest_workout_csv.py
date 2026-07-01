"""Workout CSV ingestion — source-agnostic via pluggable YAML column maps.

Entry point: ingest_csv() called from src.cli.
"""

import csv
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

import yaml

from src.db.postgres import (
    get_connection,
    start_ingestion_log,
    finish_ingestion_log,
    fail_ingestion_log,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COLUMN_MAPS_DIR = Path(__file__).resolve().parent / "column_maps"

# 12 canonical column names every column map must declare under `headers:`
_CANONICAL_KEYS = frozenset(
    {
        "date",
        "workout_name",
        "duration",
        "exercise_name",
        "set_order",
        "weight",
        "reps",
        "distance",
        "seconds",
        "notes",
        "workout_notes",
        "rpe",
    }
)

# UUIDv5 namespace: stable, arbitrary, never changes
_UUID_NS = uuid5(NAMESPACE_URL, "fitmon.ingestion")

# Timezone for Strong app naive timestamps (Germany)
_TZ_LOCAL = ZoneInfo("Europe/Berlin")


# ---------------------------------------------------------------------------
# Column-map loading and validation (§6.6)
# ---------------------------------------------------------------------------


def _load_and_validate_all_maps(source_dir: Path) -> dict[str, dict]:
    """Load and validate every *.yaml in source_dir.

    Returns a mapping of {lang_code: parsed_yaml_dict}.
    Raises SystemExit with a specific error if any file is invalid.
    """
    yaml_files = sorted(source_dir.glob("*.yaml"))
    if not yaml_files:
        sys.exit(
            f"[column-map] No YAML files found in {source_dir}. "
            "At least one <lang>.yaml is required."
        )

    maps: dict[str, dict] = {}
    for yaml_path in yaml_files:
        lang_code = yaml_path.stem
        with yaml_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            sys.exit(f"[column-map] {yaml_path.name}: root must be a YAML mapping.")

        # Validate headers section
        headers = data.get("headers")
        if not isinstance(headers, dict):
            sys.exit(
                f"[column-map] {yaml_path.name}: missing or invalid 'headers' section."
            )

        # All 12 canonical keys must be present — no missing, no unrecognized extras
        # (case-insensitive check on the canonical key names themselves)
        present_keys = {k.lower() for k in headers}
        canonical_lower = {k.lower() for k in _CANONICAL_KEYS}
        missing = canonical_lower - present_keys
        extra = present_keys - canonical_lower
        if missing:
            sys.exit(
                f"[column-map] {yaml_path.name}: missing required header keys: "
                + ", ".join(sorted(missing))
            )
        if extra:
            sys.exit(
                f"[column-map] {yaml_path.name}: unrecognized header keys: "
                + ", ".join(sorted(extra))
            )

        maps[lang_code] = data

    return maps


# ---------------------------------------------------------------------------
# Language resolution (§6.5)
# ---------------------------------------------------------------------------


def _resolve_language(
    source_dir: Path,
    all_maps: dict[str, dict],
    lang_arg: str | None,
    csv_header_row: list[str],
) -> dict:
    """Return the resolved column-map dict for the detected/requested language."""
    if lang_arg is not None:
        if lang_arg not in all_maps:
            available = ", ".join(sorted(all_maps))
            sys.exit(
                f"[lang] Language '{lang_arg}' not found in {source_dir}. "
                f"Available: {available}"
            )
        return all_maps[lang_arg]

    # Auto-detect: compare CSV headers (case-insensitive, order-insensitive)
    # against each YAML's header values
    csv_headers_lower = {h.lower() for h in csv_header_row}
    matches: list[str] = []
    for lang_code, col_map in all_maps.items():
        yaml_header_values_lower = {v.lower() for v in col_map["headers"].values()}
        if yaml_header_values_lower == csv_headers_lower:
            matches.append(lang_code)

    if len(matches) == 1:
        return all_maps[matches[0]]

    if len(matches) == 0:
        # Find which headers didn't match any YAML to help the user diagnose
        all_yaml_values_lower: set[str] = set()
        for col_map in all_maps.values():
            all_yaml_values_lower |= {v.lower() for v in col_map["headers"].values()}
        unmatched = csv_headers_lower - all_yaml_values_lower
        sys.exit(
            "[lang] Auto-detection failed: no YAML matched the CSV headers exactly.\n"
            f"       CSV headers: {sorted(csv_header_row)}\n"
            f"       Unmatched headers: {sorted(unmatched)}\n"
            "       Add a YAML file for this language or check for typos."
        )

    # More than one match — ambiguous
    sys.exit(
        "[lang] Auto-detection failed: multiple YAMLs matched the CSV headers: "
        + ", ".join(sorted(matches))
    )


# ---------------------------------------------------------------------------
# Canonical row reader
# ---------------------------------------------------------------------------


def _build_canonical_reader(col_map: dict) -> dict[str, str]:
    """Invert the YAML headers map: {csv_column_header: canonical_name}."""
    return {v: k for k, v in col_map["headers"].items()}


# ---------------------------------------------------------------------------
# Transformation helpers (§8) — pure functions, no I/O
# ---------------------------------------------------------------------------


def _parse_duration_seconds(raw: str) -> int:
    """Convert "Nh Mmin", "Mmin", or "Nh" → total integer seconds (§8.2)."""
    raw = raw.strip()
    match = re.fullmatch(
        r"(?:(\d+)h\s*)?(\d+)min|(\d+)h",
        raw,
    )
    if not match:
        raise ValueError(f"Unrecognised duration format: {raw!r}")
    if match.group(3) is not None:
        # "Nh" only form
        return int(match.group(3)) * 3600
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2))
    return hours * 3600 + minutes * 60


def _parse_timestamp(raw: str) -> datetime:
    """Parse naive datetime string, localize to Europe/Berlin, convert to UTC.
     
       Localizing to Europe/Berlin is necessary before UTC conversion."""
    naive = datetime.fromisoformat(raw.strip())
    local_dt = naive.replace(tzinfo=_TZ_LOCAL)
    return local_dt.astimezone(timezone.utc)


def _parse_decimal(raw: str) -> Decimal | None:
    """Parse a decimal value, handling both '.' and ',' as decimal separator (§8.8)."""
    raw = raw.strip()
    if not raw:
        return None
    # If contains comma but no period → treat comma as decimal separator
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _make_uuid5(key_string: str) -> UUID:
    """Generate a deterministic UUIDv5 from the composite key string (§9)."""
    return uuid5(_UUID_NS, key_string)


# ---------------------------------------------------------------------------
# Main ingestion function
# ---------------------------------------------------------------------------


def ingest_csv(file_path: str, source: str, lang: str | None) -> dict[str, Any]:
    """Ingest a workout CSV file into raw.workout_sessions, raw.exercises, raw.sets.

    Returns a summary dict with row counts, or raises on failure after
    writing a 'failed' row to meta.ingestion_log.
    """
    csv_path = Path(file_path)
    if not csv_path.exists():
        sys.exit(f"[file] CSV file not found: {csv_path}")

    # Resolve source directory
    source_dir = _COLUMN_MAPS_DIR / source
    if not source_dir.is_dir():
        available = [d.name for d in _COLUMN_MAPS_DIR.iterdir() if d.is_dir()]
        sys.exit(
            f"[source] Unknown source '{source}'. "
            f"Available: {', '.join(sorted(available)) or '(none)'}"
        )

    # Phase 1: validate all YAML maps before touching any data rows (§6.6)
    all_maps = _load_and_validate_all_maps(source_dir)

    # Read the CSV header row for language resolution
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header_row = next(reader)
        except StopIteration:
            sys.exit(f"[file] CSV file is empty: {csv_path}")

    # Phase 2: resolve language (§6.5)
    col_map = _resolve_language(source_dir, all_maps, lang, header_row)
    resolved_lang: str = col_map["language"]
    rest_marker: str = col_map["rest_marker"]
    canonical_reader = _build_canonical_reader(col_map)

    # Connect and open the audit log row
    conn = get_connection()
    log_id: UUID | None = None
    try:
        log_id = start_ingestion_log(
            conn,
            source=source,
            language=resolved_lang,
            details={"file": str(csv_path.resolve())},
        )

        rows_read = 0
        sessions_inserted = 0
        sessions_updated = 0
        exercises_inserted = 0
        sets_inserted = 0
        sets_updated = 0

        # -------------------------------------------------------------------
        # Pass 1: parse all CSV rows into in-memory structures.
        # Row counters per (date, exercise_name) pair must be assigned in
        # original file order (including rest rows), so we collect before writing.
        # -------------------------------------------------------------------

        # session_key → session metadata dict
        sessions: dict[str, dict[str, Any]] = {}
        # exercise_name_raw → source string (no trimming — §8.4)
        exercises: dict[str, str] = {}
        # ordered list of set/rest row dicts ready for DB insertion
        set_rows: list[dict[str, Any]] = []

        # row counter per (date_raw, exercise_name_raw) pair, incl. rest rows (§9)
        row_counters: dict[tuple[str, str], int] = {}

        with csv_path.open(encoding="utf-8", newline="") as fh:
            dict_reader = csv.DictReader(fh)
            for raw_row in dict_reader:
                rows_read += 1

                # Map CSV column headers → canonical names
                canonical: dict[str, str] = {}
                for csv_col, value in raw_row.items():
                    c_name = canonical_reader.get(csv_col)
                    if c_name is not None:
                        canonical[c_name] = value if value is not None else ""

                # --- Session ---
                date_raw = canonical.get("date", "").strip()
                if not date_raw:
                    continue  # skip rows with no date

                workout_name_raw = canonical.get("workout_name", "")
                duration_raw = canonical.get("duration", "")
                workout_notes_raw = canonical.get("workout_notes", "") or None

                session_key = f"{source}|{date_raw}"
                if session_key not in sessions:
                    sessions[session_key] = {
                        "date_raw": date_raw,
                        "workout_name": workout_name_raw,
                        "duration_raw": duration_raw,
                        "workout_notes": workout_notes_raw,
                    }

                # --- Exercise (no trimming — §8.4) ---
                exercise_name_raw = canonical.get("exercise_name", "")
                exercises[exercise_name_raw] = source

                # --- Row counter in original file order (§9) ---
                # Strong's CSV has no native row IDs, so we derive a stable positional counter
                # per (date, exercise_name) group to generate deterministic UUIDv5s for each row.
                # We cannot use set_number because rest rows have set_number = NULL, which would
                # cause two rest rows for the same exercise on the same day to produce identical
                # UUIDs and collide on upsert. row_counter increments for every row — including
                # rest rows — ensuring every row gets a unique, stable position across re-runs.
                counter_key = (date_raw, exercise_name_raw)
                row_counters[counter_key] = row_counters.get(counter_key, 0) + 1
                row_counter = row_counters[counter_key]

                # --- Classify set vs rest row (§8.3) ---
                set_order_raw = canonical.get("set_order", "").strip()
                is_rest = set_order_raw == rest_marker

                # Deterministic UUIDv5 PKs (§9)
                set_id = _make_uuid5(
                    f"{source}|{date_raw}|{exercise_name_raw}|{row_counter}"
                )
                workout_session_id = _make_uuid5(session_key)
                exercise_id = _make_uuid5(f"{source}|{exercise_name_raw}")

                # Type casts (§8.5, §8.8)
                weight_val = _parse_decimal(canonical.get("weight", ""))
                reps_raw = canonical.get("reps", "").strip()
                reps_val = int(float(reps_raw)) if reps_raw else None
                seconds_raw = canonical.get("seconds", "").strip()
                seconds_dec = _parse_decimal(seconds_raw)
                # rest_seconds is INTEGER in schema
                rest_seconds_val = int(seconds_dec) if seconds_dec is not None else None
                notes_val = canonical.get("notes", "") or None
                rpe_raw = canonical.get("rpe", "").strip()
                rpe_val = _parse_decimal(rpe_raw)
                # distance is dropped — no target column in raw.sets (§7)

                set_rows.append(
                    {
                        "set_id": set_id,
                        "workout_session_id": workout_session_id,
                        "exercise_id": exercise_id,
                        "set_number": None if is_rest else int(set_order_raw),
                        "set_type": "rest" if is_rest else "working",
                        "weight_kg": weight_val,
                        "reps": reps_val,
                        "rpe": rpe_val,
                        "rest_seconds": rest_seconds_val,
                        "notes": notes_val,
                    }
                )

        # -------------------------------------------------------------------
        # Pass 2: write to the database
        # -------------------------------------------------------------------
        with conn.cursor() as cur:
            # --- raw.workout_sessions ---
            for session_key, sess in sessions.items():
                session_id = _make_uuid5(session_key)
                started_at = _parse_timestamp(sess["date_raw"])
                duration_seconds = _parse_duration_seconds(sess["duration_raw"])
                ended_at = started_at + timedelta(seconds=duration_seconds)

                cur.execute(
                    """
                    INSERT INTO raw.workout_sessions
                        (workout_session_id, started_at, ended_at,
                         duration_seconds, notes, workout_name)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workout_session_id) DO UPDATE
                        SET started_at       = EXCLUDED.started_at,
                            ended_at         = EXCLUDED.ended_at,
                            duration_seconds = EXCLUDED.duration_seconds,
                            notes            = EXCLUDED.notes,
                            workout_name     = EXCLUDED.workout_name
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (
                        str(session_id),
                        started_at,
                        ended_at,
                        duration_seconds,
                        sess["workout_notes"],
                        sess["workout_name"],
                    ),
                )
                row = cur.fetchone()
                if row and row[0]:
                    sessions_inserted += 1
                else:
                    sessions_updated += 1

            # --- raw.exercises ---
            for exercise_name_raw, src in exercises.items():
                exercise_id = _make_uuid5(f"{src}|{exercise_name_raw}")
                cur.execute(
                    """
                    INSERT INTO raw.exercises (exercise_id, exercise_name, source)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (exercise_name) DO NOTHING
                    """,
                    (str(exercise_id), exercise_name_raw, src),
                )
                if cur.rowcount == 1:
                    exercises_inserted += 1

            # --- raw.sets ---
            for set_row in set_rows:
                cur.execute(
                    """
                    INSERT INTO raw.sets
                        (set_id, workout_session_id, exercise_id,
                         set_number, set_type, weight_kg, reps,
                         rpe, rest_seconds, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (set_id) DO UPDATE
                        SET workout_session_id = EXCLUDED.workout_session_id,
                            exercise_id        = EXCLUDED.exercise_id,
                            set_number         = EXCLUDED.set_number,
                            set_type           = EXCLUDED.set_type,
                            weight_kg          = EXCLUDED.weight_kg,
                            reps               = EXCLUDED.reps,
                            rpe                = EXCLUDED.rpe,
                            rest_seconds       = EXCLUDED.rest_seconds,
                            notes              = EXCLUDED.notes
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (
                        str(set_row["set_id"]),
                        str(set_row["workout_session_id"]),
                        str(set_row["exercise_id"]),
                        set_row["set_number"],
                        set_row["set_type"],
                        set_row["weight_kg"],
                        set_row["reps"],
                        set_row["rpe"],
                        set_row["rest_seconds"],
                        set_row["notes"],
                    ),
                )
                row = cur.fetchone()
                if row and row[0]:
                    sets_inserted += 1
                else:
                    sets_updated += 1

        conn.commit()

        total_inserted = sessions_inserted + exercises_inserted + sets_inserted
        total_updated = sessions_updated + sets_updated
        total_skipped = 0  # exercises DO NOTHING skips not tracked separately

        finish_ingestion_log(
            conn,
            log_id=log_id,
            rows_read=rows_read,
            rows_inserted=total_inserted,
            rows_updated=total_updated,
            rows_skipped=total_skipped,
        )

        return {
            "rows_read": rows_read,
            "language": resolved_lang,
            "sessions": {"inserted": sessions_inserted, "updated": sessions_updated},
            "exercises": {"inserted": exercises_inserted},
            "sets": {"inserted": sets_inserted, "updated": sets_updated},
        }

    except Exception as exc:
        if log_id is not None:
            try:
                fail_ingestion_log(conn, log_id, str(exc))
            except Exception:
                pass  # best-effort: don't mask the original error
        conn.rollback()
        raise
    finally:
        conn.close()
