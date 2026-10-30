"""The building's water tank and pump (FR-26). Owners only.

Settings that live on the device (thresholds, auto/manual mode) are sent as
commands. The tanks table only changes when the device confirms them, so
the app always shows what the pump controller really uses.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..control import check_command_rate, device_for, queue_command
from ..db import get_session
from ..deps import NOT_FOUND, Caller, require, visible_building
from ..models import Building, Device, DeviceCommand, Tank, TankReading
from ..schemas import (
    CommandOut,
    PumpDay,
    PumpIn,
    TankHistoryOut,
    TankLevelPoint,
    TankOut,
    TankReadingOut,
    TankSettingsIn,
    TankSettingsOut,
)
from .live import check_range, default_dates, local_range

router = APIRouter(tags=["tank and pump"])
Session = Annotated[AsyncSession, Depends(get_session)]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]

MAX_HISTORY_DAYS = 92
MIN_GAP_PCT = 10  # low and high at least 10 points apart, or the pump would switch all the time


async def _tank(session: AsyncSession, caller: Caller, tank_id: uuid.UUID) -> tuple[Tank, Building]:
    tank = await session.get(Tank, tank_id)
    if tank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    building = await visible_building(session, caller, tank.building_id)  # 404 if not yours
    return tank, building


async def _tank_out(session: AsyncSession, tank: Tank) -> TankOut:
    device = await session.scalar(
        select(Device).where(Device.tank_id == tank.id, Device.status == "active")
    )
    latest = await session.scalar(
        select(TankReading)
        .where(TankReading.tank_id == tank.id)
        .order_by(TankReading.time.desc())
        .limit(1)
    )
    open_commands = []
    if device is not None:
        open_commands = await session.scalars(
            select(DeviceCommand)
            .where(
                DeviceCommand.device_id == device.id,
                DeviceCommand.state.in_(("queued", "pending")),
            )
            .order_by(DeviceCommand.created_at)
        )
    return TankOut(
        id=tank.id,
        building_id=tank.building_id,
        capacity_l=tank.capacity_l,
        low_pct=tank.low_pct,
        high_pct=tank.high_pct,
        mode=tank.mode,
        pump_kw=float(tank.pump_kw),
        device_id=device.id if device else None,
        online=bool(device and device.online),
        last_seen_at=device.last_seen_at if device else None,
        reading=TankReadingOut.model_validate(latest, from_attributes=True) if latest else None,
        open_commands=[CommandOut.model_validate(c) for c in open_commands],
    )


@router.get("/buildings/{building_id}/tank", response_model=TankOut, responses={404: {}})
async def building_tank(building_id: uuid.UUID, session: Session, caller: OwnerOnly):
    """Live tank level, pump state, confirmed settings and commands still running."""
    building = await visible_building(session, caller, building_id)
    tank = await session.scalar(select(Tank).where(Tank.building_id == building.id))
    if tank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This building has no tank yet")
    return await _tank_out(session, tank)


@router.get("/tanks/{tank_id}/history", response_model=TankHistoryOut, responses={404: {}, 422: {}})
async def tank_history(
    tank_id: uuid.UUID,
    session: Session,
    caller: OwnerOnly,
    from_: Annotated[date | None, Query(alias="from", description="First local day")] = None,
    to: Annotated[date | None, Query(description="Last local day (included)")] = None,
):
    """Tank level over time, and pump hours and energy per day (FR-26)."""
    tank, building = await _tank(session, caller, tank_id)
    tz = building.timezone
    first, last = default_dates(from_, to, tz)
    if from_ is None:
        first = last  # default: just one day (today, or the `to` day)
    check_range(first, last, MAX_HISTORY_DAYS)
    start, end = local_range(first, last, tz)
    step = 15 if (last - first).days < 7 else 60  # minutes per level point
    zone = ZoneInfo(tz)
    params = {"tank": tank.id, "start": start, "end": end, "tz": tz}
    levels = await session.execute(
        text(
            """
            SELECT time_bucket(CAST(CAST(:step AS text) AS interval), time, CAST(:tz AS text))
                       AS start,
                   avg(level_pct) AS level
            FROM tank_readings
            WHERE tank_id = :tank AND time >= :start AND time < :end
            GROUP BY 1 ORDER BY 1
            """
        ),
        {**params, "step": f"{step} minutes"},
    )
    days = await session.execute(
        text(
            """
            SELECT time_bucket('1 day', time, CAST(:tz AS text)) AS day,
                   sum(pump_s) / 3600.0 AS hours
            FROM tank_readings
            WHERE tank_id = :tank AND time >= :start AND time < :end
            GROUP BY 1 ORDER BY 1
            """
        ),
        params,
    )
    kw = float(tank.pump_kw)
    return TankHistoryOut(
        tank_id=tank.id,
        timezone=tz,
        from_=first,
        to=last,
        step_minutes=step,
        levels=[
            TankLevelPoint(
                start=r.start.astimezone(zone),
                level_pct=None if r.level is None else round(r.level, 1),
            )
            for r in levels
        ],
        days=[
            PumpDay(
                day=r.day.astimezone(zone).date(),
                pump_hours=round(r.hours, 2),
                energy_kwh=round(r.hours * kw, 2),
            )
            for r in days
        ],
    )


@router.patch(
    "/tanks/{tank_id}/settings",
    response_model=TankSettingsOut,
    responses={202: {"model": TankSettingsOut}, 404: {}, 409: {}, 422: {}, 429: {}},
)
async def tank_settings(
    tank_id: uuid.UUID,
    body: TankSettingsIn,
    session: Session,
    caller: OwnerOnly,
    response: Response,
):
    """Change thresholds, auto/manual mode or the pump's power (kW).

    Thresholds and mode are sent to the device (202): the values shown change
    once the device confirms them. The pump's kW is only used for the energy
    estimate, so it is saved at once."""
    tank, _ = await _tank(session, caller, tank_id)
    low = body.low_pct if body.low_pct is not None else tank.low_pct
    high = body.high_pct if body.high_pct is not None else tank.high_pct
    if high - low < MIN_GAP_PCT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The high level must be at least {MIN_GAP_PCT} points above the low level",
        )
    commands = []
    wants_thresholds = (low, high) != (tank.low_pct, tank.high_pct)
    wants_mode = body.mode is not None and body.mode != tank.mode
    if wants_thresholds or wants_mode:
        check_command_rate(caller)
        device = await device_for(session, tank_id=tank.id)
        if wants_thresholds:
            commands.append(
                await queue_command(
                    session, caller, device, type="thresholds",
                    params={"lowPct": low, "highPct": high},
                )
            )  # fmt: skip
        if wants_mode:
            commands.append(
                await queue_command(session, caller, device, type="mode", action=body.mode)
            )
    if body.pump_kw is not None and float(tank.pump_kw) != body.pump_kw:
        await audit.record(
            session,
            "tank.updated",
            actor_id=caller.id,
            actor_role=caller.role,
            ip=caller.ip,
            target_type="tank",
            target_id=tank.id,
            building_id=tank.building_id,
            details={"pump_kw": [str(tank.pump_kw), str(body.pump_kw)]},
        )
        tank.pump_kw = body.pump_kw
        await session.flush()
    if commands:
        response.status_code = status.HTTP_202_ACCEPTED
    return TankSettingsOut(
        tank=await _tank_out(session, tank),
        commands=[CommandOut.model_validate(c) for c in commands],
    )


@router.post(
    "/tanks/{tank_id}/pump",
    response_model=CommandOut,
    status_code=202,
    responses={404: {}, 409: {}, 429: {}},
)
async def pump(tank_id: uuid.UUID, body: PumpIn, session: Session, caller: OwnerOnly):
    """Switch the pump on or off by hand (the tank must be in manual mode).

    The device has the last word: it refuses `on` when the sump is empty
    (UNSAFE_DRY_RUN) or the tank is full (UNSAFE_OVERFLOW), and the command
    then ends as `rejected` with that reason."""
    tank, _ = await _tank(session, caller, tank_id)
    check_command_rate(caller)
    device = await device_for(session, tank_id=tank.id)
    return await queue_command(
        session, caller, device, type="pump", action=body.action, reason=body.reason
    )
