"""worker: the jobs that run on a schedule, not because someone clicked (M6, M7).

    python -m app.worker

Every minute, for every building, in the building's OWN time zone:

* **Monthly bills (FR-35).** From the 1st at 02:00, the drafts for the month
  that just ended are generated once (or issued straight away, if the owner
  switched on bills_auto_issue). If the worker was down on the 1st, it
  catches up when it starts again, up to the 5th. The owner can always
  generate a month by hand too (POST /buildings/{id}/bills/generate).
* **Overdue bills (FR-37).** Issued bills that are not fully paid after their
  due date become "overdue".
* **Bill reminders (FR-38, M7).** 3 days before the due date, on it, and 7
  days after it, each once.

Besides the minute loop (M7):

* **Alert engine (FR-58, FR-59).** Hears every live event and runs the
  alert rules on it at once; every 10 seconds it also checks quiet devices,
  high daily use, and closes alerts whose problem is gone (app/alerts/).
* **Sending (FR-60).** Every 5 seconds, queued emails go out; failures are
  tried again 3 times.
* **Audit check (FR-73).** Once a day, the whole audit chain is verified.

A plain loop instead of a scheduler library: the jobs are small, and a loop
is easy to read and to test (tick() takes the time as a parameter).
Healthy = it finished a round in the last 3 minutes (a file it touches).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from . import audit, db, heartbeat
from .ai.registry import TRAIN_HOUR, nightly
from .alerts.deliver import deliver_due
from .alerts.engine import AlertEngine
from .alerts.store import audit_signal_saved
from .billing.generate import BillingError, generate_month
from .billing.lifecycle import mark_overdue
from .billing.periods import local_today, previous_month
from .billing.reminders import send_reminders
from .models import AuditCheck, BillingRun, Building

log = logging.getLogger("aquapulse.worker")

TICK_S = 60
SEND_S = 5
AUDIT_EVERY = timedelta(days=1)
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
    done = {"generated": 0, "overdue": 0, "reminders": 0}
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
                done["reminders"] += await send_reminders(session, building, now)
        except BillingError as err:
            log.warning("building %s: %s", code, err)
        except Exception:
            log.exception("building %s: job failed, trying again next round", code)
    done["ai_models"] = await nightly_ai(buildings, now)
    done["audit_checks"] = await daily_audit_check(now)
    return done


# building id -> the building day the AI job last ran. Kept in memory so that a
# building with too little data to train is not read again every minute; after
# a restart it simply tries once more.
_ai_tried: dict[uuid.UUID, date] = {}


async def nightly_ai(buildings: list[Building], now: datetime) -> int:
    """Nightly AI training and unusual-use flags (FR-64, FR-65, FR-67)."""
    trained = 0
    for building_id, code, tz in [(b.id, b.code, b.timezone) for b in buildings]:
        today = local_today(tz, now)
        if _ai_tried.get(building_id) == today:
            continue
        try:
            async with db.sessions()() as session, session.begin():
                building = await session.get(Building, building_id)
                assert building is not None
                done = await nightly(session, building, now)
            if done or now.astimezone(ZoneInfo(tz)).hour >= TRAIN_HOUR:
                _ai_tried[building_id] = today
            trained += done.get("models", 0)
        except Exception:
            log.exception("building %s: AI training failed, trying again tomorrow", code)
            _ai_tried[building_id] = today
    return trained


async def daily_audit_check(now: datetime) -> int:
    """Verify the audit chain if the last daily check is a day old (FR-73)."""
    async with db.sessions()() as session, session.begin():
        last = await session.scalar(
            select(func.max(AuditCheck.at)).where(AuditCheck.requested_by.is_(None))
        )
        if last is not None and now - last < AUDIT_EVERY:
            return 0
        check = await audit.check_chain(session, at=now)
        await audit_signal_saved(session, check, now)
        if check.ok:
            log.info("audit chain OK: %d entries in %.1f s", check.entries, check.seconds)
        else:
            log.error("audit chain BROKEN at entry %s", check.first_broken_id)
    return 1


async def send_forever() -> None:
    while True:
        try:
            done = await deliver_due(datetime.now(UTC))
            if any(done.values()):
                log.info("notifications: %s", done)
        except Exception:
            log.exception("sending failed, trying again")
        await asyncio.sleep(SEND_S)


async def billing_forever() -> None:
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
            try:  # the health page reads this one (FR-55)
                await heartbeat.beat("worker", {"last_round": {k: int(v) for k, v in done.items()}})
            except Exception:
                log.exception("heartbeat failed")
        await asyncio.sleep(max(1.0, TICK_S - (time.monotonic() - started)))


async def run() -> None:
    log.info("worker started: monthly bills on the 1st at %02d:00 building time", BILLING_HOUR)
    engine = AlertEngine()
    await engine.start()
    # Five jobs side by side; if one of them crashes, the worker stops and
    # Docker starts it again (restart: unless-stopped).
    async with asyncio.TaskGroup() as jobs:
        jobs.create_task(billing_forever())
        jobs.create_task(engine.listen())
        jobs.create_task(engine.consume())
        jobs.create_task(engine.sweep_forever())
        jobs.create_task(send_forever())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
