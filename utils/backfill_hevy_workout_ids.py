"""One-time backfill: populate hevy_workout_id on CSV-ingested
raw.workout_sessions rows by matching started_at against the Hevy API
(spec 05 §16).

Why: CSV ingestion left hevy_workout_id NULL on every row. Once a full API
sync runs, ON CONFLICT (hevy_workout_id) finds no match for those rows and
inserts duplicates for workouts already ingested via CSV. This script
resolves that ahead of time by matching on started_at and patching the
existing rows (and their child raw.sets rows) in place.

Not part of the regular sync pipeline — run it directly, once:

    uv run python -m utils.backfill_hevy_workout_ids

Order of operations (never skip step 1):
    1. Run this script once → confirm all CSV rows have hevy_workout_id populated
    2. Verify no NULL hevy_workout_id remains in raw.workout_sessions
    3. Only then run: uv run python -m src.cli sync-hevy --mode full
"""
import logging
from collections import Counter
from typing import Any

from src.config.logging_config import configure_logging
from src.db.postgres import get_connection
from src.ingestion.ingest_hevy_api import fetch_all_workouts, parse_session

configure_logging()
logger = logging.getLogger(__name__)


def build_api_started_at_index(workouts: list[dict]) -> dict[str, str]:
    """Map API workout started_at (ISO string) -> hevy_workout_id."""
    index: dict[str, str] = {}
    for workout in workouts:
        session = parse_session(workout)
        started_at = session["started_at"]
        if started_at is not None:
            index[started_at] = session["hevy_workout_id"]
    return index


def filter_unpopulated(csv_rows: list[dict]) -> list[dict]:
    """Rows already carrying a hevy_workout_id must never be overwritten."""
    return [r for r in csv_rows if not r.get("hevy_workout_id")]


def match_csv_rows_to_api(
    csv_rows: list[dict], api_index: dict[str, str]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Match CSV rows (already filtered to hevy_workout_id IS NULL) to API
    workouts by started_at.

    Returns (matched, unmatched, ambiguous):
      matched   — [{"workout_session_id", "hevy_workout_id"}, ...] ready to patch
      unmatched — CSV rows with no matching API workout (logged, skipped)
      ambiguous — CSV rows sharing a started_at with another CSV row,
                  skipped rather than guessed (spec §16 risk note)
    """
    started_at_counts = Counter(r["started_at"] for r in csv_rows)

    matched: list[dict] = []
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    for row in csv_rows:
        if started_at_counts[row["started_at"]] > 1:
            ambiguous.append(row)
            continue
        hevy_workout_id = api_index.get(row["started_at"])
        if hevy_workout_id is None:
            unmatched.append(row)
            continue
        matched.append(
            {
                "workout_session_id": row["workout_session_id"],
                "hevy_workout_id": hevy_workout_id,
            }
        )
    return matched, unmatched, ambiguous


def cascade_sets(cur, workout_session_id: str, hevy_workout_id: str) -> int:
    """After a session is patched, its child raw.sets rows receive the same
    hevy_workout_id. Returns the number of set rows updated.
    """
    cur.execute(
        """
        UPDATE raw.sets
           SET hevy_workout_id = %s
         WHERE workout_session_id = %s
        """,
        (hevy_workout_id, workout_session_id),
    )
    return cur.rowcount


def run_backfill() -> dict[str, Any]:
    conn = get_connection()
    matched: list[dict] = []
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    sets_cascaded = 0

    try:
        workouts = list(fetch_all_workouts())
        api_index = build_api_started_at_index(workouts)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT workout_session_id, started_at, hevy_workout_id
                  FROM raw.workout_sessions
                """
            )
            all_rows = [
                {
                    "workout_session_id": str(row[0]),
                    "started_at": row[1].isoformat(),
                    "hevy_workout_id": row[2],
                }
                for row in cur.fetchall()
            ]

            csv_rows = filter_unpopulated(all_rows)
            matched, unmatched, ambiguous = match_csv_rows_to_api(csv_rows, api_index)

            for row in unmatched:
                logger.warning(
                    "no API match for workout_session_id=%s started_at=%s — skipped",
                    row["workout_session_id"],
                    row["started_at"],
                )
            for row in ambiguous:
                logger.warning(
                    "timestamp collision at started_at=%s (workout_session_id=%s) — "
                    "multiple CSV rows share this start time, skipped rather than guessed",
                    row["started_at"],
                    row["workout_session_id"],
                )

            for row in matched:
                cur.execute(
                    """
                    UPDATE raw.workout_sessions
                       SET hevy_workout_id = %s
                     WHERE workout_session_id = %s
                       AND hevy_workout_id IS NULL
                    """,
                    (row["hevy_workout_id"], row["workout_session_id"]),
                )
                sets_cascaded += cascade_sets(
                    cur, row["workout_session_id"], row["hevy_workout_id"]
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        "backfill complete — matched=%d unmatched=%d ambiguous=%d sets_cascaded=%d",
        len(matched),
        len(unmatched),
        len(ambiguous),
        sets_cascaded,
    )

    return {
        "matched": len(matched),
        "unmatched": len(unmatched),
        "ambiguous": len(ambiguous),
        "sets_cascaded": sets_cascaded,
    }


if __name__ == "__main__":
    run_backfill()
