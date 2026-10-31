"""FR-23 (consumption in the building's time zone) and the live/telemetry endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app import db
from app.backfill import refresh_consumption
from app.models import Device, Telemetry


async def _readings(device, times_and_litres: list[tuple[datetime, float]]) -> None:
    """Store readings straight in the database: delta_l litres at each moment."""
    boot, total = uuid.uuid4(), 1000.0
    rows = []
    for seq, (when, litres) in enumerate(times_and_litres):
        total += litres
        rows.append(
            Telemetry(
                time=when, device_id=device.id, boot_id=boot, seq=seq,
                home_id=device.home_id, building_id=device.building_id,
                total_l=total, delta_l=litres, flow_lpm=2.0, ph=7.2, temp_c=24.0,
                valve="open", rssi=-60,
            )
        )  # fmt: skip
    async with db.sessions()() as session, session.begin():
        session.add_all(rows)
    # Old readings: add them to the 15-minute totals by hand. (Other tests may
    # already have refreshed past these days, and then the live part of the
    # totals would no longer cover them.)
    await refresh_consumption(rows[0].time, rows[-1].time)


@pytest.fixture
async def london(make):
    """A building in London: the clocks go back on Sunday 25 October 2026."""
    owner = await make.user("owner")
    building = await make.building(owner, timezone="Europe/London", currency_code="GBP")
    home = await make.home(building)
    resident = await make.user("resident")
    await make.occupancy(home, resident, move_in=date(2026, 10, 1))
    device = await make.device(home)
    # One litre every hour from Sat 24 Oct 00:00 BST to Tue 27 Oct 00:00 GMT.
    start = datetime(2026, 10, 23, 23, 0, tzinfo=UTC)
    hours = int((datetime(2026, 10, 27, 0, 0, tzinfo=UTC) - start).total_seconds() // 3600)
    await _readings(device, [(start + timedelta(hours=h), 1.0) for h in range(hours)])
    return owner, building, home, resident, device


async def test_daily_totals_follow_local_days_including_dst(client, make, london):
    """FR-23 criterion 3: Sunday 25 October has 25 hours in London."""
    owner, _, home, _, _ = london
    r = await client.get(
        f"/api/v1/homes/{home.id}/consumption",
        params={"granularity": "day", "from": "2026-10-24", "to": "2026-10-26"},
        headers=await make.headers(owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timezone"] == "Europe/London" and body["from"] == "2026-10-24"
    assert [(p["start"][:10], p["litres"]) for p in body["points"]] == [
        ("2026-10-24", 24.0),
        ("2026-10-25", 25.0),
        ("2026-10-26", 24.0),
    ]
    assert body["points"][0]["start"].endswith("+01:00")  # summer time
    assert body["points"][2]["start"].endswith("Z") or body["points"][2]["start"].endswith("+00:00")
    assert body["total_litres"] == 73.0


async def test_hourly_and_monthly_series(client, make, london):
    owner, _, home, _, _ = london
    headers = await make.headers(owner)
    url = f"/api/v1/homes/{home.id}/consumption"
    hourly = (
        await client.get(
            url,
            params={"granularity": "hour", "from": "2026-10-25", "to": "2026-10-25"},
            headers=headers,
        )
    ).json()
    assert len(hourly["points"]) == 25  # 01:00 to 02:00 happens twice
    monthly = (
        await client.get(
            url,
            params={"granularity": "month", "from": "2026-10-01", "to": "2026-10-31"},
            headers=headers,
        )
    ).json()
    assert [(p["start"][:7], p["litres"]) for p in monthly["points"]] == [("2026-10", 73.0)]


async def test_building_series_and_per_home_totals(client, make, london):
    owner, building, home, _, _ = london
    empty_home = await make.home(building, unit_no="ZZ")
    r = await client.get(
        f"/api/v1/buildings/{building.id}/consumption",
        params={"granularity": "day", "from": "2026-10-24", "to": "2026-10-26"},
        headers=await make.headers(owner),
    )
    body = r.json()
    assert body["total_litres"] == 73.0
    totals = {h["home_id"]: h["litres"] for h in body["homes"]}
    assert totals == {str(home.id): 73.0, str(empty_home.id): 0.0}


async def test_resident_only_sees_their_own_stay(client, make, london):
    """A new resident never sees the previous tenant's water use."""
    _, _, home, _, _ = london
    newcomer = await make.user("resident")
    await make.occupancy(home, newcomer, move_in=date(2026, 10, 26))
    headers = await make.headers(newcomer)
    body = (
        await client.get(
            f"/api/v1/homes/{home.id}/consumption",
            params={"granularity": "day", "from": "2026-10-01", "to": "2026-10-26"},
            headers=headers,
        )
    ).json()
    assert body["from"] == "2026-10-26" and body["total_litres"] == 24.0
    readings = (
        await client.get(
            f"/api/v1/homes/{home.id}/telemetry",
            params={"from": "2026-10-25T00:00:00Z", "to": "2026-10-27T00:00:00Z"},
            headers=headers,
        )
    ).json()
    assert len(readings) == 24 and readings[0]["time"] >= "2026-10-26"


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2025-01-01", "to": "2026-03-01"},  # more than 400 days
        {"from": "2026-10-10", "to": "2026-10-01"},  # backwards
        {"granularity": "minute"},
    ],
)
async def test_bad_ranges_are_422(client, make, london, params):
    owner, _, home, _, _ = london
    r = await client.get(
        f"/api/v1/homes/{home.id}/consumption", params=params, headers=await make.headers(owner)
    )
    assert r.status_code == 422


async def test_raw_telemetry_is_limited_to_seven_days(client, make, london):
    owner, _, home, _, _ = london
    headers = await make.headers(owner)
    url = f"/api/v1/homes/{home.id}/telemetry"
    r = await client.get(
        url, params={"from": "2026-10-01T00:00:00", "to": "2026-10-09T00:00:00"}, headers=headers
    )
    assert r.status_code == 422
    r = await client.get(
        url, params={"from": "2026-10-25T00:00:00Z", "to": "2026-10-24T00:00:00Z"}, headers=headers
    )
    assert r.status_code == 422
    r = await client.get(
        url, params={"from": "2026-10-24T00:00:00", "to": "2026-10-24T05:00:00"}, headers=headers
    )
    assert [x["delta_l"] for x in r.json()] == [1.0] * 5
    assert (await client.get(url, headers=headers)).json() == []  # default: the last hour


async def test_live_shows_latest_reading_and_the_device_reported_valve(client, make, london):
    owner, _, home, resident, device = london
    async with db.sessions()() as session, session.begin():
        saved = await session.get(Device, device.id)
        saved.online = True
        saved.state = {"valve": "closed"}
    for user in (owner, resident):
        live = (
            await client.get(f"/api/v1/homes/{home.id}/live", headers=await make.headers(user))
        ).json()
        assert live["device_id"] == device.id and live["online"] is True
        assert live["valve"] == "closed"
        assert live["reading"]["time"].startswith("2026-10-26T23:00")
        assert live["lock"] is None and live["pending_command"] is None


async def test_live_for_a_home_without_device(client, make):
    world = await make.world()
    live = (
        await client.get(
            f"/api/v1/homes/{world.home.id}/live", headers=await make.headers(world.resident)
        )
    ).json()
    assert live["device_id"] is None and live["reading"] is None and live["valve"] is None
