"""Pure email subject/body formatting for pipeline run outcomes.

No I/O, no DB imports — see src/notifications/email_sender.py for the
smtplib send path, and src/pipeline.py for the orchestration that calls
both.
"""


def _lateness_line(is_late_run: bool, delay_minutes: float | None) -> str:
    if not is_late_run:
        return ""
    return (
        f"\nNote: this cron run started {delay_minutes:.0f} minutes later "
        "than its 14:00 Europe/Berlin schedule — likely because the machine "
        "was asleep at trigger time. launchd caught it up on wake.\n"
    )


def format_sync_success_email(result: dict) -> tuple[str, str]:
    """Build (subject, body) for a completed sync-hevy run.

    `result` is the dict returned by ingest_hevy_api.run_sync: mode,
    inserted, updated, skipped, trigger_source, is_late_run, delay_minutes.
    """
    inserted = result["inserted"]
    updated = result["updated"]
    skipped = result["skipped"]
    trigger_source = result["trigger_source"]
    mode = result["mode"]

    if inserted == 0 and updated == 0 and skipped == 0:
        headline = "No workouts were upserted (nothing new since last sync)."
    else:
        headline = (
            f"inserted={inserted}  updated={updated}  skipped={skipped}"
        )

    subject = f"[FitMon] sync-hevy OK ({trigger_source}, {mode}) — {headline}"

    body = (
        f"FitMon Hevy API sync completed successfully.\n\n"
        f"Trigger:  {trigger_source}\n"
        f"Mode:     {mode}\n"
        f"Inserted: {inserted}\n"
        f"Updated:  {updated}\n"
        f"Skipped:  {skipped}\n"
        f"{_lateness_line(result.get('is_late_run', False), result.get('delay_minutes'))}"
    )
    return subject, body


def format_sync_failure_email(
    error: str,
    trigger_source: str,
    is_late_run: bool = False,
    delay_minutes: float | None = None,
    failure_code: str | None = None,
) -> tuple[str, str]:
    """Build (subject, body) for a failed sync-hevy run."""
    code_part = f" [{failure_code}]" if failure_code else ""
    subject = f"[FitMon] sync-hevy FAILED ({trigger_source}){code_part}"

    body = (
        f"FitMon Hevy API sync FAILED.\n\n"
        f"Trigger:      {trigger_source}\n"
        f"Failure code: {failure_code or 'n/a'}\n"
        f"Error:        {error}\n"
        f"{_lateness_line(is_late_run, delay_minutes)}"
    )
    return subject, body


def format_missed_run_warning_email(
    hours_since_last_attempt: float | None, last_attempt_at: str | None
) -> tuple[str, str]:
    """Build (subject, body) for the "pipeline hasn't run in 25h+" warning."""
    if hours_since_last_attempt is None:
        subject = "[FitMon] WARNING: sync-hevy has never run"
        body = (
            "FitMon's Hevy API sync has no recorded run at all in "
            "meta.ingestion_log. Check that the launchd job and/or a "
            "manual `make sync-hevy` has actually been run at least once."
        )
        return subject, body

    subject = (
        f"[FitMon] WARNING: sync-hevy hasn't run in "
        f"{hours_since_last_attempt:.1f}h"
    )
    body = (
        "FitMon's Hevy API sync hasn't run in over 25 hours.\n\n"
        f"Last attempt: {last_attempt_at}\n"
        f"Hours since:  {hours_since_last_attempt:.1f}\n\n"
        "Check whether the machine has been asleep/off, whether the "
        "daily launchd job (com.fitmon.sync-hevy) is loaded, or whether "
        "the sync has been silently failing before it can even log a "
        "'running' row."
    )
    return subject, body
