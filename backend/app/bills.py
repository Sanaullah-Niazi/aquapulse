"""Make draft bills from the command line: `make bills` (or MONTH=2026-09).

The same generation the worker runs on the 1st of each month (FR-35), for
every building, for last month (in each building's own time zone) or for
the month you give. Safe to run again. Recorded in the audit log as done by
"system". Owners do the same in the app or in Swagger:
POST /api/v1/buildings/{id}/bills/generate.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date

from sqlalchemy import select

from . import db
from .billing.generate import BillingError, generate_month
from .billing.periods import local_today, previous_month
from .models import Building


async def run(month_text: str | None) -> None:
    try:
        async with db.sessions()() as session:
            ids = list(await session.scalars(select(Building.id).order_by(Building.code)))
        for building_id in ids:
            async with db.sessions()() as session, session.begin():
                building = await session.get(Building, building_id)
                assert building is not None
                month = (
                    date.fromisoformat(f"{month_text}-01")
                    if month_text
                    else previous_month(local_today(building.timezone))
                )
                try:
                    s = await generate_month(session, building, month, None)
                except BillingError as err:
                    print(f"{building.code} {building.name}: {err}")
                    continue
                print(
                    f"{building.code} {building.name}, {month:%B %Y}: {s.created} new drafts, "
                    f"{s.updated} recalculated, {s.kept} already issued"
                )
                for problem in s.problems:
                    print(f"   ! {problem}")
    finally:
        await db.dispose()


def main() -> None:
    month = sys.argv[1] if len(sys.argv) > 1 else None
    if month and not (len(month) == 7 and month[4] == "-"):
        sys.exit("Give the month like 2026-09")
    asyncio.run(run(month))
    print("Review and issue them in Swagger: GET/POST /api/v1/buildings/{id}/bills ...")


if __name__ == "__main__":
    main()
