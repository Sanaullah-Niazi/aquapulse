"""FR-59: one ongoing problem is ONE alert; it auto-resolves 10 minutes after
the problem is gone; acknowledging records who and when. Also who may see
and change which alert (FR-19), and the in-app path (FR-60 criterion 1)."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from sqlalchemy import func, update

from app import db
from app.alerts.store import auto_resolve
from app.models import Alert
from app.routers.stream import _Scope

from ..conftest import audit_actions
from .conftest import alerts_of, feed

pytestmark = pytest.mark.usefixtures("outbox")


async def test_a_two_hour_problem_is_one_alert(site, engine):
    """Criterion 1. (The SRS names a 2-hour leak; leak detection is the AI of
    M10, so a 2-hour unsafe-pH problem shows the same de-duplication here.)"""
    site.twin.inject("ph_drift", site.twin.device(1), 121, direction="up")
    await feed(engine, site.twin, 120 * 60)
    [alert] = await alerts_of(site.building)
    assert (alert.rule, alert.severity, alert.state) == ("ph_unsafe", "critical", "open")
    assert alert.device_id == site.device(1) and alert.home_id == site.homes[0].id
    # Seen on hundreds of readings, but stored and emailed once.
    assert alert.seen_count > 500
    assert alert.cleared_at is None


async def test_auto_resolved_10_minutes_after_the_problem_is_gone(site, engine):
    """Criterion 2."""
    site.twin.inject("temp_spike", site.twin.device(2), 10)
    await feed(engine, site.twin, 10 * 60)
    await feed(engine, site.twin, 3 * 60)  # back to normal
    [alert] = await alerts_of(site.building)
    assert alert.rule == "temp_abnormal" and alert.cleared_at is not None
    async with db.sessions()() as session, session.begin():
        assert await auto_resolve(session, alert.cleared_at + timedelta(minutes=9)) == 0
    # It comes back for a moment: the 10 minutes start again.
    site.twin.inject("temp_spike", site.twin.device(2), 5)
    await feed(engine, site.twin, 3 * 60)
    [alert] = await alerts_of(site.building)
    assert alert.cleared_at is None
    await feed(engine, site.twin, 4 * 60)
    [alert] = await alerts_of(site.building)
    async with db.sessions()() as session, session.begin():
        assert await auto_resolve(session, alert.cleared_at + timedelta(minutes=10)) >= 1
    [alert] = await alerts_of(site.building)
    assert alert.state == "auto_resolved" and alert.resolved_by is None


async def test_after_resolving_a_new_problem_is_a_new_alert(site, engine, client, make):
    site.twin.inject("sensor_stuck", site.twin.device(2), 200)
    await feed(engine, site.twin, 65 * 60, step=60)
    [alert] = await alerts_of(site.building)
    r = await client.post(
        f"/api/v1/alerts/{alert.id}/resolve",
        json={"note": "Sensor cleaned"},
        headers=await make.headers(site.owner),
    )
    assert r.status_code == 200 and r.json()["state"] == "resolved"
    assert "alert.resolved" in await audit_actions(target_id=str(alert.id))
    # Still stuck: the next reading opens a new alert.
    await feed(engine, site.twin, 60, step=60)
    assert [a.state for a in await alerts_of(site.building)] == ["resolved", "open"]


async def test_acknowledge_records_who_and_when(site, engine, client, make):
    """Criterion 3."""
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="down")
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    url = f"/api/v1/alerts/{alert.id}/acknowledge"
    r = await client.post(url, headers=await make.headers(site.resident))
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "acknowledged" and body["acknowledged_by"] == str(site.resident.id)
    assert body["acknowledged_at"] is not None
    assert "alert.acknowledged" in await audit_actions(target_id=str(alert.id))
    assert (await client.post(url, headers=await make.headers(site.owner))).status_code == 409


async def test_who_sees_which_alert(site, engine, client, make):
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="up")
    site.twin.inject("ph_drift", site.twin.device(2), 30, direction="up")
    site.twin.inject("sump_empty", site.twin.building.tank.id, 30)
    await feed(engine, site.twin, 30 * 60)
    alerts = {(a.rule, a.home_id): a for a in await alerts_of(site.building)}
    mine = alerts[("ph_unsafe", site.homes[0].id)]
    neighbours = alerts[("ph_unsafe", site.homes[1].id)]
    tank = alerts[("dry_run", None)]

    resident = await make.headers(site.resident)
    seen = (await client.get("/api/v1/alerts", headers=resident)).json()
    assert [a["id"] for a in seen] == [str(mine.id)]  # not the neighbour's, not the tank's
    for other in (neighbours, tank):
        r = await client.get(f"/api/v1/alerts/{other.id}", headers=resident)
        assert r.status_code == 404

    owner = await make.headers(site.owner)
    r = await client.get("/api/v1/alerts", params={"building_id": str(site.building.id),
                                                   "active": "true"}, headers=owner)  # fmt: skip
    assert {a["id"] for a in r.json()} == {str(mine.id), str(neighbours.id), str(tank.id)}
    r = await client.get("/api/v1/alerts", params={"severity": "critical",
                                                   "home_id": str(site.homes[1].id)},
                         headers=owner)  # fmt: skip
    assert [a["id"] for a in r.json()] == [str(neighbours.id)]

    stranger = await make.headers(await make.user("owner"))
    assert (await client.get(f"/api/v1/alerts/{mine.id}", headers=stranger)).status_code == 404

    admin = await make.headers(await make.user("sysadmin"))
    assert (await client.get(f"/api/v1/alerts/{tank.id}", headers=admin)).status_code == 200
    r = await client.post(f"/api/v1/alerts/{tank.id}/acknowledge", headers=admin)
    assert r.status_code == 403  # read only for building alerts


async def test_a_new_tenant_does_not_see_old_alerts(site, engine, client, make):
    site.twin.inject("ph_drift", site.twin.device(2), 30, direction="up")
    await feed(engine, site.twin, 30 * 60)
    async with db.sessions()() as session, session.begin():  # it happened 2 days ago
        await session.execute(
            update(Alert)
            .where(Alert.building_id == site.building.id)
            .values(opened_at=func.now() - timedelta(days=2))
        )
    newcomer = await make.user("resident")
    await make.occupancy(site.homes[1], newcomer, move_in=date.today())
    r = await client.get("/api/v1/alerts", headers=await make.headers(newcomer))
    assert r.json() == []


async def test_in_app_within_5_seconds(site, engine, client, make):
    """FR-60 criterion 1: the alert is in the app's list as soon as the reading
    that crosses the line is handled (the worker hears it within a second)."""
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="up")
    events = [e for _, e in site.twin.run(30 * 60)]
    before = events[:-3]
    for n in range(0, len(before), 3):
        await engine.handle(before[n : n + 3])
    started = time.monotonic()
    await engine.handle(events[-3:])
    r = await client.get("/api/v1/alerts", headers=await make.headers(site.resident))
    assert len(r.json()) == 1
    assert time.monotonic() - started < 5


def test_live_stream_shows_alerts_to_the_right_people():
    """The WebSocket filter (FR-25): home alerts to that home's residents,
    tank alerts to the owner only, system alerts to System Admins only."""
    home_alert = {"type": "alert", "buildingId": "b", "homeId": "h1", "severity": "critical"}
    tank_alert = {"type": "alert", "buildingId": "b", "homeId": None, "tankId": "t"}
    system_alert = {"type": "alert", "buildingId": None, "homeId": None}
    resident = _Scope("resident", "u", homes={"h1"})
    owner = _Scope("owner", "o", buildings={"b"})
    admin = _Scope("sysadmin", "a")
    assert resident.allows(home_alert) and not resident.allows(tank_alert)
    assert not _Scope("resident", "u2", homes={"h2"}).allows(home_alert)
    assert owner.allows(home_alert) and owner.allows(tank_alert)
    assert not owner.allows(system_alert) and not resident.allows(system_alert)
    assert admin.allows(system_alert)
