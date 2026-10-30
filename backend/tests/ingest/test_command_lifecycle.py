"""FR-13 and FR-24: commands are sent safely, one at a time, and tracked to the end.

queued -> pending (sent) -> succeeded / failed / rejected (the device's ack)
                         -> timeout (no ack within 15 s)
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import ClassVar

import pytest

from app import contract
from app.ingest import commands
from app.ingest.handler import Handler
from app.ingest.service import Ingestor

from ..conftest import audit_actions
from .helpers import NOW, ack, clock, command, get_command, message


class FakeBroker:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.fail = fail

    async def publish(self, topic: str, payload: bytes) -> None:
        if self.fail:
            raise OSError("broker gone")
        self.sent.append((topic, json.loads(payload)))


@pytest.fixture
async def valve_device(make):
    world = await make.world()
    return await make.device(world.home)


async def test_queued_command_is_sent_with_expiry_and_marked_pending(valve_device):
    cmd = await command(valve_device, state="queued", action="close")
    broker = FakeBroker()
    assert await commands.dispatch(broker.publish, clock=clock) >= 1
    topic, payload = next(s for s in broker.sent if s[1]["cmdId"] == str(cmd.id))
    assert topic == f"aquapulse/v1/dev/{valve_device.id}/cmd"
    assert payload["type"] == "valve" and payload["action"] == "close"
    assert payload["by"] == "owner"
    # FR-13: expires 30 s after it is issued
    issued, expires = (
        contract.parse_ts(payload["issuedAt"]),
        contract.parse_ts(payload["expiresAt"]),
    )
    assert expires - issued == timedelta(seconds=30)
    saved = await get_command(cmd.id)
    assert saved.state == "pending" and saved.issued_at == NOW


async def test_two_quick_commands_go_one_at_a_time_in_order(valve_device):
    """FR-24 criterion 3: the second waits for the first ack, so the last one wins."""
    first = await command(valve_device, state="queued", action="close", created_at=NOW)
    second = await command(
        valve_device, state="queued", action="open", created_at=NOW + timedelta(seconds=1)
    )
    broker = FakeBroker()
    await commands.dispatch(broker.publish, clock=clock)
    mine = [p for _, p in broker.sent if p["cmdId"] in (str(first.id), str(second.id))]
    assert [p["action"] for p in mine] == ["close"]

    await commands.dispatch(broker.publish, clock=clock)  # still waiting for the ack
    assert (await get_command(second.id)).state == "queued"

    await Handler(clock=clock).handle([message(valve_device.id, "ack", ack(first.id))])
    await commands.dispatch(broker.publish, clock=clock)
    mine = [p for _, p in broker.sent if p["cmdId"] in (str(first.id), str(second.id))]
    assert [p["action"] for p in mine] == ["close", "open"]


async def test_failed_publish_puts_the_command_back_in_the_queue(valve_device):
    cmd = await command(valve_device, state="queued")
    with pytest.raises(OSError):
        await commands.dispatch(FakeBroker(fail=True).publish, clock=clock)
    saved = await get_command(cmd.id)
    assert saved.state == "queued" and saved.issued_at is None


@pytest.mark.parametrize(
    ("result", "reason", "state", "expected"),
    [
        ("ok", None, "closed", "succeeded"),
        ("rejected", "BUSY", "moving", "rejected"),
        ("rejected", "EXPIRED", None, "rejected"),
        ("failed", "VALVE_STUCK", "fault", "failed"),
    ],
)
async def test_ack_decides_the_outcome(valve_device, result, reason, state, expected):
    """FR-24 criterion 1 and 4: each outcome, and it is audited."""
    cmd = await command(valve_device)
    done = await Handler(clock=clock).handle(
        [message(valve_device.id, "ack", ack(cmd.id, result, reason, state))]
    )
    assert valve_device.id in done.finished_commands
    saved = await get_command(cmd.id)
    assert (saved.state, saved.result_reason, saved.result_state) == (expected, reason, state)
    assert "device.command_finished" in await audit_actions(target_id=valve_device.id)


async def test_no_ack_within_15_seconds_is_a_timeout(valve_device):
    cmd = await command(valve_device, issued=NOW)
    assert await commands.expire(clock=lambda: NOW + timedelta(seconds=14)) == 0
    await commands.expire(clock=lambda: NOW + timedelta(seconds=16))
    assert (await get_command(cmd.id)).state == "timeout"
    assert "device.command_finished" in await audit_actions(target_id=valve_device.id)


async def test_late_ack_after_timeout_still_records_the_truth(valve_device):
    """The device's ack is the only proof of what happened, even if it is late."""
    cmd = await command(valve_device, state="timeout")
    await Handler(clock=clock).handle([message(valve_device.id, "ack", ack(cmd.id))])
    assert (await get_command(cmd.id)).state == "succeeded"


async def test_duplicate_answer_does_not_change_the_result(valve_device):
    """FR-13 criterion 2: a resent cmdId gets DUPLICATE; the first answer counts."""
    cmd = await command(valve_device)
    handler = Handler(clock=clock)
    await handler.handle([message(valve_device.id, "ack", ack(cmd.id))])
    await handler.handle(
        [message(valve_device.id, "ack", ack(cmd.id, "rejected", "DUPLICATE", None))]
    )
    assert (await get_command(cmd.id)).state == "succeeded"


async def test_acks_for_unknown_or_other_devices_commands_are_ignored(make, valve_device):
    other_world = await make.world()
    other = await make.device(other_world.home)
    cmd = await command(valve_device)
    handler = Handler(clock=clock)
    # Another device cannot finish this device's command.
    await handler.handle([message(other.id, "ack", ack(cmd.id))])
    await handler.handle([message(other.id, "ack", ack(None, "rejected", "INVALID", None))])
    assert (await get_command(cmd.id)).state == "pending"


async def test_commands_are_never_retained():
    """FR-13 criterion 1: the ingestor publishes with QoS 1 and retain=False."""

    class Client:
        calls: ClassVar[list[dict]] = []

        async def publish(self, topic, payload, **options):
            self.calls.append(options)
            raise RuntimeError("stop here")

    ingestor = Ingestor()
    ingestor.command_wake.set()

    async def dispatch(publish, clock=None):
        await publish("t", b"{}")

    original = commands.dispatch
    commands.dispatch = dispatch
    try:
        with pytest.raises(RuntimeError):
            await ingestor.commander(Client())
    finally:
        commands.dispatch = original
    assert Client.calls == [{"qos": 1, "retain": False, "timeout": 5}]
