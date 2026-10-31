"""FR-48 (bill so far this month) and FR-49 (comparison with last month and the
anonymous building average per person, only with 3 or more occupied homes;
nothing from before the resident moved in)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import db
from app.backfill import refresh_consumption
from app.billing.periods import month_of, previous_month
from app.models import Home, Telemetry

from ..billing.conftest import add_tariff

ZONE = "Asia/Karachi"  # the Factory's default building time zone


def _today() -> date:
    return datetime.now(ZoneInfo(ZONE)).date()


async def _water(device, when: datetime, litres: float) -> None:
    """One reading with `litres` used, plus the meter reading before it."""
    boot = uuid.uuid4()
    rows = [
        Telemetry(time=when - timedelta(minutes=1), device_id=device.id, boot_id=boot, seq=0,
                  home_id=device.home_id, building_id=device.building_id, total_l=1000.0,
                  delta_l=0.0, flow_lpm=0, ph=7.2, temp_c=24, valve="open", rssi=-60),
        Telemetry(time=when, device_id=device.id, boot_id=boot, seq=1,
                  home_id=device.home_id, building_id=device.building_id,
                  total_l=1000.0 + litres, delta_l=litres, flow_lpm=2, ph=7.2, temp_c=24,
                  valve="open", rssi=-60),
    ]  # fmt: skip
    async with db.sessions()() as session, session.begin():
        session.add_all(rows)


def _just_now() -> datetime:
    """Inside this month and newer than any refreshed totals (seen live)."""
    now = datetime.now(UTC)
    start = datetime.combine(month_of(_today()), datetime.min.time(), ZoneInfo(ZONE))
    if now - timedelta(minutes=5) < start.astimezone(UTC):
        pytest.skip("too close to the start of a month")
    return now - timedelta(minutes=3)


@pytest.fixture
async def tower(make):
    """3 occupied homes: A (2 people), B (1), C (1); and an empty home D.
    One resident account per occupied home; "people" is the home's occupants."""
    owner = await make.user("owner")
    building = await make.building(owner)
    homes, people = {}, {}
    for unit, count in (("A", 2), ("B", 1), ("C", 1), ("D", 0)):
        home = await make.home(building, unit)
        async with db.sessions()() as session, session.begin():  # people living there
            (await session.get(Home, home.id)).occupants = max(count, 1)
        homes[unit] = (home, await make.device(home))
        people[unit] = [await make.user("resident") for _ in range(min(count, 1))]
        for person in people[unit]:
            await make.occupancy(home, person, move_in=date(2026, 1, 1))
    return owner, building, homes, people


async def test_comparison_with_last_month_and_the_building(tower, client, make):
    owner, _, homes, people = tower
    now = _just_now()
    for unit, litres in (("A", 300), ("B", 600), ("C", 300), ("D", 999)):
        await _water(homes[unit][1], now, litres)
    last_month = previous_month(month_of(_today()))
    noon = datetime(last_month.year, last_month.month, 15, 7, tzinfo=UTC)  # 12:00 Karachi
    await _water(homes["A"][1], noon, 900)
    await refresh_consumption(noon - timedelta(hours=1), noon)  # older data: add it by hand

    home_a = homes["A"][0]
    r = await client.get(f"/api/v1/homes/{home_a.id}/comparison",
                         headers=await make.headers(people["A"][0]))  # fmt: skip
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["litres"] == 300 and body["previous_month_litres"] == 900
    assert body["people_in_home"] == 2 and body["litres_per_person"] == 150
    # (300 + 600 + 300) / 4 people. The empty home D does not count.
    assert body["occupied_homes"] == 3 and body["building_litres_per_person"] == 300

    # The owner may ask too; a neighbour may not.
    r = await client.get(f"/api/v1/homes/{home_a.id}/comparison",
                         headers=await make.headers(owner))  # fmt: skip
    assert r.status_code == 200
    r = await client.get(f"/api/v1/homes/{home_a.id}/comparison",
                         headers=await make.headers(people["B"][0]))  # fmt: skip
    assert r.status_code == 404


async def test_no_building_average_with_fewer_than_3_homes(make, client):
    owner = await make.user("owner")
    building = await make.building(owner)
    mine, other = await make.home(building), await make.home(building)
    me = await make.user("resident")
    await make.occupancy(mine, me)
    await make.occupancy(other, await make.user("resident"))
    r = await client.get(f"/api/v1/homes/{mine.id}/comparison", headers=await make.headers(me))
    assert r.json()["occupied_homes"] == 2
    assert r.json()["building_litres_per_person"] is None  # one neighbour could be worked out


async def test_nothing_from_before_moving_in(make, client):
    owner = await make.user("owner")
    building = await make.building(owner)
    home = await make.home(building)
    device = await make.device(home)
    today = _today()
    if today.day == 1:
        pytest.skip("needs a day of this month before today")
    await _water(
        device,
        datetime.combine(today - timedelta(days=1), datetime.min.time(), ZoneInfo(ZONE)).astimezone(
            UTC
        )
        + timedelta(hours=12),
        500,
    )  # fmt: skip  (yesterday: the previous tenant)
    newcomer = await make.user("resident")
    await make.occupancy(home, newcomer, move_in=today)
    r = await client.get(f"/api/v1/homes/{home.id}/comparison",
                         headers=await make.headers(newcomer))  # fmt: skip
    assert r.json()["litres"] == 0 and r.json()["previous_month_litres"] is None
    r = await client.get(f"/api/v1/homes/{home.id}/comparison", params={"month": "2020-01"},
                         headers=await make.headers(newcomer))  # fmt: skip
    assert r.json()["litres"] == 0


async def test_bill_so_far_uses_the_real_tariff(make, client):
    owner = await make.user("owner")
    building = await make.building(owner)
    home = await make.home(building)
    device = await make.device(home)
    resident = await make.user("resident")
    await make.occupancy(home, resident, move_in=date(2026, 1, 1))
    url = f"/api/v1/homes/{home.id}/bill-estimate"

    r = await client.get(url, headers=await make.headers(resident))
    assert r.status_code == 200 and r.json()["total"] is None
    assert "not set water rates" in r.json()["message"]

    await add_tariff(building, slabs=[(0, None, "100")])  # Rs 100 per 1,000 L
    await _water(device, _just_now(), 300)
    r = await client.get(url, headers=await make.headers(resident))
    body = r.json()
    assert body["litres"] == 300 and body["total_text"] == "Rs 30.00"
    assert body["from"] == str(month_of(_today())) and "Not a bill" in body["message"]


async def test_the_quality_label_follows_the_buildings_ph_limits(make, client):
    """FR-48 criterion 2: Safe 6.5 to 8.5 by default, or the owner's own limits."""
    world = await make.world()
    url = f"/api/v1/homes/{world.home.id}/live"
    live = (await client.get(url, headers=await make.headers(world.resident))).json()
    assert (live["ph_safe_low"], live["ph_safe_high"]) == (6.5, 8.5)
    assert live["unit_no"] == world.home.unit_no and live["building_name"] == "Test Tower"
    r = await client.patch(f"/api/v1/buildings/{world.building.id}/alert-rules",
                           json={"rules": {"ph_unsafe": {"low": 6.8, "high": 8.0}}},
                           headers=await make.headers(world.owner))  # fmt: skip
    assert r.status_code == 200
    live = (await client.get(url, headers=await make.headers(world.resident))).json()
    assert (live["ph_safe_low"], live["ph_safe_high"]) == (6.8, 8.0)


async def test_readings_averaged_for_charts(make, client):
    world = await make.world()
    device = await make.device(world.home)
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=30)
    boot = uuid.uuid4()
    rows = [
        Telemetry(time=start + timedelta(seconds=10 * n), device_id=device.id, boot_id=boot, seq=n,
                  home_id=world.home.id, building_id=world.building.id, total_l=1000.0 + n,
                  delta_l=1.0, flow_lpm=6.0, ph=7.0 + (n % 2) * 0.2, temp_c=20.0, valve="open",
                  rssi=-60)
        for n in range(12)  # 2 minutes of readings
    ]  # fmt: skip
    async with db.sessions()() as session, session.begin():
        session.add_all(rows)
    r = await client.get(f"/api/v1/homes/{world.home.id}/telemetry",
                         params={"from": (start - timedelta(minutes=1)).isoformat(), "every": 60},
                         headers=await make.headers(world.resident))  # fmt: skip
    assert r.status_code == 200, r.text
    points = r.json()
    assert len(points) == 2  # one per minute
    assert points[0]["delta_l"] == 6.0 and points[0]["ph"] == 7.1 and points[1]["total_l"] == 1011
    r = await client.get(f"/api/v1/homes/{world.home.id}/telemetry", params={"every": 5},
                         headers=await make.headers(world.resident))  # fmt: skip
    assert r.status_code == 422  # at least a minute
