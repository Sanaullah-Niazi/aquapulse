"""One MQTT connection per virtual device, exactly like a real ESP32 (FR-01).

For each device this module:
* connects over TLS with its own username and password (M5, FR-11, FR-12)
  and registers a Last Will "offline" (FR-06);
* publishes a retained "online" status and its retained state;
* subscribes to its own cmd topic and hands commands to the device (FR-04);
* publishes telemetry every interval_s seconds (FR-03);
* reconnects with exponential back-off 1, 2, 4 ... 60 s plus jitter (FR-17);
* drops the connection on purpose for the `offline` and `reboot` scenarios,
  and disconnects politely when stopped from the control API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import socket
import ssl
import time

import aiomqtt

from . import contract
from .building import Building
from .config import device_password
from .device import Device

log = logging.getLogger("aquapulse_sim.mqtt")

KEEPALIVE_S = 30  # SRS 7.3: the broker declares a silent device dead after 1.5 x 30 s
BACKOFF_MAX_S = 60.0
CHECK_EVERY_S = 0.5  # how often the link checks for stop/offline/reboot


def retry_delay(attempt: int, rng: random.Random) -> float:
    """FR-17 back-off: 1, 2, 4, 8 ... seconds, at most 60, plus/minus 20% jitter.

    The jitter stops 500 devices from all reconnecting in the same millisecond
    after a broker restart (the "thundering herd" problem).
    """
    return min(2.0**attempt, BACKOFF_MAX_S) * rng.uniform(0.8, 1.2)


class LinkDropped(Exception):
    """We want this connection to end now (scenario, stop or reboot)."""


def tls_context(ca_file: str) -> ssl.SSLContext:
    """TLS that checks the broker's certificate against our CA, like an ESP32 will.
    A context that skips this check is never used (FR-12 criterion 3)."""
    return ssl.create_default_context(cafile=ca_file)


class DeviceLink:
    def __init__(
        self,
        device: Device,
        building: Building,
        host: str,
        port: int,
        *,
        secret: str = "",
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.device = device
        self.building = building
        self.host = host
        self.port = port
        # Username = device ID; the password comes from SIM_DEVICE_SECRET.
        self.password = device_password(secret, device.id) if secret else None
        self.tls = tls
        self._rng = random.Random()  # jitter only; does not need to be reproducible

    # ----- why the link should be down right now -----

    def _offline_wanted(self, now: float) -> bool:
        return self.building.scenarios.first_active(self.device.id, now, "offline") is not None

    def _down_reason(self, reboots_seen: int) -> str | None:
        now = time.time()
        if self.device.stopped:
            return "stopped"
        if self._offline_wanted(now):
            return "offline"
        if self.device.reboot_count != reboots_seen:
            return "reboot"
        return None

    # ----- main loop -----

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0  # failed connection attempts in a row
        # Spread first connections and telemetry over the interval, like real
        # devices that were switched on at different moments.
        await _sleep_or_stop(stop, self._rng.uniform(0, min(2.0, self.device.interval_s)))
        while not stop.is_set():
            if self.device.stopped or self._offline_wanted(time.time()):
                await _sleep_or_stop(stop, CHECK_EVERY_S)
                continue
            try:
                await self._session(stop)
                attempt = 0
            except LinkDropped as why:
                log.info("%s: connection dropped on purpose (%s)", self.device.id, why)
                attempt = 0  # we dropped it ourselves: reconnect quickly when allowed
            except aiomqtt.MqttError as err:
                self.device.connected = False
                delay = retry_delay(attempt, self._rng)
                log.warning("%s: MQTT error (%s), retrying in %.1f s", self.device.id, err, delay)
                await _sleep_or_stop(stop, delay)
                attempt += 1
            finally:
                self.device.connected = False

    async def _session(self, stop: asyncio.Event) -> None:
        device = self.device
        status_topic = contract.topic(device.id, "status")
        will = aiomqtt.Will(
            status_topic,
            contract.encode(device.id, "status", {"v": 1, "state": "offline"}),
            qos=1,
            retain=True,
        )
        reboots_seen = device.reboot_count
        async with aiomqtt.Client(
            self.host,
            self.port,
            identifier=device.id,
            username=device.id if self.password else None,
            password=self.password,
            tls_context=self.tls,
            keepalive=KEEPALIVE_S,
            will=will,
            timeout=10,
        ) as client:
            device.connected = True
            now = time.time()
            online = {"v": 1, "state": "online", "ts": contract.iso(now)}
            await client.publish(
                status_topic,
                contract.encode(device.id, "status", online),
                qos=1,
                retain=True,
            )
            await self._publish(client, "state", device.state(now), qos=1, retain=True)
            await client.subscribe(contract.topic(device.id, "cmd"), qos=1)
            log.info("%s: online", device.id)

            listener = asyncio.create_task(self._listen(client))
            try:
                await self._publish_loop(client, stop, reboots_seen)
            except LinkDropped as why:
                if str(why) == "stopped":
                    raise  # polite goodbye below, in the finally block
                # offline / reboot: cut the line without an MQTT DISCONNECT, so the
                # broker sees an unexpected loss and publishes the Last Will.
                await _cut_socket(client)
                raise
            finally:
                listener.cancel()
                with contextlib.suppress(asyncio.CancelledError, aiomqtt.MqttError):
                    await listener
                if stop.is_set() or device.stopped:
                    # Clean shutdown: say goodbye ourselves (a clean DISCONNECT
                    # does not trigger the Last Will).
                    with contextlib.suppress(aiomqtt.MqttError):
                        await client.publish(
                            status_topic,
                            contract.encode(
                                device.id,
                                "status",
                                {"v": 1, "state": "offline", "ts": contract.iso(time.time())},
                            ),
                            qos=1,
                            retain=True,
                        )
                    log.info("%s: offline (clean disconnect)", device.id)

    async def _publish_loop(
        self, client: aiomqtt.Client, stop: asyncio.Event, reboots_seen: int
    ) -> None:
        device = self.device
        loop = asyncio.get_running_loop()
        next_due = loop.time()
        while not stop.is_set():
            reason = self._down_reason(reboots_seen)
            if reason:
                raise LinkDropped(reason)
            await self._flush_outbox(client)
            if loop.time() >= next_due:
                self.building.tick(time.time())  # readings are fresh to the second
                await self._publish(client, "telemetry", device.telemetry(time.time()), qos=0)
                next_due += device.interval_s
                if loop.time() > next_due:  # we fell far behind (laptop asleep?)
                    next_due = loop.time() + device.interval_s
            wait = max(0.0, min(CHECK_EVERY_S, next_due - loop.time()))
            device.outbox_ready.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(device.outbox_ready.wait(), wait)
        await self._flush_outbox(client)

    async def _listen(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            payload = message.payload
            raw = payload if isinstance(payload, bytes | bytearray) else str(payload).encode()
            try:
                self.building.tick(time.time())
                self.device.handle_command(bytes(raw), time.time())
            except Exception:  # a bug must not silently stop all future commands
                log.exception("%s: error while handling a command", self.device.id)

    async def _flush_outbox(self, client: aiomqtt.Client) -> None:
        while self.device.outbox:
            item = self.device.outbox[0]
            await self._publish(client, item.channel, item.payload, item.qos, item.retain)
            self.device.outbox.popleft()  # removed only after it was handed to the broker

    async def _publish(
        self, client: aiomqtt.Client, channel: str, payload: dict, qos: int, retain: bool = False
    ) -> None:
        data = contract.encode(self.device.id, channel, payload)  # validates first
        await client.publish(contract.topic(self.device.id, channel), data, qos=qos, retain=retain)


async def _cut_socket(client: aiomqtt.Client) -> None:
    """Close the TCP connection without an MQTT DISCONNECT (like pulling the plug).

    aiomqtt has no public API for this, so we reach into it (paho's socket and
    aiomqtt's "disconnected" future). An integration test covers it, so an
    upgrade that breaks this fails CI instead of failing silently.
    """
    sock = client._client.socket()
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
    # Wait until paho notices, and collect the expected "unexpected disconnection"
    # error, so asyncio does not print it as an unhandled exception.
    with contextlib.suppress(Exception):
        await asyncio.wait_for(client._disconnected, 5)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)
