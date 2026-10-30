"""worker: the jobs that run on a schedule, not because someone clicked (M6).

    python -m app.worker

Every minute, for every building, in the building's OWN time zone:

* **Monthly bills (FR-35).** From the 1st at 02:00, the drafts for the month
  that just ended are generated once (or issued straight away, if the owner
  switched on bills_auto_issue). If the worker was down on the 1st, it
  catches up when it starts again, up to the 5th. The owner can always
  generate a month by hand too (POST /buildings/{id}/bills/generate).
* **Overdue bills (FR-37).** Issued bills that are not fully paid after their
  due date become "overdue". Reminder emails come in M7 (FR-38).

A plain loop instead of a scheduler library: the jobs are small, and a loop
is easy to read and to test (tick() takes the time as a parameter).
Healthy = it finished a round in the last 3 minutes (a file it touches).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import db
from .billing.generate import BillingError, generate_month
from .billing.lifecycle import mark_overdue
from .billing.periods import local_today, previous_month
from .models import BillingRun, Building

log = logging.getLogger("aquapulse.worker")

TICK_S = 60
BILLING_HOUR = 2  # 02:00 building time on the 1st
CATCH_UP_UNTIL_DAY = 5
HEARTBEAT = Path(os.environ.get("WORKER_HEARTBEAT", "/tmp/aquapulse-worker-alive"))  # noqa: S108


def billing_due(local_now: datetime) -> bool:
    """Is it time for last month's bills in this building?"""
    if local_now.day == 1:
        return local_now.hour >= BILLING_HOUR
    return local_now.day <= CATCH_UP_UNTIL_DAY


async def tick(now: datetime | None = None) -> dict[str, int]:
    """One round over all buildings. Returns what it did (for logs and tests)."""
    now = now or datetime.now(UTC)
    done = {"generated": 0, "overdue": 0}
    async with db.sessions()() as session:
        buildings = list(await session.scalars(select(Building).order_by(Building.code)))
    for building_id, code in [(b.id, b.code) for b in buildings]:
        # One transaction per building: a problem in one never blocks the others.
        try:
            async with db.sessions()() as session, session.begin():
                building = await session.get(Building, building_id)
                assert building is not None
                local_now = now.astimezone(ZoneInfo(building.timezone))
                month = previous_month(local_today(building.timezone, now))
                if (
                    billing_due(local_now)
                    and await session.get(BillingRun, (building.id, month)) is None
                ):
                    summary = await generate_month(session, building, month, None, automatic=True)
                    done["generated"] += summary.created
                done["overdue"] += await mark_overdue(session, building, now)
        except BillingError as err:
            log.warning("building %s: %s", code, err)
        except Exception:
            log.exception("building %s: job failed, trying again next round", code)
    return done


async def run() -> None:
    log.info("worker started: monthly bills on the 1st at %02d:00 building time", BILLING_HOUR)
    while True:
        started = time.monotonic()
        try:
            done = await tick()
            if any(done.values()):
                log.info("round done: %s", done)
        except Exception:  # e.g. the database restarts: try again next round
            log.exception("round failed")
        else:
            HEARTBEAT.touch()
        await asyncio.sleep(max(1.0, TICK_S - (time.monotonic() - started)))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
