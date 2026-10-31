"""Nightly AI training, the model registry and unusual-use alerts (FR-64, FR-65, FR-67).

Once a night per building (from 01:00 building time), the worker:

1. reads the last 60 days of each home's water use;
2. trains a new unusual-use model (Isolation Forest) and checks it on the
   last 14 days: how many real days it flags (false alarms), and how many
   copies of those days with the water doubled it catches;
3. back-tests the forecast on the last 3 weeks (Holt-Winters against the
   simple "same weekday last week");
4. saves both as new versions in `ai_models`. A version goes live only if it
   is not worse than the live one (`unusual.better_or_equal`, and for the
   forecast a MAPE no more than 2 points higher);
5. scores yesterday with the live unusual-use model and opens an
   "Unusual water use" alert (info) for each unusual home-day, with the
   model version and the features that made it unusual.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..alerts.detect import Signal
from ..alerts.rules import settings_for
from ..alerts.store import apply
from ..billing.periods import local_midnight
from ..models import AiModel, Building, Home
from . import forecast as fc
from . import unusual

log = logging.getLogger("aquapulse.ai")

TRAIN_DAYS = 60
CHECK_DAYS = 14
TRAIN_HOUR = 1  # building time
FORECAST_TOLERANCE = 0.02

HOURLY_SQL = text("""
    SELECT home_id, (bucket AT TIME ZONE :tz)::date AS day,
           extract(hour FROM bucket AT TIME ZONE :tz)::int AS hour, sum(litres) AS litres
    FROM consumption_15m
    WHERE building_id = :building AND bucket >= :start AND bucket < :end
    GROUP BY 1, 2, 3
""")

NIGHT_SQL = text("""
    SELECT home_id, (time AT TIME ZONE :tz)::date AS day, min(flow_lpm) AS low
    FROM telemetry
    WHERE building_id = :building AND time >= :start AND time < :end
      AND extract(hour FROM time AT TIME ZONE :tz) >= 2
      AND extract(hour FROM time AT TIME ZONE :tz) < 5
    GROUP BY 1, 2
""")


async def home_days(
    session: AsyncSession, building: Building, first: date, last: date
) -> list[unusual.Day]:
    """Each home's litres per day (and per hour) and its lowest night flow."""
    args = {
        "tz": building.timezone,
        "building": building.id,
        "start": local_midnight(first, building.timezone),
        "end": local_midnight(last + timedelta(days=1), building.timezone),
    }
    hourly: dict[tuple[str, date], list[float]] = {}
    for r in await session.execute(HOURLY_SQL, args):
        hourly.setdefault((str(r.home_id), r.day), [0.0] * 24)[r.hour] += float(r.litres)
    nights = {(str(r.home_id), r.day): float(r.low) for r in await session.execute(NIGHT_SQL, args)}
    return [
        unusual.Day(home, day, sum(hours), tuple(hours), nights.get((home, day), 0.0))
        for (home, day), hours in sorted(hourly.items())
    ]


def daily_series(days: list[unusual.Day], first: date, last: date, home: str | None = None):
    """Litres per day from `first` to `last` (0 for a day without data), for
    one home or (home=None) the whole building."""
    totals: dict[date, float] = {}
    for d in days:
        if home is None or d.home == home:
            totals[d.day] = totals.get(d.day, 0.0) + d.litres
    out, day = [], first
    while day <= last:
        out.append(totals.get(day, 0.0))
        day += timedelta(days=1)
    return out


def history_from(
    days: list[unusual.Day], first: date, last: date, home: str | None = None
) -> tuple[list[float], date]:
    """Like daily_series, but starting at the first day with data: a home that
    was added 10 days ago has 10 days of history, not 50 empty days and 10
    real ones. Returns the litres and their first day."""
    series = daily_series(days, first, last, home)
    skip = next((i for i, v in enumerate(series) if v > 0), len(series))
    return series[skip:], first + timedelta(days=skip)


async def live_model(session: AsyncSession, building_id: uuid.UUID, kind: str) -> AiModel | None:
    return await session.scalar(
        select(AiModel).where(
            AiModel.building_id == building_id, AiModel.kind == kind, AiModel.live
        )
    )


async def save_version(
    session: AsyncSession,
    building: Building,
    kind: str,
    now: datetime,
    first: date,
    last: date,
    rows: int,
    metrics: dict[str, Any],
    params: dict[str, Any],
    goes_live: bool,
    note: str,
    blob: bytes | None = None,
) -> AiModel:
    version = (
        await session.scalar(
            select(func.max(AiModel.version)).where(
                AiModel.building_id == building.id, AiModel.kind == kind
            )
        )
        or 0
    ) + 1
    if goes_live:
        await session.execute(
            update(AiModel)
            .where(AiModel.building_id == building.id, AiModel.kind == kind, AiModel.live)
            .values(live=False)
        )
        await session.flush()
    model = AiModel(
        building_id=building.id,
        kind=kind,
        version=version,
        trained_at=now,
        data_from=first,
        data_to=last,
        rows=rows,
        metrics=metrics,
        params=params,
        live=goes_live,
        note=note[:300],
        blob=blob,
    )
    session.add(model)
    await session.flush()
    return model


# ----- unusual use (FR-64) -----


async def train_unusual(
    session: AsyncSession,
    building: Building,
    days: list[unusual.Day],
    now: datetime,
    first: date,
    last: date,
    *,
    flag_quantile: float = unusual.FLAG_QUANTILE,
) -> AiModel | None:
    rows = unusual.rows_from(days)
    if len(rows) < unusual.MIN_TRAIN_ROWS:
        return None
    model = await asyncio.to_thread(unusual.train, rows, 0, flag_quantile)
    holdout = [r for r in rows if r.day > last - timedelta(days=CHECK_DAYS)]
    metrics = unusual.synthetic_check(model, holdout)
    live = await live_model(session, building.id, "unusual_use")
    ok = unusual.better_or_equal(metrics, live.metrics if live else None)
    note = (
        "live: not worse than the version before"
        if ok and live
        else "live: first version"
        if ok
        else "kept out: worse than the live version"
        if live
        else "kept out: does not pass the minimum check"
    )
    return await save_version(
        session, building, "unusual_use", now, first, last, len(rows), metrics,
        {"flag_quantile": flag_quantile, "features": [unusual.FEATURES[i] for i in unusual.MODEL]},
        ok, note, model.dumps(),
    )  # fmt: skip


async def flag_yesterday(
    session: AsyncSession, building: Building, days: list[unusual.Day], yesterday: date,
    now: datetime,
) -> int:  # fmt: skip
    if not settings_for(building.alert_rules, building.alert_recipients).on("unusual_use"):
        return 0  # the owner switched the rule off (FR-62)
    live = await live_model(session, building.id, "unusual_use")
    if live is None or live.blob is None:
        return 0
    model = unusual.UnusualModel.loads(live.blob)
    rows = [r for r in unusual.rows_from(days) if r.day == yesterday]
    signals = []
    for row, score in model.flags(rows):
        top = model.top_features(row)
        key = f"unusual_use:{row.home}:{row.day.isoformat()}"
        details = {
            "model_version": live.version,
            "score": round(score, 4),
            "day": row.day.isoformat(),
            "litres": round(row.get("litres"), 1),
            "usual_7d": round(row.usual_7d, 1),
            "top_features": top,
        }
        common = {"building_id": str(building.id), "home_id": row.home}
        signals.append(
            Signal(
                "unusual_use",
                key,
                True,
                value=round(row.get("litres"), 1),
                message=unusual.explain(row, top),
                details=details,
                **common,
            )
        )
        # A past day: the alert is closed again at once (it auto-resolves).
        signals.append(Signal("unusual_use", key, False, **common))  # fmt: skip
    if signals:
        await apply(session, signals, now)
    return len(signals) // 2


# ----- forecast (FR-65) -----


async def check_forecast(
    session: AsyncSession, building: Building, days: list[unusual.Day], now: datetime,
    first: date, last: date,
) -> AiModel | None:  # fmt: skip
    series, _ = history_from(days, first, last)
    if sum(1 for v in series if v > 0) < 28:
        return None
    test = await asyncio.to_thread(fc.backtest, series, max(28, len(series) - 21))
    method = "holt_winters" if test["mape"] <= test["naive_mape"] else "seasonal_naive"
    metrics = {**test, "method_mape": min(test["mape"], test["naive_mape"])}
    live = await live_model(session, building.id, "forecast")
    ok = live is None or metrics["method_mape"] <= live.metrics.get("method_mape", 1) + (
        FORECAST_TOLERANCE
    )
    note = f"{'live' if ok else 'kept out'}: {method.replace('_', ' ')} was more accurate"
    return await save_version(
        session, building, "forecast", now, first, last, len(series), metrics,
        {"method": method}, ok, note,
    )  # fmt: skip


async def forecast_method(session: AsyncSession, building_id: uuid.UUID) -> str | None:
    live = await live_model(session, building_id, "forecast")
    return live.params.get("method") if live else None


# ----- the nightly job -----


async def nightly(session: AsyncSession, building: Building, now: datetime) -> dict[str, int]:
    """Train, check, maybe promote, and flag yesterday, once per building day."""
    local = now.astimezone(ZoneInfo(building.timezone))
    today = local.date()
    if local.hour < TRAIN_HOUR:
        return {}
    last_trained = await session.scalar(
        select(func.max(AiModel.trained_at)).where(AiModel.building_id == building.id)
    )
    if last_trained is not None and last_trained.astimezone(local.tzinfo).date() >= today:
        return {}
    homes = await session.scalar(
        select(func.count())
        .select_from(Home)
        .where(Home.building_id == building.id, Home.archived_at.is_(None))
    )
    if not homes:
        return {}
    yesterday = today - timedelta(days=1)
    first = yesterday - timedelta(days=TRAIN_DAYS - 1)
    days = await home_days(session, building, first - timedelta(days=7), yesterday)
    done = {"models": 0, "unusual_days": 0}
    if await train_unusual(session, building, days, now, first, yesterday):
        done["models"] += 1
    if await check_forecast(session, building, days, now, first, yesterday):
        done["models"] += 1
    done["unusual_days"] = await flag_yesterday(session, building, days, yesterday, now)
    if done["models"]:
        log.info("AI models trained for %s: %s", building.code, done)
    return done
