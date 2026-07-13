"""Unit tests for src.ingestion.ingest_workout_csv (spec §16, §17).

No database connection required for any of these. Every function under test
takes plain Python inputs and returns plain Python outputs.
"""

from decimal import Decimal

import pytest
import yaml

from src.ingestion.ingest_workout_csv import (
    _classify_set_row,
    _detect_language,
    _load_and_validate_all_maps,
    _map_row_to_canonical,
    _parse_decimal,
    _resolve_language,
    is_cardio_row,
    parse_hevy_timestamp,
)

# ---------------------------------------------------------------------------
# Shared fixtures / fixture data
# ---------------------------------------------------------------------------

DE_MAP = {
    "language": "de",
    "headers": {
        "date": "Datum",
        "workout_name": "Workout-Name",
        "duration": "Dauer",
        "exercise_name": "Name der Übung",
        "set_order": "Reihenfolge festlegen",
        "weight": "Gewicht",
        "reps": "Wiederh.",
        "distance": "Entfernung",
        "seconds": "Sekunden",
        "notes": "Notizen",
        "workout_notes": "Workout-Notizen",
        "rpe": "RPE",
    },
    "rest_marker": "Ruhezeit",
}

EN_MAP = {
    "language": "en",
    "headers": {
        "date": "Date",
        "workout_name": "Workout Name",
        "duration": "Duration",
        "exercise_name": "Exercise Name",
        "set_order": "Set Order",
        "weight": "Weight",
        "reps": "Reps",
        "distance": "Distance",
        "seconds": "Seconds",
        "notes": "Notes",
        "workout_notes": "Workout Notes",
        "rpe": "RPE",
    },
    "rest_marker": "Rest Timer",
}

MAPS = {"de": DE_MAP, "en": EN_MAP}

RAW_ROW = {
    "Datum": "2025-08-01 20:00:50",
    "Workout-Name": "Abend-Workout",
    "Dauer": "1h 24min",
    "Name der Übung": "Pull Up",
    "Reihenfolge festlegen": "1",
    "Gewicht": "0",
    "Wiederh.": "3.0",
    "Entfernung": "0",
    "Sekunden": "0.0",
    "Notizen": "",
    "Workout-Notizen": "",
    "RPE": "",
}


def _write_yaml(tmp_path, filename: str, data: dict):
    path = tmp_path / filename
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# §16.1 YAML loader validation
# ---------------------------------------------------------------------------


def test_valid_yaml_passes(tmp_path):
    _write_yaml(tmp_path, "de.yaml", DE_MAP)
    maps = _load_and_validate_all_maps(tmp_path)
    assert "de" in maps
    assert maps["de"]["rest_marker"] == "Ruhezeit"


def test_missing_canonical_key_fails(tmp_path):
    bad_map = {**DE_MAP, "headers": {k: v for k, v in DE_MAP["headers"].items() if k != "rpe"}}
    _write_yaml(tmp_path, "de.yaml", bad_map)
    with pytest.raises(ValueError, match="de.yaml"):
        _load_and_validate_all_maps(tmp_path)
    with pytest.raises(ValueError, match="rpe"):
        _load_and_validate_all_maps(tmp_path)


def test_extra_unrecognised_key_fails(tmp_path):
    bad_map = {**DE_MAP, "headers": {**DE_MAP["headers"], "unexpected_key": "Foo"}}
    _write_yaml(tmp_path, "de.yaml", bad_map)
    with pytest.raises(ValueError, match="unexpected_key"):
        _load_and_validate_all_maps(tmp_path)


def test_empty_rest_marker_fails(tmp_path):
    bad_map = {**DE_MAP, "rest_marker": ""}
    _write_yaml(tmp_path, "de.yaml", bad_map)
    with pytest.raises(ValueError, match="rest_marker"):
        _load_and_validate_all_maps(tmp_path)


def test_missing_rest_marker_is_allowed(tmp_path):
    # A future source with no rest-timer row concept omits rest_marker
    # entirely — this must load without error (not a required key).
    no_rest_map = {k: v for k, v in DE_MAP.items() if k != "rest_marker"}
    _write_yaml(tmp_path, "de.yaml", no_rest_map)
    maps = _load_and_validate_all_maps(tmp_path)
    assert "rest_marker" not in maps["de"]


def test_unknown_lang_fails(tmp_path):
    _write_yaml(tmp_path, "de.yaml", DE_MAP)
    all_maps = _load_and_validate_all_maps(tmp_path)
    with pytest.raises(ValueError, match="de"):
        _resolve_language(tmp_path, all_maps, "fr", list(DE_MAP["headers"].values()))


# ---------------------------------------------------------------------------
# §16.2 / §17.1 Language auto-detection
# ---------------------------------------------------------------------------


def test_detects_german_headers():
    header_row = list(DE_MAP["headers"].values())
    result = _detect_language(header_row, MAPS)
    assert result == DE_MAP


def test_detects_english_headers():
    header_row = list(EN_MAP["headers"].values())
    result = _detect_language(header_row, MAPS)
    assert result == EN_MAP


def test_unknown_headers_raises():
    header_row = ["Nope", "Not", "A", "Match"]
    with pytest.raises(ValueError):
        _detect_language(header_row, MAPS)


def test_ambiguous_headers_raises():
    # Craft a second map with identical header values to DE_MAP so both match.
    duplicate_de = {**DE_MAP, "language": "de2"}
    ambiguous_maps = {"de": DE_MAP, "de2": duplicate_de}
    header_row = list(DE_MAP["headers"].values())
    with pytest.raises(ValueError):
        _detect_language(header_row, ambiguous_maps)


# ---------------------------------------------------------------------------
# §16.3 Rest-row detection
# ---------------------------------------------------------------------------


def test_rest_marker_string_is_rest_row():
    result = _classify_set_row("Ruhezeit", rest_marker="Ruhezeit")
    assert result == {"set_type": "rest", "set_number": None}


def test_numeric_string_is_working_row():
    result = _classify_set_row("3", rest_marker="Ruhezeit")
    assert result == {"set_type": "working", "set_number": 3}


def test_rest_marker_is_language_specific():
    result = _classify_set_row("Rest Timer", rest_marker="Rest Timer")
    assert result == {"set_type": "rest", "set_number": None}


def test_numeric_string_never_matches_rest():
    result = _classify_set_row("1", rest_marker="Ruhezeit")
    assert result["set_type"] == "working"


def test_no_rest_marker_treats_all_rows_as_working():
    # Sources with no rest-timer row concept pass rest_marker=None —
    # every row is a working set, parsed as a plain set number.
    result = _classify_set_row("2", rest_marker=None)
    assert result == {"set_type": "working", "set_number": 2}


# ---------------------------------------------------------------------------
# §16.4 Decimal parsing
# ---------------------------------------------------------------------------


def test_period_decimal():
    assert _parse_decimal("82.5") == Decimal("82.5")


def test_comma_decimal():
    assert _parse_decimal("82,5") == Decimal("82.5")


def test_integer_string():
    assert _parse_decimal("80") == Decimal("80")


def test_float_string_trailing_zero():
    assert _parse_decimal("3.0") == Decimal("3.0")


def test_empty_string():
    assert _parse_decimal("") is None


def test_zero_string():
    assert _parse_decimal("0") == Decimal("0")


# ---------------------------------------------------------------------------
# §17.2 Column mapping application
# ---------------------------------------------------------------------------


def test_mapping_renames_keys():
    result = _map_row_to_canonical(RAW_ROW, DE_MAP)
    for canonical_key in DE_MAP["headers"]:
        assert canonical_key in result


def test_mapping_preserves_values():
    result = _map_row_to_canonical(RAW_ROW, DE_MAP)
    assert result["date"] == RAW_ROW["Datum"]
    assert result["workout_name"] == RAW_ROW["Workout-Name"]
    assert result["duration"] == RAW_ROW["Dauer"]
    assert result["exercise_name"] == RAW_ROW["Name der Übung"]
    assert result["set_order"] == RAW_ROW["Reihenfolge festlegen"]
    assert result["weight"] == RAW_ROW["Gewicht"]
    assert result["reps"] == RAW_ROW["Wiederh."]


def test_mapping_includes_empty_columns():
    result = _map_row_to_canonical(RAW_ROW, DE_MAP)
    assert "rpe" in result
    assert result["rpe"] == ""
    assert "workout_notes" in result
    assert result["workout_notes"] == ""


# ---------------------------------------------------------------------------
# spec 04 §11.1 Hevy timestamp parsing
# ---------------------------------------------------------------------------


def test_parse_hevy_timestamp_roundtrip():
    raw = "10.07.2026, 20:20"
    dt = parse_hevy_timestamp(raw)
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.day == 10
    assert dt.hour == 20
    assert dt.minute == 20
    assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# spec 04 §11.2 Cardio row detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "distance_km,duration_seconds,expected",
    [
        ("1.5", "", True),
        ("", "300", True),
        ("", "", False),
        (None, None, False),
    ],
)
def test_is_cardio_row(distance_km, duration_seconds, expected):
    assert is_cardio_row(distance_km, duration_seconds) == expected
