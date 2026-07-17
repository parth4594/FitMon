"""Strong CSV ingestion (spec 03).

Writes raw.workout_sessions, raw.exercises, raw.sets. Column-map driven —
see src/ingestion/column_maps/strong/<lang>.yaml.
"""
import csv
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.db.postgres import finish_ingestion_log
from src.ingestion.sources.base import (
    COLUMN_MAPS_DIR,
    load_and_validate_all_maps,
    make_uuid5,
    map_row_to_canonical,
    parse_decimal,
    read_header_row,
    resolve_language,
    run_ingestion,
    upsert_exercises,
)

logger = logging.getLogger(__name__)

SOURCE = "strong"

# 12 canonical column names every Strong column map must declare under `headers:`
CANONICAL_KEYS = frozenset(
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

# Timezone for Strong app naive timestamps (Germany)
_TZ_LOCAL = ZoneInfo("Europe/Berlin")


# ---------------------------------------------------------------------------
# Strong-specific transform helpers (§8) — pure functions, no I/O
# ---------------------------------------------------------------------------


def parse_duration_seconds(raw: str) -> int:
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


def parse_timestamp(raw: str) -> datetime:
    """Parse naive datetime string, localize to Europe/Berlin, convert to UTC.

       Localizing to Europe/Berlin is necessary before UTC conversion."""
    naive = datetime.fromisoformat(raw.strip())
    local_dt = naive.replace(tzinfo=_TZ_LOCAL)
    return local_dt.astimezone(timezone.utc)


def classify_set_row(set_order_raw: str, rest_marker: str | None) -> dict[str, Any]:
    """Classify a set-order cell as a rest-timer row or a working set (§8.3).

    Compares against the active language map's rest_marker — never a
    hardcoded string. Sources with no rest-timer row concept omit
    rest_marker entirely (rest_marker=None), so every row is a working set.
    """
    if rest_marker is not None and set_order_raw == rest_marker:
        return {"set_type": "rest", "set_number": None}
    return {"set_type": "working", "set_number": int(set_order_raw)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def ingest(csv_path: Path, lang: str | None) -> dict[str, Any]:
    """Ingest a Strong CSV export into raw.workout_sessions, raw.exercises, raw.sets.

    Returns a summary dict with row counts, or raises on failure after
    writing a 'failed' row to meta.ingestion_log.
    """
    source_dir = COLUMN_MAPS_DIR / SOURCE
    if not source_dir.is_dir():
        available = [d.name for d in COLUMN_MAPS_DIR.iterdir() if d.is_dir()]
        raise RuntimeError(
            f"[source] Unknown source '{SOURCE}'. "
            f"Available: {', '.join(sorted(available)) or '(none)'}"
        )

    # Phase 1: validate all YAML maps before touching any data rows (§6.6)
    all_maps = load_and_validate_all_maps(source_dir, CANONICAL_KEYS)

    # Phase 2: resolve language (§6.5)
    header_row = read_header_row(csv_path)
    col_map = resolve_language(source_dir, all_maps, lang, header_row)
    resolved_lang: str = col_map["language"]
    rest_marker: str | None = col_map.get("rest_marker")

    if lang is None:
        logger.info("language auto-detected as '%s'", resolved_lang)

    def _work(conn, log_id) -> dict[str, Any]:
        rows_read = 0
        sessions_inserted = 0
        sessions_updated = 0
        sets_inserted = 0
        sets_updated = 0

        # ---------------------------------------------------------------
        # Pass 1: parse all CSV rows into in-memory structures.
        # Row counters per (date, exercise_name) pair must be assigned in
        # original file order (including rest rows), so we collect before writing.
        # ---------------------------------------------------------------

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

                # Map CSV column headers → canonical names (rename only, §17.2)
                canonical = map_row_to_canonical(raw_row, col_map)

                # --- Session ---
                date_raw = canonical.get("date", "").strip()
                if not date_raw:
                    continue  # skip rows with no date

                workout_name_raw = canonical.get("workout_name", "")
                duration_raw = canonical.get("duration", "")
                workout_notes_raw = canonical.get("workout_notes", "") or None

                session_key = f"{SOURCE}|{date_raw}"
                if session_key not in sessions:
                    sessions[session_key] = {
                        "date_raw": date_raw,
                        "workout_name": workout_name_raw,
                        "duration_raw": duration_raw,
                        "workout_notes": workout_notes_raw,
                    }
                    logger.debug(
                        "session uuid=%s date=%s workout=%s",
                        make_uuid5(session_key),
                        date_raw,
                        workout_name_raw,
                    )

                # --- Exercise (no trimming — §8.4) ---
                exercise_name_raw = canonical.get("exercise_name", "")
                exercises[exercise_name_raw] = SOURCE

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
                classification = classify_set_row(set_order_raw, rest_marker)

                # Deterministic UUIDv5 PKs (§9)
                set_id = make_uuid5(
                    f"{SOURCE}|{date_raw}|{exercise_name_raw}|{row_counter}"
                )
                workout_session_id = make_uuid5(session_key)
                exercise_id = make_uuid5(f"{SOURCE}|{exercise_name_raw}")

                logger.debug(
                    "set uuid=%s exercise=%s row_counter=%s set_type=%s",
                    set_id,
                    exercise_name_raw,
                    row_counter,
                    classification["set_type"],
                )

                # Type casts (§8.5, §8.8)
                weight_val = parse_decimal(canonical.get("weight", ""))
                reps_raw = canonical.get("reps", "").strip()
                reps_val = int(float(reps_raw)) if reps_raw else None
                seconds_raw = canonical.get("seconds", "").strip()
                seconds_dec = parse_decimal(seconds_raw)
                # rest_seconds is INTEGER in schema
                rest_seconds_val = int(seconds_dec) if seconds_dec is not None else None
                notes_val = canonical.get("notes", "") or None
                rpe_raw = canonical.get("rpe", "").strip()
                rpe_val = parse_decimal(rpe_raw)
                # distance is dropped — no target column in raw.sets (§7)

                set_rows.append(
                    {
                        "set_id": set_id,
                        "workout_session_id": workout_session_id,
                        "exercise_id": exercise_id,
                        "set_number": classification["set_number"],
                        "set_type": classification["set_type"],
                        "weight_kg": weight_val,
                        "reps": reps_val,
                        "rpe": rpe_val,
                        "rest_seconds": rest_seconds_val,
                        "notes": notes_val,
                    }
                )

        # ---------------------------------------------------------------
        # Pass 2: write to the database
        # ---------------------------------------------------------------
        with conn.cursor() as cur:
            # --- raw.workout_sessions ---
            for session_key, sess in sessions.items():
                session_id = make_uuid5(session_key)
                started_at = parse_timestamp(sess["date_raw"])
                duration_seconds = parse_duration_seconds(sess["duration_raw"])
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
                    logger.debug("session uuid=%s conflict_path=insert", session_id)
                else:
                    sessions_updated += 1
                    logger.debug("session uuid=%s conflict_path=update", session_id)

            # --- raw.exercises ---
            exercises_inserted = upsert_exercises(cur, exercises)

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

        logger.info(
            "complete — read=%d inserted=%d updated=%d skipped=%d",
            rows_read,
            total_inserted,
            total_updated,
            total_skipped,
        )

        return {
            "rows_read": rows_read,
            "language": resolved_lang,
            "sessions": {"inserted": sessions_inserted, "updated": sessions_updated},
            "exercises": {"inserted": exercises_inserted},
            "sets": {"inserted": sets_inserted, "updated": sets_updated},
        }

    return run_ingestion(SOURCE, resolved_lang, csv_path, _work)
