"""FR-30: CSV exports of consumption, bills and payments. Only what the
caller may see, at most 400 days, every export audited, and no spreadsheet
formula can be smuggled in through a name."""

from __future__ import annotations

import csv
import io
from datetime import date

from app import db
from app.models import User

from ..billing.conftest import add_tariff, bills_of, generate, use_water
from ..conftest import audit_actions

SEPT = date(2026, 9, 1)


def _rows(response) -> list[dict]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(response.text)))


async def _setup(make, client):
    owner = await make.user("owner")
    building = await make.building(owner)
    home = await make.home(building, unit_no="A-1")
    resident = await make.user("resident")
    async with db.sessions()() as session, session.begin():  # a name that looks like a formula
        (await session.get(User, resident.id)).full_name = "=HYPERLINK(evil)"
    await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
    device = await make.device(home)
    await add_tariff(building)
    await use_water(home, device, SEPT, [100.0] * 30, building.timezone)

    class World:
        pass

    world = World()
    world.owner, world.building, world.home, world.resident = owner, building, home, resident
    assert (await generate(client, make, world)).status_code == 200
    bill = (await bills_of(client, make, world))[0]
    headers = await make.headers(owner)
    await client.post(f"/api/v1/bills/{bill['id']}/issue", headers=headers)
    payment = {"amount": "5.00", "method": "cash", "received_on": "2026-10-03"}
    r = await client.post(f"/api/v1/bills/{bill['id']}/payments", headers=headers, json=payment)
    assert r.status_code == 201, r.text
    return world, headers


async def test_owner_exports(make, client):
    world, headers = await _setup(make, client)
    url = f"/api/v1/buildings/{world.building.id}/export"
    days = _rows(await client.get(f"{url}/consumption.csv", headers=headers,
                                  params={"from": "2026-09-01", "to": "2026-09-30"}))  # fmt: skip
    assert len(days) == 30
    assert days[0] == {"date": "2026-09-01", "unit": "A-1", "litres": "100.00"}

    bills = _rows(await client.get(f"{url}/bills.csv", headers=headers,
                                   params={"from": "2026-09-01", "to": "2026-09-30"}))  # fmt: skip
    assert len(bills) == 1 and bills[0]["litres"].startswith("3000")
    assert bills[0]["bill_to"] == "'=HYPERLINK(evil)"  # CSV injection defused

    october = {"from": "2026-10-01", "to": "2026-10-31"}
    payments = _rows(await client.get(f"{url}/payments.csv", headers=headers, params=october))
    assert [(p["amount"], p["method"]) for p in payments] == [("5.00", "cash")]
    assert (await audit_actions(actor_id=world.owner.id)).count("export.csv") == 3


async def test_limits_and_who_may_export(make, client):
    world, headers = await _setup(make, client)
    url = f"/api/v1/buildings/{world.building.id}/export/consumption.csv"
    r = await client.get(url, headers=headers, params={"from": "2025-01-01", "to": "2026-09-30"})
    assert r.status_code == 422  # more than 400 days
    stranger = await make.user("owner")
    assert (await client.get(url, headers=await make.headers(stranger))).status_code == 404
    resident = await make.headers(world.resident)
    assert (await client.get(url, headers=resident)).status_code == 403

    # A resident exports their own home: only from their move-in, only their bills.
    home_url = f"/api/v1/homes/{world.home.id}/export"
    days = _rows(await client.get(f"{home_url}/consumption.csv", headers=resident,
                                  params={"from": "2026-07-01", "to": "2026-09-30"}))  # fmt: skip
    assert days[0]["date"] >= "2026-08-01" and days[-1]["date"] == "2026-09-30"
    assert sum(float(d["litres"]) for d in days) == 3000
    bills = _rows(await client.get(f"{home_url}/bills.csv", headers=resident,
                                   params={"from": "2026-01-01", "to": "2026-12-31"}))  # fmt: skip
    assert len(bills) == 1
    neighbour = await make.user("resident")
    other = await make.home(world.building)
    await make.occupancy(other, neighbour)
    r = await client.get(f"{home_url}/bills.csv", headers=await make.headers(neighbour))
    assert r.status_code == 404
    assert "export.csv" in await audit_actions(actor_id=world.resident.id)
