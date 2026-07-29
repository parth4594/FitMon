"""Unit tests for utils.backfill_hevy_workout_ids (spec 05 §12).

No database connection required — match_csv_rows_to_api and
filter_unpopulated are pure functions; cascade_sets is tested against a
mocked cursor.
"""
from unittest.mock import MagicMock

from utils.backfill_hevy_workout_ids import (
    cascade_sets as _cascade_sets,
    filter_unpopulated as _filter_unpopulated,
    match_csv_rows_to_api as _match_csv_rows_to_api,
)


# ---------------------------------------------------------------------------
# test_match_found_patches_hevy_workout_id
# ---------------------------------------------------------------------------


def test_match_found_patches_hevy_workout_id():
    csv_rows = [
        {"workout_session_id": "uuid-aaa", "started_at": "2024-01-15T09:00:00+00:00"}
    ]
    api_index = {"2024-01-15T09:00:00+00:00": "b459cba5"}

    matched, unmatched, ambiguous = _match_csv_rows_to_api(csv_rows, api_index)

    assert matched == [{"workout_session_id": "uuid-aaa", "hevy_workout_id": "b459cba5"}]
    assert unmatched == []
    assert ambiguous == []


# ---------------------------------------------------------------------------
# test_no_match_skips_and_logs_warning
# ---------------------------------------------------------------------------


def test_no_match_skips_and_logs_warning(caplog):
    csv_rows = [
        {"workout_session_id": "uuid-aaa", "started_at": "2024-01-15T09:00:00+00:00"}
    ]

    matched, unmatched, ambiguous = _match_csv_rows_to_api(csv_rows, api_index={})

    assert matched == []
    assert unmatched == csv_rows
    assert ambiguous == []


# ---------------------------------------------------------------------------
# test_timestamp_collision_skips_both_and_logs
# ---------------------------------------------------------------------------


def test_timestamp_collision_skips_both_and_logs():
    csv_rows = [
        {"workout_session_id": "uuid-aaa", "started_at": "2024-01-15T09:00:00+00:00"},
        {"workout_session_id": "uuid-bbb", "started_at": "2024-01-15T09:00:00+00:00"},
    ]
    api_index = {"2024-01-15T09:00:00+00:00": "b459cba5"}

    matched, unmatched, ambiguous = _match_csv_rows_to_api(csv_rows, api_index)

    assert matched == []
    assert unmatched == []
    assert {r["workout_session_id"] for r in ambiguous} == {"uuid-aaa", "uuid-bbb"}


# ---------------------------------------------------------------------------
# test_already_populated_row_not_touched
# ---------------------------------------------------------------------------


def test_already_populated_row_not_touched():
    csv_rows = [
        {"workout_session_id": "uuid-aaa", "started_at": "t1", "hevy_workout_id": None},
        {
            "workout_session_id": "uuid-bbb",
            "started_at": "t2",
            "hevy_workout_id": "already-set",
        },
    ]

    result = _filter_unpopulated(csv_rows)

    assert [r["workout_session_id"] for r in result] == ["uuid-aaa"]


# ---------------------------------------------------------------------------
# test_sets_cascade_after_session_backfill
# ---------------------------------------------------------------------------


def test_sets_cascade_after_session_backfill():
    cur = MagicMock()
    cur.rowcount = 3

    rowcount = _cascade_sets(cur, workout_session_id="uuid-aaa", hevy_workout_id="b459cba5")

    assert rowcount == 3
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args[0]
    assert "UPDATE raw.sets" in sql
    assert "hevy_workout_id" in sql
    assert params == ("b459cba5", "uuid-aaa")
