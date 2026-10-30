"""FR-09 · Contract conformance (TC-09).

The JSON Schemas in /contracts are what the simulator and the real ESP32
firmware (M11) must both follow. Here we prove the simulator does, for every
kind of message it can send, including fault situations.
"""

import json

import pytest
from jsonschema import Draft202012Validator

from aquapulse_sim import contract

from .conftest import START, drain, make_cmd, run_for

EXAMPLES = sorted((contract.contracts_dir() / "examples").glob("*/*.json"))


def test_every_schema_is_a_valid_json_schema():
    for path in sorted((contract.contracts_dir() / "schemas").glob("*.json")):
        Draft202012Validator.check_schema(json.loads(path.read_text()))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_examples_follow_their_schema(path):
    schema = f"{path.parent.name}.v1"
    assert contract.validate(schema, json.loads(path.read_text())) == []


def test_every_message_the_simulator_sends_follows_the_contract(building):
    """Run a busy hour with faults and commands; check every single message."""
    now = START + 7 * 3600
    building.tick(now)
    home, tank = building.homes[0], building.tank
    building.add_scenario("leak", home.id, 30, now)
    building.add_scenario("ph_drift", building.homes[1].id, 30, now)
    building.add_scenario("tank_sensor_fail", tank.id, 1, now)
    building.add_scenario("valve_stuck", building.homes[2].id, 5, now)
    home.handle_command(make_cmd(now, action="close"), now)
    building.homes[2].handle_command(make_cmd(now, action="close"), now)
    tank.handle_command(make_cmd(now, type_="pump", action="on"), now)  # refused: auto mode
    tank.handle_command(b"garbage", now)

    sent = 0
    t = now
    for _ in range(360):
        t = run_for(building, t, 10)
        for device in building.devices.values():
            contract.encode(device.id, "telemetry", device.telemetry(t))  # raises if invalid
            contract.encode(device.id, "state", device.state(t))
            for item in drain(device):
                contract.encode(device.id, item.channel, item.payload)
                sent += 1
            sent += 2
    assert sent > 7500  # 11 devices x 360 rounds x (telemetry + state) + acks
    contract.encode("home-b01-01", "status", {"v": 1, "state": "offline"})  # the Last Will


@pytest.mark.parametrize(
    "change",
    [
        {"v": 2},  # unknown version
        {"valve": "half-open"},  # not an allowed value
        {"flowLpm": -1},  # impossible reading
        {"extra": True},  # unknown field: contract drift is caught early
    ],
)
def test_contract_violations_are_refused(building, change):
    message = building.homes[0].telemetry(START) | change
    with pytest.raises(contract.ContractError):
        contract.encode("home-b01-01", "telemetry", message)


def test_missing_field_is_refused(building):
    message = building.homes[0].telemetry(START)
    del message["totalL"]
    with pytest.raises(contract.ContractError):
        contract.encode("home-b01-01", "telemetry", message)
