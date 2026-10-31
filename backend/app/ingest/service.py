"""The ingestor service: the only server program that talks MQTT.

Three jobs run side by side while connected to the broker:

* **reader**: takes every device message off MQTT and puts it in a queue;
* **writer**: stores the queue in batches (up to 500 messages or 100 ms,
  whichever comes first), so 500 messages/s needs only ~10 transactions/s;
* **commander**: every second, and at once when the API NOTIFYs a new
  command, marks unanswered commands `timeout` and sends queued ones.

From M5 it uses two MQTT connections, both over TLS, with two different
accounts: `ingestor` may only read what devices send, and `commander` may
only send commands (FR-11 criterion 4). So a bug in the reading part can
never send a command.

If the broker or the database goes away, everything waits and reconnects
with a growing pause (1 s, 2 s, 4 s ... at most 30 s).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from datetime import UTC, datetime

import aiomqtt
from prometheus_client import start_http_server
from sqlalchemy import text

from .. import db, heartbeat
from .. import events as bus
from ..config import get_settings
from . import commands, metrics
from .handler import Handler, Incoming

log = logging.getLogger("aquapulse.ingestor")

CLIENT_ID = "aquapulse-ingestor"
# Telemetry is QoS 0 (a lost reading loses no litres: the counter is cumulative).
# Status, state and ack are QoS 1: they must arrive.
SUBSCRIPTIONS = [
    ("aquapulse/v1/dev/+/telemetry", 0),
    ("aquapulse/v1/dev/+/status", 1),
    ("aquapulse/v1/dev/+/state", 1),
    ("aquapulse/v1/dev/+/ack", 1),
]
BATCH_SIZE = 500
BATCH_WAIT_S = 0.1
COMMAND_TICK_S = 1.0
QUEUE_LIMIT = 20_000  # when full, the reader waits: MQTT slows down instead of us losing data
METRICS_PORT = 9100
HEARTBEAT_S = 15  # how often the ingestor says "alive" in the database


def mqtt_client(username: str, identifier: str, **options) -> aiomqtt.Client:
    """An MQTT connection over TLS that checks the broker's certificate against
    our CA (never "trust anything", FR-12 criterion 3)."""
    settings = get_settings()
    password = getattr(settings, f"mqtt_{username}_password").get_secret_value()
    return aiomqtt.Client(
        settings.mqtt_host,
        settings.mqtt_port,
        identifier=identifier,
        username=username,
        password=password,
        tls_context=ssl.create_default_context(cafile=settings.mqtt_ca_file),
        **options,
    )


class Ingestor:
    def __init__(self, client_id: str = CLIENT_ID, persistent: bool = True) -> None:
        self.client_id = client_id
        self.persistent = persistent  # tests use throw-away sessions
        self.handler = Handler()
        self.queue: asyncio.Queue[Incoming] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self.command_wake = asyncio.Event()

    # ----- the three jobs -----

    async def reader(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            payload = message.payload if isinstance(message.payload, bytes) else b""
            await self.queue.put(Incoming(str(message.topic), payload, datetime.now(UTC)))

    async def writer(self) -> None:
        while True:
            batch = [await self.queue.get()]
            deadline = time.monotonic() + BATCH_WAIT_S
            while len(batch) < BATCH_SIZE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self.queue.get(), remaining))
                except TimeoutError:
                    break
            await self.store(batch)

    async def store(self, batch: list[Incoming]) -> None:
        """Store one batch. Waits (never drops) while the database is down."""
        pause = 1.0
        for attempt in range(1, 4):
            try:
                with metrics.BATCH_SECONDS.time():
                    result = await self.handler.handle(batch)
                if result.finished_commands:
                    self.command_wake.set()  # those devices can get their next command
                return
            except Exception as err:
                self.handler.forget()  # the transaction was rolled back
                log.warning("storing a batch failed (try %d): %s", attempt, type(err).__name__)
                await self.wait_for_database()
                await asyncio.sleep(pause)
                pause *= 2
        # The database works but this batch keeps failing: one bad message must
        # not block all the others, so store them one by one.
        for message in batch:
            try:
                await self.handler.handle([message])
            except Exception as err:
                self.handler.forget()
                metrics.REJECTED.labels("store_failed").inc()
                log.error("could not store a message: %s", type(err).__name__)

    async def wait_for_database(self) -> None:
        pause = 1.0
        while True:
            try:
                async with db.sessions()() as session:
                    await session.execute(text("SELECT 1"))
                return
            except Exception as err:
                log.warning("database not reachable (%s), waiting", type(err).__name__)
                await asyncio.sleep(pause)
                pause = min(pause * 2, 30)

    async def commander(self, client: aiomqtt.Client) -> None:
        async def publish(topic: str, payload: bytes) -> None:
            # QoS 1 and NEVER retained (FR-13): a retained command would be
            # replayed to the device every time it reconnects.
            await client.publish(topic, payload, qos=1, retain=False, timeout=5)

        last_beat = 0.0
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.command_wake.wait(), COMMAND_TICK_S)
            self.command_wake.clear()
            await commands.expire()
            await commands.dispatch(publish)
            if time.monotonic() - last_beat >= HEARTBEAT_S:
                # Connected and working: tell the health page (FR-55).
                await heartbeat.beat("ingestor", {"connected": True, **metrics.totals()})
                last_beat = time.monotonic()

    # ----- connection handling -----

    async def run_connected(self) -> None:
        settings = get_settings()
        reading = mqtt_client(
            "ingestor",
            self.client_id,
            # A persistent session: QoS 1 messages (acks, status) sent while
            # the ingestor restarts are kept by the broker and delivered later.
            clean_session=not self.persistent,
            max_queued_incoming_messages=QUEUE_LIMIT,
        )
        sending = mqtt_client("commander", f"{self.client_id}-commander")
        async with reading, sending:
            metrics.CONNECTED.set(1)
            log.info("connected to MQTT at %s:%s (TLS)", settings.mqtt_host, settings.mqtt_port)
            try:
                await reading.subscribe(SUBSCRIPTIONS)
                self.handler.forget()  # devices may have changed while we were away
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(self.reader(reading))
                    tasks.create_task(self.writer())
                    tasks.create_task(self.commander(sending))
            finally:
                metrics.CONNECTED.set(0)

    async def run(self) -> None:
        pause = 1.0
        while True:
            started = time.monotonic()
            try:
                # The API NOTIFYs here when it queues a command: send it now,
                # not at the next 1-second tick.
                async with bus.listening(bus.COMMANDS, lambda _: self.command_wake.set()):
                    await self.run_connected()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # ExceptionGroup from the TaskGroup, MqttError, or a database error.
                log.warning("ingestor stopped (%s), reconnecting in %.0f s", _kind(err), pause)
            if time.monotonic() - started > 60:
                pause = 1.0  # it ran fine for a while: start again with a short pause
            await asyncio.sleep(pause)
            pause = min(pause * 2, 30)


def _kind(err: BaseException) -> str:
    if isinstance(err, BaseExceptionGroup):
        return ", ".join(sorted({_kind(e) for e in err.exceptions}))
    return type(err).__name__


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    start_http_server(METRICS_PORT)  # Prometheus metrics, inside the Docker network only
    asyncio.run(Ingestor().run())
