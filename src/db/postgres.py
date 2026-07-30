import json
from uuid import UUID

import psycopg2
import psycopg2.extensions
import psycopg2.extras

from src.config.settings import settings


def get_connection() -> psycopg2.extensions.connection:
    """Open and return a psycopg2 connection to the Supabase Postgres database.

    Connection parameters are read from environment variables via
    src.config.settings. Raises pydantic.ValidationError with the missing
    field name(s) if any required variable is absent from the environment.

    No default schema is set — all callers must use fully-qualified table
    names (e.g. raw.sets).
    """
    return psycopg2.connect(
        host=settings.supabase_db_host,
        port=settings.supabase_db_port,
        dbname=settings.supabase_db_name,
        user=settings.supabase_db_user,
        password=settings.supabase_db_password,
    )


# ---------------------------------------------------------------------------
# Ingestion log helpers
# Reused by ingest_workout_csv.py, ingest_hevy_api.py, ingest_apple_health_data.py
# ---------------------------------------------------------------------------


def start_ingestion_log(
    conn: psycopg2.extensions.connection,
    source: str,
    language: str | None = None,
    details: dict | None = None,
    ingestion_type: str | None = None,
    trigger_source: str = "manual",
) -> UUID:
    """Insert a 'running' row into meta.ingestion_log and return its UUID.

    Call this before processing any rows so that even a crash mid-run is
    visible in the audit table. `ingestion_type` distinguishes 'csv' vs
    'api' sources sharing the same `source` value (spec 05 §4.E, §9).
    `trigger_source` distinguishes an operator-run sync ('manual') from
    the daily launchd-triggered run ('cron').
    """
    details_json = json.dumps(details) if details is not None else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO meta.ingestion_log
                (source, language, status, details, ingestion_type, trigger_source)
            VALUES (%s, %s, 'running', %s, %s, %s)
            RETURNING ingestion_log_id
            """,
            (source, language, details_json, ingestion_type, trigger_source),
        )
        row = cur.fetchone()
    conn.commit()
    return row[0]


def get_last_sync_timestamp(
    conn: psycopg2.extensions.connection, source: str
) -> str | None:
    """Return the ISO timestamp of the most recent successful run for `source`.

    Returns None if no prior successful run exists — the caller interprets
    this as "no prior log entry" and falls back to a full sync (spec 05 §8).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT finished_at FROM meta.ingestion_log
             WHERE source = %s AND status = 'success'
             ORDER BY finished_at DESC LIMIT 1
            """,
            (source,),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0].isoformat()


def get_last_attempt(
    conn: psycopg2.extensions.connection, source: str
) -> dict | None:
    """Return the most recent meta.ingestion_log row for `source`, regardless
    of status — used by the pipeline health check to answer "when did we
    last even try to run", as opposed to get_last_sync_timestamp's "when did
    we last succeed" (used for incremental-sync cursoring).

    Returns None if no row exists for this source yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT started_at, status
              FROM meta.ingestion_log
             WHERE source = %s
             ORDER BY started_at DESC LIMIT 1
            """,
            (source,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"started_at": row[0], "status": row[1]}


def finish_ingestion_log(
    conn: psycopg2.extensions.connection,
    log_id: UUID,
    rows_read: int,
    rows_inserted: int,
    rows_updated: int,
    rows_skipped: int,
) -> None:
    """Mark an ingestion run as 'success' with final row counts."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE meta.ingestion_log
               SET status        = 'success',
                   rows_read     = %s,
                   rows_inserted = %s,
                   rows_updated  = %s,
                   rows_skipped  = %s,
                   finished_at   = now()
             WHERE ingestion_log_id = %s
            """,
            (rows_read, rows_inserted, rows_updated, rows_skipped, str(log_id)),
        )
    conn.commit()


def fail_ingestion_log(
    conn: psycopg2.extensions.connection,
    log_id: UUID,
    error_message: str,
    failure_code: str | None = None,
) -> None:
    """Mark an ingestion run as 'failed' with the error message.

    `failure_code` records the HTTP status code (e.g. '429', '500') for
    API-sourced failures (spec 05 §4.E, §9); CSV ingestion leaves it NULL.
    The caller is responsible for committing so this record survives even
    if the surrounding transaction is rolled back.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE meta.ingestion_log
               SET status        = 'failed',
                   error_message = %s,
                   failure_code  = %s,
                   finished_at   = now()
             WHERE ingestion_log_id = %s
            """,
            (error_message, failure_code, str(log_id)),
        )
    conn.commit()
