"""Bills for the demo: `make demo` runs this after loading the history (M12).

For every building it makes the bills of the last two full months from the
real (simulated) meter readings, issues them, and records cash payments:
  * the older month is fully paid,
  * last month is paid for every second home, so the unpaid list, the
    reminders and the "Pay online" button have something to show.
It uses the same code as the owner's buttons (generate, issue, record a
payment), so every step is in the audit log, done by "system".
Safe to run again: issued bills are never changed and paid bills are skipped.
"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select

from . import db
from .billing.generate import BillingError, generate_month
from .billing.lifecycle import OPEN, issue, record_payment
from .billing.periods import local_today, previous_month
from .models import Bill, Building, Home


async def _month(building_id, month: date, pay_every: int) -> str:
    async with db.sessions()() as session, session.begin():
        building = await session.get(Building, building_id)
        assert building is not None
        try:
            summary = await generate_month(session, building, month, None)
        except BillingError as err:
            return f"{building.code} {month:%B %Y}: no bills ({err})"
        rows = await session.execute(
            select(Bill, Home.unit_no)
            .join(Home, Home.id == Bill.home_id)
            .where(Bill.building_id == building.id, Bill.month == month, Bill.status != "void")
            .order_by(Home.unit_no, Bill.period_start)
        )
        issued = paid = 0
        for index, (bill, _unit) in enumerate(rows.all()):
            if bill.status == "draft":
                await issue(session, bill, building, None)
                issued += 1
            if bill.status in OPEN and index % pay_every == 0:
                await record_payment(
                    session,
                    bill,
                    building,
                    amount=bill.total - bill.amount_paid,
                    method="cash",
                    received_on=local_today(building.timezone),
                    reference="Demo: paid in cash at the office",
                    actor=None,
                )
                paid += 1
        return (
            f"{building.code} {month:%B %Y}: {summary.created} new bills, "
            f"{issued} issued, {paid} paid"
        )


async def run(building_ids: list | None = None) -> list[str]:
    """Every building, or only the given ones. Returns one line per building and month."""
    async with db.sessions()() as session:
        query = select(Building.id, Building.timezone).order_by(Building.code)
        if building_ids is not None:
            query = query.where(Building.id.in_(building_ids))
        buildings = list(await session.execute(query))
    lines = []
    for building_id, timezone in buildings:
        last = previous_month(local_today(timezone))
        lines.append(await _month(building_id, previous_month(last), pay_every=1))
        lines.append(await _month(building_id, last, pay_every=2))
    return lines


async def _main() -> None:
    try:
        for line in await run():
            print(line)
    finally:
        await db.dispose()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
