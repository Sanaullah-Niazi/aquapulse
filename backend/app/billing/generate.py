"""Monthly bill generation (FR-35): meter readings + tariff -> draft bills.

For one building and one finished month:

1. Find every billing contact's days in each home (periods.segments).
2. Split those days by tariff version (periods.runs).
3. Add up the litres of each run from the 15-minute totals (consumption_15m),
   using the building's own time zone for "midnight".
4. Let the engine calculate the lines (engine.calculate), and save them as a
   DRAFT bill, with the meter readings at the start and end.

Safe to run again (criterion 1): a draft is recalculated in place (useful
after late readings), an issued bill is never touched, and a bill that was
voided gets a new draft that says which bill it replaces.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..models import Bill, BillingRun, BillLine, Building, Currency, Home, Occupancy, User
from ..models import Tariff as TariffRow
from . import engine
from .lifecycle import Actor, announce, issue
from .periods import (
    NoTariffError,
    Stay,
    Version,
    local_midnight,
    local_today,
    month_days,
    month_end,
    runs,
    segments,
)

log = logging.getLogger("aquapulse.billing")


class BillingError(Exception):
    """Generation is not possible (the API answers 409)."""


@dataclass
class Summary:
    month: date
    created: int = 0  # new drafts
    updated: int = 0  # drafts recalculated
    replaced: int = 0  # new drafts for voided bills (counted in created too)
    kept: int = 0  # already issued: never changed
    issued: int = 0  # auto-issued (building setting)
    problems: list[str] = field(default_factory=list)
    bill_ids: list[uuid.UUID] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "month": self.month.isoformat(),
            "created": self.created,
            "updated": self.updated,
            "replaced": self.replaced,
            "kept": self.kept,
            "issued": self.issued,
            "problems": self.problems,
        }


def to_engine(row: TariffRow, label: str = "") -> engine.Tariff:
    """A tariff version from the database, in the engine's own shape."""
    return engine.Tariff(
        slabs=tuple(engine.Slab(s.from_l, s.to_l, s.rate_per_kl) for s in row.slabs),
        charges=tuple(engine.Charge(c.name, c.kind, c.value) for c in row.charges),  # type: ignore[arg-type]
        label=label,
    )


LITRES_SQL = text(
    "SELECT COALESCE(SUM(litres), 0)::numeric(14,3) FROM consumption_15m "
    "WHERE home_id = :home AND bucket >= :start AND bucket < :end"
)
READING_BEFORE_SQL = text(
    "SELECT total_l::numeric(14,3) FROM telemetry WHERE home_id = :home AND time < :at "
    "ORDER BY time DESC LIMIT 1"
)
FIRST_READING_SQL = text(
    "SELECT total_l::numeric(14,3) FROM telemetry "
    "WHERE home_id = :home AND time >= :start AND time < :end ORDER BY time LIMIT 1"
)


async def litres_between(
    session: AsyncSession, home_id: uuid.UUID, start: date, end: date, timezone: str
) -> Decimal:
    """Litres used from the start of `start` to the end of `end`, building time."""
    return await session.scalar(
        LITRES_SQL,
        {
            "home": home_id,
            "start": local_midnight(start, timezone),
            # The next midnight, not "+24 hours": a day with a clock change is 23 or 25 h.
            "end": local_midnight(end + timedelta(days=1), timezone),
        },
    )


async def meter_readings(
    session: AsyncSession, home_id: uuid.UUID, start: date, end: date, timezone: str
) -> tuple[Decimal | None, Decimal | None]:
    """The meter's counter when the period started and when it ended."""
    begin = local_midnight(start, timezone)
    finish = local_midnight(end + timedelta(days=1), timezone)
    first = await session.scalar(READING_BEFORE_SQL, {"home": home_id, "at": begin})
    if first is None:  # the meter started during the period
        first = await session.scalar(
            FIRST_READING_SQL, {"home": home_id, "start": begin, "end": finish}
        )
    last = await session.scalar(READING_BEFORE_SQL, {"home": home_id, "at": finish})
    return first, (last if first is not None else None)


def check_month(building: Building, month: date, now: datetime | None = None) -> None:
    if month.day != 1:
        raise BillingError("Give the month as its first day, e.g. 2026-09-01")
    if month_end(month) >= local_today(building.timezone, now):
        raise BillingError("That month is not over yet in the building's time zone")


async def generate_month(
    session: AsyncSession,
    building: Building,
    month: date,
    actor: Actor | None,
    *,
    automatic: bool = False,
) -> Summary:
    """Make or refresh the draft bills of one building for one finished month."""
    check_month(building, month)
    summary = Summary(month)
    currency = await session.get(Currency, building.currency_code)
    assert currency is not None  # a foreign key guarantees it

    # Only versions in the building's currency count: if the currency was changed
    # (allowed only before the first bill, FR-20), the old rates mean nothing.
    versions_rows = list(
        await session.scalars(
            select(TariffRow)
            .where(
                TariffRow.building_id == building.id,
                TariffRow.currency_code == building.currency_code,
            )
            .order_by(TariffRow.version)
        )
    )
    by_id = {t.id: t for t in versions_rows}
    versions = [Version(t.id, t.version, t.effective_from) for t in versions_rows]
    several_versions = len(versions) > 1

    homes = await session.scalars(
        select(Home).where(
            Home.building_id == building.id,
            # Homes archived during the month still get their bill.
            or_(Home.archived_at.is_(None),
                Home.archived_at >= local_midnight(month, building.timezone)),
        ).order_by(Home.unit_no)
    )  # fmt: skip
    for home in homes:
        rows = await session.execute(
            select(Occupancy, User.full_name)
            .join(User, User.id == Occupancy.user_id)
            .where(Occupancy.home_id == home.id)
        )
        names = {}
        stays = []
        for occupancy, name in rows:
            names[occupancy.user_id] = name
            stays.append(
                Stay(occupancy.user_id, occupancy.move_in, occupancy.move_out,
                     occupancy.is_billing_contact)
            )  # fmt: skip
        for segment in segments(month, stays):
            await _bill_segment(
                session, building, currency, home, segment, names[segment.user_id],
                month, versions, by_id, several_versions, summary,
            )  # fmt: skip

    if building.bills_auto_issue:
        for bill in await session.scalars(
            select(Bill).where(Bill.id.in_(summary.bill_ids), Bill.status == "draft")
        ):
            await issue(session, bill, building, actor)
            summary.issued += 1

    await _remember_run(session, building, month, automatic, summary)
    await audit.record(
        session,
        "bill.generated",
        actor_id=actor.id if actor else None,
        actor_role=actor.role if actor else "system",
        ip=actor.ip if actor else None,
        target_type="building",
        target_id=building.id,
        building_id=building.id,
        details=summary.as_dict() | {"automatic": automatic},
    )
    log.info("bills for %s, %s: %s", building.code, month, summary.as_dict())
    return summary


async def _bill_segment(
    session: AsyncSession,
    building: Building,
    currency: Currency,
    home: Home,
    segment,
    bill_to: str,
    month: date,
    versions: list[Version],
    by_id: dict[uuid.UUID, TariffRow],
    several_versions: bool,
    summary: Summary,
) -> None:
    existing = list(
        await session.scalars(
            select(Bill)
            .where(
                Bill.home_id == home.id,
                Bill.user_id == segment.user_id,
                Bill.period_start == segment.start,
            )
            .order_by(Bill.created_at.desc())
        )
    )
    live = next((b for b in existing if b.status != "void"), None)
    if live is not None and live.status != "draft":
        summary.kept += 1  # issued bills are never changed
        summary.bill_ids.append(live.id)
        return

    try:
        tariff_runs = runs(segment.start, segment.end, versions)
    except NoTariffError as err:
        summary.problems.append(f"Unit {home.unit_no}: {err}. Add a tariff that starts earlier.")
        return

    parts: list[engine.Part] = []
    part_tariffs: list[uuid.UUID] = []
    for run in tariff_runs:
        row = by_id[run.version.id]
        label = f"Tariff v{row.version}" if several_versions else ""
        litres = await litres_between(session, home.id, run.start, run.end, building.timezone)
        parts.append(engine.Part(to_engine(row, label), litres, run.days))
        part_tariffs.append(row.id)
    days_in_month = month_days(month)
    result = engine.calculate(parts, days_in_month, currency.minor_units)
    reading_start, reading_end = await meter_readings(
        session, home.id, segment.start, segment.end, building.timezone
    )

    if live is None:
        bill = Bill(
            building_id=building.id,
            home_id=home.id,
            user_id=segment.user_id,
            period_start=segment.start,
            status="draft",
            amount_paid=Decimal(0),
            replaces_bill_id=existing[0].id if existing else None,
        )
        session.add(bill)
        summary.created += 1
        summary.replaced += bool(existing)
    else:
        bill = live
        bill.lines.clear()  # recalculated below
        summary.updated += 1
    bill.bill_to, bill.unit_no = bill_to, home.unit_no
    bill.month, bill.period_end, bill.month_days = month, segment.end, days_in_month
    bill.currency_code = building.currency_code
    bill.litres = result.litres
    bill.reading_start, bill.reading_end = reading_start, reading_end
    bill.subtotal, bill.tax_total, bill.total = result.subtotal, result.tax_total, result.total
    bill.updated_at = datetime.now(UTC)
    bill.lines.extend(
        BillLine(
            position=n,
            kind=line.kind,
            description=line.description,
            quantity_l=line.quantity_l,
            rate=line.rate,
            amount=line.amount,
            tariff_id=part_tariffs[line.part],
        )
        for n, line in enumerate(result.lines)
    )
    await session.flush()
    summary.bill_ids.append(bill.id)
    await announce(session, bill)


async def _remember_run(
    session: AsyncSession, building: Building, month: date, automatic: bool, summary: Summary
) -> None:
    run = await session.get(BillingRun, (building.id, month))
    if run is None:
        session.add(
            BillingRun(
                building_id=building.id,
                month=month,
                automatic=automatic,
                run_at=datetime.now(UTC),
                summary=summary.as_dict(),
            )
        )
    else:
        run.run_at, run.summary = datetime.now(UTC), summary.as_dict()
        run.automatic = run.automatic or automatic


async def has_bills(session: AsyncSession, building_id: uuid.UUID) -> bool:
    """FR-20: once a building has bills, its currency can no longer change."""
    count = await session.scalar(
        select(func.count()).select_from(Bill).where(Bill.building_id == building_id)
    )
    return bool(count)
