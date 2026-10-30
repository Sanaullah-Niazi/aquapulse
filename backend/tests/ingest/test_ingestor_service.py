"""The ingestor end to end, with the real MQTT broker (started by `make up` or CI):
a device publishes, the reading is stored within a second (FR-22 criterion 1),
and a queued command reaches the device and its ack finishes it (FR-24)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from datetime import UTC, datetime

import aiomqtt
import pytest
from sqlalchemy import select

from app import contract, db
from app.config import get_settings
from app.ingest import service
from app.ingest.service import Ingestor
from app.models import Telemetry

from .helpers import ack, command, get_command, home_telemetry


@pytest.fixture
async def ingestor():
    """A running ingestor with its own throw-away MQTT session."""
    worker = Ingestor(client_id=f"test-ingestor-{uuid.uuid4().hex[:8]}", persistent=False)
    task = asyncio.create_task(worker.run())
    yield worker
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _broker() -> aiomqtt.Client:
    settings = get_settings()
    return aiomqtt.Client(settings.mqtt_host, settings.mqtt_port)


async def _wait_for(check, seconds: float = 5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = await check()
        if found:
            return found
        await asyncio.sleep(0.05)
    return None


async def test_published_reading_is_stored_within_a_second(make, ingestor):
    world = await make.world()
    device = await make.device(world.home)
    await asyncio.sleep(1.0)  # let the ingestor connect and subscribe
    async with _broker() as client:
        now = datetime.now(UTC)
        started = time.monotonic()
        await client.publish(
            contract.topic(device.id, "telemetry"),
            json.dumps(home_telemetry(0, 42.0, at=now)),
            qos=0,
        )

        async def stored():
            async with db.sessions()() as session:
                return await session.scalar(
                    select(Telemetry).where(Telemetry.device_id == device.id)
                )

        row = await _wait_for(stored)
    assert row is not None and row.total_l == 42.0
    assert time.monotonic() - started < 1.0


async def test_queued_command_reaches_the_device_and_its_ack_finishes_it(make, ingestor):
    world = await make.world()
    device = await make.device(world.home)
    async with _broker() as fake_device:
        await fake_device.subscribe(contract.topic(device.id, "cmd"), qos=1)
        await asyncio.sleep(1.0)
        cmd = await command(device, state="queued", action="close")
        ingestor.command_wake.set()  # what the API's NOTIFY does

        async def next_command():
            async for msg in fake_device.messages:
                return msg

        msg = await asyncio.wait_for(next_command(), 5)
        assert not msg.retain  # FR-13 criterion 1
        payload = json.loads(msg.payload)
        assert payload["cmdId"] == str(cmd.id) and payload["action"] == "close"
        await fake_device.publish(
            contract.topic(device.id, "ack"),
            json.dumps(ack(cmd.id, at=datetime.now(UTC))),
            qos=1,
        )

        async def finished():
            saved = await get_command(cmd.id)
            return saved if saved.state == "succeeded" else None

        assert await _wait_for(finished) is not None


async def test_store_waits_for_the_database_and_then_succeeds(monkeypatch):
    """A failed batch is retried, not dropped."""
    worker = Ingestor()
    calls = []

    async def flaky(batch):
        calls.append(len(batch))
        if len(calls) == 1:
            raise OSError("database restarting")
        return type("R", (), {"finished_commands": {"home-x"}})()

    monkeypatch.setattr(worker.handler, "handle", flaky)
    monkeypatch.setattr(service.asyncio, "sleep", _no_sleep)
    await worker.store(["m1", "m2"])
    assert calls == [2, 2] and worker.command_wake.is_set()


async def test_one_bad_message_does_not_block_the_batch(monkeypatch):
    """After 3 failed tries the batch is stored message by message."""
    worker = Ingestor()
    stored = []

    async def picky(batch):
        if len(batch) > 1 or batch[0] == "bad":
            raise ValueError("cannot store")
        stored.append(batch[0])
        return type("R", (), {"finished_commands": set()})()

    monkeypatch.setattr(worker.handler, "handle", picky)
    monkeypatch.setattr(service.asyncio, "sleep", _no_sleep)
    await worker.store(["good1", "bad", "good2"])
    assert stored == ["good1", "good2"]


async def _no_sleep(_seconds):
    return None


def test_error_kinds_are_listed_without_messages():
    err = ExceptionGroup("x", [OSError("secret detail"), ValueError("other secret")])
    assert service._kind(err) == "OSError, ValueError"
