"""How a household uses water over a day (FR-02).

Idea: a day is a list of *water-use events* (a shower, a toilet flush, a tap
running, the washing machine ...). Each event has a start time, a duration and
a flow rate in litres per minute. The flow at any moment is the sum of the
events running at that moment.

For every home and every day we:
1. pick a daily target: people x 105 to 140 L, plus about 17% on weekends;
2. add random events until the target is reached. Each event's start hour is
   drawn from HOURLY_WEIGHTS, so most water is used in the morning
   (06:00 to 09:00) and evening (18:00 to 22:00), and very little at night.

Everything is drawn from a random generator seeded with (seed, device, date),
so the same seed always gives exactly the same water use (FR-02 criterion 4),
and any day can be calculated on its own, in any order.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

# Relative chance that an event starts in each local hour (index 0 = 00:00-01:00).
HOURLY_WEIGHTS = (
    0.30, 0.15, 0.10, 0.10, 0.15, 0.60,  # 00-06: night, almost nothing
    2.00, 3.00, 2.60, 1.60,  # 06-10: morning peak (showers, breakfast)
    1.00, 0.90, 1.00, 0.90, 0.80, 0.80, 1.00, 1.40,  # 10-18: daytime
    2.40, 2.80, 2.60, 2.00,  # 18-22: evening peak (cooking, dishes, baths)
    1.20, 0.60,  # 22-24: winding down
)  # fmt: skip

# name: (relative chance, flow range L/min, duration range seconds)
EVENT_TYPES = {
    "tap": (0.45, (3.0, 8.0), (10, 90)),
    "toilet": (0.30, (6.0, 10.0), (40, 70)),
    "shower": (0.12, (7.0, 12.0), (240, 720)),
    "dishes": (0.08, (4.0, 8.0), (120, 480)),
    "washing_machine": (0.05, (8.0, 12.0), (300, 600)),
}

LITRES_PER_PERSON = (105.0, 140.0)  # per day; average fits FR-02's 100 to 150 L
WEEKEND_FACTOR = 1.17  # FR-02: weekends 10 to 25% higher


@dataclass(frozen=True)
class WaterEvent:
    kind: str
    start: float  # epoch seconds
    end: float  # epoch seconds
    lpm: float  # litres per minute

    @property
    def litres(self) -> float:
        return self.lpm * (self.end - self.start) / 60.0


class HouseholdProfile:
    """The water use of one home. Pure maths: no clock, no network."""

    def __init__(
        self,
        device_id: str,
        persons: int,
        seed: int,
        tz: ZoneInfo,
        weekend: frozenset[int] = frozenset({5, 6}),
    ) -> None:
        self.device_id = device_id
        self.persons = persons
        self.seed = seed
        self.tz = tz
        self.weekend = weekend

    def _local_date(self, epoch_s: float) -> date:
        return datetime.fromtimestamp(epoch_s, self.tz).date()

    def daily_target(self, day: date) -> float:
        rng = random.Random(f"{self.seed}:{self.device_id}:{day.isoformat()}:target")
        litres = self.persons * rng.uniform(*LITRES_PER_PERSON)
        return litres * (WEEKEND_FACTOR if day.weekday() in self.weekend else 1.0)

    def events_for_day(self, day: date) -> tuple[WaterEvent, ...]:
        return _events_for_day(self, day)

    def _events_near(self, epoch_s: float) -> list[WaterEvent]:
        # An event that starts late yesterday can still be running after midnight.
        today = self._local_date(epoch_s)
        return [
            *self.events_for_day(today - timedelta(days=1)),
            *self.events_for_day(today),
        ]

    def flow_at(self, epoch_s: float) -> float:
        """Flow in L/min at one moment."""
        return sum(e.lpm for e in self._events_near(epoch_s) if e.start <= epoch_s < e.end)

    def litres_between(self, t0: float, t1: float) -> float:
        """Exact litres used between two moments (sum of the overlapping parts of events)."""
        if t1 <= t0:
            return 0.0
        # Every day that can hold an event overlapping [t0, t1), including the day
        # before t0 (an event that started yesterday may still be running).
        day, last = self._local_date(t0) - timedelta(days=1), self._local_date(t1)
        events: list[WaterEvent] = []
        while day <= last:
            events.extend(self.events_for_day(day))
            day += timedelta(days=1)
        total = 0.0
        for e in events:
            overlap = min(t1, e.end) - max(t0, e.start)
            if overlap > 0:
                total += e.lpm * overlap / 60.0
        return total


@lru_cache(maxsize=4096)
def _events_for_day(profile: HouseholdProfile, day: date) -> tuple[WaterEvent, ...]:
    """Generate one day's events. Cached, because every tick asks for today's events."""
    rng = random.Random(f"{profile.seed}:{profile.device_id}:{day.isoformat()}:events")
    midnight = datetime.combine(day, time(0), profile.tz)
    target = profile.daily_target(day)
    kinds = list(EVENT_TYPES)
    weights = [EVENT_TYPES[k][0] for k in kinds]

    events: list[WaterEvent] = []
    used = 0.0
    while used < target:
        kind = rng.choices(kinds, weights)[0]
        _, (lo_flow, hi_flow), (lo_dur, hi_dur) = EVENT_TYPES[kind]
        lpm = round(rng.uniform(lo_flow, hi_flow), 2)
        duration = rng.uniform(lo_dur, hi_dur)
        litres = lpm * duration / 60.0
        if used + litres > target:  # trim the last event so the day hits its target
            duration = max(5.0, (target - used) * 60.0 / lpm)
            litres = lpm * duration / 60.0
        hour = rng.choices(range(24), HOURLY_WEIGHTS)[0]
        # Add hours as real elapsed time, so daylight-saving days still work.
        start = (midnight + timedelta(hours=hour, seconds=rng.uniform(0, 3600))).timestamp()
        events.append(WaterEvent(kind, start, start + duration, lpm))
        used += litres
    events.sort(key=lambda e: e.start)
    return tuple(events)
