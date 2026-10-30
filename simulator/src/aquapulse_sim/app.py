"""`aquapulse-sim run`: start the digital twin, its MQTT links and the control API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import time

import uvicorn

from .api import create_app
from .building import Building
from .config import Config
from .mqtt_link import DeviceLink, tls_context

log = logging.getLogger("aquapulse_sim")

TICK_S = 1.0  # physics resolution: the twin moves forward once per second


class _Server(uvicorn.Server):
    """uvicorn, but we handle Ctrl+C / docker stop ourselves (see run())."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def _physics(building: Building, stop: asyncio.Event) -> None:
    while not stop.is_set():
        building.tick(time.time())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), TICK_S)


async def run(cfg: Config) -> None:
    building = Building(cfg, time.time())
    log.info(
        "Digital twin ready: %d homes (%d people) + 1 tank, telemetry every %d s, seed %d",
        len(building.homes),
        sum(h.persons for h in building.homes),
        cfg.interval_s,
        cfg.seed,
    )
    stop = asyncio.Event()
    server = _Server(
        uvicorn.Config(
            create_app(building), host=cfg.api_host, port=cfg.api_port, log_level="warning"
        )
    )

    def shutdown() -> None:
        log.info("Stopping: every device says goodbye (retained 'offline')...")
        stop.set()
        server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    if not cfg.device_secret:
        log.error("SIM_DEVICE_SECRET is empty: the broker will refuse every device (run make env)")
    tls = tls_context(cfg.mqtt_ca_file)
    links = [
        DeviceLink(
            device, building, cfg.mqtt_host, cfg.mqtt_port, secret=cfg.device_secret, tls=tls
        )
        for device in building.devices.values()
    ]
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_physics(building, stop))
        tasks.create_task(server.serve())
        for link in links:
            tasks.create_task(link.run(stop))
        log.info("Control API: http://localhost:%d/docs", cfg.api_port)
    # Save every counter one last time, so a restart loses nothing.
    now = time.time()
    building.tick(now)
    for device in building.devices.values():
        device.save(now)
    log.info("Stopped.")
