"""FR-17 · Device resilience, simulator part (TC-17). Broker restart: test_live_mqtt.py."""

import random

from aquapulse_sim.building import Building
from aquapulse_sim.mqtt_link import retry_delay
from aquapulse_sim.store import FlashStore

from .conftest import START, make_cmd, run_for


def test_reconnect_back_off_doubles_up_to_60_seconds_with_jitter():
    rng = random.Random(1)
    delays = [retry_delay(attempt, rng) for attempt in range(10)]
    for attempt, delay in enumerate(delays):
        base = min(2**attempt, 60)
        assert 0.8 * base <= delay <= 1.2 * base
    assert max(delays) <= 72


def test_reboot_loses_at_most_10_litres(building):
    home = building.device("home-b01-05")  # 5 people: lots of water
    t = START + 7 * 3600
    for _ in range(50):
        t = run_for(building, t, 37)
        before = home.total_l
        building.add_scenario("reboot", home.id, 0, t)
        assert before - 10 <= home.total_l <= before
        assert home.telemetry(t)["boot"] is True


def test_valve_returns_to_last_commanded_state_after_reboot(building):
    home = building.homes[0]
    home.handle_command(make_cmd(START, action="close"), START)
    run_for(building, START, 10)
    building.add_scenario("reboot", home.id, 0, START + 20)
    assert home.valve_report == "closed"


def test_counters_survive_a_simulator_restart(tmp_path, cfg):
    store = FlashStore(str(tmp_path))
    first = Building(cfg, START, store)
    run_for(first, START, 6 * 3600, step=10)
    for device in first.devices.values():
        device.save(START + 6 * 3600)  # what `docker compose stop` does on the way out
    totals = {home.id: home.total_l for home in first.homes}

    second = Building(cfg, START + 6 * 3600, FlashStore(str(tmp_path)))
    for home in second.homes:
        assert home.total_l == totals[home.id]
        assert home.telemetry(START)["boot"] is True
    assert abs(second.tank.level_l - first.tank.level_l) < 0.1


def test_damaged_flash_file_is_treated_as_empty(tmp_path, cfg):
    (tmp_path / "home-b01-01.json").write_text("{not json")
    building = Building(cfg, START, FlashStore(str(tmp_path)))
    assert building.homes[0].total_l == 0
