"""Extra numbers for the "My home" and "My usage" screens (M8).

* FR-49: this month against last month, and against the building's average
  per person. The average is only shown when at least 3 homes are occupied,
  so nobody can work out a single neighbour's use from it.
* FR-48: "your bill so far this month", worked out by the same engine as the
  real bill (FR-34), from the first of the month (or move-in) until today.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing import engine
from ..billing.generate import litres_between, to_engine
from ..billing.money import format_money
from ..billing.periods import (
    NoTariffError,
    Version,
    local_midnight,
    local_today,
    month_days,
    month_end,
    month_of,
    previous_month,
    runs,
)
from ..db import get_session
from ..deps import Caller, require, resident_since, visible_home
from ..models import Building, Currency, Home, Occupancy, Tariff
from ..schemas import BillEstimateOut, ComparisonOut

router = APIRouter(tags=["live data and consumption"])
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]
OwnerOrResident = Annotated[Caller, Depends(require("owner", "resident"))]

MIN_HOMES_FOR_AVERAGE = 3  # FR-49 criterion 1

BUILDING_LITRES_SQL = text(
    "SELECT COALESCE(SUM(litres), 0) FROM consumption_15m "
    "WHERE home_id = ANY(:homes) AND bucket >= :start AND bucket < :end"
)


def _occupied_on(day: date):
    return (Occupancy.move_in <= day, (Occupancy.move_out.is_(None)) | (Occupancy.move_out >= day))


async def _home(session: AsyncSession, caller: Caller, home_id: uuid.UUID):
    home = await visible_home(session, caller, home_id)
    building = await session.get(Building, home.building_id)
    assert building is not None
    return home, building, await resident_since(session, caller, home)


async def _litres(
    session: AsyncSession, home: Home, building: Building, first: date, last: date
) -> float:
    if last < first:
        return 0.0
    return float(await litres_between(session, home.id, first, last, building.timezone))


@router.get("/homes/{home_id}/comparison", response_model=ComparisonOut, responses={404: {}})
async def comparison(
    home_id: uuid.UUID,
    session: Session,
    caller: OwnerOrResident,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")] = None,
):
    """This home's litres in a month, last month, and the building average per
    person (only with 3 or more occupied homes). Months not over yet count until today."""
    home, building, since = await _home(session, caller, home_id)
    today = local_today(building.timezone)
    first = date.fromisoformat(f"{month}-01") if month else month_of(today)
    if first > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That month has not started")
    last = min(month_end(first), today)
    before = previous_month(first)

    # A resident only sees their own time in the home (FR-49 criterion 2).
    mine_from = max(first, since) if since else first
    mine = await _litres(session, home, building, mine_from, last)
    previous = None
    if since is None or since <= before:
        previous = await _litres(session, home, building, max(before, since or before),
                                 month_end(before))  # fmt: skip

    # Occupied homes (someone lives there on the last day counted) and the
    # number of people the owner entered for each (at least 1).
    occupied = select(Occupancy.home_id).where(*_occupied_on(last)).scalar_subquery()
    rows = await session.execute(
        select(Home.id, Home.occupants).where(
            Home.building_id == building.id, Home.archived_at.is_(None), Home.id.in_(occupied)
        )
    )
    people = {home_id: max(count, 1) for home_id, count in rows}
    in_home = people.get(home.id, 0)
    average = None
    if len(people) >= MIN_HOMES_FOR_AVERAGE:
        total = await session.scalar(
            BUILDING_LITRES_SQL,
            {
                "homes": list(people),
                "start": local_midnight(first, building.timezone),
                "end": local_midnight(last + timedelta(days=1), building.timezone),
            },
        )
        average = round(float(total) / sum(people.values()), 1)
    return ComparisonOut(
        month=first,
        until=last,
        litres=round(mine, 1),
        previous_month_litres=None if previous is None else round(previous, 1),
        people_in_home=in_home,
        litres_per_person=round(mine / in_home, 1) if in_home else None,
        building_litres_per_person=average,
        occupied_homes=len(people),
    )


@router.get("/homes/{home_id}/bill-estimate", response_model=BillEstimateOut, responses={404: {}})
async def bill_estimate(home_id: uuid.UUID, session: Session, caller: OwnerOrResident):
    """What this month's water costs so far, with the building's real tariff.
    Not a bill: the real one is made after the month ends (FR-35)."""
    home, building, since = await _home(session, caller, home_id)
    today = local_today(building.timezone, datetime.now(UTC))
    first = max(month_of(today), since) if since else month_of(today)
    currency = await session.get(Currency, building.currency_code)
    assert currency is not None
    out = BillEstimateOut(month=month_of(today), from_=first, to=today, currency_code=currency.code)
    tariffs = list(
        await session.scalars(
            select(Tariff)
            .where(Tariff.building_id == building.id, Tariff.currency_code == currency.code)
            .order_by(Tariff.version)
        )
    )
    by_id = {t.id: t for t in tariffs}
    try:
        tariff_runs = runs(
            first, today, [Version(t.id, t.version, t.effective_from) for t in tariffs]
        )
    except NoTariffError:
        out.message = "The owner has not set water rates for this month yet."
        return out
    parts = [
        engine.Part(
            to_engine(by_id[run.version.id]),
            await litres_between(session, home.id, run.start, run.end, building.timezone),
            run.days,
        )
        for run in tariff_runs
    ]
    result = engine.calculate(parts, month_days(out.month), currency.minor_units)
    out.litres = float(result.litres)
    out.total = result.total
    out.total_text = format_money(result.total, currency.symbol, currency.minor_units)
    days = (today - first).days + 1
    out.message = f"So far this month ({days} day{'s' if days != 1 else ''}). Not a bill."
    return out
