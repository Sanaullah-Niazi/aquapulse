"""Shared setup for the billing tests: a building with a tariff, and meter readings.

Readings are written straight into the telemetry table (the ingestor's tests
cover how they get there), then the 15-minute totals are refreshed, exactly
as `make demo-data` does for old readings.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app import db
from app.backfill import refresh_consumption
from app.models import Building, Device, Home, Tariff, TariffCharge, TariffSlab, Telemetry, User

SEPT = date(2026, 9, 1)  # a finished month (30 days) used by most tests
PKR_SLABS = [(0, 5000, "60"), (5000, 15000, "120"), (15000, None, "200")]


@dataclass
class BillingWorld:
    owner: User
    building: Building
    home: Home
    resident: User
    device: Device


async def add_tariff(
    building: Building,
    slabs=PKR_SLABS,
    charges=(),
    effective_from: date = date(2026, 1, 1),
    version: int = 1,
) -> Tariff:
    tariff = Tariff(
        building_id=building.id,
        name=f"Test v{version}",
        version=version,
        effective_from=effective_from,
        currency_code=building.currency_code,
        slabs=[
            TariffSlab(position=n, from_l=a, to_l=b, rate_per_kl=Decimal(r))
            for n, (a, b, r) in enumerate(slabs)
        ],
        charges=[
            TariffCharge(position=n, name=name, kind=kind, value=Decimal(v))
            for n, (name, kind, v) in enumerate(charges)
        ],
    )
    async with db.sessions()() as session, session.begin():
        session.add(tariff)
    return tariff


async def use_water(
    home: Home, device: Device, first_day: date, litres_per_day: list[float], timezone: str
) -> None:
    """One reading at noon (building time) per day, plus a starting reading the
    evening before. `litres_per_day[i]` is used on first_day + i."""
    zone = ZoneInfo(timezone)
    boot = uuid.uuid4()
    total = 1000.0  # the meter did not start at zero
    start = datetime.combine(first_day - timedelta(days=1), time(20, 0), zone)
    rows = [_reading(home, device, boot, 0, start, total, 0.0)]
    for n, litres in enumerate(litres_per_day, 1):
        total += litres
        at = datetime.combine(first_day + timedelta(days=n - 1), time(12, 0), zone)
        rows.append(_reading(home, device, boot, n, at, total, litres))
    async with db.sessions()() as session, session.begin():
        session.add_all(rows)
    await refresh_consumption(rows[0].time, rows[-1].time)


def _reading(home, device, boot, seq, at, total, delta) -> Telemetry:
    return Telemetry(
        time=at, device_id=device.id, boot_id=boot, seq=seq, home_id=home.id,
        building_id=home.building_id, total_l=total, delta_l=delta, flow_lpm=0.0, ph=7.2,
        temp_c=24.0, valve="open", rssi=-60,
    )  # fmt: skip


@pytest.fixture
async def world(make) -> BillingWorld:
    """Owner, PKR building (Asia/Karachi), one home with a billing contact who
    moved in before September, its device, and the SRS demo tariff."""
    owner = await make.user("owner")
    building = await make.building(owner)
    home = await make.home(building, unit_no="A-1")
    resident = await make.user("resident")
    await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
    device = await make.device(home)
    await add_tariff(building)
    return BillingWorld(owner, building, home, resident, device)


async def generate(client, make, world, month: str = "2026-09"):
    return await client.post(
        f"/api/v1/buildings/{world.building.id}/bills/generate",
        json={"month": month},
        headers=await make.headers(world.owner),
    )


async def bills_of(client, make, world, month: str = "2026-09") -> list[dict]:
    r = await client.get(
        f"/api/v1/buildings/{world.building.id}/bills",
        params={"month": month},
        headers=await make.headers(world.owner),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def issued_bill(client, make, world, litres: float = 7000.0) -> dict:
    """A September bill for `litres`, generated and issued. Returns the bill."""
    await use_water(world.home, world.device, SEPT, [litres / 30] * 30, world.building.timezone)
    assert (await generate(client, make, world)).status_code == 200
    bill = (await bills_of(client, make, world))[0]
    r = await client.post(
        f"/api/v1/bills/{bill['id']}/issue", headers=await make.headers(world.owner)
    )
    assert r.status_code == 200, r.text
    return r.json()
