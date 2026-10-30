"""Shared helpers for the simulator tests.

Unit tests never use a real clock or broker: time is just a number we move
forward, so a simulated week takes seconds and every run gives the same result.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aquapulse_sim import contract
from aquapulse_sim.building import Building
from aquapulse_sim.config import Config
from aquapulse_sim.device import Device, Outgoing
from aquapulse_sim.store import FlashStore

# Monday 5 October 2026, 00:00 in Karachi: a fixed, ordinary week.
START = datetime(2026, 10, 5, tzinfo=ZoneInfo("Asia/Karachi")).timestamp()


@pytest.fixture
def cfg() -> Config:
    return Config()  # defaults: 10 homes, 10 s interval, seed 42, Asia/Karachi


@pytest.fixture
def building(cfg: Config) -> Building:
    return Building(cfg, START, FlashStore(None))


def run_for(building: Building, start: float, seconds: float, step: float = 1.0) -> float:
    """Tick the building forward like the real 1-second loop. Returns the end time."""
    t = start
    while t < start + seconds:
        t = min(t + step, start + seconds)
        building.tick(t)
    return t


def make_cmd(now: float, type_: str = "valve", ttl: float = 30, **fields) -> bytes:
    cmd = {
        "v": 1,
        "cmdId": fields.pop("cmd_id", str(uuid.uuid4())),
        "type": type_,
        "issuedAt": contract.iso(now),
        "expiresAt": contract.iso(now + ttl),
        "by": "owner",
        **fields,
    }
    return json.dumps(cmd).encode()


def drain(device: Device) -> list[Outgoing]:
    items = list(device.outbox)
    device.outbox.clear()
    return items


def acks(device: Device) -> list[dict]:
    return [item.payload for item in drain(device) if item.channel == "ack"]
