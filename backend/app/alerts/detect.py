"""The fast rules: look at each live event as it arrives (FR-58).

The ingestor announces every stored reading, status change and finished
command on the event bus (M4). The worker hands each one to `Detector.see`,
which answers with "signals": this problem is happening now (active) or is
gone (cleared). `store.py` turns signals into alerts.

This file never touches the database, so every rule can be tested with a list
of made-up events and a made-up clock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .rules import AUDIT_RULE, DEFAULTS, RULES, RuleSettings


def _reading_time(event: dict[str, Any], now: datetime) -> datetime:
    """When the device took the reading (the event's own time), else now."""
    try:
        return datetime.fromisoformat(event["time"])
    except (KeyError, TypeError, ValueError):
        return now


@dataclass(frozen=True)
class Signal:
    rule: str
    key: str  # the dedupe key: one alert per key while it is open
    active: bool
    building_id: str | None = None
    home_id: str | None = None
    tank_id: str | None = None
    device_id: str | None = None
    value: float | None = None
    message: str = ""

    @property
    def severity(self) -> str:
        return RULES[self.rule].severity if self.rule in RULES else AUDIT_RULE.severity


@dataclass
class _Streak:
    count: int = 0  # readings in a row that matched


@dataclass
class _Same:
    values: tuple[float, float] | None = None
    since: datetime | None = None


@dataclass
class Detector:
    """Remembers what it needs between events: streaks, and which problems are on."""

    settings: Callable[[str | None], RuleSettings] = lambda _building: DEFAULTS
    tank_high: dict[str, float] = field(default_factory=dict)  # tank id -> its high threshold %
    active: set[str] = field(default_factory=set)  # keys of problems that are on now
    _streaks: dict[str, _Streak] = field(default_factory=dict)
    _same: dict[str, _Same] = field(default_factory=dict)

    def see(self, event: dict[str, Any], now: datetime) -> list[Signal]:
        kind = event.get("type")
        if kind == "telemetry":
            return self._telemetry(event, _reading_time(event, now))
        if kind == "tank":
            return self._tank(event)
        if kind == "device":
            return self._device(event)
        if kind == "command":
            return self._command(event)
        return []

    # ----- helpers -----

    def _signal(
        self, on: bool, rule: str, key: str, event: dict[str, Any], **extra
    ) -> list[Signal]:
        """Only report changes: a problem that starts, or one that is gone.
        While a problem goes on, every matching reading is reported too, so
        the alert shows the latest value and when it was last seen."""
        if not on and key not in self.active:
            return []
        if on:
            self.active.add(key)
        else:
            self.active.discard(key)
        return [
            Signal(
                rule,
                key,
                on,
                event.get("buildingId"),
                event.get("homeId"),
                event.get("tankId"),
                event.get("deviceId"),
                **extra,
            )
        ]

    def _streak(self, key: str, matches: bool) -> int:
        streak = self._streaks.setdefault(key, _Streak())
        streak.count = streak.count + 1 if matches else 0
        return streak.count

    # ----- home readings: pH, temperature, stuck sensor -----

    def _telemetry(self, e: dict[str, Any], now: datetime) -> list[Signal]:
        s = self.settings(e.get("buildingId"))
        device, signals = e["deviceId"], []
        signals += self._clear_offline(e)
        ph, temp = float(e["ph"]), float(e["tempC"])

        key = f"ph_unsafe:{device}"
        bad = not s.get("ph_unsafe", "low") <= ph <= s.get("ph_unsafe", "high")
        count = self._streak(key, bad)
        if s.on("ph_unsafe") and count >= s.get("ph_unsafe", "readings"):
            side = "low" if ph < s.get("ph_unsafe", "low") else "high"
            signals += self._signal(
                True,
                "ph_unsafe",
                key,
                e,
                value=ph,
                message=f"pH {ph:.2f} is too {side} (safe: "
                f"{s.get('ph_unsafe', 'low'):g} to "
                f"{s.get('ph_unsafe', 'high'):g})",
            )
        elif not bad or not s.on("ph_unsafe"):
            signals += self._signal(False, "ph_unsafe", key, e)

        key = f"temp_abnormal:{device}"
        bad = not s.get("temp_abnormal", "low") <= temp <= s.get("temp_abnormal", "high")
        count = self._streak(key, bad)
        if s.on("temp_abnormal") and count >= s.get("temp_abnormal", "readings"):
            side = "cold" if temp < s.get("temp_abnormal", "low") else "hot"
            signals += self._signal(
                True,
                "temp_abnormal",
                key,
                e,
                value=temp,
                message=f"Water is {temp:.1f} °C, too {side}",
            )
        elif not bad or not s.on("temp_abnormal"):
            signals += self._signal(False, "temp_abnormal", key, e)

        key = f"sensor_stuck:{device}"
        same = self._same.setdefault(key, _Same())
        if same.values != (ph, temp) or same.since is None:
            same.values, same.since = (ph, temp), now
            signals += self._signal(False, "sensor_stuck", key, e)
        elif s.on("sensor_stuck"):
            minutes = (now - same.since).total_seconds() / 60
            if minutes >= s.get("sensor_stuck", "minutes"):
                signals += self._signal(
                    True,
                    "sensor_stuck",
                    key,
                    e,
                    value=ph,
                    message=f"pH {ph:.2f} and {temp:.1f} °C have not "
                    f"changed for {minutes:.0f} minutes",
                )
        else:
            signals += self._signal(False, "sensor_stuck", key, e)
        return signals

    # ----- tank readings -----

    def _tank(self, e: dict[str, Any]) -> list[Signal]:
        s = self.settings(e.get("buildingId"))
        tank, signals = e["tankId"], []
        signals += self._clear_offline(e)

        key = f"dry_run:{tank}"
        if s.on("dry_run") and e["dryRunProtect"]:
            message = "Pump stopped: the sump is empty (dry-run protection)"
            signals += self._signal(True, "dry_run", key, e, message=message)
        else:
            signals += self._signal(False, "dry_run", key, e)

        if e.get("levelPct") is None:
            return signals  # the level sensor failed: nothing to compare (FR-07)
        level = float(e["levelPct"])

        key = f"tank_low:{tank}"
        if s.on("tank_low") and level < s.get("tank_low", "below_pct"):
            message = f"Tank is at {level:.0f} %"
            signals += self._signal(True, "tank_low", key, e, value=level, message=message)
        else:
            signals += self._signal(False, "tank_low", key, e)

        key = f"tank_overflow:{tank}"
        high = self.tank_high.get(tank)
        pump_too_high = bool(e["pumpOn"]) and high is not None and level > high
        count = self._streak(key, pump_too_high)
        message = ""
        if s.on("tank_overflow") and level > s.get("tank_overflow", "above_pct"):
            message = f"Tank is at {level:.1f} %, close to overflowing"
        elif s.on("tank_overflow") and count >= s.get("tank_overflow", "readings"):
            message = (
                f"Pump still ON at {level:.1f} %, above the tank's high threshold ({high:g} %)"
            )
        if message:
            signals += self._signal(True, "tank_overflow", key, e, value=level, message=message)
        else:
            signals += self._signal(False, "tank_overflow", key, e)
        return signals

    # ----- device online/offline and commands -----

    def _clear_offline(self, e: dict[str, Any]) -> list[Signal]:
        return self._signal(False, "device_offline", f"device_offline:{e['deviceId']}", e)

    def _device(self, e: dict[str, Any]) -> list[Signal]:
        if "online" not in e:
            return []
        key = f"device_offline:{e['deviceId']}"
        if e["online"] or not self.settings(e.get("buildingId")).on("device_offline"):
            return self._signal(False, "device_offline", key, e)
        return self._signal(
            True,
            "device_offline",
            key,
            e,
            message="The device lost its connection (MQTT Last Will)",
        )

    def _command(self, e: dict[str, Any]) -> list[Signal]:
        if e.get("commandType") != "valve":
            return []
        key = f"command_failed:{e['deviceId']}"
        state = e.get("state")
        if state in ("failed", "timeout") and self.settings(e.get("buildingId")).on(
            "command_failed"
        ):
            reason = f" ({e['resultReason']})" if e.get("resultReason") else ""
            return self._signal(
                True,
                "command_failed",
                key,
                e,
                message=f"Valve {e.get('action')} command {state}{reason}",
            )
        if state == "succeeded":
            return self._signal(False, "command_failed", key, e)
        return []
