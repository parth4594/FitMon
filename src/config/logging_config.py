import logging
import logging.handlers
from datetime import date
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def configure_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"pipeline_{date.today()}.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything at root level

    # Console handler — INFO and above only
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))

    # File handler — DEBUG and above, with full traceback
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root.addHandler(console)
    root.addHandler(file_handler)


def set_console_level(level: int) -> None:
    """Adjust the console handler's level at runtime (§18.6 — --debug-mode / --info-mode)."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setLevel(level)
