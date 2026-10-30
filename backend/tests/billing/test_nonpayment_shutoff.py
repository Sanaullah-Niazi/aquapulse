"""FR-27 criterion 4: water may be shut off for an unpaid bill only if the
building's policy allows it, the bill is overdue, and the resident was
warned at least 48 hours before. Water is an essential service."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import update

from app import db
from app.models import Bill

from ..conftest import audit_actions
from .conftest import issued_bill

pytestmark = pytest.mark.usefixtures("outbox")


async def _make_overdue(bill_id: str) -> None:
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Bill)
            .where(Bill.id == bill_id)
            .values(status="overdue", due_date=date.today() - timedelta(days=10))
        )


async def _warn(client, make, world, bill):
    return await client.post(
        f"/api/v1/bills/{bill['id']}/shutoff-warning", headers=await make.headers(world.owner)
    )


async def _shut_off(client, make, world, bill_id, state="closed"):
    return await client.post(
        f"/api/v1/homes/{world.home.id}/valve-lock",
        json={"state": state, "reason": "Unpaid water bill, warned", "bill_id": bill_id},
        headers=await make.headers(world.owner),
    )


async def _policy(client, make, world, on: bool):
    r = await client.patch(
        f"/api/v1/buildings/{world.building.id}",
        json={"nonpayment_shutoff_enabled": on},
        headers=await make.headers(world.owner),
    )
    assert r.status_code == 200 and r.json()["nonpayment_shutoff_enabled"] is on


async def test_blocked_while_the_policy_is_off(client, make, world):
    bill = await issued_bill(client, make, world)
    await _make_overdue(bill["id"])
    r = await _warn(client, make, world, bill)
    assert r.status_code == 409 and "does not allow" in r.json()["detail"]
    r = await _shut_off(client, make, world, bill["id"])
    assert r.status_code == 409 and "does not allow" in r.json()["detail"]


async def test_the_whole_path_warning_then_48_hours_then_shut_off(client, make, world, outbox):
    await _policy(client, make, world, True)
    bill = await issued_bill(client, make, world)

    # Not overdue yet: no warning, no shut-off.
    assert (await _warn(client, make, world, bill)).status_code == 409
    r = await _shut_off(client, make, world, bill["id"])
    assert r.status_code == 409 and "not overdue" in r.json()["detail"]

    await _make_overdue(bill["id"])
    r = await _shut_off(client, make, world, bill["id"])
    assert r.status_code == 409 and "not been warned" in r.json()["detail"]

    r = await _warn(client, make, world, bill)
    assert r.status_code == 200 and r.json()["shutoff_warned_at"] is not None
    [(to, _, body)] = [m for m in outbox if "may be closed" in m[1]]
    assert to == world.resident.email and "48 hours" in body and "Rs 540.00" in body
    assert "bill.shutoff_warning" in await audit_actions(target_id=bill["id"])
    # A second warning does not restart (or shorten) the 48 hours.
    first = r.json()["shutoff_warned_at"]
    assert (await _warn(client, make, world, bill)).json()["shutoff_warned_at"] == first

    r = await _shut_off(client, make, world, bill["id"])
    assert r.status_code == 409 and "less than 48 hours" in r.json()["detail"]

    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Bill)
            .where(Bill.id == bill["id"])
            .values(shutoff_warned_at=datetime.now(UTC) - timedelta(hours=49))
        )
    r = await _shut_off(client, make, world, bill["id"])
    assert r.status_code == 201, r.text
    assert r.json()["locked_state"] == "closed"
    assert "valve.locked" in await audit_actions(target_id=str(world.home.id))


async def test_only_for_closing_and_only_this_homes_bill(client, make, world):
    await _policy(client, make, world, True)
    bill = await issued_bill(client, make, world)
    assert (await _shut_off(client, make, world, bill["id"], state="open")).status_code == 422
    other_home_bill = "00000000-0000-0000-0000-000000000000"
    r = await _shut_off(client, make, world, other_home_bill)
    assert r.status_code == 409 and "does not belong" in r.json()["detail"]


async def test_a_paid_bill_is_no_reason(client, make, world):
    await _policy(client, make, world, True)
    bill = await issued_bill(client, make, world)
    await client.post(
        f"/api/v1/bills/{bill['id']}/payments",
        json={"amount": "540", "method": "cash"},
        headers=await make.headers(world.owner),
    )
    r = await _shut_off(client, make, world, bill["id"])
    assert r.status_code == 409 and "not overdue" in r.json()["detail"]
