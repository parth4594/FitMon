"""Per-source CSV ingestion modules.

Each module here (e.g. `strong.py`, `hevy.py`) owns everything specific to
one source: its canonical header-key set, its pure parsing/transform
functions, and an `ingest(csv_path, lang) -> dict[str, Any]` entry point.

Shared plumbing (column-map loading/validation, language resolution, decimal
parsing, UUIDv5 generation, the connect/log/commit/rollback lifecycle, and
the `raw.exercises` upsert every source needs identically) lives in `base.py`.

To add a new source: create `<source>.py` here following the same shape,
then register its `ingest` function in `_SOURCE_INGESTORS` in
`src/ingestion/ingest_workout_csv.py`. Nothing else needs to change.
"""
