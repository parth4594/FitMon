"""Unit tests for src.ingestion.ingest_hevy_api (spec 05 §12).

Mocked HTTP responses only — no real Hevy API calls. Every function under
test takes plain Python inputs and returns plain Python outputs.
"""
from unittest.mock import patch

from src.ingestion.ingest_hevy_api import (
    paginate as _paginate,
    parse_exercise as _parse_exercise,
    parse_session as _parse_session,
    unpack_event as _unpack_event,
)

WORKOUT = {
    "id": "b459cba5",
    "title": "Push Day",
    "description": "Felt strong today",
    "routine_id": "routine-1",
    "start_time": "2024-01-15T09:00:00Z",
    "end_time": "2024-01-15T10:00:00Z",
    "updated_at": "2024-01-16T00:00:00Z",
    "created_at": "2024-01-14T00:00:00Z",
}


# ---------------------------------------------------------------------------
# test_parse_session_maps_fields
# ---------------------------------------------------------------------------


def test_parse_session_maps_fields():
    result = _parse_session(WORKOUT)
    assert result == {
        "hevy_workout_id": "b459cba5",
        "title": "Push Day",
        "description": "Felt strong today",
        "routine_id": "routine-1",
        "started_at": "2024-01-15T09:00:00Z",
        "ended_at": "2024-01-15T10:00:00Z",
        "hevy_updated_at": "2024-01-16T00:00:00Z",
        "hevy_created_at": "2024-01-14T00:00:00Z",
    }


def test_parse_session_handles_missing_fields():
    result = _parse_session({"id": "abc"})
    assert result["hevy_workout_id"] == "abc"
    assert result["title"] is None
    assert result["description"] is None
    assert result["started_at"] is None


# ---------------------------------------------------------------------------
# parse_exercise — the live API returns `superset_id` (singular), not
# `supersets_id` (plural) as spec 05 §8 literally documents; confirmed
# against a real GET /v1/workouts response, so this locks the field-name fix in.
# ---------------------------------------------------------------------------


def test_parse_exercise_maps_fields():
    exercise = {
        "index": 0,
        "title": "Bench Press",
        "notes": "felt easy",
        "exercise_template_id": "78683336",
        "superset_id": 2,
        "sets": [],
    }
    result = _parse_exercise(exercise, hevy_workout_id="b459cba5")
    assert result == {
        "hevy_workout_id": "b459cba5",
        "exercise_template_id": "78683336",
        "exercise_name": "Bench Press",
        "exercise_index": 0,
        "supersets_id": 2,
        "notes": "felt easy",
        "source": "hevy",
    }


# ---------------------------------------------------------------------------
# test_deleted_event_skipped / test_updated_event_unpacked
# ---------------------------------------------------------------------------


def test_deleted_event_skipped():
    event = {"type": "deleted", "id": "b459cba5", "deleted_at": "2024-01-20T00:00:00Z"}
    assert _unpack_event(event) is None


def test_updated_event_unpacked():
    event = {"type": "updated", "workout": WORKOUT}
    assert _unpack_event(event) == WORKOUT


# ---------------------------------------------------------------------------
# test_pagination_stops_at_page_count
# ---------------------------------------------------------------------------


@patch("src.ingestion.ingest_hevy_api.time.sleep")
def test_pagination_stops_at_page_count(mock_sleep):
    pages = {
        1: {"page": 1, "page_count": 3, "items": ["a"]},
        2: {"page": 2, "page_count": 3, "items": ["b"]},
        3: {"page": 3, "page_count": 3, "items": ["c"]},
    }

    def fetch_fn(page):
        return pages[page]

    result = list(_paginate(fetch_fn, "items"))

    assert result == ["a", "b", "c"]
    assert mock_sleep.call_count == 3


@patch("src.ingestion.ingest_hevy_api.time.sleep")
def test_pagination_single_page(mock_sleep):
    pages = {1: {"page": 1, "page_count": 1, "items": ["only"]}}
    result = list(_paginate(lambda page: pages[page], "items"))
    assert result == ["only"]
    assert mock_sleep.call_count == 1
