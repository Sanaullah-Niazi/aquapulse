"""Problems we can inject on purpose (FR-05), so alerts and AI can be demonstrated.

Every scenario is kept after it ends, with its exact start and end time.
That list is the *ground truth*: later (M9) the leak detector is scored by
comparing what it found with what really happened here.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Any

# What each scenario does. The effects are applied in home.py and tank.py.
SCENARIO_TYPES: dict[str, tuple[str, str]] = {
    # type: (device kind it applies to, description)
    "leak": ("home", "Small flow (0.2 to 2 L/min) that never stops, like a running toilet"),
    "burst": ("home", "Very high flow (25 to 40 L/min), like a broken pipe"),
    "abnormal": ("home", "Guests: 'extra' (default 1) more households' worth of water"),
    "ph_drift": ("home", "pH slowly moves outside the safe 6.5 to 8.5 range"),
    "temp_spike": ("home", "Water temperature jumps by about 18 °C"),
    "sensor_stuck": ("home", "pH and temperature readings freeze at one value"),
    "valve_stuck": ("home", "The valve motor fails: valve commands end with VALVE_STUCK"),
    "offline": ("any", "Device loses its connection without saying goodbye (MQTT Last Will)"),
    "reboot": ("any", "Device restarts now: new bootId, seq back to 0, counter from 'flash'"),
    "tank_sensor_fail": ("tank", "Ultrasonic level sensor gives no valid readings"),
    "sump_empty": ("tank", "The ground-level sump runs dry: pump must stay OFF"),
}  # fmt: skip

INSTANT_TYPES = {"reboot"}  # happen once, have no duration


@dataclass
class Scenario:
    id: int
    type: str
    device: str
    start: float  # epoch seconds
    end: float  # epoch seconds (planned; moved earlier if stopped by hand)
    params: dict[str, Any] = field(default_factory=dict)

    def active(self, now: float) -> bool:
        return self.start <= now < self.end


class ScenarioBook:
    """All scenarios, past and present, for the whole building."""

    def __init__(self, seed: int) -> None:
        self._items: list[Scenario] = []
        self._ids = itertools.count(1)
        self._seed = seed

    def add(
        self, type_: str, device: str, now: float, minutes: float, params: dict | None = None
    ) -> Scenario:
        if type_ not in SCENARIO_TYPES:
            raise ValueError(f"unknown scenario type {type_!r}")
        sid = next(self._ids)
        params = dict(params or {})
        rng = random.Random(f"{self._seed}:scenario:{sid}")
        # Fill in realistic random strengths, unless the caller chose one.
        if type_ == "leak":
            params.setdefault("lpm", round(rng.uniform(0.2, 2.0), 2))
        elif type_ == "burst":
            params.setdefault("lpm", round(rng.uniform(25.0, 40.0), 1))
        elif type_ == "abnormal":
            params.setdefault("extra", 1)
        elif type_ == "ph_drift":
            params.setdefault("direction", rng.choice(["up", "down"]))
        elif type_ == "temp_spike":
            params.setdefault("deltaC", 18.0)
        end = now if type_ in INSTANT_TYPES else now + minutes * 60.0
        scenario = Scenario(sid, type_, device, now, end, params)
        self._items.append(scenario)
        return scenario

    def stop(self, sid: int, now: float) -> Scenario:
        scenario = self.get(sid)
        if scenario.end > now:
            scenario.end = max(scenario.start, now)
        return scenario

    def get(self, sid: int) -> Scenario:
        for s in self._items:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def all(self) -> list[Scenario]:
        return list(self._items)

    def active(self, device: str, now: float, type_: str | None = None) -> list[Scenario]:
        return [
            s
            for s in self._items
            if s.device == device and s.active(now) and (type_ is None or s.type == type_)
        ]

    def first_active(self, device: str, now: float, type_: str) -> Scenario | None:
        found = self.active(device, now, type_)
        return found[0] if found else None
