"""Shared setup for the alert tests that use the database.

`site` is a building with 2 homes, a tank and a resident in home 1, wired to
a simulator twin whose events point at those real rows. `engine` is the
worker's alert engine with a clock the test moves along with the twin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app import db
from app.alerts.engine import AlertEngine
from app.models import Alert, Building, Home, Notification, Tank, User

from .twin import Twin


async def empty_queue() -> None:
    """Earlier tests leave unsent emails behind; the worker sends 50 at a time,
    oldest first, so mark them done to keep each test's emails at the front."""
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Notification).where(Notification.status == "pending").values(status="sent")
        )


@pytest.fixture(autouse=True)
async def _empty_queue():
    await empty_queue()


@dataclass
class Site:
    owner: User
    building: Building
    homes: list[Home]
    tank: Tank
    resident: User
    twin: Twin

    def device(self, number: int) -> str:
        """The (unique) device ID of home number `number` in the events."""
        return self.twin.id(self.twin.device(number))


@pytest.fixture
async def site(make) -> Site:
    owner = await make.user("owner")
    building = await make.building(owner, name="Alert Tower")
    homes = [await make.home(building, unit_no=f"0{n}") for n in (1, 2)]
    tank = await make.tank(building)
    resident = await make.user("resident")
    await make.occupancy(homes[0], resident)
    # Starts 4 hours ago, so the alerts happen after the resident moved in.
    start = datetime.now(UTC).timestamp() - 4 * 3600
    twin = Twin(homes=2, building_id=str(building.id), tank_id=str(tank.id),
                tag=uuid.uuid4().hex[:8], start=start)  # fmt: skip
    twin.home_ids = {twin.device(n): str(homes[n - 1].id) for n in (1, 2)}
    return Site(owner, building, homes, tank, resident, twin)


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def engine() -> AlertEngine:
    engine = AlertEngine(clock=Clock())
    await engine.start()
    return engine


async def feed(engine: AlertEngine, twin: Twin, seconds: float, step: float = 10.0) -> None:
    """Run the twin and hand each round of readings to the engine, as the worker does."""
    batch: list[dict] = []
    last = None
    for when, event in twin.run(seconds, step):
        if last is not None and when != last:
            engine.clock.now = last
            await engine.handle(batch)
            batch = []
        batch.append(event)
        last = when
    if batch:
        engine.clock.now = last
        await engine.handle(batch)


async def alerts_of(building: Building) -> list[Alert]:
    async with db.sessions()() as session:
        return list(
            await session.scalars(
                select(Alert).where(Alert.building_id == building.id).order_by(Alert.opened_at)
            )
        )


async def notifications_of(alert_id: uuid.UUID) -> list[Notification]:
    async with db.sessions()() as session:
        return list(
            await session.scalars(select(Notification).where(Notification.alert_id == alert_id))
        )
