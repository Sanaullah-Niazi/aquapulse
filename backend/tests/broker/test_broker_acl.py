"""FR-11: every account may only use its own topics (against the real broker).

Needs the stack's broker (`make up`, or CI). The test device is home-test-01
(see tests/mqtt_helpers.py); "home-acl-02" is a made-up neighbour, so no real
device is ever touched.
"""

from __future__ import annotations

import asyncio
import uuid

import aiomqtt
import pytest

from ..mqtt_helpers import TEST_DEVICE, as_test_device, broker

OWN = f"aquapulse/v1/dev/{TEST_DEVICE}"
NEIGHBOUR = "aquapulse/v1/dev/home-acl-02"


async def _received(client: aiomqtt.Client, seconds: float, until: str | None = None) -> list:
    """Messages that arrive within `seconds` (stops early when `until` text arrives)."""
    found = []
    try:
        async with asyncio.timeout(seconds):
            async for message in client.messages:
                found.append((str(message.topic), message.payload.decode()))
                if until and until in found[-1][1]:
                    break
    except TimeoutError:
        pass
    return found


@pytest.mark.parametrize(
    ("username", "password"),
    [(None, None), (TEST_DEVICE, "wrong-password"), ("nobody", "x"), ("monitor", "")],
    ids=["anonymous", "wrong-password", "unknown-user", "empty-password"],
)
async def test_login_without_the_right_password_is_refused(username, password):
    """Criterion 1."""
    with pytest.raises(aiomqtt.MqttError):
        async with broker(username, password):
            pass


async def test_device_cannot_publish_to_another_homes_topics():
    """Criterion 2: the broker drops it; nobody ever receives it."""
    marker = uuid.uuid4().hex
    async with broker("monitor") as watcher, as_test_device() as device:
        await watcher.subscribe([(f"{NEIGHBOUR}/telemetry", 1), (f"{OWN}/telemetry", 1)])
        await asyncio.sleep(0.3)
        await device.publish(f"{NEIGHBOUR}/telemetry", f"forged {marker}", qos=1)
        await device.publish(f"{OWN}/telemetry", f"own {marker}", qos=1)  # allowed
        got = await _received(watcher, 5, until=f"own {marker}")
    assert (f"{OWN}/telemetry", f"own {marker}") in got
    assert not any("forged" in payload for _, payload in got)


async def test_device_cannot_read_another_homes_commands_or_everything():
    """Criteria 2 and 3: subscribing to a neighbour's cmd topic or to # gets nothing
    but the device's own commands."""
    marker = uuid.uuid4().hex
    async with as_test_device() as device, broker("commander") as server:
        await device.subscribe([(f"{NEIGHBOUR}/cmd", 1), ("#", 1), ("aquapulse/#", 1)])
        await asyncio.sleep(0.3)
        await server.publish(f"{NEIGHBOUR}/cmd", f"theirs {marker}", qos=1)
        await server.publish(f"{OWN}/cmd", f"mine {marker}", qos=1)
        got = await _received(device, 5)
    topics = {topic for topic, _ in got}
    assert topics == {f"{OWN}/cmd"}  # its own command (once per matching subscription)
    assert not any("theirs" in payload for _, payload in got)


async def test_only_the_command_account_can_send_commands():
    """Criterion 4: the ingestor's account reads, but cannot send commands."""
    marker = uuid.uuid4().hex
    async with (
        as_test_device() as device,
        broker("ingestor") as ingestor,
        broker("commander") as commander,
    ):
        await device.subscribe(f"{OWN}/cmd", qos=1)
        await asyncio.sleep(0.3)
        await ingestor.publish(f"{OWN}/cmd", f"from-ingestor {marker}", qos=1)
        await commander.publish(f"{OWN}/cmd", f"from-commander {marker}", qos=1)
        got = await _received(device, 5, until=f"from-commander {marker}")
    payloads = [payload for _, payload in got]
    assert f"from-commander {marker}" in payloads
    assert f"from-ingestor {marker}" not in payloads


async def test_the_ingestor_account_reads_device_messages():
    marker = uuid.uuid4().hex
    async with broker("ingestor") as ingestor, as_test_device() as device:
        await ingestor.subscribe(f"{OWN}/telemetry", qos=1)
        await asyncio.sleep(0.3)
        await device.publish(f"{OWN}/telemetry", marker, qos=1)
        got = await _received(ingestor, 5, until=marker)
    assert (f"{OWN}/telemetry", marker) in got
