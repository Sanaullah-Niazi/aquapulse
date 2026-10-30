"""FR-35: monthly bill generation from meter readings, with occupancy proration.

Also the worker that runs it on the 1st of the month at 02:00 building time.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import pytest

from app import db, worker
from app.billing.pdf import bill_pdf
from app.models import Bill, Building

from ..conftest import audit_actions
from .conftest import SEPT, add_tariff, bills_of, generate, use_water

pytestmark = pytest.mark.usefixtures("outbox")


async def test_generation_makes_a_draft_from_the_meter_readings(client, make, world):
    await use_water(world.home, world.device, SEPT, [200.0] * 30, world.building.timezone)
    r = await generate(client, make, world)
    assert r.status_code == 200, r.text
    assert r.json() | {"problems": []} == {
        "month": "2026-09-01", "created": 1, "updated": 0, "replaced": 0, "kept": 0,
        "issued": 0, "problems": [],
    }  # fmt: skip

    [bill] = await bills_of(client, make, world)
    assert bill["status"] == "draft" and bill["bill_no"] is None
    assert (bill["unit_no"], bill["bill_to"]) == ("A-1", world.resident.full_name)
    assert (bill["period_start"], bill["period_end"]) == ("2026-09-01", "2026-09-30")
    # 6,000 L: 5,000 x 60/1000 + 1,000 x 120/1000 = 300 + 120
    assert (bill["litres"], bill["total"], bill["total_text"]) == (
        "6000.000",
        "420.00",
        "Rs 420.00",
    )

    detail = (
        await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(world.owner))
    ).json()
    # Criterion 3: start and end meter readings (the meter showed 1,000 L at the start).
    assert (detail["reading_start"], detail["reading_end"]) == ("1000.000", "7000.000")
    assert [(ln["description"], ln["amount"]) for ln in detail["lines"]] == [
        ("Slab 1 (0-5,000 L)", "300.00"),
        ("Slab 2 (5,000-15,000 L)", "120.00"),
    ]
    assert "bill.generated" in await audit_actions(building_id=world.building.id)


async def test_running_twice_makes_no_duplicates_and_refreshes_drafts(client, make, world):
    """Criterion 1. Readings that arrive late are picked up by the second run."""
    await use_water(world.home, world.device, SEPT, [100.0] * 15, world.building.timezone)
    await generate(client, make, world)
    await use_water(world.home, world.device, SEPT + timedelta(days=15), [100.0] * 15,
                    world.building.timezone)  # fmt: skip
    r = await generate(client, make, world)
    assert (r.json()["created"], r.json()["updated"]) == (0, 1)
    [bill] = await bills_of(client, make, world)
    assert bill["litres"] == "3000.000"


async def test_issued_bills_are_never_changed_by_a_new_run(client, make, world):
    await use_water(world.home, world.device, SEPT, [100.0] * 30, world.building.timezone)
    await generate(client, make, world)
    [bill] = await bills_of(client, make, world)
    await client.post(f"/api/v1/bills/{bill['id']}/issue", headers=await make.headers(world.owner))
    await use_water(world.home, world.device, SEPT + timedelta(days=5), [999.0],
                    world.building.timezone)  # fmt: skip
    r = await generate(client, make, world)
    assert (r.json()["created"], r.json()["updated"], r.json()["kept"]) == (0, 0, 1)
    [again] = await bills_of(client, make, world)
    assert again["total"] == bill["total"] and again["status"] == "issued"


async def test_moving_in_on_the_16th_gets_a_prorated_bill(client, make, world):
    """Criterion 2 (and FR-21 criterion 3): two residents, each billed for their own days."""
    new = await make.user("resident")
    from sqlalchemy import update

    from app.models import Occupancy

    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Occupancy)
            .where(Occupancy.home_id == world.home.id)
            .values(move_out=date(2026, 9, 15))
        )
    await make.occupancy(world.home, new, move_in=date(2026, 9, 16), billing=True)
    # 100 L a day for the first 15 days, 500 L a day for the next 15.
    await use_water(world.home, world.device, SEPT, [100.0] * 15 + [500.0] * 15,
                    world.building.timezone)  # fmt: skip
    r = await generate(client, make, world)
    assert r.json()["created"] == 2

    old_bill, new_bill = await bills_of(client, make, world)
    assert (old_bill["user_id"], old_bill["period_end"]) == (str(world.resident.id), "2026-09-15")
    assert old_bill["litres"] == "1500.000" and old_bill["total"] == "90.00"  # 1,500 x 0.06
    assert (new_bill["user_id"], new_bill["period_start"]) == (str(new.id), "2026-09-16")
    # 7,500 L with slabs x 15/30: 2,500 x 0.06 = 150 + 5,000 x 0.12 = 600
    assert new_bill["litres"] == "7500.000" and new_bill["total"] == "750.00"
    detail = (
        await client.get(f"/api/v1/bills/{new_bill['id']}", headers=await make.headers(world.owner))
    ).json()
    assert detail["lines"][0]["description"] == "Slab 1 (0-2,500 L, 15/30 days)"


async def test_people_who_are_not_the_billing_contact_get_no_bill(client, make, world):
    flatmate = await make.user("resident")
    await make.occupancy(world.home, flatmate, move_in=date(2026, 8, 1), billing=False)
    await use_water(world.home, world.device, SEPT, [10.0] * 30, world.building.timezone)
    await generate(client, make, world)
    assert [b["user_id"] for b in await bills_of(client, make, world)] == [str(world.resident.id)]


async def test_a_month_that_is_not_over_cannot_be_billed(client, make, world):
    this_month = datetime.now(UTC).date().strftime("%Y-%m")
    r = await generate(client, make, world, month=this_month)
    assert r.status_code == 409 and "not over yet" in r.json()["detail"]
    future = (datetime.now(UTC).date() + timedelta(days=40)).strftime("%Y-%m")
    assert (await generate(client, make, world, month=future)).status_code == 409


async def test_days_without_a_tariff_are_reported_not_guessed(client, make):
    owner = await make.user("owner")
    building = await make.building(owner)
    home = await make.home(building)
    resident = await make.user("resident")
    await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
    await add_tariff(building, effective_from=date(2026, 9, 10))
    r = await client.post(
        f"/api/v1/buildings/{building.id}/bills/generate",
        json={"month": "2026-09"},
        headers=await make.headers(owner),
    )
    assert r.json()["created"] == 0
    assert r.json()["problems"] == [
        f"Unit {home.unit_no}: No tariff covers 2026-09-01. Add a tariff that starts earlier."
    ]


async def test_zero_use_gives_a_zero_bill(client, make, world):
    await generate(client, make, world)
    [bill] = await bills_of(client, make, world)
    assert (bill["litres"], bill["total"]) == ("0.000", "0.00")
    detail = (
        await client.get(f"/api/v1/bills/{bill['id']}", headers=await make.headers(world.owner))
    ).json()
    assert detail["reading_start"] is None and detail["lines"] == []


async def test_auto_issue_issues_new_bills_at_once(client, make, world):
    r = await client.patch(
        f"/api/v1/buildings/{world.building.id}",
        json={"bills_auto_issue": True},
        headers=await make.headers(world.owner),
    )
    assert r.status_code == 200 and r.json()["bills_auto_issue"] is True
    await use_water(world.home, world.device, SEPT, [50.0] * 30, world.building.timezone)
    assert (await generate(client, make, world)).json()["issued"] == 1
    [bill] = await bills_of(client, make, world)
    assert bill["status"] == "issued" and bill["bill_no"].startswith(world.building.code)


async def test_an_archived_home_still_gets_its_last_bill(client, make, world):
    await use_water(world.home, world.device, SEPT, [10.0] * 30, world.building.timezone)
    async with db.sessions()() as session, session.begin():
        from app.models import Home

        home = await session.get(Home, world.home.id)
        home.archived_at = datetime(2026, 9, 20, tzinfo=UTC)
    assert (await generate(client, make, world)).json()["created"] == 1


async def test_ten_homes_with_pdfs_in_under_ten_seconds(client, make):
    """Criterion 4: a 10-home building, generation + all 10 PDFs, under 10 s."""
    owner = await make.user("owner")
    building = await make.building(owner)
    await add_tariff(building)
    for n in range(10):
        home = await make.home(building, unit_no=f"{n + 1:02d}")
        resident = await make.user("resident")
        await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
        device = await make.device(home)
        await use_water(home, device, SEPT, [150.0 + n * 20] * 30, building.timezone)

    started = time.perf_counter()
    r = await client.post(
        f"/api/v1/buildings/{building.id}/bills/generate",
        json={"month": "2026-09"},
        headers=await make.headers(owner),
    )
    assert r.json()["created"] == 10
    async with db.sessions()() as session:
        from sqlalchemy import select

        bills = list(await session.scalars(select(Bill).where(Bill.building_id == building.id)))
        pdfs = [await bill_pdf(session, bill) for bill in bills]
    elapsed = time.perf_counter() - started
    assert len(pdfs) == 10 and all(p.startswith(b"%PDF") for p in pdfs)
    assert elapsed < 10, f"took {elapsed:.1f} s"


async def test_only_the_buildings_owner_can_generate(client, make, world):
    stranger = await make.user("owner")
    r = await client.post(
        f"/api/v1/buildings/{world.building.id}/bills/generate",
        json={"month": "2026-09"},
        headers=await make.headers(stranger),
    )
    assert r.status_code == 404


# ----- the worker -----


def test_billing_is_due_from_the_first_at_two_until_the_fifth():
    assert not worker.billing_due(datetime(2026, 11, 1, 1, 59))
    assert worker.billing_due(datetime(2026, 11, 1, 2, 0))
    assert worker.billing_due(datetime(2026, 11, 5, 23, 0))  # catching up after downtime
    assert not worker.billing_due(datetime(2026, 11, 6, 0, 0))
    assert not worker.billing_due(datetime(2026, 11, 30, 12, 0))


async def test_the_worker_generates_last_month_once(client, make, world):
    await use_water(world.home, world.device, SEPT, [100.0] * 30, world.building.timezone)
    # 1 October, 02:30 in Karachi (UTC+5) = 21:30 UTC on 30 September.
    first_of_october = datetime(2026, 9, 30, 21, 30, tzinfo=UTC)
    await worker.tick(first_of_october)
    [bill] = await bills_of(client, make, world)
    assert bill["status"] == "draft" and bill["total"] == "180.00"  # 3,000 L x 0.06
    assert "bill.generated" in await audit_actions(building_id=world.building.id)

    # The next rounds the same morning do nothing more for this building.
    await worker.tick(first_of_october + timedelta(minutes=1))
    assert len(await bills_of(client, make, world)) == 1
    async with db.sessions()() as session:
        from app.models import BillingRun

        run = await session.get(BillingRun, (world.building.id, SEPT))
        assert run is not None and run.automatic


async def test_the_worker_waits_for_two_in_the_morning_building_time(client, make, world):
    # 1 October 01:00 in Karachi.
    await worker.tick(datetime(2026, 9, 30, 20, 0, tzinfo=UTC))
    assert await bills_of(client, make, world) == []


async def test_a_broken_building_does_not_stop_the_others(client, make, world, monkeypatch):
    other = await make.building(world.owner)
    await add_tariff(other)
    calls = []
    real = worker.generate_month

    async def flaky(session, building: Building, *args, **kwargs):
        calls.append(building.id)
        if building.id == world.building.id:
            raise RuntimeError("boom")
        return await real(session, building, *args, **kwargs)

    monkeypatch.setattr(worker, "generate_month", flaky)
    await worker.tick(datetime(2026, 9, 30, 21, 30, tzinfo=UTC))
    assert {world.building.id, other.id} <= set(calls)
