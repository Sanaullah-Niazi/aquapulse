"""The virtual tank node: overhead tank + level sensor + pump from the sump (FR-07).

Water path:  sump (ground level) --pump--> overhead tank --pipes--> 10 homes.

The automatic control is the "hysteresis" rule from the old thesis, improved
with safety checks (SRS diagram 10.8):
* level below lowPct  -> pump ON
* level above highPct -> pump OFF
* in between          -> keep doing what it was doing (so the pump does not
                         flick on and off around one value)
* safety, in every mode: pump OFF if the sump is empty (dry-run protection)
  or the tank is 98% full (overflow protection), and OFF after 5 failed
  level readings in a row (a pump must not run blind).
"""

from __future__ import annotations

from typing import Any

from . import contract
from .config import Config
from .device import Device
from .scenarios import ScenarioBook
from .store import FlashStore

OVERFLOW_PCT = 98.0
SENSOR_FAILURES_BEFORE_STOP = 5
READING_EVERY_S = 1.0  # the level sensor is read once per second
MIN_THRESHOLD_GAP = 10.0  # highPct - lowPct must be at least this


class TankNode(Device):
    kind = "tank"

    def __init__(
        self, device_id: str, cfg: Config, scenarios: ScenarioBook, store: FlashStore, now: float
    ) -> None:
        self.capacity_l = cfg.tank_capacity_l
        self.pump_lpm = cfg.pump_lpm
        self.level_l = cfg.tank_capacity_l * cfg.tank_start_pct / 100.0
        self.pump_on = False
        self.mode = "auto"
        self.low_pct = 25.0
        self.high_pct = 95.0
        self.sensor_failures = 0
        self._last_reading = float("-inf")
        self.dry_run_protect = False
        self.overflow_protect = False
        super().__init__(device_id, cfg, scenarios, store, now)
        self.control(now)

    # ----- flash -----

    def restore(self, now: float) -> None:
        data = self.store.load(self.id)
        if data:
            self.level_l = min(float(data["levelL"]), self.capacity_l)
            self.mode = data["mode"]
            self.low_pct = float(data["lowPct"])
            self.high_pct = float(data["highPct"])
        self.pump_on = False  # after power loss the relay starts OFF; control decides again
        self.sensor_failures = 0

    def save(self, now: float) -> None:
        self.store.save(
            self.id,
            {
                "levelL": round(self.level_l, 1),
                "mode": self.mode,
                "lowPct": self.low_pct,
                "highPct": self.high_pct,
                "savedAt": contract.iso(now),
            },
        )

    # ----- sensors -----

    @property
    def level_pct(self) -> float:
        return self.level_l / self.capacity_l * 100.0

    def sump_ok(self, now: float) -> bool:
        return self.scenarios.first_active(self.id, now, "sump_empty") is None

    def sensor_ok(self, now: float) -> bool:
        return self.scenarios.first_active(self.id, now, "tank_sensor_fail") is None

    def can_supply(self) -> bool:
        return self.level_l > 0.0

    # ----- physics + control -----

    def step(self, t0: float, t1: float, outflow_l: float) -> None:
        """Advance from t0 to t1. outflow_l = litres the homes used in this time."""
        inflow_l = self.pump_lpm * (t1 - t0) / 60.0 if self.pump_on else 0.0
        self.level_l = min(max(self.level_l + inflow_l - outflow_l, 0.0), self.capacity_l)
        self.control(t1)
        self.maybe_save(t1)

    def control(self, now: float) -> None:
        """Decide whether the pump should run (SRS diagram 10.8)."""
        sensor_ok = self.sensor_ok(now)
        if now - self._last_reading >= READING_EVERY_S:  # count each reading once
            self._last_reading = now
            self.sensor_failures = 0 if sensor_ok else self.sensor_failures + 1
        want = self.pump_on
        if self.mode == "auto" and sensor_ok:
            if self.level_pct < self.low_pct:
                want = True
            elif self.level_pct > self.high_pct:
                want = False
        if self.sensor_failures >= SENSOR_FAILURES_BEFORE_STOP:
            want = False

        self.dry_run_protect = not self.sump_ok(now)
        # Without a working sensor the device cannot know the level; the
        # sensor-failure rule above already stops the pump.
        self.overflow_protect = sensor_ok and self.level_pct >= OVERFLOW_PCT
        if self.dry_run_protect or self.overflow_protect:
            want = False

        if want != self.pump_on:
            self.pump_on = want
            self.queue_state(now)

    # ----- readings -----

    def telemetry(self, now: float) -> dict[str, Any]:
        ok = self.sensor_ok(now)
        return {
            **self.header(now),
            "levelPct": round(self.level_pct, 1) if ok else None,
            "levelL": round(self.level_l, 1) if ok else None,
            "capacityL": self.capacity_l,
            "pumpOn": self.pump_on,
            "sumpOk": self.sump_ok(now),
            "sensorOk": ok,
            "dryRunProtect": self.dry_run_protect,
            "overflowProtect": self.overflow_protect,
            "mode": self.mode,
            "lowPct": self.low_pct,
            "highPct": self.high_pct,
            "rssi": self.rssi(now),
            "fw": self.cfg.fw,
            "boot": self.take_boot_flag(),
        }

    def state(self, now: float) -> dict[str, Any]:
        return {
            "v": 1,
            "ts": contract.iso(now),
            "pumpOn": self.pump_on,
            "mode": self.mode,
            "lowPct": self.low_pct,
            "highPct": self.high_pct,
            "intervalS": self.interval_s,
            "fw": self.cfg.fw,
        }

    # ----- commands -----

    def execute(self, cmd: dict[str, Any], now: float) -> None:
        cmd_id, kind = cmd["cmdId"], cmd["type"]
        if kind == "mode":
            self.mode = cmd["action"]
            self.save(now)
            self.ack(cmd_id, "ok", None, self.mode, now)
            self.control(now)  # back in auto: apply the thresholds straight away
            self.queue_state(now)
        elif kind == "thresholds":
            low, high = float(cmd["lowPct"]), float(cmd["highPct"])
            if high - low < MIN_THRESHOLD_GAP:
                self.ack(cmd_id, "rejected", "INVALID", None, now)
                return
            self.low_pct, self.high_pct = low, high
            self.save(now)
            self.ack(cmd_id, "ok", None, None, now)
            self.control(now)
            self.queue_state(now)
        elif kind == "pump":
            self._pump_command(cmd_id, cmd["action"] == "on", now)
        else:
            self.ack(cmd_id, "rejected", "UNSUPPORTED", None, now)

    def _pump_command(self, cmd_id: str, turn_on: bool, now: float) -> None:
        if self.mode != "manual":
            # In auto mode the tank decides; switch to manual first.
            self.ack(cmd_id, "rejected", "NOT_MANUAL_MODE", self._pump_word(), now)
            return
        if turn_on and not self.sump_ok(now):
            self.ack(cmd_id, "rejected", "UNSAFE_DRY_RUN", self._pump_word(), now)
            return
        if turn_on and (not self.sensor_ok(now) or self.level_pct >= OVERFLOW_PCT):
            self.ack(cmd_id, "rejected", "UNSAFE_OVERFLOW", self._pump_word(), now)
            return
        self.pump_on = turn_on
        self.ack(cmd_id, "ok", None, self._pump_word(), now)
        self.queue_state(now)

    def _pump_word(self) -> str:
        return "on" if self.pump_on else "off"
