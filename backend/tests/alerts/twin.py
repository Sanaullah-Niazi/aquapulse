"""The simulator's digital twin (M2), run inside the tests, as event source.

The alert tests do not invent readings: they run the real simulator code
(simulator/src) with a scenario injected, the way `aquapulse-sim` does, and
turn each telemetry message into the event the ingestor would announce.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "simulator" / "src"))

from aquapulse_sim.building import Building
from aquapulse_sim.config import Config
from aquapulse_sim.store import FlashStore

START = datetime(2026, 9, 7, 6, 0, tzinfo=UTC).timestamp()  # an ordinary Monday morning


class Twin:
    def __init__(
        self,
        homes: int = 10,
        building_id: str | None = None,
        home_ids: dict[str, str] | None = None,
        tank_id: str | None = None,
        start: float = START,
        tag: str = "",
    ) -> None:
        self.cfg = Config(homes=homes)
        self.building = Building(self.cfg, start, FlashStore(None))
        self.now = start
        self.building_id = building_id or str(uuid.uuid4())
        self.home_ids = home_ids or {h.id: str(uuid.uuid4()) for h in self.building.homes}
        self.tank_id = tank_id or str(uuid.uuid4())
        self.tag = tag  # added to device IDs, so tests sharing a database never clash

    def id(self, device_id: str) -> str:
        return f"{device_id}-{self.tag}" if self.tag else device_id

    def device(self, number: int) -> str:
        """The simulator's ID of home number `number` (1 = home-b01-01)."""
        return self.building.homes[number - 1].id

    def inject(self, type_: str, device: str, minutes: float, **params) -> None:
        self.building.scenarios.add(type_, device, self.now, minutes, params)

    def run(self, seconds: float, step: float = 10.0) -> Iterator[tuple[datetime, dict[str, Any]]]:
        """(time, event) for every device, every `step` seconds."""
        end = self.now + seconds
        while self.now + step <= end + 1e-6:
            self.now += step
            self.building.tick(self.now, max_step_s=min(step, 5.0))
            when = datetime.fromtimestamp(self.now, UTC)
            for device in self.building.devices.values():
                yield when, self.event(device.id, device.telemetry(self.now), when)

    def event(self, device_id: str, p: dict[str, Any], when: datetime) -> dict[str, Any]:
        """The same fields as app/ingest/handler.py announces."""
        if device_id.startswith("tank"):
            return {"type": "tank", "buildingId": self.building_id, "tankId": self.tank_id,
                    "deviceId": self.id(device_id), "time": when.isoformat(),
                    "levelPct": p["levelPct"],
                    "pumpOn": p["pumpOn"], "mode": p["mode"], "sumpOk": p["sumpOk"],
                    "dryRunProtect": p["dryRunProtect"],
                    "overflowProtect": p["overflowProtect"]}  # fmt: skip
        return {"type": "telemetry", "buildingId": self.building_id,
                "homeId": self.home_ids[device_id], "deviceId": self.id(device_id),
                "time": when.isoformat(), "totalL": p["totalL"], "flowLpm": p["flowLpm"],
                "ph": p["ph"], "tempC": p["tempC"], "valve": p["valve"]}  # fmt: skip
