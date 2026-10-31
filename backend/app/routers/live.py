"""Live readings and water consumption (FR-23, SRS section 8.4).

Litres come from the meter's cumulative counter (the ingestor turns it into
`delta_l` per reading), so a lost message never loses litres. Totals are
added up per 15 minutes by a TimescaleDB continuous aggregate and grouped
into hours, days or months here, in the building's own time zone: a "day"
is midnight to midnight where the building is, including DST changes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..alerts.rules import settings_for
from ..db import get_session
from ..deps import Caller, require, resident_since, visible_building, visible_home
from ..models import Building, Device, DeviceCommand, Home, Telemetry, ValveLock
from ..schemas import (
    CommandOut,
    ConsumptionOut,
    ConsumptionPoint,
    HomeLiveOut,
    HomeTotal,
    ReadingOut,
    ValveLockOut,
)

router = APIRouter(tags=["live data and consumption"])
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]
OwnerOrResident = Annotated[Caller, Depends(require("owner", "resident"))]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]

MAX_RANGE_DAYS = 400  # FR-23: any range up to 400 days
MAX_RAW_DAYS = 7  # raw readings: at most 7 days per call
BUCKETS = {"hour": "1 hour", "day": "1 day", "month": "1 month"}
Granularity = Literal["hour", "day", "month"]


def local_range(first: date, last: date, tz: str) -> tuple[datetime, datetime]:
    """[first 00:00, the day after `last` 00:00) in the building's zone, as UTC."""
    zone = ZoneInfo(tz)
    start = datetime.combine(first, time(), zone)
    end = datetime.combine(last + timedelta(days=1), time(), zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def check_range(first: date, last: date, max_days: int) -> None:
    if last < first:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "'to' is before 'from'")
    if (last - first).days + 1 > max_days:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Ask for at most {max_days} days at a time"
        )


def default_dates(first: date | None, last: date | None, tz: str) -> tuple[date, date]:
    today = datetime.now(ZoneInfo(tz)).date()
    last = last or today
    return first or last - timedelta(days=29), last


# The continuous aggregate `consumption_15m` (migration 0002) already holds
# the litres per home per 15 minutes; it also includes the newest readings
# that are not aggregated yet (materialized_only = false).
SERIES_SQL = text(
    """
    SELECT time_bucket(CAST(CAST(:width AS text) AS interval), bucket, CAST(:tz AS text))
               AS start,
           sum(litres) AS litres
    FROM consumption_15m
    WHERE {where} AND bucket >= :start AND bucket < :end
    GROUP BY 1 ORDER BY 1
    """
)


async def _series(
    session: AsyncSession, where: str, params: dict, granularity: str, tz: str, start, end
) -> list[ConsumptionPoint]:
    statement = text(SERIES_SQL.text.format(where=where))
    rows = await session.execute(
        statement,
        {**params, "width": BUCKETS[granularity], "tz": tz, "start": start, "end": end},
    )
    zone = ZoneInfo(tz)
    return [
        ConsumptionPoint(start=r.start.astimezone(zone), litres=round(r.litres or 0.0, 2))
        for r in rows
    ]


# ----- one home -----


async def _home_and_since(session, caller, home_id) -> tuple[Home, Building, date | None]:
    """The home (404 if not yours) and, for a resident, their move-in day."""
    home = await visible_home(session, caller, home_id)
    building = await session.get(Building, home.building_id)
    assert building is not None
    return home, building, await resident_since(session, caller, home)


def _start_of(day: date | None, tz: str) -> datetime | None:
    return None if day is None else local_range(day, day, tz)[0]


@router.get("/homes/{home_id}/live", response_model=HomeLiveOut, responses={404: {}})
async def home_live(home_id: uuid.UUID, session: Session, caller: OwnerOrResident):
    """Latest reading, the valve state the device reported, and any lock."""
    home, building, since = await _home_and_since(session, caller, home_id)
    rules = settings_for(building.alert_rules, building.alert_recipients)
    visible_from = _start_of(since, building.timezone)
    device = await session.scalar(
        select(Device).where(Device.home_id == home.id, Device.status == "active")
    )
    reading = None
    if device is not None:
        latest = await session.scalar(
            select(Telemetry)
            .where(Telemetry.home_id == home.id)
            .order_by(Telemetry.time.desc())
            .limit(1)
        )
        if latest is not None and (visible_from is None or latest.time >= visible_from):
            reading = ReadingOut.model_validate(latest, from_attributes=True)
    lock = await session.scalar(
        select(ValveLock).where(ValveLock.home_id == home.id, ValveLock.unlocked_at.is_(None))
    )
    pending = await session.scalar(
        select(DeviceCommand)
        .where(DeviceCommand.home_id == home.id, DeviceCommand.state.in_(("queued", "pending")))
        .order_by(DeviceCommand.created_at.desc())
        .limit(1)
    )
    return HomeLiveOut(
        home_id=home.id,
        device_id=device.id if device else None,
        online=bool(device and device.online),
        last_seen_at=device.last_seen_at if device else None,
        # The retained `state` message is the device's own word on the valve.
        valve=(device.state or {}).get("valve") if device else None,
        reading=reading,
        lock=ValveLockOut.model_validate(lock) if lock else None,
        pending_command=CommandOut.model_validate(pending) if pending else None,
        unit_no=home.unit_no,
        building_name=building.name,
        # The building's own pH limits (FR-62), for the Safe / Unsafe label (FR-48).
        ph_safe_low=rules.get("ph_unsafe", "low"),
        ph_safe_high=rules.get("ph_unsafe", "high"),
    )


@router.get(
    "/homes/{home_id}/telemetry", response_model=list[ReadingOut], responses={404: {}, 422: {}}
)
async def home_telemetry(
    home_id: uuid.UUID,
    session: Session,
    caller: OwnerOrResident,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    every: Annotated[
        int | None,
        Query(ge=60, le=86_400, description="Average per this many seconds (for charts)"),
    ] = None,
):
    """Raw readings, oldest first: at most 7 days per call (default: the last hour).
    With `every`, one averaged reading per period instead (a 7-day chart needs
    a few hundred points, not 60,000)."""
    home, building, since = await _home_and_since(session, caller, home_id)
    end = _aware(to) if to else datetime.now(UTC)
    start = _aware(from_) if from_ else end - timedelta(hours=1)
    if end < start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "'to' is before 'from'")
    if end - start > timedelta(days=MAX_RAW_DAYS):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Ask for at most {MAX_RAW_DAYS} days at a time"
        )
    visible_from = _start_of(since, building.timezone)
    if visible_from is not None:
        start = max(start, visible_from)  # a resident sees only their own stay
    if every is not None:
        return await _averaged(session, home.id, start, end, every)
    rows = await session.scalars(
        select(Telemetry)
        .where(Telemetry.home_id == home.id, Telemetry.time >= start, Telemetry.time < end)
        .order_by(Telemetry.time)
    )
    return [ReadingOut.model_validate(r, from_attributes=True) for r in rows]


async def _averaged(
    session: AsyncSession, home_id: uuid.UUID, start: datetime, end: datetime, every: int
) -> list[ReadingOut]:
    bucket = func.time_bucket(timedelta(seconds=every), Telemetry.time).label("bucket")
    rows = await session.execute(
        select(
            bucket,
            func.max(Telemetry.total_l),
            func.sum(Telemetry.delta_l),
            func.avg(Telemetry.flow_lpm),
            func.avg(Telemetry.ph),
            func.avg(Telemetry.temp_c),
            func.min(Telemetry.valve),  # one value per period is enough for a chart
            func.avg(Telemetry.rssi),
        )
        .where(Telemetry.home_id == home_id, Telemetry.time >= start, Telemetry.time < end)
        .group_by(bucket)
        .order_by(bucket)
    )
    return [
        ReadingOut(time=b, total_l=total, delta_l=round(delta, 3), flow_lpm=round(flow, 2),
                   ph=round(ph, 2), temp_c=round(temp, 1), valve=valve, rssi=round(rssi))
        for b, total, delta, flow, ph, temp, valve, rssi in rows
    ]  # fmt: skip


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@router.get(
    "/homes/{home_id}/consumption", response_model=ConsumptionOut, responses={404: {}, 422: {}}
)
async def home_consumption(
    home_id: uuid.UUID,
    session: Session,
    caller: OwnerOrResident,
    granularity: Granularity = "day",
    from_: Annotated[date | None, Query(alias="from", description="First local day")] = None,
    to: Annotated[date | None, Query(description="Last local day (included)")] = None,
):
    """Litres per hour, day or month, in the building's time zone (FR-23)."""
    home, building, since = await _home_and_since(session, caller, home_id)
    first, last = default_dates(from_, to, building.timezone)
    check_range(first, last, MAX_RANGE_DAYS)
    if since is not None:
        first = max(first, since)  # a resident sees only their own stay
        last = max(last, first)
    start, end = local_range(first, last, building.timezone)
    points = await _series(
        session, "home_id = :home", {"home": home.id}, granularity, building.timezone, start, end
    )
    return ConsumptionOut(
        granularity=granularity,
        timezone=building.timezone,
        from_=first,
        to=last,
        total_litres=round(sum(p.litres for p in points), 2),
        points=points,
    )


# ----- a whole building -----


@router.get(
    "/buildings/{building_id}/consumption",
    response_model=ConsumptionOut,
    responses={404: {}, 422: {}},
)
async def building_consumption(
    building_id: uuid.UUID,
    session: Session,
    caller: OwnerOnly,
    granularity: Granularity = "day",
    from_: Annotated[date | None, Query(alias="from", description="First local day")] = None,
    to: Annotated[date | None, Query(description="Last local day (included)")] = None,
):
    """All homes together per hour, day or month, plus the total of each home."""
    building = await visible_building(session, caller, building_id)
    first, last = default_dates(from_, to, building.timezone)
    check_range(first, last, MAX_RANGE_DAYS)
    start, end = local_range(first, last, building.timezone)
    points = await _series(
        session,
        "building_id = :building",
        {"building": building.id},
        granularity,
        building.timezone,
        start,
        end,
    )
    per_home = await session.execute(
        text(
            """
            SELECT h.id, h.unit_no, coalesce(sum(c.litres), 0) AS litres
            FROM homes h
            LEFT JOIN consumption_15m c
              ON c.home_id = h.id AND c.bucket >= :start AND c.bucket < :end
            WHERE h.building_id = :building
            GROUP BY h.id, h.unit_no
            -- archived homes only if they used water in this period
            HAVING bool_and(h.archived_at IS NULL) OR coalesce(sum(c.litres), 0) > 0
            ORDER BY h.unit_no
            """
        ),
        {"building": building.id, "start": start, "end": end},
    )
    return ConsumptionOut(
        granularity=granularity,
        timezone=building.timezone,
        from_=first,
        to=last,
        total_litres=round(sum(p.litres for p in points), 2),
        points=points,
        homes=[
            HomeTotal(home_id=r.id, unit_no=r.unit_no, litres=round(r.litres, 2)) for r in per_home
        ],
    )
