"""FR-37: bill states, bill numbers, void and replace. Also who sees which bill."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import db
from app.billing import lifecycle
from app.models import Bill, Building, Occupancy

from ..conftest import audit_actions
from .conftest import SEPT, bills_of, generate, issued_bill, use_water

pytestmark = pytest.mark.usefixtures("outbox")


async def _post(client, make, user, url, body=None):
    return await client.post(url, json=body, headers=await make.headers(user))


async def test_issuing_gives_a_number_a_due_date_and_an_email(client, make, world, outbox):
    bill = await issued_bill(client, make, world)
    year = datetime.now(UTC).year
    assert bill["status"] == "issued"
    assert bill["bill_no"] == f"{world.building.code}-{year}-000001"
    issued_on = date.fromisoformat(bill["issued_at"][:10])
    # Default due date: issue date + 15 days (criterion 3 of FR-35); allow for
    # the building's time zone being a day ahead of UTC.
    assert date.fromisoformat(bill["due_date"]) - issued_on in (
        timedelta(days=15),
        timedelta(days=16),
    )
    [(to, subject, body)] = [m for m in outbox if "water bill" in m[1]]
    assert to == world.resident.email and "September 2026" in subject
    assert "Rs 540.00" in body and bill["bill_no"] in body


async def test_bill_numbers_are_sequential_per_building(client, make, world):
    for _ in range(3):
        home = await make.home(world.building)
        resident = await make.user("resident")
        await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
    await generate(client, make, world)
    r = await _post(
        client, make, world.owner,
        f"/api/v1/buildings/{world.building.id}/bills/issue-all", {"month": "2026-09"},
    )  # fmt: skip
    assert r.json()["issued"] == 4
    numbers = sorted(b["bill_no"] for b in r.json()["bills"])
    year = datetime.now(UTC).year
    assert numbers == [f"{world.building.code}-{year}-{n:06d}" for n in range(1, 5)]
    # Another building starts its own sequence.
    other = await make.building(world.owner)
    assert other.code != world.building.code


async def test_illegal_transitions_answer_409(client, make, world):
    bill = await issued_bill(client, make, world)
    url = f"/api/v1/bills/{bill['id']}"
    assert (await _post(client, make, world.owner, f"{url}/issue")).status_code == 409
    await _post(client, make, world.owner, f"{url}/payments", {"amount": "540", "method": "cash"})
    # paid -> void, paid -> draft (issue again) and paying a paid bill are refused.
    r = await _post(client, make, world.owner, f"{url}/void", {"reason": "Testing a paid bill"})
    assert r.status_code == 409
    assert (await _post(client, make, world.owner, f"{url}/issue")).status_code == 409
    r = await _post(client, make, world.owner, f"{url}/payments", {"amount": "1", "method": "cash"})
    assert r.status_code == 409


async def test_void_needs_a_reason_and_the_month_can_be_billed_again(client, make, world):
    bill = await issued_bill(client, make, world)
    url = f"/api/v1/bills/{bill['id']}/void"
    assert (await _post(client, make, world.owner, url, {"reason": "short"})).status_code == 422
    r = await _post(client, make, world.owner, url, {"reason": "Meter was read wrongly"})
    assert r.status_code == 200 and r.json()["status"] == "void"
    assert r.json()["void_reason"] == "Meter was read wrongly"
    assert "bill.voided" in await audit_actions(target_id=bill["id"])

    # Generate again: a new draft that links to the voided bill (criterion 2).
    result = (await generate(client, make, world)).json()
    assert (result["created"], result["replaced"]) == (1, 1)
    bills = await bills_of(client, make, world)
    [new] = [b for b in bills if b["status"] == "draft"]
    detail = (
        await client.get(f"/api/v1/bills/{new['id']}", headers=await make.headers(world.owner))
    ).json()
    assert detail["replaces_bill_id"] == bill["id"]


async def test_a_bill_with_payments_cannot_be_voided(client, make, world):
    bill = await issued_bill(client, make, world)
    url = f"/api/v1/bills/{bill['id']}"
    await _post(client, make, world.owner, f"{url}/payments", {"amount": "100", "method": "cash"})
    r = await _post(client, make, world.owner, f"{url}/void", {"reason": "Wrong tariff used"})
    assert r.status_code == 409 and "reverse them first" in r.json()["detail"]


async def test_a_draft_can_be_voided(client, make, world):
    await generate(client, make, world)
    [bill] = await bills_of(client, make, world)
    r = await _post(
        client, make, world.owner, f"/api/v1/bills/{bill['id']}/void", {"reason": "Not needed now"}
    )
    assert r.json()["status"] == "void"


async def test_a_zero_bill_is_paid_as_soon_as_it_is_issued(client, make, world):
    await generate(client, make, world)
    [bill] = await bills_of(client, make, world)
    r = await _post(client, make, world.owner, f"/api/v1/bills/{bill['id']}/issue")
    assert r.json()["status"] == "paid" and r.json()["total"] == "0.00"


async def test_overdue_after_the_due_date(client, make, world):
    bill = await issued_bill(client, make, world)
    async with db.sessions()() as session, session.begin():
        building = await session.get(Building, world.building.id)
        later = datetime.now(UTC) + timedelta(days=40)
        assert await lifecycle.mark_overdue(session, building, later) == 1
        assert await lifecycle.mark_overdue(session, building, later) == 0  # once only
    detail = (
        await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(world.owner))
    ).json()
    assert detail["status"] == "overdue"
    assert "bill.overdue" in await audit_actions(target_id=bill["id"])
    # Paying an overdue bill in full makes it paid.
    r = await _post(
        client, make, world.owner, f"/api/v1/bills/{bill['id']}/payments",
        {"amount": "540.00", "method": "bank"},
    )  # fmt: skip
    assert r.status_code == 201
    status = (
        await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(world.owner))
    ).json()["status"]
    assert status == "paid"


def test_status_rules():
    bill = Bill(total=100, amount_paid=0, due_date=date(2026, 10, 15))
    assert lifecycle.status_from_amounts(bill, date(2026, 10, 15)) == "issued"
    assert lifecycle.status_from_amounts(bill, date(2026, 10, 16)) == "overdue"
    bill.amount_paid = 40
    assert lifecycle.status_from_amounts(bill, date(2026, 10, 1)) == "partially_paid"
    assert lifecycle.status_from_amounts(bill, date(2026, 10, 20)) == "overdue"
    bill.amount_paid = 100
    assert lifecycle.status_from_amounts(bill, date(2026, 10, 20)) == "paid"


# ----- who sees which bill (FR-19, FR-21) -----


async def test_residents_see_only_their_own_issued_bills(client, make, world):
    await use_water(world.home, world.device, SEPT, [100.0] * 30, world.building.timezone)
    await generate(client, make, world)
    [draft] = await bills_of(client, make, world)
    resident = await make.headers(world.resident)
    # A draft is not shown to the resident yet.
    assert (await client.get("/api/v1/me/bills", headers=resident)).json() == []
    assert (await client.get(f"/api/v1/bills/{draft['id']}", headers=resident)).status_code == 404
    await _post(client, make, world.owner, f"/api/v1/bills/{draft['id']}/issue")
    mine = (await client.get("/api/v1/me/bills", headers=resident)).json()
    assert [b["id"] for b in mine] == [draft["id"]]
    assert (await client.get(f"/api/v1/bills/{draft['id']}", headers=resident)).status_code == 200

    # Someone else in the same building, an owner of another building: 404.
    neighbour = await make.user("resident")
    await make.occupancy(await make.home(world.building), neighbour, billing=True)
    for user in (neighbour, await make.user("owner")):
        r = await client.get(f"/api/v1/bills/{draft['id']}", headers=await make.headers(user))
        assert r.status_code == 404
    # A System Admin can read it, but not change it.
    admin = await make.user("sysadmin")
    r = await client.get(f"/api/v1/bills/{draft['id']}", headers=await make.headers(admin))
    assert r.status_code == 200


async def test_a_moved_out_resident_keeps_their_old_bills(client, make, world):
    bill = await issued_bill(client, make, world)
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Occupancy)
            .where(Occupancy.user_id == world.resident.id)
            .values(move_out=date(2026, 10, 5))
        )
    headers = await make.headers(world.resident)
    assert [b["id"] for b in (await client.get("/api/v1/me/bills", headers=headers)).json()] == [
        bill["id"]
    ]
    # ... and the new tenant does not see them.
    new = await make.user("resident")
    await make.occupancy(world.home, new, move_in=date(2026, 10, 6), billing=True)
    r = await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(new))
    assert r.status_code == 404


async def test_list_filters_by_status(client, make, world):
    bill = await issued_bill(client, make, world)
    url = f"/api/v1/buildings/{world.building.id}/bills"
    headers = await make.headers(world.owner)
    issued = (await client.get(url, params={"status": "issued"}, headers=headers)).json()
    assert [b["id"] for b in issued] == [bill["id"]]
    assert (await client.get(url, params={"status": "paid"}, headers=headers)).json() == []
    assert (await client.get(url, params={"month": "2026-13"}, headers=headers)).status_code == 422


async def test_bill_events_reach_only_the_billed_resident():
    """The WebSocket filter (FR-25): bill events go to the bill's own resident,
    never as drafts, and never to the home's next tenant."""
    from app.routers.stream import _Scope

    resident = _Scope("resident", "user-1", homes={"home-1"})
    event = {"type": "bill", "homeId": "home-1", "userId": "user-1", "status": "issued"}
    assert resident.allows(event)
    assert not resident.allows(event | {"status": "draft"})
    assert not resident.allows(event | {"userId": "previous-tenant"})
    assert _Scope("owner", "o", buildings={"b"}).allows(event | {"buildingId": "b"})


async def test_bills_cannot_be_deleted_from_the_database(world):
    """The API's database account has no DELETE right on bills (FR-20, FR-40)."""
    from sqlalchemy.exc import ProgrammingError

    async with db.sessions()() as session:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await session.execute(select(Bill.id).limit(1))  # reading is fine
            await session.execute(Bill.__table__.delete().where(Bill.id.is_(None)))
