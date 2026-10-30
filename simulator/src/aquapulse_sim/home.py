"""A virtual home node: water meter + motorised valve + pH and temperature sensors.

It behaves like the ESP32 home node of FR-15: it counts litres, reports
readings, and opens or closes its valve when commanded (FR-01 to FR-05).
"""

from __future__ import annotations

import math
import random
from datetime import datetime
from typing import Any

from . import contract
from .config import Config
from .device import Device
from .profile import HouseholdProfile
from .scenarios import ScenarioBook
from .store import FlashStore

VALVE_TRAVEL_S = (2.0, 5.0)  # a motorised ball valve takes 2 to 5 s to turn
VALVE_TIMEOUT_S = 8.0  # no position feedback after 8 s = stuck (SRS diagram 10.5)
PH_DRIFT_MINUTES = 20.0  # ph_drift reaches its full +/- 2.0 after 20 minutes
NORMAL_PH = (6.8, 7.8)  # FR-03: pH stays in this range unless a fault is active


class HomeNode(Device):
    kind = "home"

    def __init__(
        self,
        device_id: str,
        persons: int,
        cfg: Config,
        scenarios: ScenarioBook,
        store: FlashStore,
        now: float,
    ) -> None:
        self.persons = persons
        self.profile = HouseholdProfile(device_id, persons, cfg.seed, cfg.tz, cfg.weekend)
        self.total_l = 0.0  # cumulative litres: only ever goes up (FR-01)
        self.flow_lpm = 0.0
        self.valve_pos = "open"  # where the valve physically is: open or closed
        self.valve_report = "open"  # what we report: open, closed, moving or fault
        self._pending: dict[str, Any] | None = None  # valve movement in progress
        self._saved_l = 0.0
        rng = random.Random(f"{cfg.seed}:{device_id}:water")
        self._ph_base = rng.uniform(7.0, 7.4)  # each building pipe has its own pH
        super().__init__(device_id, cfg, scenarios, store, now)

    # ----- flash (FR-17) -----

    def restore(self, now: float) -> None:
        data = self.store.load(self.id)
        if data:
            # Litres used since the last save are lost (at most 10 L).
            self.total_l = float(data["totalL"])
            self.valve_pos = data.get("valve", "open")  # valve returns to last commanded state
        self.valve_report = self.valve_pos
        self._pending = None
        self._saved_l = self.total_l

    def save(self, now: float) -> None:
        data = {"totalL": self.total_l, "valve": self.valve_pos}
        self.store.save(self.id, data | {"savedAt": contract.iso(now)})
        self._saved_l = self.total_l

    # ----- physics: called every simulation tick by the Building -----

    def extra_lpm(self, now: float) -> float:
        """Flow from injected leak or burst scenarios."""
        return sum(
            s.params["lpm"]
            for s in self.scenarios.active(self.id, now)
            if s.type in ("leak", "burst")
        )

    def step(self, t0: float, t1: float, supply_ok: bool) -> float:
        """Advance from t0 to t1. Returns the litres taken from the tank."""
        if self._pending and t1 >= self._pending["done_at"]:
            self._finish_valve(t1)

        flowing = self.valve_pos == "open" and supply_ok
        if flowing:
            extra = self.extra_lpm(t1)
            litres = self.profile.litres_between(t0, t1) + extra * (t1 - t0) / 60.0
            self.flow_lpm = self.profile.flow_at(t1) + extra
        else:
            # Valve closed (or no water in the tank): flow is exactly 0, counter stops.
            litres = 0.0
            self.flow_lpm = 0.0
        self.total_l += litres

        unsaved = self.total_l - self._saved_l
        if unsaved > 0:
            self.maybe_save(t1, unsaved)  # saves after 10 L, or 60 s, whichever comes first
        return litres

    # ----- readings -----

    def _raw_quality(self, now: float) -> tuple[float, float]:
        """pH and temperature without faults: a gentle daily curve plus sensor noise."""
        local = datetime.fromtimestamp(now, self.cfg.tz)
        hour = local.hour + local.minute / 60.0
        rng = random.Random(f"{self.cfg.seed}:{self.id}:quality:{int(now)}")
        # Warmest around 15:00 (28 °C), coolest around 03:00 (18 °C).
        temp = 23.0 + 5.0 * math.cos(2 * math.pi * (hour - 15.0) / 24.0) + rng.gauss(0, 0.15)
        ph = self._ph_base + 0.08 * math.sin(2 * math.pi * hour / 24.0) + rng.gauss(0, 0.03)
        return min(max(ph, NORMAL_PH[0]), NORMAL_PH[1]), temp

    def water_quality(self, now: float) -> tuple[float, float]:
        stuck = self.scenarios.first_active(self.id, now, "sensor_stuck")
        if stuck:
            return self._raw_quality(stuck.start)  # frozen at the value it had when it got stuck
        ph, temp = self._raw_quality(now)
        drift = self.scenarios.first_active(self.id, now, "ph_drift")
        if drift:
            progress = min(1.0, (now - drift.start) / (PH_DRIFT_MINUTES * 60.0))
            ph += (2.0 if drift.params["direction"] == "up" else -2.0) * progress
        spike = self.scenarios.first_active(self.id, now, "temp_spike")
        if spike:
            temp += spike.params["deltaC"] * min(1.0, (now - spike.start) / 60.0)
        return ph, temp

    def telemetry(self, now: float) -> dict[str, Any]:
        ph, temp = self.water_quality(now)
        return {
            **self.header(now),
            "totalL": round(self.total_l, 3),
            "flowLpm": round(self.flow_lpm, 2),
            "ph": round(ph, 2),
            "tempC": round(temp, 1),
            "valve": self.valve_report,
            "rssi": self.rssi(now),
            "fw": self.cfg.fw,
            "boot": self.take_boot_flag(),
        }

    def state(self, now: float) -> dict[str, Any]:
        return {
            "v": 1,
            "ts": contract.iso(now),
            "valve": self.valve_report,
            "intervalS": self.interval_s,
            "fw": self.cfg.fw,
        }

    # ----- valve commands (FR-04, SRS diagram 10.5) -----

    def execute(self, cmd: dict[str, Any], now: float) -> None:
        cmd_id = cmd["cmdId"]
        if cmd["type"] != "valve":
            self.ack(cmd_id, "rejected", "UNSUPPORTED", None, now)
            return
        if self._pending:
            self.ack(cmd_id, "rejected", "BUSY", self.valve_report, now)
            return
        target = "open" if cmd["action"] == "open" else "closed"
        if self.valve_pos == target and self.valve_report != "fault":
            self.ack(cmd_id, "ok", None, target, now)  # already there: nothing to move
            return

        stuck = self.scenarios.first_active(self.id, now, "valve_stuck") is not None
        travel = random.Random(f"{self.cfg.seed}:{cmd_id}").uniform(*VALVE_TRAVEL_S)
        self._pending = {
            "cmd_id": cmd_id,
            "target": target,
            "done_at": now + (VALVE_TIMEOUT_S if stuck else travel),
            "stuck": stuck,
        }
        self.valve_report = "moving"
        self.queue_state(now)

    def _finish_valve(self, now: float) -> None:
        pending, self._pending = self._pending, None
        assert pending is not None
        if pending["stuck"]:
            # The motor did not reach the end position: the valve stays where it was.
            self.valve_report = "fault"
            self.ack(pending["cmd_id"], "failed", "VALVE_STUCK", "fault", now)
        else:
            self.valve_pos = self.valve_report = pending["target"]
            self.save(now)  # remember the commanded state across a power cut
            self.ack(pending["cmd_id"], "ok", None, self.valve_pos, now)
        self.queue_state(now)
