"""Send queued notifications, and try again when a channel fails (FR-60 criterion 3).

A failed email is tried again after 10 s, 30 s and 90 s (3 retries, each wait
three times longer). After that it is marked "failed": System Admins see it in
GET /api/v1/admin/notifications. Every attempt is counted on the row and
logged, without the address or the text (NFR-15).

M9: the same for push notifications to the Android app. A push counts as
sent when at least one of the person's phones accepted it. A phone Firebase
says is gone (app removed) is forgotten.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db, emailer, push
from ..models import Alert, Notification, PushDevice, User

log = logging.getLogger("aquapulse.notify")

RETRIES = 3
FIRST_WAIT = timedelta(seconds=10)
BATCH = 50


NO_PHONE = "no phone registered any more"


async def _push(session: AsyncSession, note: Notification) -> str | None:
    """Send to each of the person's phones. None = at least one accepted it."""
    phones = list(
        await session.scalars(select(PushDevice).where(PushDevice.user_id == note.user_id))
    )
    if not phones:
        return NO_PHONE
    data = {"alertId": str(note.alert_id or ""), "kind": note.kind}
    alert = await session.get(Alert, note.alert_id) if note.alert_id else None
    critical = alert is not None and alert.severity == "critical"  # wakes the phone at once
    results = [await push.send(p.token, note.subject, note.body, data, critical) for p in phones]
    gone = [p.id for p, r in zip(phones, results, strict=True) if r == "gone"]
    if gone:
        await session.execute(delete(PushDevice).where(PushDevice.id.in_(gone)))
    if "sent" in results:
        return None
    return NO_PHONE if len(gone) == len(phones) else "Firebase did not accept it"


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
            if user is None:
                error = "user not found"
            elif note.channel == "push":
                error = await _push(session, note)
            elif await emailer.send_email(user.email, note.subject, note.body):
                error = None
            else:
                error = "the mail server did not accept it"
            if error is None:
                note.status, note.sent_at, note.last_error = "sent", now, None
                done["sent"] += 1
                continue
            note.last_error = error
            if error == NO_PHONE:  # nothing to try again
                note.status = "failed"
                done["failed"] += 1
                continue
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
