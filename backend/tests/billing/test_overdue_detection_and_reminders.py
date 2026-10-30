"""FR-38: overdue within one job run; reminders 3 days before, on the due date
and 7 days after, each sent once; the owner's unpaid list with totals per currency."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import db, worker
from app.alerts import deliver
from app.billing.reminders import reminder_due, send_reminders
from app.models import Bill, Building, Notification

from ..alerts.conftest import empty_queue
from .conftest import BillingWorld, add_tariff, issued_bill

pytestmark = pytest.mark.usefixtures("outbox")


def test_which_reminder_fits_which_day():
    due = date(2026, 11, 14)
    assert reminder_due(due, date(2026, 11, 10)) is None
    assert reminder_due(due, date(2026, 11, 11)) == "before_due"
    assert reminder_due(due, date(2026, 11, 13)) == "before_due"
    assert reminder_due(due, date(2026, 11, 14)) == "due_today"
    assert reminder_due(due, date(2026, 11, 20)) == "due_today"  # catching up after a break
    assert reminder_due(due, date(2026, 11, 21)) == "after_due"
    assert reminder_due(due, date(2027, 1, 1)) == "after_due"


async def _reminders(bill_id) -> list[Notification]:
    async with db.sessions()() as session:
        return list(
            await session.scalars(
                select(Notification)
                .where(Notification.bill_id == bill_id)
                .order_by(Notification.created_at)
            )
        )


def _at(day: date, timezone: str) -> datetime:
    return datetime.combine(day, time(10), ZoneInfo(timezone)).astimezone(UTC)


async def _run(world, day: date) -> int:
    async with db.sessions()() as session, session.begin():
        building = await session.get(Building, world.building.id)
        return await send_reminders(session, building, _at(day, world.building.timezone))


async def test_three_reminders_each_once(client, make, world, outbox):
    await empty_queue()
    bill = await issued_bill(client, make, world)
    due = date.fromisoformat(bill["due_date"])
    for day in (due - timedelta(days=5), due - timedelta(days=3), due - timedelta(days=2),
                due, due + timedelta(days=1), due + timedelta(days=7),
                due + timedelta(days=8), due + timedelta(days=30)):  # fmt: skip
        await _run(world, day)
        await _run(world, day)  # the job runs every minute: twice changes nothing
    notes = await _reminders(bill["id"])
    assert [n.subject for n in notes] == [
        f"Reminder: your water bill {bill['bill_no']} is due on {due}",
        f"Your water bill {bill['bill_no']} is due",
        f"Your water bill {bill['bill_no']} is overdue",
    ]
    assert all(n.user_id == world.resident.id and n.kind == "bill_reminder" for n in notes)
    assert "Still to pay: Rs 540.00" in notes[0].body

    latest = max(n.next_attempt_at for n in notes)
    while any(n.status == "pending" for n in await _reminders(bill["id"])):
        await deliver.deliver_due(latest)  # 50 at a time, oldest first, like the worker
    assert [m for m in outbox if bill["bill_no"] in m[1] and m[0] == world.resident.email]


async def test_no_reminder_for_a_paid_bill(client, make, world):
    bill = await issued_bill(client, make, world)
    await client.post(
        f"/api/v1/bills/{bill['id']}/payments",
        json={"amount": "540", "method": "cash"},
        headers=await make.headers(world.owner),
    )
    due = date.fromisoformat(bill["due_date"])
    assert await _run(world, due) == 0
    assert await _reminders(bill["id"]) == []


async def test_overdue_within_one_job_run(client, make, world):
    """Criterion 1: the worker's own round (not a helper) marks it."""
    bill = await issued_bill(client, make, world)
    day = date.fromisoformat(bill["due_date"]) + timedelta(days=1)
    while day.day <= worker.CATCH_UP_UNTIL_DAY:  # stay clear of the monthly bill run
        day += timedelta(days=1)
    done = await worker.tick(_at(day, world.building.timezone))
    assert done["overdue"] >= 1 and done["reminders"] >= 1
    async with db.sessions()() as session:
        assert (await session.get(Bill, bill["id"])).status == "overdue"


async def test_unpaid_list_with_totals_per_currency(client, make, world):
    pkr = await issued_bill(client, make, world)
    # A second building of the same owner, in KWD.
    gulf = await make.building(world.owner, currency_code="KWD", timezone="Asia/Kuwait")
    home = await make.home(gulf)
    resident = await make.user("resident")
    await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
    device = await make.device(home)
    await add_tariff(gulf, slabs=[(0, None, "0.250")])
    kwd = await issued_bill(client, make, BillingWorld(world.owner, gulf, home, resident, device))
    await client.post(
        f"/api/v1/bills/{pkr['id']}/payments",
        json={"amount": "40", "method": "cash"},
        headers=await make.headers(world.owner),
    )
    headers = await make.headers(world.owner)
    r = await client.get("/api/v1/bills/unpaid", headers=headers)
    assert r.status_code == 200
    assert {b["id"] for b in r.json()["bills"]} == {pkr["id"], kwd["id"]}
    totals = {t["currency_code"]: t for t in r.json()["totals"]}
    assert totals["PKR"]["amount_text"] == "Rs 500.00" and totals["PKR"]["bills"] == 1
    assert totals["KWD"]["amount_text"] == "KWD 1.750"
    # One building only; another owner sees nothing of these.
    r = await client.get("/api/v1/bills/unpaid", params={"building_id": str(gulf.id)},
                         headers=headers)  # fmt: skip
    assert [b["id"] for b in r.json()["bills"]] == [kwd["id"]]
    stranger = await make.headers(await make.user("owner"))
    assert (await client.get("/api/v1/bills/unpaid", headers=stranger)).json()["bills"] == []
    r = await client.get("/api/v1/bills/unpaid", params={"building_id": str(gulf.id)},
                         headers=stranger)  # fmt: skip
    assert r.status_code == 404
