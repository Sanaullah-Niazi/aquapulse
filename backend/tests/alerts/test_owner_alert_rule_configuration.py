"""FR-62: owners switch rules on and off and change their numbers, within safe
limits; changes are checked and audited, and a switched-off rule is silent.
Also the two rules that need the database: quiet devices and high daily use."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app import db
from app.alerts.rules import settings_for
from app.alerts.store import offline_signals, usage_signals
from app.backfill import refresh_consumption
from app.models import Building, Telemetry

from ..conftest import audit_actions
from .conftest import alerts_of, feed

pytestmark = pytest.mark.usefixtures("outbox")


def _url(site) -> str:
    return f"/api/v1/buildings/{site.building.id}/alert-rules"


async def test_defaults_come_from_the_srs(site, client, make):
    r = await client.get(_url(site), headers=await make.headers(site.owner))
    rules = {rule["rule"]: rule for rule in r.json()["rules"]}
    assert len(rules) == 11 and all(rule["enabled"] for rule in rules.values())  # 9 + 2 AI (M10)
    assert rules["ph_unsafe"]["values"] == {"low": 6.5, "high": 8.5, "readings": 5}
    assert rules["ph_unsafe"]["limits"]["low"] == {"default": 6.5, "min": 4.0, "max": 7.0,
                                                   "help": "unsafe below this pH"}  # fmt: skip
    assert rules["tank_low"]["values"] == {"below_pct": 15.0}
    assert rules["high_usage"]["values"] == {"percent": 150, "after_hour": 18}
    assert rules["leak"]["values"] == {"night_minutes": 60, "continuous_hours": 2}
    assert rules["burst"]["values"] == {"minutes": 5}
    assert r.json()["recipients"]["critical"] == ["owner", "resident"]


async def test_changes_are_checked_and_audited(site, client, make):
    """Criterion 1."""
    headers = await make.headers(site.owner)
    r = await client.patch(_url(site), json={"rules": {"ph_unsafe": {"low": 6.8, "high": 8.2}}},
                           headers=headers)  # fmt: skip
    assert r.status_code == 200, r.text
    ph = next(rule for rule in r.json()["rules"] if rule["rule"] == "ph_unsafe")
    assert ph["values"] == {"low": 6.8, "high": 8.2, "readings": 5}
    actions = await audit_actions(target_id=str(site.building.id))
    assert "alert_rules.changed" in actions

    # Only what is sent changes: high stays 8.2.
    r = await client.patch(_url(site), json={"rules": {"ph_unsafe": {"readings": 3}}},
                           headers=headers)  # fmt: skip
    ph = next(rule for rule in r.json()["rules"] if rule["rule"] == "ph_unsafe")
    assert ph["values"] == {"low": 6.8, "high": 8.2, "readings": 3}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rules": {"ph_unsafe": {"low": 8.0, "high": 7.8}}}, "'low' must be less than 'high'"),
        ({"rules": {"ph_unsafe": {"low": 2.0}}}, "between 4 and 7"),
        ({"rules": {"tank_low": {"below_pct": 90}}}, "between 1 and 50"),
        ({"rules": {"ph_unsafe": {"readings": 2.5}}}, "whole number"),
        ({"rules": {"ph_unsafe": {"colour": 1}}}, "unknown setting"),
        ({"rules": {"leak_ai": {"enabled": True}}}, "Unknown rule"),
        ({"rules": {"dry_run": {"enabled": 1}}}, "true or false"),
    ],
)
async def test_unsafe_values_are_refused(site, client, make, change, message):
    r = await client.patch(_url(site), json=change, headers=await make.headers(site.owner))
    assert r.status_code == 422 and message in r.json()["detail"], r.text


async def test_recipients_must_be_known_kinds(site, client, make):
    r = await client.patch(_url(site), json={"recipients": {"critical": ["plumber"]}},
                           headers=await make.headers(site.owner))  # fmt: skip
    assert r.status_code == 422


async def test_only_the_buildings_owner(site, client, make):
    other = await make.headers(await make.user("owner"))
    assert (await client.get(_url(site), headers=other)).status_code == 404
    r = await client.patch(_url(site), json={"rules": {}}, headers=other)
    assert r.status_code == 404


async def test_a_disabled_rule_produces_no_alerts(site, engine, client, make):
    """Criterion 2, end to end: switched off in the API, silent in the worker."""
    r = await client.patch(_url(site), json={"rules": {"ph_unsafe": {"enabled": False}}},
                           headers=await make.headers(site.owner))  # fmt: skip
    assert r.status_code == 200
    await engine.start()  # the worker reloads the settings every 10 seconds
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="up")
    await feed(engine, site.twin, 30 * 60)
    assert await alerts_of(site.building) == []


# ----- the rules that need the database -----


async def test_a_quiet_device_is_reported_offline(site, make):
    seen = datetime.now(UTC) - timedelta(minutes=2)
    device = await make.device(home=site.homes[0], last_seen_at=seen)
    await make.device(home=site.homes[1])  # never sent anything: not counted
    async with db.sessions()() as session:
        settings = {str(site.building.id): settings_for({}, {})}
        signals = await offline_signals(session, settings, datetime.now(UTC))
    [signal] = [s for s in signals if s.building_id == str(site.building.id)]
    assert signal.device_id == device.id and signal.rule == "device_offline"
    assert "No message for 2 minutes" in signal.message
    # Switched off: nothing.
    off = {str(site.building.id): settings_for({"device_offline": {"enabled": False}}, {})}
    async with db.sessions()() as session:
        signals = await offline_signals(session, off, datetime.now(UTC))
    assert [s for s in signals if s.building_id == str(site.building.id)] == []


async def _use(home, device_id, building, day: date, litres: float):
    """Water used on one local day: two readings, so the 15-minute totals see it."""
    start = datetime(day.year, day.month, day.day, 8, tzinfo=UTC)
    rows = [
        Telemetry(time=start, device_id=device_id, boot_id=_BOOT, seq=day.toordinal() * 2,
                  home_id=home.id, building_id=building.id, total_l=0, delta_l=litres,
                  flow_lpm=0, ph=7.2, temp_c=24, valve="open", rssi=-60),
    ]  # fmt: skip
    async with db.sessions()() as session, session.begin():
        session.add_all(rows)
    return start


_BOOT = uuid.uuid4()


async def test_high_daily_use_after_18_00(site, make):
    building: Building = site.building  # Asia/Karachi (UTC+5)
    home = site.homes[0]
    device = await make.device(home=home)
    # A fixed day in the past: refreshing the totals up to "now" would hide
    # newer readings that other tests add later.
    today = date(2025, 6, 15)
    for n in range(1, 11):  # 10 days at 200 L
        first = await _use(home, device.id, building, today - timedelta(days=n), 200.0)
    last = await _use(home, device.id, building, today, 450.0)  # today: 225 %
    await refresh_consumption(first - timedelta(hours=1), last + timedelta(hours=1))

    settings = settings_for({}, {})
    evening = datetime(today.year, today.month, today.day, 14, tzinfo=UTC)  # 19:00 local
    morning = datetime(today.year, today.month, today.day, 9, tzinfo=UTC)  # 14:00 local
    async with db.sessions()() as session:
        late = await usage_signals(session, building, settings, evening)
        early = await usage_signals(session, building, settings, morning)
        stricter = await usage_signals(
            session, building, settings_for({"high_usage": {"percent": 300}}, {}), evening
        )
    [signal] = [s for s in late if s.home_id == str(home.id)]
    assert signal.active and signal.severity == "info"
    assert signal.message == "450 L today, 225 % of an average day (200 L)"
    assert not next(s for s in early if s.home_id == str(home.id)).active
    assert not next(s for s in stricter if s.home_id == str(home.id)).active
