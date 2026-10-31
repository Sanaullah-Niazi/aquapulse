"""Demo history: N days of readings in a few seconds (FR-08).

`aquapulse-sim backfill --days 90` runs the same digital twin (same homes,
same water-use profiles, same tank) from N days ago up to now, as fast as
the computer can, and prints every telemetry message it would have sent as
one JSON line:

    {"topic": "aquapulse/v1/dev/home-b01-03/telemetry", "payload": {...}}

The backend loads these lines through exactly the same checks as live
MQTT messages (`python -m app.backfill`), so demo history and live data
can never disagree on the format. `make demo-data` connects the two.

A few problems are planted on purpose (3 leaks, a burst, 2 pH drifts, a
temperature spike, a reboot and an empty sump). Each one is printed as a
label line, {"label": {...}}, so the backend knows the "ground truth" that
the leak detector (M10) is later scored against.

Differences from live mode, to finish 90 days in seconds:
* one reading every 5 minutes instead of every 10 seconds (the contract
  allows 1 to 300 s; live devices keep 10 s);
* the physics moves in 5-minute steps instead of 5-second steps.
Litres stay exact: the water-use profile is integrated over each step.

Everything is deterministic: the same seed, days and end time give exactly
the same lines, including the boot IDs, so loading twice changes nothing.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, TextIO

from . import contract
from .building import Building
from .config import Config
from .device import Device
from .store import FlashStore

STEP_S = 300  # one reading per device every 5 minutes
BOOT_NAMESPACE = uuid.UUID("5b0c7f3e-6a51-4c8e-9a0f-2f3c1d9e8b47")  # any fixed UUID


@dataclass(frozen=True)
class Planned:
    """A problem planted in the history, placed as a fraction of the period."""

    type: str
    home: int | None  # home number (1 = home-b01-01); None = the tank
    at: float  # 0.0 = start of the period, 1.0 = end
    hour: int  # local hour it starts
    minutes: float
    params: dict[str, Any]
    minute: float = 0  # minutes after the hour


PLAN = (
    Planned("leak", 3, 0.22, 1, 360, {"lpm": 1.2}),  # running toilet, one night
    Planned("leak", 7, 0.45, 22, 1800, {"lpm": 0.4}),  # small drip for 30 hours
    Planned("leak", 5, 0.70, 3, 240, {"lpm": 1.8}),
    Planned("burst", 9, 0.78, 14, 25, {"lpm": 32.0}),  # broken pipe, noticed after 25 min
    Planned("ph_drift", 2, 0.31, 10, 180, {"direction": "up"}),
    Planned("ph_drift", 8, 0.61, 16, 120, {"direction": "down"}),
    Planned("temp_spike", 6, 0.52, 13, 45, {"deltaC": 18.0}),
    Planned("reboot", 4, 0.37, 5, 0, {}),  # power cut: new bootId, counter from flash
    Planned("sump_empty", None, 0.55, 11, 180, {}),  # tank: pump must stay off
)


def _device_id(cfg: Config, home: int | None) -> str:
    if home is None:
        return f"tank-{cfg.building}-01"
    return f"home-{cfg.building}-{(home - 1) % cfg.homes + 1:02d}"


def _boot_id(cfg: Config, device: Device, boots: int) -> str:
    return str(uuid.uuid5(BOOT_NAMESPACE, f"backfill:{cfg.seed}:{device.id}:{boots}"))


def history(
    cfg: Config,
    days: int,
    end: float,
    plan: tuple[Planned, ...] = PLAN,
    step_s: int = STEP_S,
    homes_only: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield label lines and telemetry lines, oldest first."""
    first_day = datetime.fromtimestamp(end, cfg.tz).date() - timedelta(days=days)
    start = datetime.combine(first_day, time(), cfg.tz).timestamp()

    building = Building(cfg, start, FlashStore(None))  # memory only: no files touched
    boots = {d.id: 0 for d in building.devices.values()}
    for device in building.devices.values():
        device.boot_id = _boot_id(cfg, device, 0)  # deterministic, unlike a real boot
        device.interval_s = step_s

    reboots: list[tuple[float, str]] = []
    for planned in plan:
        day = first_day + timedelta(days=int(planned.at * days))
        begins = datetime.combine(day, time(planned.hour), cfg.tz).timestamp()
        begins += planned.minute * 60
        if not start <= begins < end:
            continue
        device_id = _device_id(cfg, planned.home)
        scenario = building.scenarios.add(
            planned.type, device_id, begins, planned.minutes, dict(planned.params)
        )
        if planned.type == "reboot":
            reboots.append((begins, device_id))
        yield {
            "label": {
                "deviceId": device_id,
                "kind": planned.type,
                "start": contract.iso(scenario.start),
                "end": contract.iso(min(scenario.end, end)),
                "details": scenario.params,
            }
        }

    now = start
    while now + step_s <= end:
        now += step_s
        building.tick(now, max_step_s=step_s)
        for when, device_id in [r for r in reboots if r[0] <= now]:
            reboots.remove((when, device_id))
            device = building.devices[device_id]
            device.reboot(when)
            boots[device_id] += 1
            device.boot_id = _boot_id(cfg, device, boots[device_id])
        for device in building.devices.values():
            if homes_only and not device.id.startswith("home-"):
                continue
            payload = device.telemetry(now)
            contract.encode(device.id, "telemetry", payload)  # checked before "sending"
            yield {"topic": contract.topic(device.id, "telemetry"), "payload": payload}


def write(cfg: Config, days: int, end: float, out: TextIO = sys.stdout) -> int:
    """Print the history as JSON lines. Returns the number of telemetry lines."""
    count = 0
    for line in history(cfg, days, end):
        out.write(json.dumps(line, separators=(",", ":")) + "\n")
        count += "topic" in line
    out.flush()
    return count
