"""FR-08: demo history goes through the same checks as live data, and loading
it twice changes nothing."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select, text

from app import backfill, contract, db
from app.ingest.handler import Handler
from app.models import DeviceEvent, Telemetry

from .helpers import NOW, clock, home_telemetry, message

HISTORY_BOOT = "1b8e0f43-9b5e-4d26-9d55-3f0e8a1c2b7d"


def _line(device_id: str, payload: dict) -> str:
    return json.dumps({"topic": contract.topic(device_id, "telemetry"), "payload": payload})


def _label(device_id: str, start, kind: str = "leak") -> str:
    return json.dumps(
        {
            "label": {
                "deviceId": device_id,
                "kind": kind,
                "start": contract.iso(start),
                "end": contract.iso(start + timedelta(hours=2)),
                "details": {"lpm": 1.2},
            }
        }
    )


async def test_history_is_loaded_before_live_data_once(make):
    world = await make.world()
    device = await make.device(world.home)
    idle = await make.device(await make.home(world.building))  # no live data yet
    # The first live reading: history may only go before it.
    await Handler(clock=clock).handle([message(device.id, "telemetry", home_telemetry(0, 9000.0))])

    old = NOW - timedelta(days=2)
    lines = [
        _label(device.id, old),
        _label(device.id, NOW + timedelta(hours=1)),  # after live data starts: ignored
        *[
            _line(device.id, home_telemetry(
                i, 100.0 + 5 * i, at=old + timedelta(minutes=5 * i), boot=HISTORY_BOOT
            ))
            for i in range(4)
        ],
        _line(device.id, home_telemetry(9, 500.0, at=NOW + timedelta(minutes=5))),  # newer
        _line(idle.id, home_telemetry(0, 1.0, at=old)),
        _line(device.id, {"v": 1, "ts": contract.iso(old - timedelta(hours=1))}),  # broken
        "",
    ]  # fmt: skip
    summary = await backfill.load(lines)
    assert summary.stored == 4 and summary.skipped == 1 and summary.labels == 1
    assert summary.no_live_data == {idle.id}
    assert summary.rejected == {"schema": 1}

    async with db.sessions()() as session:
        rows = list(
            await session.scalars(
                select(Telemetry).where(Telemetry.device_id == device.id).order_by(Telemetry.time)
            )
        )
        # History counts its own litres; the live reading is untouched.
        assert [r.delta_l for r in rows] == [0.0, 5.0, 5.0, 5.0, 0.0]
        events = list(
            await session.scalars(select(DeviceEvent).where(DeviceEvent.device_id == device.id))
        )
        assert [(e.kind, e.source) for e in events] == [("leak", "backfill")]
        # The 15-minute totals were refreshed, so old days show up at once.
        litres = await session.scalar(
            text("SELECT sum(litres) FROM consumption_15m WHERE home_id = :h AND bucket < :t"),
            {"h": world.home.id, "t": NOW - timedelta(days=1)},
        )
        assert litres == 15.0

    again = await backfill.load(lines)
    assert again.stored == 0 and again.labels == 0
