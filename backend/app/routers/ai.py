"""AI insights, forecasts and the model registry (FR-63 to FR-67)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import forecast as fc
from ..ai.projection import projected_bill
from ..ai.registry import TRAIN_DAYS, forecast_method, history_from, home_days
from ..alerts.rules import AI_RULES
from ..billing.money import format_money
from ..billing.periods import local_today
from ..db import get_session
from ..deps import Caller, require, resident_since, visible_building, visible_home
from ..models import AiModel, Alert, Building, Currency, Home
from ..schemas import (
    AiModelOut,
    BillProjectionOut,
    DayLitresOut,
    ForecastDayOut,
    ForecastOut,
    InsightOut,
    MonthEndOut,
)
from .alerts import _resident_can_see

router = APIRouter(tags=["ai"])
Session = Annotated[AsyncSession, Depends(get_session)]
AnyRole = Annotated[Caller, Depends(require("owner", "sysadmin", "resident"))]
OwnerOrAdmin = Annotated[Caller, Depends(require("owner", "sysadmin"))]


def insight(alert: Alert) -> InsightOut:
    details = dict(alert.details or {})
    confidence = details.pop("confidence", None)
    high = bool(details.pop("high", False))
    return InsightOut(
        alert_id=alert.id,
        kind=alert.rule,
        title=alert.title,
        severity=alert.severity,
        state=alert.state,
        explanation=alert.message,
        confidence=confidence,
        high_confidence=high,
        opened_at=alert.opened_at,
        last_seen_at=alert.last_seen_at,
        resolved_at=alert.resolved_at,
        evidence=details,
    )


@router.get("/homes/{home_id}/insights", response_model=list[InsightOut], responses={404: {}})
async def home_insights(
    home_id: uuid.UUID,
    session: Session,
    caller: AnyRole,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
):
    """Leak and burst findings of the last `days` days, newest first, each with
    a plain-language reason and the readings behind it."""
    home = await visible_home(session, caller, home_id)
    query = (
        select(Alert)
        .where(
            Alert.home_id == home.id,
            Alert.rule.in_(AI_RULES),
            Alert.opened_at >= datetime.now(UTC) - timedelta(days=days),
        )
        .order_by(Alert.opened_at.desc())
        .limit(100)
    )
    if caller.role == "resident":
        query = query.where(_resident_can_see(caller.id))
    return [insight(a) for a in await session.scalars(query)]


# ----- forecasts (FR-65) -----


def _explain(f: fc.Forecast, history: list[float], first_day: date) -> str:
    """Why the forecast looks the way it does, from the numbers (FR-66)."""
    used = [v for v in history if v > 0]
    if not used:
        return "No water use recorded yet, so the forecast is 0 L."
    if f.method == "seasonal_naive":
        head = (
            f"Only {f.history_days} days of history: each day is forecast as the same "
            "weekday last week. With 3 weeks of history a model that learns the weekly "
            "pattern (Holt-Winters) takes over."
        )
    else:
        head = (
            f"Learned from {f.history_days} days: the usual level and the weekly pattern, "
            "with recent days counting most (Holt-Winters)."
        )
    weekend = [v for i, v in enumerate(history) if (first_day + timedelta(days=i)).weekday() >= 5]
    weekday = [v for i, v in enumerate(history) if (first_day + timedelta(days=i)).weekday() < 5]
    if weekend and weekday and sum(weekday) > 0:
        ratio = (sum(weekend) / len(weekend)) / (sum(weekday) / len(weekday))
        if abs(ratio - 1) >= 0.05:
            more = "more" if ratio > 1 else "less"
            head += f" Weekends use about {abs(ratio - 1):.0%} {more} than weekdays here."
    return head + " On 8 days out of 10 the real litres should fall inside the shaded range."


async def _forecast(
    session: AsyncSession,
    building: Building,
    history: list[float],
    first: date,
    today: date,
    method: str | None,
) -> tuple[fc.Forecast, dict[date, float], dict[date, float], dict[date, float]]:
    f = fc.seasonal_naive(history) if method == "seasonal_naive" else fc.forecast(history)
    month_last = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    mean, low, high = {}, {}, {}
    day, i = today, 0
    while day <= month_last:
        j = i % len(f.mean)
        mean[day], low[day], high[day] = f.mean[j], f.low[j], f.high[j]
        day, i = day + timedelta(days=1), i + 1
    return f, mean, low, high


def _out(f, history, first, today, mean, low, high, bills, currency, explanation) -> ForecastOut:
    so_far = sum(
        v for i, v in enumerate(history)
        if (first + timedelta(days=i)) >= today.replace(day=1)
    )  # fmt: skip
    last_day = max(mean) if mean else today
    total, lo, hi = bills
    return ForecastOut(
        method=f.method,
        history_days=f.history_days,
        explanation=explanation,
        recent=[
            DayLitresOut(day=first + timedelta(days=i), litres=round(v, 1))
            for i, v in list(enumerate(history))[-14:]
        ],
        days=[
            ForecastDayOut(
                day=today + timedelta(days=i),
                litres=round(m, 1),
                low=round(lo_, 1),
                high=round(hi_, 1),
            )
            for i, (m, lo_, hi_) in enumerate(zip(f.mean, f.low, f.high, strict=True))
        ],
        month_end=MonthEndOut(
            last_day=last_day,
            litres=round(so_far + sum(mean.values()), 1),
            low=round(so_far + sum(low.values()), 1),
            high=round(so_far + sum(high.values()), 1),
        ),
        bill=BillProjectionOut(
            currency_code=currency.code,
            total=total.total,
            total_text=total.text,
            low_text=lo.text,
            high_text=hi.text,
            message=total.message,
        ),
    )


@router.get("/homes/{home_id}/forecast", response_model=ForecastOut, responses={404: {}})
async def home_forecast(home_id: uuid.UUID, session: Session, caller: AnyRole):
    """The next 7 days of litres with an 80% range, and the month-end total and
    bill estimate, made with the building's real tariff (FR-65). Residents:
    from their move-in day only."""
    home = await visible_home(session, caller, home_id)
    building = await session.get(Building, home.building_id)
    assert building is not None
    since = await resident_since(session, caller, home)
    today = local_today(building.timezone, datetime.now(UTC))
    first = today - timedelta(days=TRAIN_DAYS)
    if since and since > first:
        first = since
    yesterday = today - timedelta(days=1)
    days = await home_days(session, building, first, yesterday) if first <= yesterday else []
    history, first = (
        history_from(days, first, yesterday, str(home.id)) if first <= yesterday else ([], first)
    )
    method = await forecast_method(session, building.id)
    f, mean, low, high = await _forecast(session, building, history, first, today, method)
    bills = [
        await projected_bill(session, home, building, today, by_day, since)
        for by_day in (mean, low, high)
    ]
    currency = await session.get(Currency, building.currency_code)
    return _out(
        f, history, first, today, mean, low, high, bills, currency, _explain(f, history, first)
    )


@router.get("/buildings/{building_id}/forecast", response_model=ForecastOut, responses={404: {}})
async def building_forecast(building_id: uuid.UUID, session: Session, caller: OwnerOrAdmin):
    """The whole building: total litres forecast, and the sum of every home's
    month-end bill estimate."""
    building = await visible_building(session, caller, building_id)
    today = local_today(building.timezone, datetime.now(UTC))
    first, yesterday = today - timedelta(days=TRAIN_DAYS), today - timedelta(days=1)
    days = await home_days(session, building, first, yesterday)
    history, first = history_from(days, first, yesterday)
    method = await forecast_method(session, building.id)
    f, mean, low, high = await _forecast(session, building, history, first, today, method)
    currency = await session.get(Currency, building.currency_code)
    assert currency is not None
    homes = list(
        await session.scalars(
            select(Home).where(Home.building_id == building.id, Home.archived_at.is_(None))
        )
    )
    # Each home's own forecast through the real tariff, then added up: the
    # slabs make a bill depend on each home's litres, not on the building total.
    totals: list[Decimal | None] = [Decimal(0), Decimal(0), Decimal(0)]
    message = "An estimate for the whole building this month: the sum of every home's. Not a bill."
    for home in homes:
        series, _ = history_from(days, first, yesterday, str(home.id))
        _, h_mean, h_low, h_high = await _forecast(session, building, series, first, today, method)
        for i, by_day in enumerate((h_mean, h_low, h_high)):
            projection = await projected_bill(session, home, building, today, by_day)
            if projection.total is None:
                message = projection.message
                totals = [None, None, None]
                break
            totals[i] += projection.total
        if totals[0] is None:
            break

    def text_of(v: Decimal | None) -> str | None:
        return None if v is None else format_money(v, currency.symbol, currency.minor_units)

    sums = [_Sum(v, text_of(v), message) for v in totals]
    return _out(
        f, history, first, today, mean, low, high, sums, currency, _explain(f, history, first)
    )


class _Sum:
    def __init__(self, total, text, message):
        self.total, self.text, self.message = total, text, message


# ----- the model registry (FR-67) -----


@router.get("/buildings/{building_id}/ai/models", response_model=list[AiModelOut],
            responses={404: {}})  # fmt: skip
async def ai_models(building_id: uuid.UUID, session: Session, caller: OwnerOrAdmin):
    """Every trained version, newest first, with its scores and whether it is live."""
    building = await visible_building(session, caller, building_id)
    query = (
        select(AiModel)
        .where(AiModel.building_id == building.id)
        .order_by(AiModel.trained_at.desc(), AiModel.kind)
        .limit(60)
    )
    return list(await session.scalars(query))
