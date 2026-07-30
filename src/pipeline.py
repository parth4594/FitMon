"""Orchestration entry points for the Hevy API pipeline.

Wires together src/ingestion/ingest_hevy_api.py (the actual sync),
src/db/postgres.py (ingestion-log reads), src/services/notification_formatting.py
(pure email content), and src/notifications/email_sender.py (SMTP delivery).
Both the CLI (`sync-hevy`, `check-pipeline-health`) and the launchd-triggered
cron scripts call into this module rather than the lower-level pieces
directly, so alerting behavior lives in exactly one place.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.db.postgres import get_connection, get_last_attempt
from src.ingestion.hevy_api_client import HevyAPIError
from src.ingestion.ingest_hevy_api import SOURCE, run_sync
from src.notifications.email_sender import send_email
from src.services.notification_formatting import (
    format_missed_run_warning_email,
    format_sync_failure_email,
    format_sync_success_email,
)

logger = logging.getLogger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")
SCHEDULED_HOUR_BERLIN = 14  # daily sync-hevy launchd trigger, local Berlin time
LATE_THRESHOLD_MINUTES = 15  # grace window before a cron run counts as "late"

STALE_THRESHOLD_HOURS = 25
ALERT_COOLDOWN_HOURS = 24  # don't re-send the staleness warning more than once/day

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HEALTH_ALERT_SENTINEL = _PROJECT_ROOT / ".data" / ".last_health_alert_at"


def _try_send(subject: str, body: str) -> None:
    """Send an alert email, logging (not raising) on failure.

    A dead SMTP config shouldn't turn a real ingestion failure into a
    crash that masks the original error, or block a successful sync from
    returning its result.
    """
    try:
        send_email(subject, body)
    except Exception:
        logger.warning("failed to send alert email: %s", subject, exc_info=True)


def _compute_cron_lateness(
    trigger_source: str, now: datetime | None = None
) -> tuple[bool, float | None]:
    """Flag a cron-triggered run as "late" if it started more than
    LATE_THRESHOLD_MINUTES after today's 14:00 Europe/Berlin schedule —
    the signature of launchd catching up a run that was missed because the
    machine was asleep at trigger time.

    Always (False, None) for manual runs. `now` is injectable for tests.
    """
    if trigger_source != "cron":
        return False, None

    now = now.astimezone(BERLIN) if now is not None else datetime.now(BERLIN)
    scheduled = now.replace(
        hour=SCHEDULED_HOUR_BERLIN, minute=0, second=0, microsecond=0
    )
    delay_minutes = (now - scheduled).total_seconds() / 60
    is_late = delay_minutes > LATE_THRESHOLD_MINUTES
    return is_late, round(delay_minutes, 1) if is_late else None


def _clear_health_alert_sentinel(sentinel_path: Path | None = None) -> None:
    """Reset the staleness-alert dedup state after a successful sync, so the
    next time the pipeline goes stale, it warns right away instead of
    waiting out a stale cooldown window from a prior episode.
    """
    path = sentinel_path if sentinel_path is not None else _HEALTH_ALERT_SENTINEL
    path.unlink(missing_ok=True)


def _recently_alerted(
    sentinel_path: Path | None = None,
    now: datetime | None = None,
    cooldown_hours: float = ALERT_COOLDOWN_HOURS,
) -> bool:
    path = sentinel_path if sentinel_path is not None else _HEALTH_ALERT_SENTINEL
    if not path.exists():
        return False
    now = now or datetime.now(BERLIN)
    last_alert = datetime.fromisoformat(path.read_text().strip())
    return (now - last_alert) < timedelta(hours=cooldown_hours)


def _record_alert_sent(
    sentinel_path: Path | None = None, now: datetime | None = None
) -> None:
    path = sentinel_path if sentinel_path is not None else _HEALTH_ALERT_SENTINEL
    now = now or datetime.now(BERLIN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now.isoformat())


def run_hevy_sync(mode: str = "auto", trigger_source: str = "manual") -> dict:
    """Run the Hevy API sync and send a result email — success or failure.

    This is what the CLI's `sync-hevy` command and the daily launchd cron
    script both call, so every trigger path gets the same alerting.
    """
    is_late_run, delay_minutes = _compute_cron_lateness(trigger_source)

    try:
        result = run_sync(
            mode=mode,
            trigger_source=trigger_source,
            is_late_run=is_late_run,
            delay_minutes=delay_minutes,
        )
    except HevyAPIError as exc:
        subject, body = format_sync_failure_email(
            error=str(exc),
            trigger_source=trigger_source,
            is_late_run=is_late_run,
            delay_minutes=delay_minutes,
            failure_code=str(exc.status_code) if exc.status_code else None,
        )
        _try_send(subject, body)
        raise
    except Exception as exc:
        subject, body = format_sync_failure_email(
            error=str(exc),
            trigger_source=trigger_source,
            is_late_run=is_late_run,
            delay_minutes=delay_minutes,
        )
        _try_send(subject, body)
        raise

    subject, body = format_sync_success_email(result)
    _try_send(subject, body)
    _clear_health_alert_sentinel()
    return result


def check_pipeline_health(now: datetime | None = None) -> dict:
    """Warn by email if sync-hevy hasn't even been attempted in the last
    STALE_THRESHOLD_HOURS hours.

    Deduplicated via a local sentinel file: while the pipeline stays
    stale, this only re-sends once per ALERT_COOLDOWN_HOURS rather than
    every time the hourly launchd health-check job fires. The sentinel is
    cleared on the next successful sync (see run_hevy_sync), so a fresh
    stale episode always alerts immediately. `now` is injectable for tests.
    """
    conn = get_connection()
    try:
        last_attempt = get_last_attempt(conn, source=SOURCE)
    finally:
        conn.close()

    now = now or datetime.now(BERLIN)

    if last_attempt is None:
        hours_since = None
        stale = True
    else:
        started_at = last_attempt["started_at"]
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=BERLIN)
        hours_since = (now - started_at).total_seconds() / 3600
        stale = hours_since > STALE_THRESHOLD_HOURS

    last_attempt_at = (
        last_attempt["started_at"].isoformat() if last_attempt is not None else None
    )

    if stale and not _recently_alerted(now=now):
        subject, body = format_missed_run_warning_email(hours_since, last_attempt_at)
        _try_send(subject, body)
        _record_alert_sent(now=now)

    return {
        "stale": stale,
        "hours_since_last_attempt": hours_since,
        "last_attempt_at": last_attempt_at,
    }
