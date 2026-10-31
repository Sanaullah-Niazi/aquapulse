"""The month-end bill estimate (FR-65 criterion 3): the same billing engine
as the real bills (FR-34), fed with the litres used so far this month plus
the forecast for the rest of it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing import engine
from ..billing.generate import litres_between, to_engine
from ..billing.money import format_money
from ..billing.periods import NoTariffError, Version, month_days, runs
from ..models import Building, Currency, Home, Tariff


@dataclass
class Projection:
    total: Decimal | None
    text: str | None
    message: str


async def projected_bill(
    session: AsyncSession,
    home: Home,
    building: Building,
    today: date,
    forecast_by_day: dict[date, float],
    since: date | None = None,
) -> Projection:
    """The bill for this whole month: real litres up to yesterday, forecast
    litres from today to the month's last day. `since`: a resident's move-in
    day, when it is in this month (their bill starts there)."""
    month = today.replace(day=1)
    first = max(month, since) if since else month
    last = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    currency = await session.get(Currency, building.currency_code)
    assert currency is not None
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
            first, last, [Version(t.id, t.version, t.effective_from) for t in tariffs]
        )
    except NoTariffError:
        return Projection(None, None, "The owner has not set water rates for this month yet.")
    parts = []
    for run in tariff_runs:
        litres = Decimal(0)
        if run.start < today:
            litres += await litres_between(
                session, home.id, run.start, min(run.end, today - timedelta(days=1)),
                building.timezone,
            )  # fmt: skip
        day = max(run.start, today)
        while day <= run.end:
            litres += Decimal(str(round(forecast_by_day.get(day, 0.0), 3)))
            day += timedelta(days=1)
        parts.append(engine.Part(to_engine(by_id[run.version.id]), litres, run.days))
    result = engine.calculate(parts, month_days(month), currency.minor_units)
    return Projection(
        result.total,
        format_money(result.total, currency.symbol, currency.minor_units),
        "An estimate for the whole month: real use so far plus the forecast. Not a bill.",
    )
