"""Integration tests against the running stack: `make up`, then `make sim-test-live`.

These use the real Mosquitto broker and the simulator container, the same way
the backend does. They cover what unit tests cannot: real connections,
retained messages, the Last Will, timing, and a broker restart.

Since M5 the broker only speaks TLS and wants a password: listening uses the
read-only `monitor` account and sending commands the `commander` account,
with passwords from the repo's .env (like `make` uses).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import shutil
import ssl
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import aiomqtt
import pytest

from aquapulse_sim import contract

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
HOST = os.environ.get("MQTT_HOST", "localhost")
PORT = int(os.environ.get("MQTT_PORT", "8883"))
CA_FILE = os.environ.get("MQTT_CA_FILE", str(REPO / "infra/certs/public/ca.crt"))
API = os.environ.get("SIM_API_URL", "http://localhost:8090")
HOMES = [f"home-b01-{n:02d}" for n in range(1, 11)]
ALL = [*HOMES, "tank-b01-01"]


def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def env(name: str) -> str:
    """A setting from the environment, or else from the repo's .env file."""
    if name in os.environ:
        return os.environ[name]
    for line in (REPO / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip()
    raise KeyError(f"{name} is not set (run make env)")


def client(account: str = "monitor") -> aiomqtt.Client:
    """A TLS connection that checks the broker's certificate, as a server account."""
    return aiomqtt.Client(
        HOST,
        PORT,
        identifier=f"test-{uuid.uuid4().hex[:8]}",
        username=account,
        password=env(f"MQTT_{account.upper()}_PASSWORD"),
        tls_context=ssl.create_default_context(cafile=CA_FILE),
    )


async def collect(topic: str, seconds: float, until=None) -> list[aiomqtt.Message]:
    """Subscribe and gather messages for up to `seconds` (or until `until(messages)`)."""
    found: list[aiomqtt.Message] = []
    async with client() as c:
        await c.subscribe(topic, qos=1)
        try:
            async with asyncio.timeout(seconds):
                async for message in c.messages:
                    found.append(message)
                    if until and until(found):
                        break
        except TimeoutError:
            pass
    return found


def payload(message: aiomqtt.Message) -> dict:
    return json.loads(message.payload)


def device_of(message: aiomqtt.Message) -> str:
    return contract.parse_topic(str(message.topic))[0]


async def wait_all_online(seconds: float) -> set[str]:
    """Return devices whose retained status is online, waiting up to `seconds`."""
    deadline = time.monotonic() + seconds
    online: set[str] = set()
    while time.monotonic() < deadline and online != set(ALL):
        messages = await collect("aquapulse/v1/dev/+/status", 2)
        online = {device_of(m) for m in messages if payload(m)["state"] == "online"}
    return online


async def test_fr01_ten_homes_and_a_tank_are_connected():
    assert await wait_all_online(30) == set(ALL)


async def test_fr03_every_message_on_the_wire_follows_the_contract():
    messages = await collect("aquapulse/v1/dev/+/+", 12)
    telemetry = {device_of(m) for m in messages if str(m.topic).endswith("/telemetry")}
    assert telemetry == set(ALL)  # all 11 devices sent telemetry within 12 s
    for m in messages:
        device, channel = contract.parse_topic(str(m.topic))
        schema = contract.SCHEMA_FOR[(contract.device_kind(device), channel)]
        assert contract.validate(schema, payload(m)) == [], m.topic


async def test_fr03_interval_and_seq():
    device = "home-b01-01"
    await send_cmd(device, {"type": "interval", "seconds": 2})
    try:
        messages = await collect(
            f"aquapulse/v1/dev/{device}/telemetry", 25, until=lambda found: len(found) >= 7
        )
        # The first two may still follow the old 10 s rhythm while the device
        # switches over (the new interval starts at its next tick).
        stamps = [contract.parse_iso(payload(m)["ts"]) for m in messages[2:]]
        gaps = [b - a for a, b in itertools.pairwise(stamps)]
        assert gaps and all(1.8 <= gap <= 2.2 for gap in gaps), gaps  # 2 s +/- 10%
        seqs = [payload(m)["seq"] for m in messages]
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    finally:
        await send_cmd(device, {"type": "interval", "seconds": 10})


async def send_cmd(device: str, fields: dict) -> dict:
    """Publish a command like the backend's command sender, and return the device's ack."""
    now = time.time()
    cmd = {
        "v": 1,
        "cmdId": str(uuid.uuid4()),
        "issuedAt": contract.iso(now),
        "expiresAt": contract.iso(now + 30),
        "by": "owner",
        **fields,
    }
    async with client("monitor") as listener, client("commander") as sender:
        await listener.subscribe(f"aquapulse/v1/dev/{device}/ack", qos=1)
        await sender.publish(f"aquapulse/v1/dev/{device}/cmd", json.dumps(cmd), qos=1)
        async with asyncio.timeout(10):
            async for message in listener.messages:
                ack = payload(message)
                if ack["cmdId"] == cmd["cmdId"]:
                    return ack
    raise AssertionError("no ack")


async def test_fr04_close_valve_gives_zero_flow_then_reopen():
    device = "home-b01-02"
    started = time.monotonic()
    ack = await send_cmd(device, {"type": "valve", "action": "close"})
    assert time.monotonic() - started <= 6
    assert (ack["result"], ack["state"]) == ("ok", "closed")
    messages = await collect(
        f"aquapulse/v1/dev/{device}/telemetry", 25, until=lambda f: len(f) >= 2
    )
    for m in messages:
        assert payload(m)["valve"] == "closed" and payload(m)["flowLpm"] == 0
    ack = await send_cmd(device, {"type": "valve", "action": "open"})
    assert ack["state"] == "open"


async def test_fr06_last_will_on_unexpected_disconnect_then_online_again():
    device = "home-b01-04"
    status = f"aquapulse/v1/dev/{device}/status"
    async with client() as c:
        await c.subscribe(status, qos=1)
        api("POST", "/scenarios", {"type": "offline", "device": device, "minutes": 0.25})
        states = []
        async with asyncio.timeout(60):
            async for message in c.messages:
                states.append(payload(message))
                if states[-1]["state"] == "online" and any(s["state"] == "offline" for s in states):
                    break
    offline = next(s for s in states if s["state"] == "offline")
    assert "ts" not in offline  # it is the Last Will, published by the broker, not the device

    # A new subscriber gets the retained "online" straight away.
    [retained] = await collect(status, 3, until=lambda f: len(f) >= 1)
    assert retained.retain and payload(retained)["state"] == "online"


async def test_fr01_stopping_one_device_does_not_affect_the_others():
    api("POST", "/devices/home-b01-06/stop")
    try:
        await asyncio.sleep(2)
        messages = await collect("aquapulse/v1/dev/+/telemetry", 12)
        senders = {device_of(m) for m in messages}
        assert "home-b01-06" not in senders
        assert senders == set(ALL) - {"home-b01-06"}
    finally:
        api("POST", "/devices/home-b01-06/start")
    assert await wait_all_online(20) == set(ALL)


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")
async def test_fr17_all_devices_back_online_within_90_s_after_broker_restart():
    subprocess.run(["docker", "compose", "restart", "mqtt"], check=True, timeout=60)
    started = time.monotonic()
    online: set[str] = set()
    while time.monotonic() - started < 90 and online != set(ALL):
        await asyncio.sleep(3)
        try:
            online = {d["deviceId"] for d in api("GET", "/devices") if d["connected"]}
        except OSError:
            continue
    assert online == set(ALL), f"still offline after 90 s: {set(ALL) - online}"
