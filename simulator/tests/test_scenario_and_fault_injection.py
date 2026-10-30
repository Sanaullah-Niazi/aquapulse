"""FR-05 · Scenario and fault injection (TC-05): one test per scenario type, plus the API."""

import pytest
from fastapi.testclient import TestClient

from aquapulse_sim.api import create_app
from aquapulse_sim.scenarios import SCENARIO_TYPES

from .conftest import START, acks, make_cmd, run_for

NIGHT = START + 2 * 3600  # 02:00: almost no normal water use, so faults stand out


def test_leak_flow_never_drops_to_zero_while_active(building):
    home = building.device("home-b01-03")
    building.tick(NIGHT)
    scenario = building.add_scenario("leak", home.id, 120, NIGHT)
    assert 0.2 <= scenario.params["lpm"] <= 2.0
    t = NIGHT
    for _ in range(120 * 6 - 1):  # every 10 s until the planned end
        t += 10
        building.tick(t)
        assert home.telemetry(t)["flowLpm"] > 0
    building.tick(t + 60)  # after the end, quiet night flow can be 0 again
    assert not building.scenarios.active(home.id, t + 60)


def test_burst_is_very_high_flow(building):
    home = building.homes[0]
    building.tick(NIGHT)
    building.add_scenario("burst", home.id, 10, NIGHT)
    building.tick(NIGHT + 5)
    assert home.flow_lpm >= 25


def test_ph_drift_leaves_the_safe_range(building):
    home = building.homes[0]
    building.add_scenario("ph_drift", home.id, 60, NIGHT, {"direction": "up"})
    assert home.telemetry(NIGHT + 30 * 60)["ph"] > 8.5
    building.add_scenario("ph_drift", building.homes[1].id, 60, NIGHT, {"direction": "down"})
    assert building.homes[1].telemetry(NIGHT + 30 * 60)["ph"] < 6.5


def test_temp_spike_raises_temperature(building):
    home = building.homes[0]
    before = home.telemetry(NIGHT)["tempC"]
    building.add_scenario("temp_spike", home.id, 30, NIGHT)
    assert home.telemetry(NIGHT + 120)["tempC"] >= before + 15


def test_sensor_stuck_freezes_ph_and_temperature(building):
    home = building.homes[0]
    building.add_scenario("sensor_stuck", home.id, 60, NIGHT)
    readings = {(m["ph"], m["tempC"]) for m in (home.telemetry(NIGHT + i * 600) for i in range(6))}
    assert len(readings) == 1


def test_valve_stuck_makes_valve_commands_fail(building):
    home = building.homes[0]
    building.add_scenario("valve_stuck", home.id, 10, NIGHT)
    home.handle_command(make_cmd(NIGHT, action="close"), NIGHT)
    run_for(building, NIGHT, 10)
    assert acks(home)[-1]["reason"] == "VALVE_STUCK"


def test_offline_marks_the_device_as_wanted_offline(building):
    client = TestClient(create_app(building, clock=lambda: NIGHT + 1))
    building.add_scenario("offline", "home-b01-02", 5, NIGHT)
    health = client.get("/healthz").json()
    assert health["shouldBeOnline"] == 10  # 11 devices minus the one that is "offline"


def test_reboot_restarts_the_device(building):
    home = building.homes[0]
    old = home.boot_id
    building.add_scenario("reboot", home.id, 0, NIGHT)
    assert home.boot_id != old and home.seq == 0


def test_tank_sensor_fail_hides_the_level(building):
    building.add_scenario("tank_sensor_fail", building.tank.id, 10, NIGHT)
    assert building.tank.telemetry(NIGHT + 1)["levelPct"] is None


def test_sump_empty_reports_sump_not_ok(building):
    building.add_scenario("sump_empty", building.tank.id, 10, NIGHT)
    assert building.tank.telemetry(NIGHT + 1)["sumpOk"] is False


def test_every_scenario_type_has_a_test_above():
    tested = {
        "leak", "burst", "ph_drift", "temp_spike", "sensor_stuck", "valve_stuck",
        "offline", "reboot", "tank_sensor_fail", "sump_empty",
    }  # fmt: skip
    assert tested == set(SCENARIO_TYPES)


# ----- control API -----


@pytest.fixture
def api(building):
    clock = {"now": NIGHT}
    client = TestClient(create_app(building, clock=lambda: clock["now"]))
    client.clock = clock
    return client


def test_api_starts_lists_and_stops_scenarios(api):
    response = api.post(
        "/scenarios", json={"type": "leak", "device": "home-b01-03", "minutes": 120}
    )
    assert response.status_code == 201
    created = response.json()
    assert created["active"] is True
    assert created["start"] < created["end"]

    api.clock["now"] += 600
    assert [s["id"] for s in api.get("/scenarios", params={"active": True}).json()] == [
        created["id"]
    ]
    stopped = api.delete(f"/scenarios/{created['id']}").json()
    assert stopped["active"] is False
    # Ground truth is kept after the end, with the real end time.
    history = api.get("/scenarios").json()
    assert history[0]["end"] == stopped["end"]


def test_api_refuses_scenarios_for_the_wrong_device(api):
    assert api.post("/scenarios", json={"type": "leak", "device": "tank-b01-01"}).status_code == 422
    assert (
        api.post("/scenarios", json={"type": "sump_empty", "device": "home-b01-01"}).status_code
        == 422
    )
    assert api.post("/scenarios", json={"type": "leak", "device": "home-b01-99"}).status_code == 404
    assert (
        api.post("/scenarios", json={"type": "flood", "device": "home-b01-01"}).status_code == 422
    )


def test_api_stop_and_start_a_device(api, building):
    assert api.post("/devices/home-b01-04/stop").json()["stopped"] is True
    assert building.device("home-b01-04").stopped
    assert api.post("/devices/home-b01-04/start").json()["stopped"] is False


def test_api_lists_devices_with_ground_truth(api):
    devices = api.get("/devices").json()
    assert len(devices) == 11
    assert {"persons", "totalL", "valve"} <= set(devices[0])
    assert {"levelPct", "pumpOn", "mode"} <= set(devices[-1])
