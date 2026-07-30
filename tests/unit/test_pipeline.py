"""Unit tests for src.pipeline (cron lateness detection, health-check
staleness/dedup, and run_hevy_sync's alerting wrapper).

DB, SMTP, and the underlying Hevy sync are all mocked — no real I/O.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import src.pipeline as pipeline
from src.ingestion.hevy_api_client import HevyAPIError

BERLIN = ZoneInfo("Europe/Berlin")


# ---------------------------------------------------------------------------
# _compute_cron_lateness
# ---------------------------------------------------------------------------


def test_manual_trigger_is_never_late():
    is_late, delay = pipeline._compute_cron_lateness("manual")
    assert (is_late, delay) == (False, None)


def test_cron_on_time_is_not_late():
    now = datetime(2026, 7, 30, 14, 5, tzinfo=BERLIN)
    is_late, delay = pipeline._compute_cron_lateness("cron", now=now)
    assert (is_late, delay) == (False, None)


def test_cron_run_after_grace_window_is_late():
    now = datetime(2026, 7, 30, 15, 0, tzinfo=BERLIN)
    is_late, delay = pipeline._compute_cron_lateness("cron", now=now)
    assert is_late is True
    assert delay == 60.0


# ---------------------------------------------------------------------------
# staleness-alert sentinel dedup
# ---------------------------------------------------------------------------


def test_recently_alerted_false_when_no_sentinel(tmp_path):
    sentinel = tmp_path / "sentinel"
    assert pipeline._recently_alerted(sentinel_path=sentinel) is False


def test_recently_alerted_true_within_cooldown(tmp_path):
    sentinel = tmp_path / "sentinel"
    now = datetime(2026, 7, 30, 12, 0, tzinfo=BERLIN)
    pipeline._record_alert_sent(sentinel_path=sentinel, now=now)

    later = now + timedelta(hours=2)
    assert pipeline._recently_alerted(sentinel_path=sentinel, now=later) is True


def test_recently_alerted_false_after_cooldown_expires(tmp_path):
    sentinel = tmp_path / "sentinel"
    now = datetime(2026, 7, 30, 12, 0, tzinfo=BERLIN)
    pipeline._record_alert_sent(sentinel_path=sentinel, now=now)

    much_later = now + timedelta(hours=25)
    assert pipeline._recently_alerted(sentinel_path=sentinel, now=much_later) is False


def test_clear_health_alert_sentinel_removes_file(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("2026-07-30T12:00:00+02:00")
    pipeline._clear_health_alert_sentinel(sentinel_path=sentinel)
    assert not sentinel.exists()


def test_clear_health_alert_sentinel_missing_file_is_noop(tmp_path):
    sentinel = tmp_path / "does-not-exist"
    pipeline._clear_health_alert_sentinel(sentinel_path=sentinel)  # no raise


# ---------------------------------------------------------------------------
# check_pipeline_health
# ---------------------------------------------------------------------------


def test_check_pipeline_health_not_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_HEALTH_ALERT_SENTINEL", tmp_path / "sentinel")
    now = datetime(2026, 7, 30, 14, 0, tzinfo=BERLIN)
    last_attempt_started = now - timedelta(hours=2)

    conn = MagicMock()
    with (
        patch.object(pipeline, "get_connection", return_value=conn),
        patch.object(
            pipeline,
            "get_last_attempt",
            return_value={"started_at": last_attempt_started, "status": "success"},
        ),
        patch.object(pipeline, "_try_send") as mock_send,
    ):
        status = pipeline.check_pipeline_health(now=now)

    assert status["stale"] is False
    mock_send.assert_not_called()
    conn.close.assert_called_once()


def test_check_pipeline_health_stale_sends_warning(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    monkeypatch.setattr(pipeline, "_HEALTH_ALERT_SENTINEL", sentinel)
    now = datetime(2026, 7, 30, 14, 0, tzinfo=BERLIN)
    last_attempt_started = now - timedelta(hours=30)

    conn = MagicMock()
    with (
        patch.object(pipeline, "get_connection", return_value=conn),
        patch.object(
            pipeline,
            "get_last_attempt",
            return_value={"started_at": last_attempt_started, "status": "success"},
        ),
        patch.object(pipeline, "_try_send") as mock_send,
    ):
        status = pipeline.check_pipeline_health(now=now)

    assert status["stale"] is True
    mock_send.assert_called_once()
    assert sentinel.exists()


def test_check_pipeline_health_stale_but_deduped(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    monkeypatch.setattr(pipeline, "_HEALTH_ALERT_SENTINEL", sentinel)
    now = datetime(2026, 7, 30, 14, 0, tzinfo=BERLIN)
    pipeline._record_alert_sent(sentinel_path=sentinel, now=now - timedelta(hours=1))
    last_attempt_started = now - timedelta(hours=30)

    conn = MagicMock()
    with (
        patch.object(pipeline, "get_connection", return_value=conn),
        patch.object(
            pipeline,
            "get_last_attempt",
            return_value={"started_at": last_attempt_started, "status": "success"},
        ),
        patch.object(pipeline, "_try_send") as mock_send,
    ):
        status = pipeline.check_pipeline_health(now=now)

    assert status["stale"] is True
    mock_send.assert_not_called()


def test_check_pipeline_health_never_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_HEALTH_ALERT_SENTINEL", tmp_path / "sentinel")
    now = datetime(2026, 7, 30, 14, 0, tzinfo=BERLIN)

    conn = MagicMock()
    with (
        patch.object(pipeline, "get_connection", return_value=conn),
        patch.object(pipeline, "get_last_attempt", return_value=None),
        patch.object(pipeline, "_try_send") as mock_send,
    ):
        status = pipeline.check_pipeline_health(now=now)

    assert status["stale"] is True
    assert status["hours_since_last_attempt"] is None
    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# run_hevy_sync
# ---------------------------------------------------------------------------


def test_run_hevy_sync_success_sends_email_and_clears_sentinel(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_HEALTH_ALERT_SENTINEL", tmp_path / "sentinel")
    (tmp_path / "sentinel").write_text("2026-07-29T10:00:00+02:00")

    result = {
        "mode": "incremental",
        "inserted": 0,
        "updated": 3,
        "skipped": 0,
        "trigger_source": "manual",
        "is_late_run": False,
        "delay_minutes": None,
    }
    with (
        patch.object(pipeline, "run_sync", return_value=result) as mock_run_sync,
        patch.object(pipeline, "_try_send") as mock_send,
    ):
        out = pipeline.run_hevy_sync(mode="auto", trigger_source="manual")

    mock_run_sync.assert_called_once_with(
        mode="auto",
        trigger_source="manual",
        is_late_run=False,
        delay_minutes=None,
    )
    mock_send.assert_called_once()
    assert not (tmp_path / "sentinel").exists()
    assert out == result


def test_run_hevy_sync_failure_sends_failure_email_and_reraises(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_HEALTH_ALERT_SENTINEL", tmp_path / "sentinel")
    error = HevyAPIError("rate limited")
    error.status_code = 429

    with (
        patch.object(pipeline, "run_sync", side_effect=error),
        patch.object(pipeline, "_try_send") as mock_send,
    ):
        try:
            pipeline.run_hevy_sync(mode="auto", trigger_source="cron")
            assert False, "expected HevyAPIError to propagate"
        except HevyAPIError:
            pass

    mock_send.assert_called_once()
    subject = mock_send.call_args[0][0]
    assert "FAILED" in subject
