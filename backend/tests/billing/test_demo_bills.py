"""M12: `make demo` issues the last two months of bills and records some payments,
with the same code as the owner's buttons, and running it again changes nothing."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app import db, demo
from app.billing.periods import local_today, previous_month
from app.models import Bill, Home

from ..conftest import audit_actions
from .conftest import use_water

pytestmark = pytest.mark.usefixtures("outbox")


async def _bills(building_id) -> list[tuple[str, date, str]]:
    async with db.sessions()() as session:
        rows = await session.execute(
            select(Home.unit_no, Bill.month, Bill.status)
            .join(Home, Home.id == Bill.home_id)
            .where(Bill.building_id == building_id)
            .order_by(Bill.month, Home.unit_no)
        )
        return [tuple(row) for row in rows]


async def test_demo_issues_two_months_and_pays_some(make, world):
    zone = world.building.timezone
    last = previous_month(local_today(zone))
    before = previous_month(last)
    second = await make.home(world.building, unit_no="A-2")
    resident2 = await make.user("resident")
    await make.occupancy(second, resident2, move_in=before, billing=True)
    device2 = await make.device(second)
    days = (date(last.year + last.month // 12, last.month % 12 + 1, 1) - before).days
    await use_water(world.home, world.device, before, [200.0] * days, zone)
    await use_water(second, device2, before, [150.0] * days, zone)

    lines = await demo.run([world.building.id])
    assert len(lines) == 2 and all("issued" in line for line in lines), lines

    assert await _bills(world.building.id) == [
        ("A-1", before, "paid"),  # the older month is fully paid
        ("A-2", before, "paid"),
        ("A-1", last, "paid"),  # last month: every second home
        ("A-2", last, "issued"),
    ]
    actions = await audit_actions(building_id=world.building.id)
    assert {"bill.generated", "bill.issued", "payment.recorded"} <= set(actions)

    # Again: nothing new, nothing paid twice.
    await demo.run([world.building.id])
    assert [s for *_, s in await _bills(world.building.id)] == ["paid", "paid", "paid", "issued"]
