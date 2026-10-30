"""Valve control and owner locks (FR-24, FR-27).

Pressing "close" does not close anything by itself: it queues a command.
The command's state then moves queued -> pending -> succeeded / failed /
rejected / timeout, and only the device's ack says what really happened.
Apps watch it live over the WebSocket or with GET /commands/{id}.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from .. import events as bus
from ..control import check_command_rate, device_for, queue_command
from ..db import get_session
from ..deps import NOT_FOUND, Caller, require, visible_home
from ..emailer import send_email
from ..models import Building, DeviceCommand, Home, Occupancy, User, ValveLock
from ..schemas import CommandOut, ValveIn, ValveLockIn, ValveLockOut, ValveUnlockIn

router = APIRouter(tags=["valve control"])
Session = Annotated[AsyncSession, Depends(get_session)]
OwnerOrResident = Annotated[Caller, Depends(require("owner", "resident"))]
OwnerOnly = Annotated[Caller, Depends(require("owner"))]

VALVE_STATE = {"open": "open", "close": "closed"}


async def active_lock(session: AsyncSession, home_id: uuid.UUID) -> ValveLock | None:
    return await session.scalar(
        select(ValveLock).where(ValveLock.home_id == home_id, ValveLock.unlocked_at.is_(None))
    )


@router.post(
    "/homes/{home_id}/valve",
    response_model=CommandOut,
    status_code=202,
    responses={404: {}, 409: {}, 423: {}, 429: {}},
)
async def valve(home_id: uuid.UUID, body: ValveIn, session: Session, caller: OwnerOrResident):
    """Open or close the home's water valve. Returns the command (202 Accepted)."""
    home = await visible_home(session, caller, home_id)
    if home.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This home is archived")
    lock = await active_lock(session, home.id)
    if lock is not None:
        # Locked: nobody changes the valve until the owner unlocks it (FR-27).
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"The valve is locked {lock.locked_state} by the building owner: {lock.reason}",
        )
    check_command_rate(caller)
    device = await device_for(session, home_id=home.id)
    return await queue_command(
        session, caller, device, type="valve", action=body.action, reason=body.reason
    )


@router.get("/commands/{command_id}", response_model=CommandOut, responses={404: {}})
async def get_command(command_id: uuid.UUID, session: Session, caller: OwnerOrResident):
    """What became of a command (FR-24)."""
    command = await session.get(DeviceCommand, command_id)
    if command is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if command.home_id is not None:
        await visible_home(session, caller, command.home_id)  # 404 if not yours
    else:  # a tank command: owner of the building only
        building = await session.get(Building, command.building_id)
        if caller.role != "owner" or building is None or building.owner_id != caller.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return command


# ----- locks (owner only) -----


async def _tell_residents(session: AsyncSession, home: Home, subject: str, body: str) -> int:
    """Email everyone who lives in the home now. Returns how many emails were sent."""
    today = datetime.now(UTC).date()
    emails = await session.scalars(
        select(User.email)
        .join(Occupancy, Occupancy.user_id == User.id)
        .where(
            Occupancy.home_id == home.id,
            Occupancy.move_in <= today,
            or_(Occupancy.move_out.is_(None), Occupancy.move_out >= today),
            User.is_active.is_(True),
        )
    )
    sent = 0
    for email in emails:
        sent += await send_email(email, subject, body)
    return sent


async def _lock_event(session: AsyncSession, home: Home, lock: ValveLock, locked: bool) -> None:
    await bus.notify(
        session,
        bus.EVENTS,
        {
            "type": "lock",
            "buildingId": home.building_id,
            "homeId": home.id,
            "locked": locked,
            "state": lock.locked_state,
            "reason": lock.reason if locked else lock.unlock_reason,
        },
    )


@router.post(
    "/homes/{home_id}/valve-lock",
    response_model=ValveLockOut,
    status_code=201,
    responses={404: {}, 409: {}, 422: {}},
)
async def lock_valve(home_id: uuid.UUID, body: ValveLockIn, session: Session, caller: OwnerOnly):
    """Lock the valve open or closed, with a reason of 10+ characters (FR-27).

    The valve is moved to the locked position at once, and the residents
    are told by email. Nobody can change it until the lock is removed."""
    home = await visible_home(session, caller, home_id, manage=True)
    if await active_lock(session, home.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This valve is already locked")
    device = await device_for(session, home_id=home.id)
    lock = ValveLock(
        home_id=home.id,
        locked_state=body.state,
        reason=body.reason,
        locked_by=caller.id,
        locked_at=datetime.now(UTC),
    )
    session.add(lock)
    try:
        await session.flush()
    except IntegrityError:  # two owners locking at the same moment
        raise HTTPException(status.HTTP_409_CONFLICT, "This valve is already locked") from None
    action = "open" if body.state == "open" else "close"
    command = await queue_command(
        session, caller, device, type="valve", action=action, reason=f"Lock: {body.reason}"
    )
    building = await session.get(Building, home.building_id)
    sent = await _tell_residents(
        session,
        home,
        f"Your water valve was locked {body.state}",
        f"The owner of {building.name if building else 'your building'} locked the water "
        f"valve of unit {home.unit_no} {body.state}.\n\nReason: {body.reason}\n\n"
        "You cannot change the valve until it is unlocked. Contact the owner if you "
        "have questions.\n",
    )
    await audit.record(
        session,
        "valve.locked",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="home",
        target_id=home.id,
        building_id=home.building_id,
        details={
            "state": body.state,
            "reason": body.reason,
            "command": str(command.id),
            "residents_emailed": sent,
        },
    )
    await _lock_event(session, home, lock, locked=True)
    return lock


@router.post(
    "/homes/{home_id}/valve-unlock",
    response_model=ValveLockOut,
    responses={404: {}, 409: {}, 422: {}},
)
async def unlock_valve(
    home_id: uuid.UUID, body: ValveUnlockIn, session: Session, caller: OwnerOnly
):
    """Remove the lock, with a reason of 10+ characters. The valve stays where it is."""
    home = await visible_home(session, caller, home_id, manage=True)
    lock = await active_lock(session, home.id)
    if lock is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This valve is not locked")
    lock.unlocked_at = datetime.now(UTC)
    lock.unlocked_by = caller.id
    lock.unlock_reason = body.reason
    sent = await _tell_residents(
        session,
        home,
        "Your water valve was unlocked",
        f"The water valve of unit {home.unit_no} is unlocked again. You can open and "
        f"close it yourself.\n\nReason: {body.reason}\n",
    )
    await audit.record(
        session,
        "valve.unlocked",
        actor_id=caller.id,
        actor_role=caller.role,
        ip=caller.ip,
        target_type="home",
        target_id=home.id,
        building_id=home.building_id,
        details={"reason": body.reason, "lock": str(lock.id), "residents_emailed": sent},
    )
    await _lock_event(session, home, lock, locked=False)
    return lock
