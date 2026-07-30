"""SMTP email delivery for pipeline alerts.

The only module that talks to an SMTP server — subject/body content is
built by src/services/notification_formatting.py (pure, no I/O) and passed
in here. Uses smtplib from the standard library only; no new dependency.
"""
import logging
import smtplib
from email.message import EmailMessage

from src.config.settings import settings

logger = logging.getLogger(__name__)


def send_email(subject: str, body: str) -> None:
    """Send a plain-text email via SMTP_SSL using settings from .env.

    Raises on failure — callers that don't want an email outage to break
    the pipeline run should catch and log rather than let this propagate.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = settings.alert_email_to
    msg.set_content(body)

    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)

    logger.info("alert email sent: %s", subject)
