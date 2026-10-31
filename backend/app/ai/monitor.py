"""The leak detector inside the worker's alert engine (FR-63, FR-61).

Every live reading of a home goes into that home's `FlowWatch`; the answer
becomes a "leak" or "burst" signal for the alert store, like any other rule.
When the worker starts, the last 12 hours are replayed from the database, so
a restart does not forget a leak that has been running all night.

Once a day, each home's baseline (its normal maximum flow and its usual
lowest night flow) is worked out again from the last 30 days.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..alerts.detect import Signal
from ..alerts.rules import DEFAULTS, LIVE_AI_RULES, RuleSettings
from .leak import Baseline, Finding, FlowWatch, LeakLimits, baseline_from

log = logging.getLogger("aquapulse.ai")

REPLAY = timedelta(hours=12)
HISTORY = timedelta(days=30)
NIGHTS = 14
REFRESH = timedelta(minutes=5)  # an ongoing finding updates its alert this often

REPLAY_SQL = text("""
    SELECT t.device_id, t.home_id, t.building_id, t.time, t.flow_lpm
    FROM telemetry t
    WHERE t.time >= :since
    ORDER BY t.device_id, t.time
""")

# The 99.9th percentile of normal use: readings above twice the 99th
# percentile (an earlier burst) are left out first, like leak.percentile_999.
BASELINE_SQL = text("""
    WITH recent AS (
        SELECT home_id, flow_lpm FROM telemetry WHERE time >= :since
    ), p99 AS (
        SELECT home_id, count(*) AS n,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY flow_lpm) AS p99
        FROM recent GROUP BY home_id
    )
    SELECT r.home_id, p.n,
           percentile_cont(0.999) WITHIN GROUP (ORDER BY r.flow_lpm) AS p999
    FROM recent r JOIN p99 p USING (home_id)
    WHERE p.p99 = 0 OR r.flow_lpm <= 2 * p.p99
    GROUP BY r.home_id, p.n
""")

NIGHT_SQL = text("""
    SELECT t.home_id, min(t.flow_lpm) AS low
    FROM telemetry t JOIN buildings b ON b.id = t.building_id
    WHERE t.time >= :since
      AND extract(hour FROM t.time AT TIME ZONE b.timezone) >= 2
      AND extract(hour FROM t.time AT TIME ZONE b.timezone) < 5
    GROUP BY t.home_id, (t.time AT TIME ZONE b.timezone)::date
""")


def limits_of(settings: RuleSettings) -> LeakLimits:
    return LeakLimits(
        night_minutes=settings.get("leak", "night_minutes"),
        continuous_hours=settings.get("leak", "continuous_hours"),
        burst_minutes=settings.get("burst", "minutes"),
    )


@dataclass
class _Home:
    watch: FlowWatch
    building_id: str
    home_id: str
    kind: str | None = None  # the finding that is on now
    emitted: datetime | None = None


class LeakMonitor:
    def __init__(self, settings: Callable[[str | None], RuleSettings] = lambda _b: DEFAULTS):
        self.settings = settings
        self.zones: dict[str, ZoneInfo] = {}  # building id -> its time zone
        self.baselines: dict[str, Baseline] = {}  # home id -> its normal values
        self.homes: dict[str, _Home] = {}  # device id -> its watch
        self.baseline_day: date | None = None

    # ----- set up -----

    async def load_zones(self, session: AsyncSession) -> None:
        rows = await session.execute(text("SELECT id, timezone FROM buildings"))
        self.zones = {str(r.id): ZoneInfo(r.timezone) for r in rows}

    async def load_baselines(self, session: AsyncSession, now: datetime) -> None:
        stats = {
            str(r.home_id): (float(r.p999) if r.n >= 1000 else None)
            for r in await session.execute(BASELINE_SQL, {"since": now - HISTORY})
        }
        nights: dict[str, list[float]] = {}
        for r in await session.execute(NIGHT_SQL, {"since": now - timedelta(days=NIGHTS)}):
            nights.setdefault(str(r.home_id), []).append(float(r.low))
        self.baselines = {
            home: baseline_from(stats.get(home), nights.get(home, []))
            for home in stats.keys() | nights.keys()
        }
        for h in self.homes.values():
            h.watch.baseline = self.baselines.get(h.home_id, Baseline())
        self.baseline_day = now.date()

    async def start(self, session: AsyncSession, now: datetime, open_keys: set[str]) -> None:
        """Baselines, then the last 12 hours replayed, then which leak alerts are on."""
        await self.load_zones(session)
        await self.load_baselines(session, now)
        count = 0
        for r in await session.execute(REPLAY_SQL, {"since": now - REPLAY}):
            self._home(str(r.device_id), str(r.building_id), str(r.home_id)).watch.add(
                r.time, float(r.flow_lpm)
            )
            count += 1
        for key in open_keys:
            rule, _, device = key.partition(":")
            if rule in LIVE_AI_RULES and device in self.homes:
                self.homes[device].kind = rule
        log.info("leak detector replayed %d readings for %d homes", count, len(self.homes))

    async def daily(self, session: AsyncSession, now: datetime) -> None:
        if self.baseline_day != now.date():
            await self.load_zones(session)
            await self.load_baselines(session, now)

    def _home(self, device: str, building_id: str, home_id: str) -> _Home:
        home = self.homes.get(device)
        if home is None:
            zone = self.zones.get(building_id, ZoneInfo("UTC"))
            settings = self.settings(building_id)
            watch = FlowWatch(zone, self.baselines.get(home_id, Baseline()), limits_of(settings))
            home = self.homes[device] = _Home(watch, building_id, home_id)
        return home

    # ----- live readings -----

    def see(self, event: dict[str, Any], now: datetime) -> list[Signal]:
        if event.get("type") != "telemetry" or not event.get("homeId"):
            return []
        device = event["deviceId"]
        home = self._home(device, str(event["buildingId"]), str(event["homeId"]))
        settings = self.settings(home.building_id)
        home.watch.limits = limits_of(settings)
        try:
            when = datetime.fromisoformat(event["time"])
        except (KeyError, TypeError, ValueError):
            when = now
        home.watch.add(when, float(event.get("flowLpm") or 0))
        finding = home.watch.assess()
        if finding is not None and not settings.on(finding.kind):
            finding = None
        return self._signals(device, home, finding, event, now)

    def _signals(
        self, device: str, home: _Home, finding: Finding | None, event: dict, now: datetime
    ) -> list[Signal]:
        signals = []
        new_kind = finding.kind if finding else None
        if home.kind and home.kind != new_kind:
            signals.append(Signal(home.kind, f"{home.kind}:{device}", False, home.building_id,
                                  home.home_id, device_id=device))  # fmt: skip
        if finding is not None and (
            home.kind != new_kind or home.emitted is None or now - home.emitted >= REFRESH
        ):
            signals.append(
                Signal(
                    finding.kind,
                    f"{finding.kind}:{device}",
                    True,
                    home.building_id,
                    home.home_id,
                    device_id=device,
                    value=round(finding.lowest_lpm, 2),
                    message=finding.explanation[:500],
                    details={
                        "confidence": finding.confidence,
                        "high": finding.high,
                        "since": finding.since.isoformat(),
                        **finding.evidence,
                    },
                )
            )
            home.emitted = now
        home.kind = new_kind
        return signals
