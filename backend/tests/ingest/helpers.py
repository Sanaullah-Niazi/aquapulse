"""Valid device messages for the ingestor tests (the same shapes as /contracts/examples)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app import contract, db
from app.ingest.handler import Incoming
from app.models import DeviceCommand

NOW = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def home_telemetry(
    seq: int, total: float, *, at: datetime = NOW, boot: uuid.UUID | str | None = None, **extra
) -> dict[str, Any]:
    return {
        "v": 1,
        "ts": contract.iso(at),
        "bootId": str(boot or BOOT),
        "seq": seq,
        "totalL": total,
        "flowLpm": 4.5,
        "ph": 7.2,
        "tempC": 24.5,
        "valve": "open",
        "rssi": -60,
        "fw": "1.0.0",
        "boot": seq == 0,
        **extra,
    }


def tank_telemetry(seq: int, level: float, pump_on: bool, *, at: datetime = NOW, **extra):
    return {
        "v": 1,
        "ts": contract.iso(at),
        "bootId": str(BOOT),
        "seq": seq,
        "levelPct": level,
        "levelL": level * 50,
        "capacityL": 5000,
        "pumpOn": pump_on,
        "sumpOk": True,
        "sensorOk": True,
        "dryRunProtect": False,
        "overflowProtect": False,
        "mode": "auto",
        "lowPct": 25,
        "highPct": 95,
        "rssi": -60,
        "fw": "1.0.0",
        "boot": seq == 0,
        **extra,
    }


BOOT = uuid.UUID("6f1c2b8e-3a0d-4f43-9a51-0e8f2a7c1d55")


def message(device_id: str, channel: str, payload: Any, received: datetime = NOW) -> Incoming:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return Incoming(contract.topic(device_id, channel), raw, received)


def ack(command_id, result="ok", reason=None, state="closed", at: datetime = NOW) -> dict:
    return {
        "v": 1,
        "cmdId": str(command_id) if command_id else None,
        "result": result,
        "reason": reason,
        "state": state,
        "ts": contract.iso(at + timedelta(seconds=3)),
    }


async def command(device, state="pending", issued: datetime = NOW, **values) -> DeviceCommand:
    async with db.sessions()() as session, session.begin():
        cmd = DeviceCommand(
            device_id=device.id,
            building_id=device.building_id,
            home_id=device.home_id,
            type=values.pop("type", "valve"),
            action=values.pop("action", "close"),
            requested_role="owner",
            state=state,
            issued_at=issued if state != "queued" else None,
            **values,
        )
        session.add(cmd)
    return cmd


async def get_command(command_id) -> DeviceCommand:
    async with db.sessions()() as session:
        return await session.scalar(select(DeviceCommand).where(DeviceCommand.id == command_id))
