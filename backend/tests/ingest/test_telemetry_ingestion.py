"""FR-22 and FR-72 (MQTT part): every device message is checked before it is stored."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import select

from app import db
from app.ingest.handler import Handler, Incoming
from app.models import Device, Tank, TankReading, Telemetry

from .helpers import NOW, clock, home_telemetry, message, tank_telemetry


def _rejected(reason: str) -> float:
    return REGISTRY.get_sample_value("ingest_rejected_total", {"reason": reason}) or 0.0


async def _rows(model, device_id: str) -> list:
    async with db.sessions()() as session:
        return list(
            await session.scalars(
                select(model).where(model.device_id == device_id).order_by(model.time)
            )
        )


@pytest.fixture
async def home_device(make):
    world = await make.world()
    return await make.device(world.home)


async def test_valid_readings_are_stored_with_litres_from_the_counter(home_device):
    handler = Handler(clock=clock)
    result = await handler.handle(
        [
            message(home_device.id, "telemetry", home_telemetry(0, 1000.0)),
            message(
                home_device.id,
                "telemetry",
                home_telemetry(1, 1004.5, at=NOW + timedelta(seconds=10)),
                NOW + timedelta(seconds=10),
            ),
        ]
    )
    assert result.stored == 2 and not result.rejected
    rows = await _rows(Telemetry, home_device.id)
    assert [r.delta_l for r in rows] == [0.0, 4.5]
    assert rows[0].home_id == home_device.home_id
    async with db.sessions()() as session:
        device = await session.get(Device, home_device.id)
    assert device.online and device.last_seen_at is not None


async def test_duplicates_are_stored_once(home_device):
    """FR-22 criterion 2: same deviceId, bootId and seq = one row."""
    reading = message(home_device.id, "telemetry", home_telemetry(5, 2000.0))
    first = await Handler(clock=clock).handle([reading, reading])
    again = await Handler(clock=clock).handle([reading])  # e.g. after an ingestor restart
    assert (first.stored, first.duplicates) == (1, 1)
    assert (again.stored, again.duplicates) == (0, 1)
    assert len(await _rows(Telemetry, home_device.id)) == 1


async def test_unknown_and_revoked_devices_are_rejected_and_counted(make):
    """FR-22 criterion 3."""
    world = await make.world()
    revoked = await make.device(world.home, status="revoked")
    before = (_rejected("unknown_device"), _rejected("revoked_device"))
    result = await Handler(clock=clock).handle(
        [
            message("home-nobody", "telemetry", home_telemetry(0, 1.0)),
            message(revoked.id, "telemetry", home_telemetry(0, 1.0)),
        ]
    )
    assert result.stored == 0
    assert result.rejected == {"unknown_device": 1, "revoked_device": 1}
    assert _rejected("unknown_device") == before[0] + 1
    assert _rejected("revoked_device") == before[1] + 1


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"not json at all", "not_json"),
        (b"{" + b" " * 2100 + b"}", "too_large"),  # FR-10: 2 KB at most
        (home_telemetry(0, 1.0, ph=99), "schema"),  # out of range
        (home_telemetry(0, 1.0, extra="field"), "schema"),  # unknown fields are rejected
        ({"v": 1}, "schema"),  # required fields missing
        (home_telemetry(0, 1.0, at=NOW - timedelta(minutes=11)), "clock"),  # FR-22: +-10 min
        (home_telemetry(0, 1.0, at=NOW + timedelta(minutes=11)), "clock"),
    ],
)
async def test_invalid_messages_are_rejected_by_reason(home_device, payload, reason):
    result = await Handler(clock=clock).handle([message(home_device.id, "telemetry", payload)])
    assert result.rejected == {reason: 1} and result.stored == 0


async def test_bad_topics_are_rejected(home_device):
    bad = [
        Incoming(topic, json.dumps(home_telemetry(0, 1.0)).encode(), NOW)
        for topic in (
            "aquapulse/v2/dev/x/telemetry",
            "aquapulse/v1/dev/x",
            f"aquapulse/v1/dev/{home_device.id}/cmd",  # our own commands are never stored
        )
    ]
    result = await Handler(clock=clock).handle(bad)
    assert result.rejected == {"bad_topic": 3}


async def test_rejection_logs_never_contain_the_payload(home_device, caplog):
    """NFR-15: device ID and reason only."""
    secret = "SECRET-" + uuid.uuid4().hex
    with caplog.at_level("INFO", logger="aquapulse.ingest"):
        await Handler(clock=clock).handle(
            [message(home_device.id, "telemetry", home_telemetry(0, 1.0, fw=secret))]
        )
    assert home_device.id in caplog.text and "schema" in caplog.text
    assert secret not in caplog.text


async def test_status_and_state_update_the_device(home_device):
    handler = Handler(clock=clock)
    state = {
        "v": 1,
        "ts": "2026-10-30T08:00:00Z",
        "valve": "closed",
        "intervalS": 10,
        "fw": "1.0.1",
    }
    await handler.handle(
        [
            message(home_device.id, "status", {"v": 1, "state": "online", "ts": state["ts"]}),
            message(home_device.id, "state", state),
        ]
    )  # fmt: skip
    async with db.sessions()() as session:
        device = await session.get(Device, home_device.id)
    assert device.online and device.state["valve"] == "closed" and device.fw_version == "1.0.1"

    await handler.handle([message(home_device.id, "status", {"v": 1, "state": "offline"})])
    async with db.sessions()() as session:
        assert not (await session.get(Device, home_device.id)).online


async def test_counter_continues_across_handler_restarts(home_device):
    """The ingestor remembers the last reading in the database, not only in memory."""
    await Handler(clock=clock).handle(
        [message(home_device.id, "telemetry", home_telemetry(0, 100.0))]
    )
    later = NOW + timedelta(seconds=10)
    await Handler(clock=lambda: later).handle(
        [message(home_device.id, "telemetry", home_telemetry(1, 103.0, at=later), later)]
    )
    assert [r.delta_l for r in await _rows(Telemetry, home_device.id)] == [0.0, 3.0]


async def test_tank_readings_count_pump_time_and_sync_settings(make):
    world = await make.world()
    tank = await make.tank(world.building)
    device = await make.device(tank=tank)
    handler = Handler(clock=clock)
    t1, t2 = NOW + timedelta(seconds=10), NOW + timedelta(seconds=20)
    await handler.handle(
        [
            message(device.id, "telemetry", tank_telemetry(0, 40.0, True)),
            message(device.id, "telemetry", tank_telemetry(1, 41.0, True, at=t1), t1),
            message(device.id, "telemetry",
                    tank_telemetry(2, 42.0, False, at=t2, lowPct=30, highPct=90), t2),
        ]
    )  # fmt: skip
    rows = await _rows(TankReading, device.id)
    assert [r.pump_s for r in rows] == [0.0, 10.0, 10.0]  # pump was on before each reading
    async with db.sessions()() as session:
        saved = await session.get(Tank, tank.id)
    assert (saved.low_pct, saved.high_pct) == (30, 90)  # what the device reports


async def test_backfill_mode_skips_the_clock_check_and_live_side_effects(home_device):
    old = NOW - timedelta(days=30)
    result = await Handler(live=False, clock=clock).handle(
        [message(home_device.id, "telemetry", home_telemetry(0, 5.0, at=old))]
    )
    assert result.stored == 1
    async with db.sessions()() as session:
        assert (await session.get(Device, home_device.id)).last_seen_at is None
