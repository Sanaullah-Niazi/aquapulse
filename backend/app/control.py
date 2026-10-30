"""Queue a device command from the API (FR-13, FR-24, FR-26).

The API only writes the command to the database as `queued` and NOTIFYs the
ingestor, which sends it over MQTT and tracks the device's answer. So the
API never needs an MQTT connection, and a command is never lost if the
ingestor restarts: it is still in the table, waiting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from . import events as bus
from .deps import Caller
from .ingest.handler import command_event
from .models import Device, DeviceCommand
from .security.ratelimit import SlidingWindow

# FR-72: at most 10 valve or pump commands per user per minute (the 11th gets 429).
command_limiter = SlidingWindow(limit=10, window_s=60)


def check_command_rate(caller: Caller) -> None:
    key = str(caller.id)
    if command_limiter.blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many commands. Wait a minute and try again",
            headers={"Retry-After": str(command_limiter.retry_after(key))},
        )
    command_limiter.hit(key)


async def device_for(session: AsyncSession, *, home_id=None, tank_id=None) -> Device:
    """The active device of a home or tank, or 409 with a clear message."""
    query = (
        select(Device)
        .where(Device.home_id == home_id if home_id is not None else Device.tank_id == tank_id)
        # the active one first; old revoked devices may also be there (M5)
        .order_by((Device.status == "active").desc(), Device.created_at.desc())
        .limit(1)
    )
    device = await session.scalar(query)
    if device is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No device is installed here yet")
    if device.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "This device is disabled")
    return device


async def queue_command(
    session: AsyncSession,
    caller: Caller | None,
    device: Device,
    *,
    type: str,
    action: str | None = None,
    params: dict[str, Any] | None = None,
    reason: str = "",
) -> DeviceCommand:
    """Save a command as `queued`, audit it, and wake the ingestor."""
    command = DeviceCommand(
        device_id=device.id,
        building_id=device.building_id,
        home_id=device.home_id,
        type=type,
        action=action,
        params=params or {},
        reason=reason,
        requested_by=caller.id if caller else None,
        requested_role=caller.role if caller else "system",
        state="queued",
        # Set here, not by the database, so the API can return it at once.
        created_at=datetime.now(UTC),
    )
    session.add(command)
    await session.flush()
    await audit.record(
        session,
        "device.command_requested",
        actor_id=caller.id if caller else None,
        actor_role=caller.role if caller else "system",
        ip=caller.ip if caller else None,
        target_type="home" if device.home_id else "tank",
        target_id=device.home_id or device.tank_id,
        building_id=device.building_id,
        details={
            "command": str(command.id),
            "device": device.id,
            "type": type,
            "action": action,
            **(params or {}),
            "reason": reason,
        },
    )
    # Both are delivered only when the request's transaction commits.
    await bus.notify(session, bus.COMMANDS, str(command.id))
    await bus.notify(session, bus.EVENTS, command_event(command))
    return command
