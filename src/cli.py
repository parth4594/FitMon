import sys
from pathlib import Path

import click
from rich.console import Console

from src.db.postgres import get_connection

console = Console()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SETUP_SCRIPTS = [
    _PROJECT_ROOT / "utils" / "create_raw_tables.sql",
    _PROJECT_ROOT / "utils" / "create_meta_tables.sql",
]


@click.group()
def cli():
    """FitMon — personal fitness analytics CLI."""


@cli.command("setup-db")
def setup_db():
    """Create all schemas and tables in Supabase."""
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


if __name__ == "__main__":
    cli()
