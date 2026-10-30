"""The whole digital twin: one building with N homes and one tank.

`tick(now)` moves the physics forward: the homes use water, the tank level
falls by exactly what they used and rises while the pump runs. The MQTT
side (mqtt_link.py) only reads from here and delivers commands.
"""

from __future__ import annotations

import random
from typing import Any

from .config import Config
from .device import Device
from .home import HomeNode
from .scenarios import SCENARIO_TYPES, Scenario, ScenarioBook
from .store import FlashStore
from .tank import TankNode

MAX_STEP_S = 5.0  # long gaps (e.g. laptop asleep) are replayed in small steps
HOUSEHOLD_SIZES = (1, 2, 2, 3, 3, 4, 4, 4, 5, 5, 6)  # typical mix, 1 to 6 people


class Building:
    def __init__(self, cfg: Config, now: float, store: FlashStore | None = None) -> None:
        self.cfg = cfg
        self.store = store or FlashStore(cfg.data_dir)
        self.scenarios = ScenarioBook(cfg.seed)
        rng = random.Random(f"{cfg.seed}:{cfg.building}:households")
        self.homes = [
            HomeNode(
                f"home-{cfg.building}-{n:02d}",
                rng.choice(HOUSEHOLD_SIZES),
                cfg,
                self.scenarios,
                self.store,
                now,
            )
            for n in range(1, cfg.homes + 1)
        ]
        self.tank = TankNode(f"tank-{cfg.building}-01", cfg, self.scenarios, self.store, now)
        self.devices: dict[str, Device] = {d.id: d for d in [*self.homes, self.tank]}
        self.last_tick = now

    def tick(self, now: float, max_step_s: float = MAX_STEP_S) -> None:
        """Move the physics to `now`. The backfill uses bigger steps (it has 90 days to do)."""
        t0 = self.last_tick
        while t0 < now:
            t1 = min(now, t0 + max_step_s)
            supply_ok = self.tank.can_supply()
            used = sum(home.step(t0, t1, supply_ok) for home in self.homes)
            self.tank.step(t0, t1, used)
            t0 = t1
        self.last_tick = max(self.last_tick, now)

    # ----- control API helpers -----

    def device(self, device_id: str) -> Device:
        try:
            return self.devices[device_id]
        except KeyError:
            raise KeyError(f"unknown device {device_id!r}") from None

    def add_scenario(
        self,
        type_: str,
        device_id: str,
        minutes: float,
        now: float,
        params: dict[str, Any] | None = None,
    ) -> Scenario:
        device = self.device(device_id)
        if type_ not in SCENARIO_TYPES:
            raise ValueError(f"unknown scenario type {type_!r}")
        applies_to = SCENARIO_TYPES[type_][0]
        if applies_to not in ("any", device.kind):
            raise ValueError(f"{type_} only works on a {applies_to} device")
        self.tick(now)  # bring physics up to date, so the scenario starts exactly now
        scenario = self.scenarios.add(type_, device_id, now, minutes, params)
        if type_ == "reboot":
            device.reboot(now)
        self.tank.control(now)  # the tank reacts at once (e.g. sump_empty -> pump OFF)
        return scenario

    def describe(self, device: Device) -> dict[str, Any]:
        info: dict[str, Any] = {
            "deviceId": device.id,
            "kind": device.kind,
            "connected": device.connected,
            "stopped": device.stopped,
            "intervalS": device.interval_s,
            "bootId": device.boot_id,
            "seq": device.seq,
        }
        if isinstance(device, HomeNode):
            info |= {
                "persons": device.persons,
                "totalL": round(device.total_l, 3),
                "flowLpm": round(device.flow_lpm, 2),
                "valve": device.valve_report,
            }
        elif isinstance(device, TankNode):
            info |= {
                "levelPct": round(device.level_pct, 1),
                "pumpOn": device.pump_on,
                "mode": device.mode,
                "lowPct": device.low_pct,
                "highPct": device.high_pct,
            }
        return info
