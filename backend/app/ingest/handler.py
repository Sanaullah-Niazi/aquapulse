"""Check and store device messages (FR-22, FR-72 MQTT part).

For every message, in this order:
1. topic must be `aquapulse/v1/dev/{deviceId}/{channel}`;
2. the device must be registered and active (unknown or revoked: rejected);
3. size <= 2 KB, valid JSON, valid against the contract schema;
4. telemetry time within 10 minutes of server time (live data only);
5. stored; a repeated (deviceId, bootId, seq) is stored only once.

Rejected messages are counted by reason (`ingest_rejected_total`), and logged
with the device ID and reason only, never the payload (NFR-15).

The same code loads the demo backfill (FR-08), so demo history goes through
exactly the same checks as live data; only the clock check is skipped.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, contract, db
from .. import events as bus
from ..models import Device, DeviceCommand, Tank, TankReading, Telemetry
from . import metrics
from .counter import Reading, litres_since

log = logging.getLogger("aquapulse.ingest")

CLOCK_TOLERANCE = timedelta(minutes=10)
MAX_PUMP_GAP_S = 900.0  # pump time is not counted across longer gaps (device was offline)
ACK_STATES = {"ok": "succeeded", "rejected": "rejected", "failed": "failed"}


@dataclass(frozen=True)
class Incoming:
    topic: str
    payload: bytes
    received_at: datetime


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    kind: str
    status: str
    building_id: uuid.UUID
    home_id: uuid.UUID | None
    tank_id: uuid.UUID | None


@dataclass
class _TankLast:
    time: datetime
    pump_on: bool


@dataclass
class BatchResult:
    stored: int = 0
    duplicates: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    finished_commands: set[str] = field(default_factory=set)  # devices that can get the next one

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


class Handler:
    def __init__(
        self,
        *,
        live: bool = True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.live = live  # False for the backfill: no clock check, no live events
        self.clock = clock
        self._devices: dict[str, DeviceInfo] = {}
        self._devices_loaded_at: datetime | None = None
        self._last: dict[str, Reading | None] = {}  # last counted reading per home device
        self._tank_last: dict[str, _TankLast | None] = {}

    # ----- device registry -----

    async def _device(self, session: AsyncSession, device_id: str) -> DeviceInfo | None:
        now = self.clock()
        stale = self._devices_loaded_at is None or now - self._devices_loaded_at > timedelta(
            seconds=30
        )
        if stale or device_id not in self._devices:
            # Re-read the (small) devices table: picks up new and revoked devices.
            rows = await session.execute(
                select(
                    Device.id, Device.kind, Device.status, Device.building_id,
                    Device.home_id, Device.tank_id,
                )
            )  # fmt: skip
            self._devices = {r.id: DeviceInfo(*r) for r in rows}
            self._devices_loaded_at = now
        return self._devices.get(device_id)

    def forget(self) -> None:
        """Drop every cache (after a failed batch, whose changes were rolled back)."""
        self._devices_loaded_at = None
        self._last.clear()
        self._tank_last.clear()

    # ----- main entry point -----

    async def handle(self, messages: list[Incoming]) -> BatchResult:
        """Check and store a batch of messages in one database transaction."""
        result = BatchResult()
        async with db.sessions()() as session, session.begin():
            telemetry_rows: list[dict[str, Any]] = []
            tank_rows: list[dict[str, Any]] = []
            events: list[dict[str, Any]] = []
            seen: dict[str, datetime] = {}
            for message in messages:
                try:
                    await self._one(
                        session, message, result, telemetry_rows, tank_rows, events, seen
                    )
                except contract.ContractError as err:
                    result.reject(err.reason)
                    log.info("rejected %s: %s", _device_label(message.topic), err.reason)

            stored = await self._insert(session, Telemetry, telemetry_rows)
            stored += await self._insert(session, TankReading, tank_rows)
            result.stored = stored
            result.duplicates = len(telemetry_rows) + len(tank_rows) - stored
            if self.live:
                for device_id, when in seen.items():
                    await session.execute(
                        update(Device)
                        .where(Device.id == device_id)
                        .values(last_seen_at=when, online=True)
                    )
                await _notify(session, events)

        for reason, count in result.rejected.items():
            metrics.REJECTED.labels(reason).inc(count)
        metrics.STORED.inc(result.stored)
        metrics.DUPLICATES.inc(result.duplicates)
        return result

    async def _insert(self, session: AsyncSession, model, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        # ON CONFLICT DO NOTHING: a repeated (time, device, boot, seq) is stored once.
        statement = insert(model).values(rows).on_conflict_do_nothing()
        return (await session.execute(statement)).rowcount or 0

    async def _one(
        self,
        session: AsyncSession,
        message: Incoming,
        result: BatchResult,
        telemetry_rows: list[dict[str, Any]],
        tank_rows: list[dict[str, Any]],
        events: list[dict[str, Any]],
        seen: dict[str, datetime],
    ) -> None:
        device_id, channel = contract.parse_topic(message.topic)
        if channel == "cmd":
            raise contract.ContractError("bad_topic")  # our own commands: never stored
        metrics.RECEIVED.labels(channel).inc()
        device = await self._device(session, device_id)
        if device is None:
            raise contract.ContractError("unknown_device")
        if device.status != "active":
            raise contract.ContractError("revoked_device")
        payload = contract.decode(device_id, channel, message.payload)

        if channel == "telemetry":
            when = contract.parse_ts(payload["ts"])
            if self.live and abs(when - message.received_at) > CLOCK_TOLERANCE:
                raise contract.ContractError("clock")
            seen[device_id] = message.received_at
            if device.kind == "home":
                row = await self._home_row(session, device, payload, when)
                telemetry_rows.append(row)
                events.append(_telemetry_event(device, row))
            else:
                row = await self._tank_row(session, device, payload, when)
                tank_rows.append(row)
                events.append(_tank_event(device, row, payload))
                await self._sync_tank(session, device, payload)
        elif channel == "status":
            if self.live:
                online = payload["state"] == "online"
                await session.execute(
                    update(Device).where(Device.id == device_id).values(online=online)
                )
                events.append(_device_event(device, {"online": online}))
        elif channel == "state":
            if self.live:
                await session.execute(
                    update(Device)
                    .where(Device.id == device_id)
                    .values(state=payload, fw_version=payload["fw"])
                )
                if device.kind == "tank":
                    await self._sync_tank(session, device, payload)
                events.append(_device_event(device, {"state": payload}))
        elif self.live and await self._ack(session, device, payload, events):  # channel == "ack"
            result.finished_commands.add(device_id)

    # ----- telemetry -----

    async def _home_row(
        self, session: AsyncSession, device: DeviceInfo, p: dict[str, Any], when: datetime
    ) -> dict[str, Any]:
        if device.id not in self._last:
            # Live: continue from the newest stored reading. Backfill: the history
            # is older than anything stored, so it starts its own chain.
            self._last[device.id] = await _latest_reading(session, device.id) if self.live else None
        new = Reading(when, uuid.UUID(p["bootId"]), p["seq"], float(p["totalL"]))
        step = litres_since(self._last[device.id], new)
        self._last[device.id] = step.baseline
        if step.note in ("counter_down", "counter_jump"):
            log.info("%s: %s, counting from the new value", device.id, step.note)
            metrics.COUNTER_RESETS.labels(step.note).inc()
        return {
            "time": when,
            "device_id": device.id,
            "boot_id": new.boot_id,
            "seq": new.seq,
            "home_id": device.home_id,
            "building_id": device.building_id,
            "total_l": new.total_l,
            "delta_l": step.delta_l,
            "flow_lpm": p["flowLpm"],
            "ph": p["ph"],
            "temp_c": p["tempC"],
            "valve": p["valve"],
            "rssi": p["rssi"],
        }

    async def _tank_row(
        self, session: AsyncSession, device: DeviceInfo, p: dict[str, Any], when: datetime
    ) -> dict[str, Any]:
        if device.id not in self._tank_last and not self.live:
            self._tank_last[device.id] = None
        if device.id not in self._tank_last:
            row = await session.execute(
                select(TankReading.time, TankReading.pump_on)
                .where(TankReading.device_id == device.id)
                .order_by(TankReading.time.desc())
                .limit(1)
            )
            last = row.first()
            self._tank_last[device.id] = _TankLast(last.time, last.pump_on) if last else None
        last = self._tank_last[device.id]
        pump_s = 0.0
        if last is not None and when > last.time:
            gap = (when - last.time).total_seconds()
            if last.pump_on and gap <= MAX_PUMP_GAP_S:
                pump_s = gap  # the pump ran for the whole time since the previous reading
        if last is None or when > last.time:
            self._tank_last[device.id] = _TankLast(when, p["pumpOn"])
        return {
            "time": when,
            "device_id": device.id,
            "boot_id": uuid.UUID(p["bootId"]),
            "seq": p["seq"],
            "tank_id": device.tank_id,
            "level_pct": p["levelPct"],
            "level_l": p["levelL"],
            "pump_on": p["pumpOn"],
            "pump_s": pump_s,
            "sump_ok": p["sumpOk"],
            "sensor_ok": p["sensorOk"],
            "dry_run_protect": p["dryRunProtect"],
            "overflow_protect": p["overflowProtect"],
            "mode": p["mode"],
        }

    async def _sync_tank(self, session: AsyncSession, device: DeviceInfo, p: dict[str, Any]):
        """Keep the tanks table equal to what the device itself reports."""
        if self.live:
            await session.execute(
                update(Tank)
                .where(Tank.id == device.tank_id)
                .values(mode=p["mode"], low_pct=p["lowPct"], high_pct=p["highPct"])
            )

    # ----- command answers -----

    async def _ack(
        self,
        session: AsyncSession,
        device: DeviceInfo,
        p: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> bool:
        if p["cmdId"] is None:
            return False  # an answer to a message that had no readable cmdId
        command = await session.get(DeviceCommand, uuid.UUID(p["cmdId"]), with_for_update=True)
        if command is None or command.device_id != device.id:
            metrics.REJECTED.labels("unknown_command").inc()
            return False
        if command.state not in ("pending", "timeout"):
            return False  # e.g. the DUPLICATE answer to a command that already finished
        if p["reason"] == "DUPLICATE":
            return False  # the device saw it twice; its first answer is the one that counts
        late = command.state == "timeout"
        command.state = ACK_STATES[p["result"]]
        command.result_reason = p["reason"]
        command.result_state = p["state"]
        command.acked_at = contract.parse_ts(p["ts"])
        metrics.COMMANDS.labels(command.state).inc()
        if command.issued_at is not None:
            metrics.COMMAND_SECONDS.observe(
                max(0.0, (self.clock() - command.issued_at).total_seconds())
            )
        await audit.record(
            session,
            "device.command_finished",
            actor_id=command.requested_by,
            actor_role=command.requested_role,
            target_type="device",
            target_id=device.id,
            building_id=command.building_id,
            details={
                "command": str(command.id),
                "type": command.type,
                "action": command.action,
                "outcome": command.state,
                "reason": command.result_reason,
                "late": late,
            },
        )
        events.append(command_event(command))
        return True


# ----- helpers -----


async def _latest_reading(session: AsyncSession, device_id: str) -> Reading | None:
    row = (
        await session.execute(
            select(Telemetry.time, Telemetry.boot_id, Telemetry.seq, Telemetry.total_l)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.time.desc())
            .limit(1)
        )
    ).first()
    return Reading(row.time, row.boot_id, row.seq, row.total_l) if row else None


def _device_label(topic: str) -> str:
    try:
        return contract.parse_topic(topic)[0]
    except contract.ContractError:
        return "(bad topic)"


async def _notify(session: AsyncSession, events: list[dict[str, Any]]) -> None:
    """Tell the API (and through it, the WebSockets) what just happened (FR-25).
    NOTIFY is delivered when this transaction commits, never before."""
    for event in events:
        await bus.notify(session, bus.EVENTS, event)


def _telemetry_event(device: DeviceInfo, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "telemetry",
        "buildingId": device.building_id,
        "homeId": device.home_id,
        "deviceId": device.id,
        "time": contract.iso(row["time"]),
        "totalL": row["total_l"],
        "deltaL": round(row["delta_l"], 3),
        "flowLpm": row["flow_lpm"],
        "ph": row["ph"],
        "tempC": row["temp_c"],
        "valve": row["valve"],
    }


def _tank_event(device: DeviceInfo, row: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "tank",
        "buildingId": device.building_id,
        "tankId": device.tank_id,
        "deviceId": device.id,
        "time": contract.iso(row["time"]),
        "levelPct": p["levelPct"],
        "pumpOn": p["pumpOn"],
        "mode": p["mode"],
        "sumpOk": p["sumpOk"],
        "dryRunProtect": p["dryRunProtect"],
        "overflowProtect": p["overflowProtect"],
    }


def _device_event(device: DeviceInfo, values: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "device",
        "buildingId": device.building_id,
        "homeId": device.home_id,
        "tankId": device.tank_id,
        "deviceId": device.id,
        **values,
    }


def command_event(command: DeviceCommand) -> dict[str, Any]:
    return {
        "type": "command",
        "buildingId": command.building_id,
        "homeId": command.home_id,
        "deviceId": command.device_id,
        "commandId": command.id,
        "commandType": command.type,
        "action": command.action,
        "state": command.state,
        "resultReason": command.result_reason,
        "resultState": command.result_state,
    }
