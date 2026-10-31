"""FR-61 (automatic leak shut-off) and the leak alerts of FR-63, through the
worker's alert engine, with the simulator's burst and leak scenarios."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app import db
from app.ai.shutoff import maybe_shut_off
from app.alerts.detect import Signal
from app.models import AuditEntry, DeviceCommand, Home, Notification, ValveLock

from .conftest import alerts_of, feed


async def _setup(site, make, *, on: bool):
    device = await make.device(site.homes[0], device_id=site.device(1))
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Home).where(Home.id == site.homes[0].id).values(auto_shutoff_enabled=on)
        )
    return device


async def _commands(home_id: uuid.UUID) -> list[DeviceCommand]:
    async with db.sessions()() as session:
        query = select(DeviceCommand).where(DeviceCommand.home_id == home_id)
        return list(await session.scalars(query.order_by(DeviceCommand.created_at)))


async def test_a_burst_raises_a_critical_alert_with_its_reason(site, engine):
    site.twin.inject("burst", site.twin.device(1), 30, lpm=35.0)
    await feed(engine, site.twin, 6 * 60)
    bursts = [a for a in await alerts_of(site.building) if a.rule == "burst"]
    assert len(bursts) == 1
    burst = bursts[0]
    assert burst.severity == "critical" and burst.title == "Burst pipe suspected"
    # The twin's normal use is added on top of the burst, and that depends on
    # the time of day the test runs, so the peak is at least the burst's 35 L/min.
    peak = re.search(r"up to ([0-9.]+) L/min", burst.message)
    assert peak and float(peak.group(1)) >= 35.0 and "broken pipe" in burst.message
    assert burst.details["high"] is True and burst.details["points"]


async def test_switched_off_no_command_is_ever_sent(site, engine, make):
    await _setup(site, make, on=False)
    site.twin.inject("burst", site.twin.device(1), 30, lpm=35.0)
    await feed(engine, site.twin, 10 * 60)
    assert any(a.rule == "burst" for a in await alerts_of(site.building))
    assert await _commands(site.homes[0].id) == []


async def test_switched_on_a_burst_closes_the_valve_within_60_s_and_tells_both(site, engine, make):
    await _setup(site, make, on=True)
    started = site.twin.now
    site.twin.inject("burst", site.twin.device(1), 30, lpm=35.0)
    await feed(engine, site.twin, 6 * 60)
    commands = await _commands(site.homes[0].id)
    assert len(commands) == 1
    cmd = commands[0]
    assert (cmd.type, cmd.action, cmd.requested_role) == ("valve", "close", "system")
    assert cmd.reason.startswith("Automatic shut-off: Burst pipe suspected")
    burst = next(a for a in await alerts_of(site.building) if a.rule == "burst")
    # The burst rule needs 5 minutes of very high flow; the valve closes with the alert.
    assert burst.opened_at.timestamp() - started <= 5 * 60 + 10
    assert (cmd.created_at - burst.opened_at) < timedelta(seconds=60)

    async with db.sessions()() as session:
        notes = list(
            await session.scalars(select(Notification).where(Notification.kind == "auto_shutoff",
                                                            Notification.alert_id == burst.id))
        )  # fmt: skip
        audited = await session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "device.command_requested",
                AuditEntry.details["command"].astext == str(cmd.id),
            )
        )
    assert {n.user_id for n in notes} == {site.owner.id, site.resident.id}
    assert all("Water turned off" in n.subject for n in notes)
    assert audited is not None and audited.actor_role == "system"


async def test_never_more_than_once_per_hour(site, engine, make):
    await _setup(site, make, on=True)
    # The twin keeps the burst going (nobody runs the command in this test),
    # so the detector sees it for 40 minutes.
    site.twin.inject("burst", site.twin.device(1), 40, lpm=35.0)
    await feed(engine, site.twin, 40 * 60, step=20)
    assert len(await _commands(site.homes[0].id)) == 1


def _midday_zone(epoch_s: float) -> ZoneInfo:
    """A time zone where the twin's clock reads 09:00 now, so a 5-hour test
    never touches the 02:00 to 05:00 quiet window, whenever CI runs."""
    offset = 9 - datetime.fromtimestamp(epoch_s, UTC).hour
    offset = (offset + 12) % 24 - 12  # between -12 and +11
    return ZoneInfo(f"Etc/GMT{-offset:+d}") if offset else ZoneInfo("UTC")


async def test_a_long_leak_is_found_and_closes_the_valve_only_when_sure(site, engine, make):
    await _setup(site, make, on=True)
    engine.leaks.zones[str(site.building.id)] = _midday_zone(site.twin.now)
    site.twin.inject("leak", site.twin.device(1), 6 * 60, lpm=1.5)
    await feed(engine, site.twin, 3 * 3600, step=60)
    leaks = [a for a in await alerts_of(site.building) if a.rule == "leak"]
    assert len(leaks) == 1 and leaks[0].severity == "warning"
    assert leaks[0].details["confidence"] < 0.8
    assert await _commands(site.homes[0].id) == []  # found, but not sure yet
    await feed(engine, site.twin, 2 * 3600, step=60)
    leak = next(a for a in await alerts_of(site.building) if a.rule == "leak")
    assert leak.details["confidence"] >= 0.8
    assert len(await _commands(site.homes[0].id)) == 1


async def test_a_locked_valve_is_left_alone(site, make):
    await _setup(site, make, on=True)
    async with db.sessions()() as session, session.begin():
        session.add(ValveLock(home_id=site.homes[0].id, locked_state="open",
                              reason="Owner keeps water on", locked_by=site.owner.id))  # fmt: skip
    signal = Signal("burst", f"burst:{site.device(1)}", True, str(site.building.id),
                    str(site.homes[0].id), device_id=site.device(1),
                    details={"high": True, "confidence": 1.0})  # fmt: skip
    async with db.sessions()() as session, session.begin():
        assert await maybe_shut_off(session, signal, datetime.now(UTC)) is None
