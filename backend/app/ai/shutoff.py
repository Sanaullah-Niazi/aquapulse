"""Automatic leak shut-off (FR-61).

Only for homes where the owner switched it on (`homes.auto_shutoff_enabled`).
When the leak detector is sure (a burst, or a leak with confidence 0.8 or
more), the worker closes the valve as the user `system`: the command is
audited like any other. The resident and the owner get an email (and a push)
saying so. The resident may open the valve again, unless the owner locked it.

Never more than once per hour per home, so a leak that goes on after the
resident opened the valve again does not turn it on and off all day.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import push
from ..alerts.detect import Signal
from ..alerts.rules import LIVE_AI_RULES, RULES
from ..control import queue_command
from ..models import (
    Building,
    Device,
    DeviceCommand,
    Home,
    Notification,
    Occupancy,
    PushDevice,
    User,
    ValveLock,
)

log = logging.getLogger("aquapulse.ai")

ONCE_PER = timedelta(hours=1)
REASON_PREFIX = "Automatic shut-off"


async def _people(session: AsyncSession, home: Home, building: Building) -> list[User]:
    """The owner and the people living in the home today: everyone is told."""
    today = datetime.now(UTC).astimezone(ZoneInfo(building.timezone)).date()
    residents = select(Occupancy.user_id).where(
        Occupancy.home_id == home.id,
        Occupancy.move_in <= today,
        or_(Occupancy.move_out.is_(None), Occupancy.move_out >= today),
    )
    query = select(User).where(
        User.is_active, or_(User.id == building.owner_id, User.id.in_(residents))
    )
    return list(await session.scalars(query))


async def maybe_shut_off(
    session: AsyncSession, signal: Signal, now: datetime, alert_id: uuid.UUID | None = None
) -> DeviceCommand | None:
    """Close the valve if this finding calls for it. Returns the command, or None."""
    if signal.rule not in LIVE_AI_RULES or not signal.active or not signal.home_id:
        return None
    if not (signal.details or {}).get("high"):
        return None
    home = await session.get(Home, uuid.UUID(signal.home_id))
    if home is None or not home.auto_shutoff_enabled or home.archived_at is not None:
        return None
    locked = await session.scalar(
        select(ValveLock.id).where(ValveLock.home_id == home.id, ValveLock.unlocked_at.is_(None))
    )
    if locked is not None:  # the owner decided the valve's state: leave it
        return None
    recent = await session.scalar(
        select(DeviceCommand.id).where(
            DeviceCommand.home_id == home.id,
            DeviceCommand.requested_role == "system",
            DeviceCommand.reason.startswith(REASON_PREFIX),
            DeviceCommand.created_at > now - ONCE_PER,
        )
    )
    if recent is not None:
        return None
    device = await session.scalar(
        select(Device).where(Device.home_id == home.id, Device.status == "active").limit(1)
    )
    if device is None:
        return None
    rule = RULES[signal.rule]
    confidence = float((signal.details or {}).get("confidence", 1.0))
    command = await queue_command(
        session,
        None,
        device,
        type="valve",
        action="close",
        reason=f"{REASON_PREFIX}: {rule.title} ({confidence:.0%} sure)",
    )
    command.created_at = now  # the engine's clock: "once per hour" is counted on it
    building = await session.get(Building, home.building_id)
    assert building is not None
    subject = f"[AquaPulse] Water turned off: {rule.title} (unit {home.unit_no})"
    body = (
        f"AquaPulse closed the water valve of unit {home.unit_no} in {building.name} "
        f"automatically, because of: {rule.title}.\n\n{signal.message}\n\n"
        "Please check for a leak (taps, toilet cistern, pipes). The resident can open the "
        "valve again in the app when it is safe, unless the owner has locked it.\n\n"
        "Automatic shut-off is switched on for this home by the building owner. It happens "
        "at most once per hour.\n"
    )
    short = f"Water turned off: unit {home.unit_no}"
    for person in await _people(session, home, building):
        channels = ["email"]
        if push.available() and await session.scalar(
            select(PushDevice.id).where(PushDevice.user_id == person.id).limit(1)
        ):
            channels.append("push")
        for channel in channels:
            session.add(
                Notification(
                    user_id=person.id,
                    channel=channel,
                    kind="auto_shutoff",
                    alert_id=alert_id,
                    subject=subject if channel == "email" else short,
                    body=body if channel == "email" else f"{rule.title}. Check for a leak.",
                    next_attempt_at=now,
                )
            )
    log.warning("automatic shut-off: home %s (%s)", home.id, signal.rule)
    return command
