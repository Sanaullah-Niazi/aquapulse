"""The owner's building overview and CSV exports (M8).

* FR-42 / FR-43: one call gives the numbers for the building dashboard and
  the homes list (litres today and this month, live flow, valves, pH,
  temperature, online, open alerts, the tank, unpaid bills), instead of the
  app asking once per home.
* FR-30: consumption, bills and payments as CSV files for a date range (at
  most 400 days), only what the caller may see, and every export is audited.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..alerts.rules import settings_for
from ..billing import lifecycle
from ..billing.money import format_money, round_money
from ..billing.periods import local_midnight, local_today, month_of, previous_month
from ..db import get_session
from ..deps import Caller, require, resident_since, visible_building, visible_home
from ..models import (
    Alert,
    Bill,
    Building,
    Currency,
    Device,
    Home,
    Occupancy,
    Payment,
    Tank,
    TankReading,
    User,
    ValveLock,
)
from ..schemas import OverviewHomeOut, OverviewOut, OverviewTankOut
from .live import MAX_RANGE_DAYS, check_range, default_dates, local_range

router = APIRouter(tags=["owner overview and exports"])
Session = Annotated[AsyncSession, Depends(get_session)]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]
OwnerOrResident = Annotated[Caller, Depends(require("owner", "resident"))]

LIVE_SECONDS = 60  # a reading older than this does not count as "water running now"

LITRES_SQL = text(
    """
    SELECT h.id,
           coalesce(sum(c.litres) FILTER (WHERE c.bucket >= :today), 0) AS today,
           coalesce(sum(c.litres) FILTER (WHERE c.bucket >= :month), 0) AS month,
           coalesce(sum(c.litres) FILTER (WHERE c.bucket < :prev_to), 0) AS prev_to_date
    FROM homes h
    LEFT JOIN consumption_15m c ON c.home_id = h.id AND c.bucket >= :prev
    WHERE h.building_id = :building AND h.archived_at IS NULL
    GROUP BY h.id
    """
)

LATEST_SQL = text(
    """
    SELECT DISTINCT ON (home_id) home_id, time, ph, temp_c, flow_lpm
    FROM telemetry
    WHERE building_id = :building AND time > :since
    ORDER BY home_id, time DESC
    """
)


@router.get("/buildings/{building_id}/overview", response_model=OverviewOut, responses={404: {}})
async def overview(building_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Everything the building dashboard and the homes list show, in one answer."""
    building = await visible_building(session, caller, building_id)
    now = datetime.now(UTC)
    tz = building.timezone
    today = local_today(tz, now)
    month_start = local_midnight(month_of(today), tz)
    prev_start = local_midnight(previous_month(today), tz)
    # "Last month so far": the same stretch of time into last month, so a
    # half-finished month is compared fairly.
    prev_to = min(prev_start + (now - month_start), month_start)

    homes = list(
        await session.scalars(
            select(Home)
            .where(Home.building_id == building.id, Home.archived_at.is_(None))
            .order_by(Home.unit_no)
        )
    )
    litres = {
        r.id: r
        for r in await session.execute(
            LITRES_SQL,
            {
                "building": building.id,
                "today": local_midnight(today, tz),
                "month": month_start,
                "prev": prev_start,
                "prev_to": prev_to,
            },
        )
    }
    latest = {
        r.home_id: r
        for r in await session.execute(
            LATEST_SQL, {"building": building.id, "since": now - timedelta(days=1)}
        )
    }
    devices = {
        d.home_id: d
        for d in await session.scalars(
            select(Device).where(
                Device.building_id == building.id, Device.status == "active", Device.kind == "home"
            )
        )
    }
    locks = {
        lock.home_id: lock
        for lock in await session.scalars(
            select(ValveLock).where(
                ValveLock.home_id.in_([h.id for h in homes]), ValveLock.unlocked_at.is_(None)
            )
        )
    }
    residents: dict[uuid.UUID, list[str]] = defaultdict(list)
    for home_id, name in await session.execute(
        select(Occupancy.home_id, User.full_name)
        .join(User, User.id == Occupancy.user_id)
        .where(
            Occupancy.home_id.in_([h.id for h in homes]),
            Occupancy.move_in <= today,
            (Occupancy.move_out.is_(None)) | (Occupancy.move_out >= today),
        )
        .order_by(User.full_name)
    ):
        residents[home_id].append(name)
    open_alerts = await session.execute(
        select(Alert.home_id, Alert.severity, func.count())
        .where(Alert.building_id == building.id, Alert.state.in_(("open", "acknowledged")))
        .group_by(Alert.home_id, Alert.severity)
    )
    alerts_by_home: dict[uuid.UUID, int] = defaultdict(int)
    alerts_by_severity = {"critical": 0, "warning": 0, "info": 0}
    for home_id, severity, count in open_alerts:
        alerts_by_severity[severity] += count
        if home_id is not None:
            alerts_by_home[home_id] += count

    rules = settings_for(building.alert_rules, building.alert_recipients)
    rows = []
    for home in homes:
        device = devices.get(home.id)
        reading = latest.get(home.id)
        fresh = reading is not None and (now - reading.time).total_seconds() <= LIVE_SECONDS
        used = litres.get(home.id)
        lock = locks.get(home.id)
        rows.append(
            OverviewHomeOut(
                home_id=home.id,
                unit_no=home.unit_no,
                floor=home.floor,
                residents=residents.get(home.id, []),
                litres_today=round(float(used.today), 1) if used else 0.0,
                litres_month=round(float(used.month), 1) if used else 0.0,
                flow_lpm=float(reading.flow_lpm) if fresh and reading.flow_lpm else 0.0,
                ph=reading.ph if reading else None,
                temp_c=reading.temp_c if reading else None,
                reading_at=reading.time if reading else None,
                device_id=device.id if device else None,
                online=bool(device and device.online),
                last_seen_at=device.last_seen_at if device else None,
                valve=(device.state or {}).get("valve") if device else None,
                locked=lock.locked_state if lock else None,
                lock_reason=lock.reason if lock else None,
                open_alerts=alerts_by_home.get(home.id, 0),
            )
        )

    currency = await session.get(Currency, building.currency_code)
    assert currency is not None
    unpaid = await session.execute(
        select(func.count(), func.coalesce(func.sum(Bill.total - Bill.amount_paid), 0)).where(
            Bill.building_id == building.id, Bill.status.in_(lifecycle.OPEN)
        )
    )
    unpaid_bills, unpaid_amount = unpaid.one()
    return OverviewOut(
        building_id=building.id,
        name=building.name,
        timezone=tz,
        currency_code=currency.code,
        litres_today=round(sum(r.litres_today for r in rows), 1),
        litres_month=round(sum(r.litres_month for r in rows), 1),
        litres_last_month_to_date=round(sum(float(r.prev_to_date) for r in litres.values()), 1),
        flow_lpm=round(sum(r.flow_lpm for r in rows), 2),
        homes=len(rows),
        homes_online=sum(r.online for r in rows),
        homes_with_device=sum(r.device_id is not None for r in rows),
        valves_closed=sum(r.valve == "closed" for r in rows),
        valves_locked=sum(r.locked is not None for r in rows),
        alerts=alerts_by_severity,
        tank=await _tank(session, building.id),
        unpaid_bills=unpaid_bills,
        unpaid_text=format_money(Decimal(unpaid_amount), currency.symbol, currency.minor_units),
        ph_safe_low=rules.get("ph_unsafe", "low"),
        ph_safe_high=rules.get("ph_unsafe", "high"),
        rows=rows,
    )


async def _tank(session: AsyncSession, building_id: uuid.UUID) -> OverviewTankOut | None:
    tank = await session.scalar(select(Tank).where(Tank.building_id == building_id))
    if tank is None:
        return None
    reading = await session.scalar(
        select(TankReading)
        .where(TankReading.tank_id == tank.id)
        .order_by(TankReading.time.desc())
        .limit(1)
    )
    device = await session.scalar(
        select(Device).where(Device.tank_id == tank.id, Device.status == "active")
    )
    return OverviewTankOut(
        tank_id=tank.id,
        level_pct=reading.level_pct if reading else None,
        level_l=reading.level_l if reading else None,
        pump_on=reading.pump_on if reading else None,
        mode=tank.mode,
        online=bool(device and device.online),
    )


# ----- CSV exports (FR-30) -----

Kind = Literal["consumption", "bills", "payments"]


def _csv(name: str, header: list[str], rows) -> Response:
    """A CSV file the browser downloads. Spreadsheet formulas are defused:
    a text cell starting with = + - @ gets a ' in front (CSV injection)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow(
            ["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v for v in row]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


async def _minor_units(session: AsyncSession, building: Building) -> int:
    currency = await session.get(Currency, building.currency_code)
    assert currency is not None
    return currency.minor_units


async def _audit_export(session, caller: Caller, building: Building, **details):
    await audit.record(
        session,
        "export.csv",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="building",
        target_id=building.id,
        building_id=building.id,
        details={k: str(v) for k, v in details.items()},
    )
    await session.commit()


@router.get(
    "/buildings/{building_id}/export/{kind}.csv",
    responses={200: {"content": {"text/csv": {}}}, 404: {}, 422: {}},
)
async def export_building(
    building_id: uuid.UUID,
    kind: Kind,
    session: Session,
    caller: OwnerOnly,
    from_: Annotated[date | None, Query(alias="from", description="First local day")] = None,
    to: Annotated[date | None, Query(description="Last local day (included)")] = None,
):
    """Litres per home per day, bills by month, or payments by day received,
    for at most 400 days."""
    building = await visible_building(session, caller, building_id)
    first, last = default_dates(from_, to, building.timezone)
    check_range(first, last, MAX_RANGE_DAYS)
    minor = await _minor_units(session, building)
    name = f"{building.code}-{kind}-{first}-to-{last}.csv"
    if kind == "consumption":
        start, end = local_range(first, last, building.timezone)
        rows = await session.execute(
            text(
                """
                SELECT (time_bucket('1 day', c.bucket, CAST(:tz AS text))
                        AT TIME ZONE CAST(:tz AS text))::date AS day,
                       h.unit_no, round(sum(c.litres)::numeric, 2) AS litres
                FROM consumption_15m c JOIN homes h ON h.id = c.home_id
                WHERE c.building_id = :building AND c.bucket >= :start AND c.bucket < :end
                GROUP BY 1, 2 ORDER BY 1, 2
                """
            ),
            {"building": building.id, "tz": building.timezone, "start": start, "end": end},
        )
        response = _csv(name, ["date", "unit", "litres"], [tuple(r) for r in rows])
    elif kind == "bills":
        bills = await session.scalars(
            select(Bill)
            .where(
                Bill.building_id == building.id,
                Bill.month >= month_of(first),
                Bill.month <= last,
            )
            .order_by(Bill.month, Bill.unit_no, Bill.period_start)
        )
        response = _csv(name, BILL_HEADER, [_bill_row(b, minor) for b in bills])
    else:
        payments = await session.execute(
            select(Payment, Bill.bill_no, Bill.unit_no, Bill.currency_code)
            .join(Bill, Bill.id == Payment.bill_id)
            .where(
                Bill.building_id == building.id,
                Payment.received_on >= first,
                Payment.received_on <= last,
            )
            .order_by(Payment.received_on, Payment.created_at)
        )
        response = _csv(
            name,
            [
                "received_on",
                "bill_no",
                "unit",
                "currency",
                "amount",
                "method",
                "reference",
                "reversed_at",
                "reversal_reason",
            ],
            [
                (
                    p.received_on,
                    bill_no,
                    unit,
                    code,
                    round_money(p.amount, minor),
                    p.method,
                    p.reference,
                    p.reversed_at.isoformat() if p.reversed_at else "",
                    p.reversal_reason or "",
                )
                for p, bill_no, unit, code in payments
            ],
        )
    await _audit_export(session, caller, building, kind=kind, first=first, last=last)
    return response


BILL_HEADER = [
    "month",
    "bill_no",
    "unit",
    "bill_to",
    "period_start",
    "period_end",
    "litres",
    "currency",
    "total",
    "paid",
    "status",
    "due_date",
]


def _bill_row(b: Bill, minor: int) -> tuple:
    return (
        b.month.strftime("%Y-%m"),
        b.bill_no or "",
        b.unit_no,
        b.bill_to,
        b.period_start,
        b.period_end,
        b.litres,
        b.currency_code,
        round_money(b.total, minor),
        round_money(b.amount_paid, minor),
        b.status,
        b.due_date or "",
    )


@router.get(
    "/homes/{home_id}/export/{kind}.csv",
    responses={200: {"content": {"text/csv": {}}}, 404: {}, 422: {}},
)
async def export_home(
    home_id: uuid.UUID,
    kind: Literal["consumption", "bills"],
    session: Session,
    caller: OwnerOrResident,
    from_: Annotated[date | None, Query(alias="from", description="First local day")] = None,
    to: Annotated[date | None, Query(description="Last local day (included)")] = None,
):
    """One home's litres per day, or its bills. A resident gets only their
    own stay and their own bills."""
    home = await visible_home(session, caller, home_id)
    building = await session.get(Building, home.building_id)
    assert building is not None
    first, last = default_dates(from_, to, building.timezone)
    check_range(first, last, MAX_RANGE_DAYS)
    minor = await _minor_units(session, building)
    since = await resident_since(session, caller, home)
    if since is not None:
        first = max(first, since)
    name = f"{building.code}-unit-{home.unit_no}-{kind}-{first}-to-{last}.csv"
    if kind == "consumption":
        start, end = local_range(first, max(last, first), building.timezone)
        rows = await session.execute(
            text(
                """
                SELECT (time_bucket('1 day', bucket, CAST(:tz AS text))
                        AT TIME ZONE CAST(:tz AS text))::date AS day,
                       round(sum(litres)::numeric, 2) AS litres
                FROM consumption_15m
                WHERE home_id = :home AND bucket >= :start AND bucket < :end
                GROUP BY 1 ORDER BY 1
                """
            ),
            {"home": home.id, "tz": building.timezone, "start": start, "end": end},
        )
        response = _csv(
            name, ["date", "litres"], [tuple(r) for r in rows if first <= r.day <= last]
        )
    else:
        query = select(Bill).where(
            Bill.home_id == home.id, Bill.month >= month_of(first), Bill.month <= last
        )
        if caller.role == "resident":
            query = query.where(Bill.user_id == caller.id, Bill.status != "draft")
        bills = await session.scalars(query.order_by(Bill.month, Bill.period_start))
        response = _csv(name, BILL_HEADER, [_bill_row(b, minor) for b in bills])
    await _audit_export(
        session, caller, building, kind=kind, home=home.unit_no, first=first, last=last
    )
    return response
