"""Workout CSV ingestion — source-agnostic dispatcher.

Entry point: ingest_csv() called from src.cli. All source-specific parsing
and write logic lives in src/ingestion/sources/<source>.py; shared plumbing
(column-map loading, language resolution, UUIDv5 keys, the DB lifecycle)
lives in src/ingestion/sources/base.py.

To add a new source: create src/ingestion/sources/<source>.py with its own
CANONICAL_KEYS and an `ingest(csv_path, lang) -> dict` function (see
sources/strong.py or sources/hevy.py as a template), then register it in
_SOURCE_INGESTORS below. Nothing else in this file changes.
"""
import logging
from pathlib import Path
from typing import Any

from src.ingestion.sources import hevy, strong

logger = logging.getLogger(__name__)

_SOURCE_INGESTORS = {
    "strong": strong.ingest,
    "hevy": hevy.ingest,
}


def ingest_csv(file_path: str, source: str, lang: str | None) -> dict[str, Any]:
    """Ingest a workout CSV file, dispatching to the source-specific ingestor.

    Returns a summary dict with row counts, or raises on failure after
    writing a 'failed' row to meta.ingestion_log.
    """
    logger.info("source=%s lang=%s file=%s", source, lang, file_path)

    csv_path = Path(file_path)
    if not csv_path.exists():
        raise RuntimeError(f"[file] CSV file not found: {csv_path}")

    ingestor = _SOURCE_INGESTORS.get(source)
    if ingestor is None:
        raise RuntimeError(
            f"[source] Unknown source '{source}'. "
            f"Available: {', '.join(sorted(_SOURCE_INGESTORS))}"
        )

    return ingestor(csv_path, lang)
