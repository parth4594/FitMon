"""Unit tests for src.services.notification_formatting.

Pure functions only — no I/O, no DB, no SMTP.
"""
from src.services.notification_formatting import (
    format_missed_run_warning_email,
    format_sync_failure_email,
    format_sync_success_email,
)


def test_format_sync_success_email_reports_counts():
    result = {
        "mode": "incremental",
        "inserted": 2,
        "updated": 5,
        "skipped": 0,
        "trigger_source": "cron",
        "is_late_run": False,
        "delay_minutes": None,
    }
    subject, body = format_sync_success_email(result)
    assert "OK" in subject
    assert "cron" in subject
    assert "inserted=2" in subject
    assert "Inserted: 2" in body
    assert "Updated:  5" in body
    assert "Note:" not in body


def test_format_sync_success_email_no_workouts_upserted():
    result = {
        "mode": "incremental",
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "trigger_source": "manual",
        "is_late_run": False,
        "delay_minutes": None,
    }
    subject, _ = format_sync_success_email(result)
    assert "No workouts were upserted" in subject


def test_format_sync_success_email_flags_late_run():
    result = {
        "mode": "full",
        "inserted": 24,
        "updated": 0,
        "skipped": 0,
        "trigger_source": "cron",
        "is_late_run": True,
        "delay_minutes": 62.0,
    }
    _, body = format_sync_success_email(result)
    assert "62 minutes later" in body
    assert "asleep" in body


def test_format_sync_failure_email_includes_error_and_code():
    subject, body = format_sync_failure_email(
        error="rate limited",
        trigger_source="cron",
        failure_code="429",
    )
    assert "FAILED" in subject
    assert "429" in subject
    assert "rate limited" in body


def test_format_missed_run_warning_email_never_run():
    subject, body = format_missed_run_warning_email(None, None)
    assert "never run" in subject
    assert "no recorded run" in body


def test_format_missed_run_warning_email_stale():
    subject, body = format_missed_run_warning_email(30.4, "2026-07-29T10:00:00+02:00")
    assert "30.4h" in subject
    assert "2026-07-29T10:00:00+02:00" in body
