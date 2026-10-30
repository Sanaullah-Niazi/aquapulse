"""Sending email (invitations, password resets). Locally everything lands in Mailpit."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from .config import get_settings

log = logging.getLogger("aquapulse.email")


def _send(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> bool:
    """Send in a worker thread (smtplib blocks). Returns False if it failed."""
    try:
        await asyncio.to_thread(_send, to, subject, body)
        return True
    except (OSError, smtplib.SMTPException) as err:
        # Never log the address or the body: they contain personal data and tokens (NFR-15).
        log.warning("email could not be sent: %s", type(err).__name__)
        return False
