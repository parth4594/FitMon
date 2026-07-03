
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from src.config.logging_config import configure_logging, set_console_level
from src.db.postgres import get_connection
from src.ingestion.ingest_workout_csv import ingest_csv

configure_logging()
logger = logging.getLogger(__name__)

console = Console()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SETUP_SCRIPTS = [
    _PROJECT_ROOT / "utils" / "create_raw_tables.sql",
    _PROJECT_ROOT / "utils" / "create_meta_tables.sql",
]

_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(
    context_settings=_CONTEXT_SETTINGS,
    invoke_without_command=True,
    epilog=(
        "Examples:\n\n"
        "  uv run python -m src.cli setup-db\n\n"
        "  uv run python -m src.cli ingest-csv --file path/to/export.csv --source strong --lang de\n\n"
        "  uv run python -m src.cli --debug-mode ingest-csv --file path/to/export.csv --source strong\n\n"
        "Run `uv run python -m src.cli COMMAND --help` for a command's own options."
    ),
)
@click.option(
    "--debug-mode",
    is_flag=True,
    help="Run with verbose DEBUG-level console logging.",
)
@click.option(
    "--info-mode",
    is_flag=True,
    help="Run with INFO-level console logging (default).",
)
@click.pass_context
def cli(ctx: click.Context, debug_mode: bool, info_mode: bool):
    """FitMon — personal fitness analytics CLI.

    Ingests workout and biometric data into Supabase for the FitMon
    pipeline. Use one of the commands below; pass -h/--help after any
    command to see its specific flags.
    """
    set_console_level(logging.DEBUG if debug_mode else logging.INFO)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(
    "setup-db",
    epilog="Example:\n\n  uv run python -m src.cli setup-db",
)
def setup_db():
    """Create all schemas and tables (raw, meta) in Supabase."""
    conn = get_connection()
    current_script = None
    try:
        with conn.cursor() as cur:
            for script_path in _SETUP_SCRIPTS:
                current_script = script_path.name
                cur.execute(script_path.read_text())
                console.print(f"[green]✓[/] {current_script}")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        console.print(f"[red]✗[/] {current_script} failed: {exc}", err=True)
        sys.exit(1)
    finally:
        conn.close()


@cli.command(
    "ingest-csv",
    epilog=(
        "Examples:\n\n"
        "  uv run python -m src.cli ingest-csv --file path/to/export.csv --source strong --lang de\n\n"
        "  uv run python -m src.cli ingest-csv --file path/to/export.csv --source strong"
    ),
)
@click.option(
    "--file",
    "file_path",
    required=True,
    type=click.Path(exists=True, readable=True, dir_okay=False),
    help="Path to the workout CSV export file.",
)
@click.option(
    "--source",
    required=True,
    help="Source app name (e.g. 'strong'). Must match a column_maps/ subdirectory.",
)
@click.option(
    "--lang",
    default=None,
    help=(
        "Language code of the export (e.g. 'de', 'en'). "
        "If omitted, auto-detected from the CSV header row."
    ),
)
def ingest_csv_cmd(file_path: str, source: str, lang: str | None) -> None:
    """Ingest a workout CSV export into the raw Supabase tables."""
    try:
        summary = ingest_csv(file_path=file_path, source=source, lang=lang)
    except (ValueError, RuntimeError) as exc:
        logger.error("Ingestion failed: %s", exc, exc_info=True)
        console.print(f"[red]✗[/] Ingestion failed: {exc}")
        raise SystemExit(1)

    sess = summary["sessions"]
    exer = summary["exercises"]
    sets = summary["sets"]
    console.print(
        f"[green]✓[/] Ingested [bold]{summary['rows_read']}[/] rows "
        f"(lang=[bold]{summary['language']}[/])"
    )
    console.print(
        f"   sessions  inserted={sess['inserted']}  updated={sess['updated']}"
    )
    console.print(f"   exercises inserted={exer['inserted']}")
    console.print(
        f"   sets      inserted={sets['inserted']}  updated={sets['updated']}"
    )


if __name__ == "__main__":
    cli()
