"""FR-60: in-app always; email to the right people by their choice; a failed
email is tried again 3 times with growing waits, then shown to System Admins.
(Push to the Android app comes with M9.)"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app import db, emailer
from app.alerts import deliver
from app.models import User

from .conftest import alerts_of, feed, notifications_of


async def _set_pref(client, make, user, value):
    r = await client.patch(
        "/api/v1/me/notifications", json={"alert_email_min": value},
        headers=await make.headers(user),
    )  # fmt: skip
    assert r.status_code == 200 and r.json()["alert_email_min"] == value


async def _send_all(alert_id, outbox):
    """Deliver this alert's emails now, the way the worker does every 5 seconds."""
    latest = max(n.next_attempt_at for n in await notifications_of(alert_id))
    while any(n.status == "pending" for n in await notifications_of(alert_id)):
        await deliver.deliver_due(latest)  # 50 at a time, oldest first, like the worker
    return [(to, subject) for to, subject, _ in outbox]


async def test_critical_alert_emails_owner_and_resident(site, engine, outbox):
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="up")
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    sent = await _send_all(alert.id, outbox)
    assert {to for to, _ in sent} >= {site.owner.email, site.resident.email}
    subject = next(s for to, s in sent if to == site.owner.email)
    assert subject == "[AquaPulse] Critical: pH unsafe (unit 01, Alert Tower)"
    _, _, body = next(m for m in outbox if m[0] == site.resident.email)
    assert "too high" in body and "acknowledge" in body
    assert {n.status for n in await notifications_of(alert.id)} == {"sent"}
    # Readings that only update the same alert send nothing more.
    await feed(engine, site.twin, 60)
    assert len(await notifications_of(alert.id)) == 2


async def test_each_person_chooses_from_which_severity(site, engine, client, make, outbox):
    await _set_pref(client, make, site.resident, "critical")
    await _set_pref(client, make, site.owner, "off")
    site.twin.inject("temp_spike", site.twin.device(1), 10)  # a warning
    await feed(engine, site.twin, 5 * 60)
    [alert] = await alerts_of(site.building)
    assert alert.severity == "warning" and await notifications_of(alert.id) == []
    r = await client.get("/api/v1/me/notifications", headers=await make.headers(site.resident))
    assert r.json() == {"alert_email_min": "critical"}
    bad = await client.patch("/api/v1/me/notifications", json={"alert_email_min": "loud"},
                             headers=await make.headers(site.resident))  # fmt: skip
    assert bad.status_code == 422


async def test_the_building_chooses_who_gets_which_severity(site, engine, client, make):
    """FR-62: e.g. residents only for critical alerts, the owner for all."""
    r = await client.patch(
        f"/api/v1/buildings/{site.building.id}/alert-rules",
        json={"recipients": {"warning": ["owner"], "info": []}},
        headers=await make.headers(site.owner),
    )
    assert r.status_code == 200
    await engine.start()  # the worker reloads settings every 10 s
    site.twin.inject("temp_spike", site.twin.device(1), 10)
    await feed(engine, site.twin, 5 * 60)
    [alert] = await alerts_of(site.building)
    assert [n.user_id for n in await notifications_of(alert.id)] == [site.owner.id]


async def test_disabled_accounts_get_no_email(site, engine):
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(User).where(User.id == site.resident.id).values(is_active=False)
        )
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="down")
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    assert [n.user_id for n in await notifications_of(alert.id)] == [site.owner.id]


async def test_failed_email_is_tried_3_more_times_then_shown_to_admins(
    site, engine, client, make, monkeypatch
):
    """Criterion 3."""
    tries: list[str] = []

    async def mail_server_down(to, subject, body):
        tries.append(to)
        return False

    monkeypatch.setattr(emailer, "send_email", mail_server_down)
    site.twin.inject("ph_drift", site.twin.device(2), 30, direction="up")  # owner only: home 2
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    [note] = await notifications_of(alert.id)
    at = note.next_attempt_at

    waits = []
    for _ in range(4):
        await deliver.deliver_due(at)
        [note] = await notifications_of(alert.id)
        waits.append(note.next_attempt_at - at)
        at = note.next_attempt_at
    assert note.status == "failed" and note.attempts == 4
    assert tries.count(site.owner.email) == 4
    assert waits[:3] == [timedelta(seconds=10), timedelta(seconds=30), timedelta(seconds=90)]
    assert note.last_error == "the mail server did not accept it"

    admin = await make.headers(await make.user("sysadmin"))
    r = await client.get("/api/v1/admin/notifications", headers=admin)
    failed = [n for n in r.json() if n["alert_id"] == str(alert.id)]
    assert failed and failed[0]["attempts"] == 4 and failed[0]["status"] == "failed"
    assert "subject" not in failed[0] and "body" not in failed[0]  # no personal data here


async def test_a_retry_waits_for_its_time(site, engine, monkeypatch):
    calls = []

    async def flaky(to, subject, body):
        calls.append(to)
        return len(calls) > 1  # the 2nd try works

    monkeypatch.setattr(emailer, "send_email", flaky)
    site.twin.inject("ph_drift", site.twin.device(2), 30, direction="up")
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    [note] = await notifications_of(alert.id)
    first = note.next_attempt_at
    await deliver.deliver_due(first)
    await deliver.deliver_due(first + timedelta(seconds=5))  # too early: not tried
    [note] = await notifications_of(alert.id)
    assert (note.status, note.attempts) == ("pending", 1)
    await deliver.deliver_due(first + timedelta(seconds=10))
    [note] = await notifications_of(alert.id)
    assert (note.status, note.attempts, note.last_error) == ("sent", 2, None)


@pytest.mark.parametrize("status", ["pending", "sent"])
async def test_admins_can_list_by_status(client, make, status):
    admin = await make.headers(await make.user("sysadmin"))
    r = await client.get("/api/v1/admin/notifications", params={"status": status}, headers=admin)
    assert r.status_code == 200 and all(n["status"] == status for n in r.json())
    r = await client.get("/api/v1/admin/notifications", params={"status": "x"}, headers=admin)
    assert r.status_code == 422


def test_the_worker_sends_every_5_seconds():
    """Email in Mailpit within 60 s (criterion 1): queued at once, sent within 5 s."""
    from app import worker

    assert worker.SEND_S <= 5
