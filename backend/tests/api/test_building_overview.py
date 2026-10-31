"""FR-42 / FR-43: the owner's building overview. The numbers match the data:
litres today and this month, live flow, online homes, closed and locked
valves, open alerts by severity, the tank, and unpaid bills."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app import db
from app.models import Alert, Device, TankReading, ValveLock

from .test_my_home_and_usage import _just_now, _water


async def test_the_overview_adds_up(make, client):
    owner = await make.user("owner")
    building = await make.building(owner)
    a, b, _empty = [await make.home(building, unit) for unit in ("01", "02", "03")]
    resident = await make.user("resident")
    await make.occupancy(a, resident)
    dev_a = await make.device(a, online=True, state={"valve": "open"},
                              last_seen_at=datetime.now(UTC))  # fmt: skip
    dev_b = await make.device(b, online=False, state={"valve": "closed"})
    now = _just_now()
    await _water(dev_a, now, 120)
    await _water(dev_b, now, 30)
    tank = await make.tank(building)
    await make.device(tank=tank, online=True)
    async with db.sessions()() as session, session.begin():
        session.add(ValveLock(home_id=b.id, locked_state="closed", reason="Plumbing work today",
                              locked_by=owner.id))  # fmt: skip
        session.add(TankReading(time=datetime.now(UTC), device_id="tank-x", boot_id=uuid.uuid4(),
                                seq=1, tank_id=tank.id, level_pct=64.0, level_l=3200.0,
                                pump_on=True, pump_s=10, sump_ok=True, sensor_ok=True,
                                dry_run_protect=False, overflow_protect=False,
                                mode="auto"))  # fmt: skip
        for severity, home in (("critical", a), ("warning", a), ("info", None)):
            session.add(Alert(rule="ph_unsafe", severity=severity, dedupe_key=uuid.uuid4().hex,
                              building_id=building.id, home_id=home.id if home else None,
                              title="t", message="m", opened_at=datetime.now(UTC),
                              last_seen_at=datetime.now(UTC)))  # fmt: skip

    r = await client.get(f"/api/v1/buildings/{building.id}/overview",
                         headers=await make.headers(owner))  # fmt: skip
    assert r.status_code == 200, r.text
    o = r.json()
    assert (o["homes"], o["homes_online"], o["homes_with_device"]) == (3, 1, 2)
    assert (o["valves_closed"], o["valves_locked"]) == (1, 1)
    assert o["litres_today"] == 150 and o["litres_month"] == 150
    assert o["alerts"] == {"critical": 1, "warning": 1, "info": 1}
    assert o["tank"]["level_pct"] == 64.0 and o["tank"]["pump_on"] is True
    assert o["unpaid_bills"] == 0 and o["unpaid_text"] == "Rs 0.00"
    rows = {row["unit_no"]: row for row in o["rows"]}
    assert rows["01"]["litres_today"] == 120 and rows["01"]["residents"] == ["Test resident"]
    assert rows["01"]["open_alerts"] == 2 and rows["01"]["ph"] == 7.2
    assert rows["02"]["locked"] == "closed" and rows["02"]["lock_reason"] == "Plumbing work today"
    assert rows["03"]["device_id"] is None and rows["03"]["litres_today"] == 0


async def test_only_the_owner_of_the_building(make, client):
    world = await make.world()
    url = f"/api/v1/buildings/{world.building.id}/overview"
    assert (await client.get(url, headers=await make.headers(world.resident))).status_code == 403
    stranger = await make.user("owner")
    assert (await client.get(url, headers=await make.headers(stranger))).status_code == 404


async def test_old_readings_are_not_live_flow(make, client):
    world = await make.world()
    device = await make.device(world.home)
    async with db.sessions()() as session, session.begin():
        (await session.get(Device, device.id)).online = True
    await _water(device, datetime.now(UTC) - timedelta(minutes=10), 50)  # flow 2 L/min, old
    r = await client.get(f"/api/v1/buildings/{world.building.id}/overview",
                         headers=await make.headers(world.owner))  # fmt: skip
    assert r.json()["flow_lpm"] == 0
