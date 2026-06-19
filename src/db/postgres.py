import psycopg2
import psycopg2.extensions

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
