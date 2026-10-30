"""FR-26: tank level, pump runtime and energy, thresholds, mode and manual pump."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.models import TankReading


@pytest.fixture
async def tank_world(make):
    world = await make.world()
    world.tank = await make.tank(world.building, pump_kw=1.5)
    world.device = await make.device(tank=world.tank)
    return world


async def _tank_readings(world, start: datetime, pump_seconds: list[float]) -> None:
    boot = uuid.uuid4()
    rows = [
        TankReading(
            time=start + timedelta(minutes=10 * i), device_id=world.device.id, boot_id=boot,
            seq=i, tank_id=world.tank.id, level_pct=40.0 + i, level_l=2000.0 + 50 * i,
            pump_on=s > 0, pump_s=s, sump_ok=True, sensor_ok=True, dry_run_protect=False,
            overflow_protect=False, mode="auto",
        )
        for i, s in enumerate(pump_seconds)
    ]  # fmt: skip
    async with db.sessions()() as session, session.begin():
        session.add_all(rows)


async def test_tank_shows_confirmed_settings_and_latest_reading(client, make, tank_world):
    await _tank_readings(tank_world, datetime(2026, 10, 20, 5, tzinfo=UTC), [0, 600])
    r = await client.get(
        f"/api/v1/buildings/{tank_world.building.id}/tank",
        headers=await make.headers(tank_world.owner),
    )
    assert r.status_code == 200, r.text
    tank = r.json()
    assert (tank["low_pct"], tank["high_pct"], tank["mode"]) == (25, 95, "auto")
    assert tank["device_id"] == tank_world.device.id
    assert tank["reading"]["level_pct"] == 41.0 and tank["reading"]["pump_on"] is True


async def test_building_without_tank_is_404(client, make):
    world = await make.world()
    r = await client.get(
        f"/api/v1/buildings/{world.building.id}/tank", headers=await make.headers(world.owner)
    )
    assert r.status_code == 404


async def test_history_has_daily_pump_hours_and_energy(client, make, tank_world):
    """FR-26 criterion 2: runtime x pump kW (1.5 kW here)."""
    # Karachi is UTC+5: 2026-10-20 00:00 local = 2026-10-19 19:00 UTC.
    await _tank_readings(tank_world, datetime(2026, 10, 19, 19, tzinfo=UTC), [0] + [600] * 6)
    r = await client.get(
        f"/api/v1/tanks/{tank_world.tank.id}/history",
        params={"from": "2026-10-20", "to": "2026-10-20"},
        headers=await make.headers(tank_world.owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == [{"day": "2026-10-20", "pump_hours": 1.0, "energy_kwh": 1.5}]
    assert body["step_minutes"] == 15 and len(body["levels"]) == 5


async def test_threshold_and_mode_changes_are_sent_to_the_device(client, make, tank_world):
    """FR-26 criterion 1: validated, then confirmed by the device (202)."""
    r = await client.patch(
        f"/api/v1/tanks/{tank_world.tank.id}/settings",
        json={"low_pct": 30, "high_pct": 90, "mode": "manual"},
        headers=await make.headers(tank_world.owner),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert [(c["type"], c["action"], c["params"]) for c in body["commands"]] == [
        ("thresholds", None, {"lowPct": 30, "highPct": 90}),
        ("mode", "manual", {}),
    ]
    # Not changed yet: the device has not confirmed.
    assert (body["tank"]["low_pct"], body["tank"]["mode"]) == (25, "auto")
    assert len(body["tank"]["open_commands"]) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"low_pct": 4},  # below 5 %
        {"high_pct": 98},  # above 97 % (the device's limit)
        {"low_pct": 50, "high_pct": 55},  # less than 10 points apart
        {"low_pct": 90},  # above the current high (95) minus 10
        {"pump_kw": 0},
        {"colour": "blue"},
    ],
)
async def test_bad_settings_are_422(client, make, tank_world, body):
    r = await client.patch(
        f"/api/v1/tanks/{tank_world.tank.id}/settings",
        json=body,
        headers=await make.headers(tank_world.owner),
    )
    assert r.status_code == 422


async def test_pump_power_is_saved_at_once(client, make, tank_world):
    r = await client.patch(
        f"/api/v1/tanks/{tank_world.tank.id}/settings",
        json={"pump_kw": 2.2},
        headers=await make.headers(tank_world.owner),
    )
    assert r.status_code == 200 and r.json()["tank"]["pump_kw"] == 2.2
    assert r.json()["commands"] == []


async def test_manual_pump_command_is_queued(client, make, tank_world):
    r = await client.post(
        f"/api/v1/tanks/{tank_world.tank.id}/pump",
        json={"action": "on", "reason": "Fill before the weekend"},
        headers=await make.headers(tank_world.owner),
    )
    assert r.status_code == 202 and r.json()["type"] == "pump"
    command_id = r.json()["id"]
    r = await client.get(
        f"/api/v1/commands/{command_id}", headers=await make.headers(tank_world.owner)
    )
    assert r.status_code == 200
    # Residents never see tank commands, not even their own building's.
    r = await client.get(
        f"/api/v1/commands/{command_id}", headers=await make.headers(tank_world.resident)
    )
    assert r.status_code == 404


async def test_other_owners_cannot_see_or_control_the_tank(client, make, tank_world):
    stranger = await make.headers(await make.user("owner"))
    tank = f"/api/v1/tanks/{tank_world.tank.id}"
    assert (await client.get(f"{tank}/history", headers=stranger)).status_code == 404
    r = await client.post(f"{tank}/pump", json={"action": "off"}, headers=stranger)
    assert r.status_code == 404
    r = await client.post(
        f"/api/v1/tanks/{uuid.uuid4()}/pump", json={"action": "off"}, headers=stranger
    )
    assert r.status_code == 404
