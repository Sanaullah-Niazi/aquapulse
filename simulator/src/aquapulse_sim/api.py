"""The simulator's control API (FR-05). Open http://localhost:8090/docs for Swagger UI.

It is a developer tool, not part of the product: Docker publishes it on
127.0.0.1 only, so nobody else on your network can inject problems.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .building import Building
from .contract import iso
from .scenarios import SCENARIO_TYPES, Scenario

ScenarioType = Literal[
    "leak",
    "burst",
    "ph_drift",
    "temp_spike",
    "sensor_stuck",
    "valve_stuck",
    "offline",
    "reboot",
    "tank_sensor_fail",
    "sump_empty",
]


class ScenarioIn(BaseModel):
    type: ScenarioType
    device: str = Field(examples=["home-b01-03"])
    minutes: float = Field(
        60, gt=0, le=7 * 24 * 60, description="How long it lasts (ignored by reboot)"
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description='Optional: {"lpm": 1.2} for leak/burst, {"direction": "up"} for ph_drift, '
        '{"deltaC": 18} for temp_spike. Left empty, realistic random values are chosen.',
    )


class ScenarioOut(BaseModel):
    id: int
    type: str
    device: str
    start: str
    end: str
    active: bool
    params: dict[str, Any]


def _out(s: Scenario, now: float) -> ScenarioOut:
    return ScenarioOut(
        id=s.id,
        type=s.type,
        device=s.device,
        start=iso(s.start),
        end=iso(s.end),
        active=s.active(now),
        params=s.params,
    )


def create_app(building: Building, clock=time.time) -> FastAPI:
    app = FastAPI(
        title="AquaPulse simulator",
        version="1.0.0",
        description="Control the digital twin: list devices, switch them off and on, "
        "and inject problems (leaks, bursts, sensor faults ...). Every scenario is kept "
        "with its exact start and end time as ground truth for the AI (M9).",
    )

    def device_or_404(device_id: str):
        try:
            return building.device(device_id)
        except KeyError:
            raise HTTPException(404, f"No device called {device_id!r}") from None

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, Any]:
        """healthy = every device that should be online is connected to MQTT."""
        now = clock()
        devices = list(building.devices.values())
        should_be_online = [
            d
            for d in devices
            if not d.stopped and not building.scenarios.first_active(d.id, now, "offline")
        ]
        connected = sum(d.connected for d in should_be_online)
        return {
            "healthy": connected == len(should_be_online),
            "devices": len(devices),
            "shouldBeOnline": len(should_be_online),
            "connected": connected,
        }

    @app.get("/devices", tags=["devices"])
    def list_devices() -> list[dict[str, Any]]:
        building.tick(clock())
        return [building.describe(d) for d in building.devices.values()]

    @app.get("/devices/{device_id}", tags=["devices"])
    def get_device(device_id: str) -> dict[str, Any]:
        device = device_or_404(device_id)
        building.tick(clock())
        return building.describe(device)

    @app.post("/devices/{device_id}/stop", tags=["devices"])
    def stop_device(device_id: str) -> dict[str, Any]:
        """Switch the device off the network politely (it sends 'offline' itself)."""
        device = device_or_404(device_id)
        device.stopped = True
        return building.describe(device)

    @app.post("/devices/{device_id}/start", tags=["devices"])
    def start_device(device_id: str) -> dict[str, Any]:
        device = device_or_404(device_id)
        device.stopped = False
        return building.describe(device)

    @app.get("/scenario-types", tags=["scenarios"])
    def scenario_types() -> dict[str, dict[str, str]]:
        return {k: {"device": kind, "what": what} for k, (kind, what) in SCENARIO_TYPES.items()}

    @app.post("/scenarios", status_code=201, tags=["scenarios"])
    def add_scenario(body: ScenarioIn) -> ScenarioOut:
        device_or_404(body.device)
        now = clock()
        try:
            scenario = building.add_scenario(body.type, body.device, body.minutes, now, body.params)
        except ValueError as err:
            raise HTTPException(422, str(err)) from None
        return _out(scenario, now)

    @app.get("/scenarios", tags=["scenarios"])
    def list_scenarios(active: bool | None = None) -> list[ScenarioOut]:
        """All scenarios with start and end times: the ground-truth labels."""
        now = clock()
        items = building.scenarios.all()
        if active is not None:
            items = [s for s in items if s.active(now) == active]
        return [_out(s, now) for s in items]

    @app.delete("/scenarios/{scenario_id}", tags=["scenarios"])
    def stop_scenario(scenario_id: int) -> ScenarioOut:
        """End a scenario now (its record is kept, with the new end time)."""
        now = clock()
        building.tick(now)  # count the scenario's effect right up to this moment
        try:
            scenario = building.scenarios.stop(scenario_id, now)
        except KeyError:
            raise HTTPException(404, f"No scenario {scenario_id}") from None
        return _out(scenario, now)

    return app
