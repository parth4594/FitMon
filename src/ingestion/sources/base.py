"""Shared plumbing for all per-source CSV ingestors.

Nothing in this module is source-specific — no assumptions about column
names, timestamp formats, or which raw tables get written beyond the
`raw.exercises` table every source shares identically. Source modules
(`strong.py`, `hevy.py`, ...) import what they need from here.
"""
import csv
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid5, NAMESPACE_URL

import yaml

from src.db.postgres import (
    fail_ingestion_log,
    get_connection,
    start_ingestion_log,
)

logger = logging.getLogger(__name__)

COLUMN_MAPS_DIR = Path(__file__).resolve().parent.parent / "column_maps"

# UUIDv5 namespace: stable, arbitrary, never changes
_UUID_NS = uuid5(NAMESPACE_URL, "fitmon.ingestion")


# ---------------------------------------------------------------------------
# Column-map loading and validation (§6.6)
# ---------------------------------------------------------------------------


def load_and_validate_all_maps(source_dir: Path, canonical_keys: frozenset[str]) -> dict[str, dict]:
    """Load and validate every *.yaml in source_dir.

    `canonical_keys` is the required key set for the `headers:` section —
    each source module passes its own (see e.g. strong.CANONICAL_KEYS).

    Returns a mapping of {lang_code: parsed_yaml_dict}.
    Raises ValueError with a specific error if any file is invalid.
    """
    yaml_files = sorted(source_dir.glob("*.yaml"))
    if not yaml_files:
        raise ValueError(
            f"[column-map] No YAML files found in {source_dir}. "
            "At least one <lang>.yaml is required."
        )

    maps: dict[str, dict] = {}
    for yaml_path in yaml_files:
        lang_code = yaml_path.stem
        with yaml_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        if not isinstance(data, dict):
            raise ValueError(f"[column-map] {yaml_path.name}: root must be a YAML mapping.")

        # Validate headers section
        headers = data.get("headers")
        if not isinstance(headers, dict):
            raise ValueError(
                f"[column-map] {yaml_path.name}: missing or invalid 'headers' section."
            )

        # All canonical keys must be present — no missing, no unrecognized extras
        # (case-insensitive check on the canonical key names themselves)
        present_keys = {k.lower() for k in headers}
        canonical_lower = {k.lower() for k in canonical_keys}
        missing = canonical_lower - present_keys
        extra = present_keys - canonical_lower
        if missing:
            raise ValueError(
                f"[column-map] {yaml_path.name}: missing required header keys: "
                + ", ".join(sorted(missing))
            )
        if extra:
            raise ValueError(
                f"[column-map] {yaml_path.name}: unrecognized header keys: "
                + ", ".join(sorted(extra))
            )

        # rest_marker is optional — some sources have no rest-timer row concept
        # at all. When present it must be a non-empty string; when absent,
        # every row is treated as a working set (see strong.classify_set_row).
        if "rest_marker" in data:
            rest_marker = data["rest_marker"]
            if not isinstance(rest_marker, str) or not rest_marker:
                raise ValueError(
                    f"[column-map] {yaml_path.name}: 'rest_marker' must be a "
                    "non-empty string when present."
                )

        # ignored_columns is optional — source CSV columns to silently drop
        # before the headers mapping is applied (spec 04 §7.6, Hevy-specific,
        # but generic enough that any source may use it).
        if "ignored_columns" in data:
            ignored_columns = data["ignored_columns"]
            if not isinstance(ignored_columns, list) or not all(
                isinstance(c, str) for c in ignored_columns
            ):
                raise ValueError(
                    f"[column-map] {yaml_path.name}: 'ignored_columns' must be "
                    "a list of strings when present."
                )

        maps[lang_code] = data

    return maps


# ---------------------------------------------------------------------------
# Language resolution (§6.5)
# ---------------------------------------------------------------------------


def detect_language(header_row: list[str], loaded_maps: dict[str, dict]) -> dict:
    """Auto-detect language by comparing CSV headers against each loaded YAML's
    header values (case-insensitive, order-insensitive, exact match — §6.5).

    Returns the matched column-map dict. Raises ValueError if zero or more
    than one YAML matches.
    """
    csv_headers_lower = {h.lower() for h in header_row}
    matches: list[str] = []
    for lang_code, col_map in loaded_maps.items():
        yaml_header_values_lower = {v.lower() for v in col_map["headers"].values()}
        if yaml_header_values_lower == csv_headers_lower:
            matches.append(lang_code)

    if len(matches) == 1:
        return loaded_maps[matches[0]]

    if len(matches) == 0:
        # Find which headers didn't match any YAML to help the user diagnose
        all_yaml_values_lower: set[str] = set()
        for col_map in loaded_maps.values():
            all_yaml_values_lower |= {v.lower() for v in col_map["headers"].values()}
        unmatched = csv_headers_lower - all_yaml_values_lower
        raise ValueError(
            "[lang] Auto-detection failed: no YAML matched the CSV headers exactly.\n"
            f"       CSV headers: {sorted(header_row)}\n"
            f"       Unmatched headers: {sorted(unmatched)}\n"
            "       Add a YAML file for this language or check for typos."
        )

    # More than one match — ambiguous
    raise ValueError(
        "[lang] Auto-detection failed: multiple YAMLs matched the CSV headers: "
        + ", ".join(sorted(matches))
    )


def resolve_language(
    source_dir: Path,
    all_maps: dict[str, dict],
    lang_arg: str | None,
    csv_header_row: list[str],
) -> dict:
    """Return the resolved column-map dict for the detected/requested language."""
    if lang_arg is not None:
        if lang_arg not in all_maps:
            available = ", ".join(sorted(all_maps))
            raise ValueError(
                f"[lang] Language '{lang_arg}' not found in {source_dir}. "
                f"Available: {available}"
            )
        return all_maps[lang_arg]

    return detect_language(csv_header_row, all_maps)


def read_header_row(csv_path: Path) -> list[str]:
    """Read just the CSV header row (used for language auto-detection)."""
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        try:
            return next(reader)
        except StopIteration:
            raise RuntimeError(f"[file] CSV file is empty: {csv_path}")


# ---------------------------------------------------------------------------
# Canonical row reader
# ---------------------------------------------------------------------------


def build_canonical_reader(col_map: dict) -> dict[str, str]:
    """Invert the YAML headers map: {csv_column_header: canonical_name}."""
    return {v: k for k, v in col_map["headers"].items()}


def map_row_to_canonical(raw_row: dict[str, str], col_map: dict) -> dict[str, str]:
    """Rename raw CSV column headers to canonical keys per col_map.

    Pure rename only — values are passed through unchanged, never parsed or
    cast (§17.2). Type casting happens later, in each source's own helpers.
    """
    canonical_reader = build_canonical_reader(col_map)
    canonical: dict[str, str] = {}
    for csv_col, value in raw_row.items():
        c_name = canonical_reader.get(csv_col)
        if c_name is not None:
            canonical[c_name] = value if value is not None else ""
    return canonical


# ---------------------------------------------------------------------------
# Generic transform helpers
# ---------------------------------------------------------------------------


def parse_decimal(raw: str) -> Decimal | None:
    """Parse a decimal value, handling both '.' and ',' as decimal separator (§8.8)."""
    raw = raw.strip()
    if not raw:
        return None
    # If contains comma but no period → treat comma as decimal separator
    if "," in raw and "." not in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def make_uuid5(key_string: str) -> UUID:
    """Generate a deterministic UUIDv5 from the composite key string (§9)."""
    return uuid5(_UUID_NS, key_string)


# ---------------------------------------------------------------------------
# raw.exercises — identical for every source, so it lives here once
# ---------------------------------------------------------------------------


def upsert_exercises(cur, exercises: dict[str, str]) -> int:
    """Upsert {exercise_name_raw: source} into raw.exercises.

    ON CONFLICT DO NOTHING: exercise_name is globally UNIQUE, so the first
    source to insert a given name "owns" its exercise_id. raw has no FK
    constraints (see CLAUDE.md), so later sources referencing the same name
    with a different computed exercise_id is expected, not a bug.
    Returns the number of rows actually inserted.
    """
    inserted = 0
    for exercise_name_raw, src in exercises.items():
        exercise_id = make_uuid5(f"{src}|{exercise_name_raw}")
        cur.execute(
            """
            INSERT INTO raw.exercises (exercise_id, exercise_name, source)
            VALUES (%s, %s, %s)
            ON CONFLICT (exercise_name) DO NOTHING
            """,
            (str(exercise_id), exercise_name_raw, src),
        )
        if cur.rowcount == 1:
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Connect / log / commit / rollback lifecycle — identical for every source
# ---------------------------------------------------------------------------


def run_ingestion(
    source: str,
    language: str,
    csv_path: Path,
    work_fn: Callable[[Any, UUID], dict[str, Any]],
) -> dict[str, Any]:
    """Wrap the connect → start_ingestion_log → work → close lifecycle.

    `work_fn(conn, log_id) -> summary_dict` does the actual parsing and
    writing; it is responsible for calling `conn.commit()` and
    `finish_ingestion_log(...)` itself before returning, since only it knows
    the row counts. On any exception, this wrapper writes a failed log row,
    rolls back, and re-raises; the connection is always closed.
    """
    conn = get_connection()
    log_id: UUID | None = None
    try:
        log_id = start_ingestion_log(
            conn,
            source=source,
            language=language,
            details={"file": str(csv_path.resolve())},
        )
        return work_fn(conn, log_id)
    except Exception as exc:
        if log_id is not None:
            try:
                fail_ingestion_log(conn, log_id, str(exc))
            except Exception:
                pass  # best-effort: don't mask the original error
        conn.rollback()
        raise
    finally:
        conn.close()
