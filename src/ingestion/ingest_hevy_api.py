"""Hevy API ingestion (spec 05).

Fetches workouts from the Hevy REST API — a full historical sync via
GET /v1/workouts, or an incremental sync via GET /v1/workouts/events — and
upserts them into raw.workout_sessions, raw.exercises, and raw.sets. This
module never imports `requests` directly; all HTTP concerns live in
src/ingestion/hevy_api_client.py.

Conflict keys (distinct from the CSV path's DB-generated UUIDs):
  raw.workout_sessions  ON CONFLICT (hevy_workout_id)
  raw.exercises         ON CONFLICT (exercise_template_id)
  raw.sets              ON CONFLICT (hevy_workout_id, exercise_template_id, set_index)

Run utils/backfill_hevy_workout_ids.py once, before the first `--mode full`
run, so existing CSV-ingested rows don't get duplicated (spec 05 §16).
"""
import logging
import time
from typing import Any, Callable, Iterator

from src.db.postgres import (
    fail_ingestion_log,
    finish_ingestion_log,
    get_connection,
    get_last_sync_timestamp,
    start_ingestion_log,
)
from src.ingestion.hevy_api_client import (
    HevyAPIError,
    REQUEST_DELAY,
    fetch_workout_events_page,
    fetch_workouts_page,
)

logger = logging.getLogger(__name__)

SOURCE = "hevy_api"


# ---------------------------------------------------------------------------
# Pure unpacking functions (spec §8) — no I/O, no DB imports
# ---------------------------------------------------------------------------


def parse_session(workout: dict) -> dict:
    """Map workout-level API fields to raw.workout_sessions column names."""
    return {
        "hevy_workout_id": workout.get("id"),
        "title": workout.get("title"),
        "description": workout.get("description"),
        "routine_id": workout.get("routine_id"),
        "started_at": workout.get("start_time"),
        "ended_at": workout.get("end_time"),
        "hevy_updated_at": workout.get("updated_at"),
        "hevy_created_at": workout.get("created_at"),
    }


def parse_exercise(exercise: dict, hevy_workout_id: str) -> dict:
    """Map exercise-level API fields to raw.exercises column names.

    Note: the live API returns the field as `superset_id` (singular) despite
    spec 05 §8 documenting it as `supersets_id` (plural) — confirmed against
    a real GET /v1/workouts response. The DB column keeps the spec's name;
    only the source dict key read here follows the real API.
    """
    return {
        "hevy_workout_id": hevy_workout_id,
        "exercise_template_id": exercise.get("exercise_template_id"),
        "exercise_name": exercise.get("title"),
        "exercise_index": exercise.get("index"),
        "supersets_id": exercise.get("superset_id"),
        "notes": exercise.get("notes"),
        "source": "hevy",
    }


def parse_set(set_: dict, exercise_template_id: str, hevy_workout_id: str) -> dict:
    """Map set-level API fields to raw.sets column names."""
    return {
        "hevy_workout_id": hevy_workout_id,
        "exercise_template_id": exercise_template_id,
        "set_index": set_.get("index"),
        "set_type": set_.get("type"),
        "weight_kg": set_.get("weight_kg"),
        "reps": set_.get("reps"),
        "rpe": set_.get("rpe"),
        "distance_meters": set_.get("distance_meters"),
        "duration_seconds": set_.get("duration_seconds"),
        "custom_metric": set_.get("custom_metric"),
    }


def unpack_event(event: dict) -> dict | None:
    """Branch on event['type'] before unpacking (spec §8) — never assume all
    events contain a 'workout' key.

    Returns the nested workout dict for 'updated' events, or None for
    'deleted' events (logged by the caller and skipped — raw tables are
    never deleted from, per spec §11/§15).
    """
    if event.get("type") == "deleted":
        logger.info(
            "deleted event for hevy_workout_id=%s deleted_at=%s — logged, skipped",
            event.get("id"),
            event.get("deleted_at"),
        )
        return None
    return event.get("workout")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def paginate(fetch_page_fn: Callable[[int], dict], items_key: str) -> Iterator[dict]:
    """Page through a Hevy list endpoint, yielding each item under `items_key`.

    Sleeps REQUEST_DELAY after every page (spec §15 — "no exceptions"), and
    stops once the current page reaches `page_count`.
    """
    page = 1
    while True:
        response = fetch_page_fn(page)
        yield from response.get(items_key, [])
        time.sleep(REQUEST_DELAY)
        page_count = response.get("page_count", page)
        if response.get("page", page) >= page_count:
            break
        page += 1


def fetch_all_workouts() -> Iterator[dict]:
    """Full paginated fetch of GET /v1/workouts."""
    yield from paginate(fetch_workouts_page, "workouts")


def fetch_all_events(since: str) -> Iterator[dict]:
    """Full paginated fetch of GET /v1/workouts/events?since=<since>."""
    yield from paginate(lambda page: fetch_workout_events_page(since, page), "events")


# ---------------------------------------------------------------------------
# Upserts — SQL patterns from spec §8
# ---------------------------------------------------------------------------


def upsert_session(cur, row: dict) -> tuple[str, bool]:
    """Upsert one row into raw.workout_sessions on hevy_workout_id.

    Returns (workout_session_id, inserted) so callers can populate
    raw.sets.workout_session_id, which stays NOT NULL for every source.
    """
    cur.execute(
        """
        INSERT INTO raw.workout_sessions
            (hevy_workout_id, title, description, routine_id,
             started_at, ended_at, hevy_updated_at, hevy_created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hevy_workout_id) DO UPDATE SET
            title           = EXCLUDED.title,
            description     = EXCLUDED.description,
            routine_id      = EXCLUDED.routine_id,
            started_at      = EXCLUDED.started_at,
            ended_at        = EXCLUDED.ended_at,
            hevy_updated_at = EXCLUDED.hevy_updated_at,
            hevy_created_at = EXCLUDED.hevy_created_at
        RETURNING workout_session_id, (xmax = 0) AS inserted
        """,
        (
            row["hevy_workout_id"],
            row["title"],
            row["description"],
            row["routine_id"],
            row["started_at"],
            row["ended_at"],
            row["hevy_updated_at"],
            row["hevy_created_at"],
        ),
    )
    workout_session_id, inserted = cur.fetchone()
    return workout_session_id, bool(inserted)


def upsert_exercise(cur, row: dict) -> tuple[str, bool]:
    """Upsert one row into raw.exercises, keyed on exercise_template_id.

    Confirmed against real data: a plain `ON CONFLICT (exercise_template_id)`
    insert fails on the first-ever API sync, because raw.exercises already
    has a UNIQUE constraint on exercise_name from CSV ingestion (spec 04),
    and CSV rows have exercise_template_id = NULL — so the API's INSERT for
    an exercise name that already exists hits "exercises_exercise_name_key"
    instead of the intended conflict target. This does an explicit
    find-by-template-id-then-by-name lookup so the API sync "claims" the
    existing CSV row (attaching its exercise_template_id) instead of trying
    to insert a duplicate name.

    Returns (exercise_id, inserted) so callers can populate
    raw.sets.exercise_id, which stays NOT NULL for every source.
    """
    existing = None
    if row["exercise_template_id"] is not None:
        cur.execute(
            "SELECT exercise_id FROM raw.exercises WHERE exercise_template_id = %s",
            (row["exercise_template_id"],),
        )
        existing = cur.fetchone()

    if existing is None:
        cur.execute(
            "SELECT exercise_id FROM raw.exercises WHERE exercise_name = %s",
            (row["exercise_name"],),
        )
        existing = cur.fetchone()

    if existing is not None:
        exercise_id = existing[0]
        cur.execute(
            """
            UPDATE raw.exercises
               SET exercise_template_id = %s,
                   exercise_name        = %s,
                   exercise_index       = %s,
                   supersets_id         = %s,
                   notes                = %s
             WHERE exercise_id = %s
            """,
            (
                row["exercise_template_id"],
                row["exercise_name"],
                row["exercise_index"],
                row["supersets_id"],
                row["notes"],
                exercise_id,
            ),
        )
        return exercise_id, False

    cur.execute(
        """
        INSERT INTO raw.exercises
            (exercise_template_id, exercise_name, exercise_index,
             supersets_id, notes, source)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING exercise_id
        """,
        (
            row["exercise_template_id"],
            row["exercise_name"],
            row["exercise_index"],
            row["supersets_id"],
            row["notes"],
            row["source"],
        ),
    )
    exercise_id = cur.fetchone()[0]
    return exercise_id, True


def upsert_set(cur, row: dict, workout_session_id: str, exercise_id: str) -> bool:
    """Upsert one row into raw.sets on the composite
    (hevy_workout_id, exercise_template_id, set_index) key. Returns True if inserted.
    """
    cur.execute(
        """
        INSERT INTO raw.sets
            (workout_session_id, exercise_id, hevy_workout_id, exercise_template_id,
             set_index, set_type, weight_kg, reps, rpe,
             distance_meters, duration_seconds, custom_metric)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hevy_workout_id, exercise_template_id, set_index) DO UPDATE SET
            workout_session_id = EXCLUDED.workout_session_id,
            exercise_id        = EXCLUDED.exercise_id,
            set_type           = EXCLUDED.set_type,
            weight_kg          = EXCLUDED.weight_kg,
            reps               = EXCLUDED.reps,
            rpe                = EXCLUDED.rpe,
            distance_meters    = EXCLUDED.distance_meters,
            duration_seconds   = EXCLUDED.duration_seconds,
            custom_metric      = EXCLUDED.custom_metric
        RETURNING (xmax = 0) AS inserted
        """,
        (
            workout_session_id,
            exercise_id,
            row["hevy_workout_id"],
            row["exercise_template_id"],
            row["set_index"],
            row["set_type"],
            row["weight_kg"],
            row["reps"],
            row["rpe"],
            row["distance_meters"],
            row["duration_seconds"],
            row["custom_metric"],
        ),
    )
    (inserted,) = cur.fetchone()
    return bool(inserted)


# ---------------------------------------------------------------------------
# Main ingestion loop (spec §8)
# ---------------------------------------------------------------------------


def _iter_incremental_workouts(since: str, counts: dict) -> Iterator[dict]:
    for event in fetch_all_events(since):
        workout = unpack_event(event)
        if workout is None:
            counts["skipped"] += 1
            continue
        yield workout


def _write_workout(cur, workout: dict, counts: dict) -> None:
    session_row = parse_session(workout)
    workout_session_id, inserted = upsert_session(cur, session_row)
    counts["inserted" if inserted else "updated"] += 1

    hevy_workout_id = session_row["hevy_workout_id"]

    for exercise in workout.get("exercises", []):
        exercise_row = parse_exercise(exercise, hevy_workout_id)
        exercise_id, _ = upsert_exercise(cur, exercise_row)

        for set_ in exercise.get("sets", []):
            set_row = parse_set(
                set_, exercise_row["exercise_template_id"], hevy_workout_id
            )
            upsert_set(cur, set_row, workout_session_id, exercise_id)


def run_sync(mode: str = "auto") -> dict[str, Any]:
    """Sync workouts from the Hevy API into raw.workout_sessions,
    raw.exercises, and raw.sets.

    mode='full' always does a full historical sync via GET /v1/workouts.
    mode='incremental' always does an event-diff sync via
    GET /v1/workouts/events?since=<last successful run>.
    mode='auto' (default) picks 'full' if no prior successful run exists,
    else 'incremental'.

    Writes a 'running' row to meta.ingestion_log before fetching anything,
    then 'success' or 'failed' (with failure_code for HTTP errors) — never
    silently swallows an exception.
    """
    conn = get_connection()
    log_id = None
    counts = {"inserted": 0, "updated": 0, "skipped": 0}

    try:
        last_sync = get_last_sync_timestamp(conn, source=SOURCE)

        effective_mode = mode
        if mode == "auto":
            effective_mode = "incremental" if last_sync is not None else "full"
        elif mode == "incremental" and last_sync is None:
            effective_mode = "full"

        log_id = start_ingestion_log(
            conn, source=SOURCE, ingestion_type="api", details={"mode": effective_mode}
        )

        if effective_mode == "full":
            workouts = fetch_all_workouts()
        else:
            workouts = _iter_incremental_workouts(last_sync, counts)

        with conn.cursor() as cur:
            for workout in workouts:
                _write_workout(cur, workout, counts)

        conn.commit()

        rows_total = counts["inserted"] + counts["updated"] + counts["skipped"]
        finish_ingestion_log(
            conn,
            log_id=log_id,
            rows_read=rows_total,
            rows_inserted=counts["inserted"],
            rows_updated=counts["updated"],
            rows_skipped=counts["skipped"],
        )

        logger.info(
            "hevy_api sync complete — mode=%s inserted=%d updated=%d skipped=%d",
            effective_mode,
            counts["inserted"],
            counts["updated"],
            counts["skipped"],
        )

        return {"mode": effective_mode, **counts}

    except HevyAPIError as exc:
        # Roll back first: a DB error earlier in the loop leaves the
        # transaction aborted, and any query on it (including this log
        # write) would raise InFailedSqlTransaction and mask the real cause.
        conn.rollback()
        if log_id is not None:
            failure_code = str(exc.status_code) if exc.status_code else None
            fail_ingestion_log(conn, log_id, str(exc), failure_code=failure_code)
        raise
    except Exception as exc:
        conn.rollback()
        if log_id is not None:
            fail_ingestion_log(conn, log_id, str(exc))
        raise
    finally:
        conn.close()
