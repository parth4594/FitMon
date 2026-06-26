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
) -> UUID:
    """Insert a 'running' row into meta.ingestion_log and return its UUID.

    Call this before processing any rows so that even a crash mid-run is
    visible in the audit table.
    """
    details_json = json.dumps(details) if details is not None else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO meta.ingestion_log (source, language, status, details)
            VALUES (%s, %s, 'running', %s)
            RETURNING ingestion_log_id
            """,
            (source, language, details_json),
        )
        row = cur.fetchone()
    conn.commit()
    return row[0]


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
) -> None:
    """Mark an ingestion run as 'failed' with the error message.

    The caller is responsible for committing so this record survives even
    if the surrounding transaction is rolled back.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE meta.ingestion_log
               SET status        = 'failed',
                   error_message = %s,
                   finished_at   = now()
             WHERE ingestion_log_id = %s
            """,
            (error_message, str(log_id)),
        )
    conn.commit()
