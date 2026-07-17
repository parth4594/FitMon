"""Hevy CSV ingestion (spec 04).

Writes raw.workout_sessions, raw.exercises, raw.sets, and raw.cardio_sets.
Column-map driven — see src/ingestion/column_maps/hevy/<lang>.yaml.

Hevy's CSV shape (separate start/end timestamps, zero-based set index,
cardio columns, per-set notes) has no equivalent in Strong's canonical key
set, so this source has its own canonical keys and its own write path.
"""
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.sources.base import (
    COLUMN_MAPS_DIR,
    finish_ingestion_log,
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

SOURCE = "hevy"

# 13 canonical column names the Hevy column map must declare under `headers:` (spec 04 §6)
CANONICAL_KEYS = frozenset(
    {
        "workout_name",
        "started_at",
        "ended_at",
        "notes",
        "exercise_name",
        "set_number",
        "set_type",
        "weight_kg",
        "reps",
        "rpe",
        "exercise_notes",
        "distance_km",
        "duration_seconds",
    }
)


# ---------------------------------------------------------------------------
# Hevy-specific transform helpers (spec 04 §7) — pure functions, no I/O
# ---------------------------------------------------------------------------


def parse_hevy_timestamp(raw: str) -> datetime:
    """Parse a Hevy CSV timestamp ("DD.MM.YYYY, HH:MM") as UTC (spec 04 §7.1).

    Hevy exports have no timezone info; assume UTC until the user adds
    timezone handling in the transformation layer.
    """
    dt = datetime.strptime(raw.strip(), "%d.%m.%Y, %H:%M")
    return dt.replace(tzinfo=timezone.utc)


def is_cardio_row(distance_km: str | None, duration_seconds: str | None) -> bool:
    """True if either cardio field is non-null and non-empty (spec 04 §7.5)."""
    return bool((distance_km or "").strip()) or bool((duration_seconds or "").strip())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def ingest(csv_path: Path, lang: str | None) -> dict[str, Any]:
    """Ingest a Hevy CSV export into raw.workout_sessions, raw.exercises,
    raw.sets, and raw.cardio_sets.

    Returns a summary dict with row counts, or raises on failure after
    writing a 'failed' row to meta.ingestion_log.
    """
    source_dir = COLUMN_MAPS_DIR / SOURCE
    if not source_dir.is_dir():
        raise RuntimeError(f"[source] Unknown source '{SOURCE}'. Expected {source_dir} to exist.")

    # Phase 1: validate all YAML maps before touching any data rows (§6.6)
    all_maps = load_and_validate_all_maps(source_dir, CANONICAL_KEYS)

    # Phase 2: resolve language (§6.5)
    header_row = read_header_row(csv_path)
    col_map = resolve_language(source_dir, all_maps, lang, header_row)
    resolved_lang: str = col_map["language"]
    ignored_columns: set[str] = set(col_map.get("ignored_columns", []))

    if lang is None:
        logger.info("language auto-detected as '%s'", resolved_lang)

    def _work(conn, log_id) -> dict[str, Any]:
        rows_read = 0
        sessions_inserted = 0
        sessions_updated = 0
        sets_inserted = 0
        sets_updated = 0
        cardio_inserted = 0
        cardio_updated = 0

        # ---------------------------------------------------------------
        # Pass 1: parse all CSV rows into in-memory structures.
        # ---------------------------------------------------------------

        # session_key → session metadata dict
        sessions: dict[str, dict[str, Any]] = {}
        # exercise_name_raw → source string
        exercises: dict[str, str] = {}
        # ordered list of set row dicts ready for DB insertion
        set_rows: list[dict[str, Any]] = []
        # ordered list of cardio row dicts ready for DB insertion
        cardio_rows: list[dict[str, Any]] = []

        # row counter per (started_at_raw, exercise_name_raw) pair (§8)
        row_counters: dict[tuple[str, str], int] = {}

        with csv_path.open(encoding="utf-8", newline="") as fh:
            dict_reader = csv.DictReader(fh)
            for raw_row in dict_reader:
                rows_read += 1

                # Silently drop ignored source columns before mapping (§7.6)
                filtered_row = {
                    k: v for k, v in raw_row.items() if k not in ignored_columns
                }
                canonical = map_row_to_canonical(filtered_row, col_map)

                # --- Session ---
                started_at_raw = canonical.get("started_at", "").strip()
                if not started_at_raw:
                    continue  # skip rows with no start time

                workout_name_raw = canonical.get("workout_name", "")
                ended_at_raw = canonical.get("ended_at", "").strip()
                notes_raw = canonical.get("notes", "") or None

                # Session identity key: (title, started_at) — same as Strong (§7.2)
                session_key = f"{workout_name_raw}|{started_at_raw}"
                if session_key not in sessions:
                    sessions[session_key] = {
                        "started_at_raw": started_at_raw,
                        "ended_at_raw": ended_at_raw,
                        "workout_name": workout_name_raw,
                        "notes": notes_raw,
                    }

                # --- Exercise ---
                exercise_name_raw = canonical.get("exercise_name", "")
                exercises[exercise_name_raw] = SOURCE

                # --- Row counter in original file order (§8) ---
                counter_key = (started_at_raw, exercise_name_raw)
                row_counters[counter_key] = row_counters.get(counter_key, 0) + 1
                row_counter = row_counters[counter_key]

                # Deterministic UUIDv5 PKs, reusing make_uuid5 (§9 — see plan
                # note: Strong-style source-prefixed keys, not the spec's
                # literal NAMESPACE_DNS strings; still deterministic/idempotent)
                workout_session_id = make_uuid5(f"{SOURCE}|{session_key}")
                exercise_id = make_uuid5(f"{SOURCE}|{exercise_name_raw}")
                set_id = make_uuid5(
                    f"{SOURCE}|{session_key}|{exercise_name_raw}|{row_counter}"
                )

                # --- set_number: zero-based, written as-is (§7.3) ---
                set_index_raw = canonical.get("set_number", "").strip()
                set_number_val = int(set_index_raw) if set_index_raw else None
                set_type_val = canonical.get("set_type", "") or None
                weight_val = parse_decimal(canonical.get("weight_kg", ""))
                reps_raw = canonical.get("reps", "").strip()
                reps_val = int(float(reps_raw)) if reps_raw else None
                rpe_val = parse_decimal(canonical.get("rpe", ""))
                exercise_notes_val = canonical.get("exercise_notes", "") or None

                set_rows.append(
                    {
                        "set_id": set_id,
                        "workout_session_id": workout_session_id,
                        "exercise_id": exercise_id,
                        "set_number": set_number_val,
                        "set_type": set_type_val,
                        "weight_kg": weight_val,
                        "reps": reps_val,
                        "rpe": rpe_val,
                        "exercise_notes": exercise_notes_val,
                    }
                )

                # --- Cardio row detection and routing (§7.5) ---
                distance_km_raw = canonical.get("distance_km", "")
                duration_seconds_raw = canonical.get("duration_seconds", "")
                if is_cardio_row(distance_km_raw, duration_seconds_raw):
                    distance_val = parse_decimal(distance_km_raw)
                    duration_dec = parse_decimal(duration_seconds_raw)
                    duration_val = int(duration_dec) if duration_dec is not None else None
                    cardio_set_id = make_uuid5(
                        f"{SOURCE}|cardio|{session_key}|{exercise_name_raw}|{set_index_raw}"
                    )
                    cardio_rows.append(
                        {
                            "cardio_set_id": cardio_set_id,
                            "workout_session_id": workout_session_id,
                            "exercise_id": exercise_id,
                            "set_index": set_number_val,
                            "exercise_title": exercise_name_raw,
                            "distance_km": distance_val,
                            "duration_seconds": duration_val,
                        }
                    )

        # ---------------------------------------------------------------
        # Pass 2: write to the database
        # ---------------------------------------------------------------
        with conn.cursor() as cur:
            # --- raw.workout_sessions ---
            for session_key, sess in sessions.items():
                session_id = make_uuid5(f"{SOURCE}|{session_key}")
                started_at = parse_hevy_timestamp(sess["started_at_raw"])
                ended_at = (
                    parse_hevy_timestamp(sess["ended_at_raw"])
                    if sess["ended_at_raw"]
                    else None
                )

                cur.execute(
                    """
                    INSERT INTO raw.workout_sessions
                        (workout_session_id, started_at, ended_at, notes, workout_name)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (workout_session_id) DO UPDATE
                        SET started_at   = EXCLUDED.started_at,
                            ended_at     = EXCLUDED.ended_at,
                            notes        = EXCLUDED.notes,
                            workout_name = EXCLUDED.workout_name
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (
                        str(session_id),
                        started_at,
                        ended_at,
                        sess["notes"],
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
                         rpe, exercise_notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (set_id) DO UPDATE
                        SET workout_session_id = EXCLUDED.workout_session_id,
                            exercise_id        = EXCLUDED.exercise_id,
                            set_number         = EXCLUDED.set_number,
                            set_type           = EXCLUDED.set_type,
                            weight_kg          = EXCLUDED.weight_kg,
                            reps               = EXCLUDED.reps,
                            rpe                = EXCLUDED.rpe,
                            exercise_notes     = EXCLUDED.exercise_notes
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
                        set_row["exercise_notes"],
                    ),
                )
                row = cur.fetchone()
                if row and row[0]:
                    sets_inserted += 1
                else:
                    sets_updated += 1

            # --- raw.cardio_sets ---
            for cardio_row in cardio_rows:
                cur.execute(
                    """
                    INSERT INTO raw.cardio_sets
                        (cardio_set_id, workout_session_id, exercise_id,
                         set_index, exercise_title, distance_km, duration_seconds)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (cardio_set_id) DO UPDATE
                        SET workout_session_id = EXCLUDED.workout_session_id,
                            exercise_id        = EXCLUDED.exercise_id,
                            set_index          = EXCLUDED.set_index,
                            exercise_title     = EXCLUDED.exercise_title,
                            distance_km        = EXCLUDED.distance_km,
                            duration_seconds   = EXCLUDED.duration_seconds
                    RETURNING (xmax = 0) AS inserted
                    """,
                    (
                        str(cardio_row["cardio_set_id"]),
                        str(cardio_row["workout_session_id"]),
                        str(cardio_row["exercise_id"]),
                        cardio_row["set_index"],
                        cardio_row["exercise_title"],
                        cardio_row["distance_km"],
                        cardio_row["duration_seconds"],
                    ),
                )
                row = cur.fetchone()
                if row and row[0]:
                    cardio_inserted += 1
                else:
                    cardio_updated += 1

        conn.commit()

        finish_ingestion_log(
            conn,
            log_id=log_id,
            rows_read=rows_read,
            rows_inserted=sets_inserted,
            rows_updated=sets_updated,
            rows_skipped=0,
        )

        logger.info(
            "complete — read=%d sets_inserted=%d sets_updated=%d cardio_inserted=%d cardio_updated=%d",
            rows_read,
            sets_inserted,
            sets_updated,
            cardio_inserted,
            cardio_updated,
        )

        return {
            "rows_read": rows_read,
            "language": resolved_lang,
            "sessions": {"inserted": sessions_inserted, "updated": sessions_updated},
            "exercises": {"inserted": exercises_inserted},
            "sets": {"inserted": sets_inserted, "updated": sets_updated},
            "cardio_sets": {"inserted": cardio_inserted, "updated": cardio_updated},
        }

    return run_ingestion(SOURCE, resolved_lang, csv_path, _work)
