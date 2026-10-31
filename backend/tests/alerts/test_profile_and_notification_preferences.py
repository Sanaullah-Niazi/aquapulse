"""FR-52: per alert type "never by email", and quiet hours when non-critical
emails wait. Critical alerts ignore quiet hours. Changes count from the next alert."""

from __future__ import annotations

from datetime import UTC, datetime, time
from types import SimpleNamespace

import pytest

from app.alerts.store import after_quiet_hours

from .conftest import alerts_of, feed, notifications_of

URL = "/api/v1/me/notifications"
ZONE = "Asia/Karachi"  # UTC+5, no daylight saving


def _person(start: str | None, end: str | None):
    return SimpleNamespace(
        quiet_from=time.fromisoformat(start) if start else None,
        quiet_until=time.fromisoformat(end) if end else None,
    )


def _utc(hour: int, minute: int = 0, day: int = 30) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "end", "now", "expected"),
    [
        (None, None, _utc(20), _utc(20)),  # no quiet hours
        ("22:00", "07:00", _utc(12), _utc(12)),  # 17:00 local: not quiet
        ("22:00", "07:00", _utc(18), _utc(2, day=31)),  # 23:00 local -> 07:00 next day
        ("22:00", "07:00", _utc(0), _utc(2)),  # 05:00 local -> 07:00 the same day
        ("22:00", "07:00", _utc(2), _utc(2)),  # exactly 07:00: quiet is over
        ("13:00", "15:00", _utc(8, 30), _utc(10)),  # 13:30 local -> 15:00
    ],
)
def test_quiet_hours(start, end, now, expected):
    assert after_quiet_hours(_person(start, end), ZONE, now) == expected


async def test_preferences_are_checked(client, make):
    headers = await make.headers(await make.user("resident"))
    r = await client.patch(URL, json={"email_muted_rules": ["no_such_rule"]}, headers=headers)
    assert r.status_code == 422 and "no_such_rule" in r.json()["detail"]
    r = await client.patch(URL, json={"quiet_from": "22:00"}, headers=headers)
    assert r.status_code == 422  # both or neither
    r = await client.patch(URL, json={"quiet_from": "22:00", "quiet_until": "22:00"},
                           headers=headers)  # fmt: skip
    assert r.status_code == 422
    r = await client.patch(URL, json={"email_muted_rules": ["high_usage", "high_usage"],
                                      "quiet_from": "22:00", "quiet_until": "07:00"},
                           headers=headers)  # fmt: skip
    assert r.status_code == 200
    assert r.json() == {"alert_email_min": "warning", "email_muted_rules": ["high_usage"],
                        "quiet_from": "22:00:00", "quiet_until": "07:00:00"}  # fmt: skip
    # Only what is sent changes; null switches quiet hours off.
    r = await client.patch(URL, json={"quiet_from": None, "quiet_until": None}, headers=headers)
    assert r.json()["quiet_from"] is None and r.json()["email_muted_rules"] == ["high_usage"]


async def test_a_muted_alert_type_is_not_emailed(site, engine, client, make):
    r = await client.patch(URL, json={"email_muted_rules": ["temp_abnormal"]},
                           headers=await make.headers(site.resident))  # fmt: skip
    assert r.status_code == 200
    site.twin.inject("temp_spike", site.twin.device(1), 10)
    await feed(engine, site.twin, 5 * 60)
    [alert] = await alerts_of(site.building)
    assert [n.user_id for n in await notifications_of(alert.id)] == [site.owner.id]


async def test_critical_alerts_ignore_quiet_hours(site, engine, client, make):
    """FR-52 criterion 1. Quiet all day except one minute: a warning would wait."""
    for person in (site.owner, site.resident):
        r = await client.patch(URL, json={"quiet_from": "00:00", "quiet_until": "23:59"},
                               headers=await make.headers(person))  # fmt: skip
        assert r.status_code == 200
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="up")  # critical
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    notes = await notifications_of(alert.id)
    assert len(notes) == 2 and all(n.next_attempt_at <= alert.last_seen_at for n in notes)


async def test_a_warning_waits_for_the_end_of_quiet_hours(site, engine, client, make):
    for person in (site.owner, site.resident):
        await client.patch(URL, json={"quiet_from": "00:00", "quiet_until": "23:59"},
                           headers=await make.headers(person))  # fmt: skip
    site.twin.inject("temp_spike", site.twin.device(1), 10)  # a warning
    await feed(engine, site.twin, 5 * 60)
    [alert] = await alerts_of(site.building)
    notes = await notifications_of(alert.id)
    assert len(notes) == 2 and all(n.next_attempt_at > alert.last_seen_at for n in notes)
