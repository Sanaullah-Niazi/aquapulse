"""The alert engine inside the worker (FR-58): live events in, alerts out.

* `listen()` hears every event the ingestor announces (LISTEN, M4) and puts
  it in a queue; `consume()` runs the fast rules on them at once, so a
  critical alert is in the app within a few seconds.
* `sweep()` runs every 10 seconds for what needs the clock or the database:
  quiet devices, high daily use (every 5 minutes), switched-off rules and
  auto-resolving alerts whose problem is gone.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from .. import db
from .. import events as bus
from ..models import Alert, Building
from .detect import Detector, Signal
from .rules import DEFAULTS, RuleSettings
from .store import (
    apply,
    auto_resolve,
    clear_disabled,
    load_settings,
    offline_signals,
    open_keys,
    usage_signals,
)

log = logging.getLogger("aquapulse.alerts")

SWEEP_S = 10
USAGE_EVERY = timedelta(minutes=5)
QUEUE_SIZE = 10_000
BATCH = 200


class AlertEngine:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.clock = clock
        self._settings: dict[str, RuleSettings] = {}
        self.detector = Detector(settings=self.settings_of)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(QUEUE_SIZE)
        self._usage_at: datetime | None = None

    def settings_of(self, building_id: str | None) -> RuleSettings:
        return self._settings.get(str(building_id), DEFAULTS)

    async def start(self) -> None:
        """Load settings, and the problems that were still on before a restart."""
        async with db.sessions()() as session:
            self._settings, self.detector.tank_high = await load_settings(session)
            self.detector.active = await open_keys(session)

    # ----- live events -----

    async def handle(self, events: list[dict[str, Any]]) -> list[Alert]:
        """Run the fast rules on some events and save the result."""
        now = self.clock()
        signals: list[Signal] = []
        for event in events:
            signals += self.detector.see(event, now)
        if not signals:
            return []
        async with db.sessions()() as session, session.begin():
            return await apply(session, signals, now)

    def _on_notify(self, payload: str) -> None:
        event = json.loads(payload)
        if event.get("type") in ("telemetry", "tank", "device", "command"):
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("alert queue full: an event was skipped")

    async def listen(self) -> None:
        """Hear the ingestor's events; connect again if the database restarts."""
        while True:
            try:
                async with bus.listening(bus.EVENTS, self._on_notify) as conn:
                    log.info("alert engine listening for live events")
                    while not conn.is_closed():
                        await asyncio.sleep(2)
            except Exception as err:
                log.warning("event connection lost (%s), connecting again", type(err).__name__)
            await asyncio.sleep(2)

    async def consume(self) -> None:
        while True:
            events = [await self.queue.get()]
            while not self.queue.empty() and len(events) < BATCH:
                events.append(self.queue.get_nowait())
            try:
                await self.handle(events)
            except Exception:
                log.exception("alert rules failed for %d events", len(events))

    # ----- every 10 seconds -----

    async def sweep(self) -> dict[str, int]:
        now = self.clock()
        done = {"opened": 0, "resolved": 0}
        async with db.sessions()() as session, session.begin():
            self._settings, self.detector.tank_high = await load_settings(session)
            signals = await offline_signals(session, self._settings, now)
            self.detector.active |= {s.key for s in signals}  # a reading clears them again
            if self._usage_at is None or now - self._usage_at >= USAGE_EVERY:
                self._usage_at = now
                for building in await session.scalars(select(Building)):
                    signals += await usage_signals(
                        session, building, self.settings_of(str(building.id)), now
                    )
            done["opened"] = len(await apply(session, signals, now))
            await clear_disabled(session, self._settings, now)
            done["resolved"] = await auto_resolve(session, now)
        return done

    async def sweep_forever(self) -> None:
        while True:
            try:
                done = await self.sweep()
                if any(done.values()):
                    log.info("alert sweep: %s", done)
            except Exception:
                log.exception("alert sweep failed, trying again")
            await asyncio.sleep(SWEEP_S)
