"""Sending commands to devices, one at a time per device (FR-13, FR-24).

The API never talks to MQTT itself. It saves a command as `queued` and
sends a NOTIFY. This dispatcher then, for each device:

1. waits until the device has no command `pending` (a valve that is still
   moving would answer BUSY, so two quick opposite presses are sent in order
   and the last one wins);
2. sends the oldest queued command: QoS 1, never retained, `expiresAt` 30 s
   after sending, and marks it `pending`;
3. marks `pending` commands with no ack after 15 s as `timeout`.

The device's ack (handled in handler.py) is the only thing that marks a
command succeeded, failed or rejected.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from .. import audit, contract, db
from ..models import DeviceCommand
from . import metrics
from .handler import _notify, command_event

log = logging.getLogger("aquapulse.commands")

EXPIRES_AFTER = timedelta(seconds=30)  # the device refuses it after this (EXPIRED)
ACK_TIMEOUT = timedelta(seconds=15)  # no answer by then: timeout
ROLE_FOR_DEVICE = {
    "owner": "owner",
    "resident": "resident",
    "sysadmin": "admin",
    "system": "system",
}

Publish = Callable[[str, bytes], Awaitable[None]]  # (topic, payload) -> sent with QoS 1


def build_payload(command: DeviceCommand, now: datetime) -> dict:
    payload = {
        "v": 1,
        "cmdId": str(command.id),
        "type": command.type,
        "issuedAt": contract.iso(now),
        "expiresAt": contract.iso(now + EXPIRES_AFTER),
        "by": ROLE_FOR_DEVICE[command.requested_role],
    }
    if command.action:
        payload["action"] = command.action
    payload.update(command.params)  # lowPct/highPct for thresholds
    return payload


async def dispatch(publish: Publish, clock=lambda: datetime.now(UTC)) -> int:
    """Send the next queued command of every idle device. Returns how many were sent."""
    outgoing: list[tuple[DeviceCommand, str, bytes]] = []
    async with db.sessions()() as session, session.begin():
        busy = select(DeviceCommand.device_id).where(DeviceCommand.state == "pending")
        candidates = await session.scalars(
            select(DeviceCommand)
            .where(DeviceCommand.state == "queued", DeviceCommand.device_id.not_in(busy))
            .order_by(DeviceCommand.device_id, DeviceCommand.created_at)
            .with_for_update(skip_locked=True)
        )
        done_devices: set[str] = set()
        for command in candidates:
            if command.device_id in done_devices:
                continue  # one at a time per device; the rest wait for the ack
            done_devices.add(command.device_id)
            now = clock()
            data = contract.encode(command.device_id, "cmd", build_payload(command, now))
            command.state = "pending"
            command.issued_at = now
            command.expires_at = now + EXPIRES_AFTER
            outgoing.append((command, contract.topic(command.device_id, "cmd"), data))
        await _notify(session, [command_event(c) for c, _, _ in outgoing])
    # Marked `pending` and committed BEFORE sending: a fast device can answer
    # within milliseconds, and its ack must find the command already pending.
    for index, (_command, topic, data) in enumerate(outgoing):
        try:
            await publish(topic, data)
        except Exception:
            # Not sent: put this one and the rest back in the queue for the next try.
            await _requeue([c.id for c, _, _ in outgoing[index:]])
            raise
    return len(outgoing)


async def _requeue(ids: list) -> None:
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(DeviceCommand)
            .where(DeviceCommand.id.in_(ids), DeviceCommand.state == "pending")
            .values(state="queued", issued_at=None, expires_at=None)
        )


async def expire(clock=lambda: datetime.now(UTC)) -> int:
    """Mark commands without an answer after 15 s as timeout."""
    async with db.sessions()() as session, session.begin():
        late = list(
            await session.scalars(
                select(DeviceCommand)
                .where(
                    DeviceCommand.state == "pending",
                    DeviceCommand.issued_at < clock() - ACK_TIMEOUT,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for command in late:
            command.state = "timeout"
            metrics.COMMANDS.labels("timeout").inc()
            log.warning("command %s to %s: no ack within 15 s", command.id, command.device_id)
            await audit.record(
                session,
                "device.command_finished",
                actor_id=command.requested_by,
                actor_role=command.requested_role,
                target_type="device",
                target_id=command.device_id,
                building_id=command.building_id,
                details={
                    "command": str(command.id),
                    "type": command.type,
                    "action": command.action,
                    "outcome": "timeout",
                },
            )
        await _notify(session, [command_event(c) for c in late])
    return len(late)
