"""Send queued notifications, and try again when a channel fails (FR-60 criterion 3).

A failed email is tried again after 10 s, 30 s and 90 s (3 retries, each wait
three times longer). After that it is marked "failed": System Admins see it in
GET /api/v1/admin/notifications. Every attempt is counted on the row and
logged, without the address or the text (NFR-15).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from .. import db, emailer
from ..models import Notification, User

log = logging.getLogger("aquapulse.notify")

RETRIES = 3
FIRST_WAIT = timedelta(seconds=10)
BATCH = 50


def next_wait(attempts: int) -> timedelta:
    """Wait before attempt number attempts+1: 10 s, 30 s, 90 s."""
    return FIRST_WAIT * 3 ** (attempts - 1)


async def deliver_due(now: datetime) -> dict[str, int]:
    """Send everything that is due. Returns counts, for logs and tests."""
    done = {"sent": 0, "retry": 0, "failed": 0}
    async with db.sessions()() as session, session.begin():
        due = list(
            await session.scalars(
                select(Notification)
                .where(Notification.status == "pending", Notification.next_attempt_at <= now)
                .order_by(Notification.next_attempt_at)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        )
        for note in due:
            user = await session.get(User, note.user_id)
            note.attempts += 1
            if user is not None and await emailer.send_email(user.email, note.subject, note.body):
                note.status, note.sent_at, note.last_error = "sent", now, None
                done["sent"] += 1
                continue
            note.last_error = (
                "user not found" if user is None else "the mail server did not accept it"
            )
            if note.attempts > RETRIES:
                note.status = "failed"
                done["failed"] += 1
                log.error("notification %s failed after %d attempts", note.id, note.attempts)
            else:
                note.next_attempt_at = now + next_wait(note.attempts)
                done["retry"] += 1
                log.warning(
                    "notification %s: attempt %d failed, trying again", note.id, note.attempts
                )
    return done
