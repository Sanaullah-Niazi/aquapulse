"""FR-07 · Tank and pump simulation (TC-07). SRS diagram 10.8."""

import pytest

from aquapulse_sim.building import Building
from aquapulse_sim.config import Config
from aquapulse_sim.store import FlashStore

from .conftest import START, acks, make_cmd, run_for


@pytest.fixture
def tank(building):
    return building.tank


def test_auto_mode_keeps_level_between_5_and_100_percent_for_24_hours(building, tank):
    low, high = 100.0, 0.0
    switches = 0
    was_on = tank.pump_on
    t = START
    for _ in range(24 * 3600):
        t += 1
        building.tick(t)
        low, high = min(low, tank.level_pct), max(high, tank.level_pct)
        switches += tank.pump_on != was_on
        was_on = tank.pump_on
    assert low >= 5 and high <= 100
    assert switches >= 2  # the pump really cycled during the day


def test_pump_switches_within_one_interval_of_the_thresholds(building, tank, cfg):
    t = START
    below_since = above_since = None
    for _ in range(24 * 360):
        t += 10
        building.tick(t)
        if tank.level_pct < tank.low_pct and below_since is None:
            below_since = t
        if below_since and tank.pump_on:
            assert t - below_since <= cfg.interval_s
            below_since = None
        if tank.level_pct > tank.high_pct and above_since is None:
            above_since = t
        if above_since and not tank.pump_on:
            assert t - above_since <= cfg.interval_s
            above_since = None


def test_sump_empty_forces_pump_off_with_dry_run_protection(building, tank):
    tank.level_l = 0.1 * tank.capacity_l  # low: auto mode wants the pump ON
    building.tick(START + 1)
    assert tank.pump_on
    building.add_scenario("sump_empty", tank.id, 30, START + 2)
    building.tick(START + 3)
    message = tank.telemetry(START + 3)
    assert message["pumpOn"] is False
    assert message["dryRunProtect"] is True
    assert message["sumpOk"] is False


def manual(tank, now):
    tank.handle_command(make_cmd(now, type_="mode", action="manual"), now)
    acks(tank)


def test_pump_command_needs_manual_mode(tank):
    tank.handle_command(make_cmd(START, type_="pump", action="on"), START)
    assert acks(tank)[0]["reason"] == "NOT_MANUAL_MODE"


def test_manual_pump_on_and_off_are_acknowledged(building, tank):
    manual(tank, START)
    tank.handle_command(make_cmd(START, type_="pump", action="on"), START)
    [ack] = acks(tank)
    assert (ack["result"], ack["state"]) == ("ok", "on")
    building.tick(START + 60)
    assert tank.pump_on  # manual mode: auto thresholds are not applied
    tank.handle_command(make_cmd(START + 61, type_="pump", action="off"), START + 61)
    assert acks(tank)[0]["state"] == "off"


def test_manual_pump_on_refused_when_it_would_dry_run(building, tank):
    manual(tank, START)
    building.add_scenario("sump_empty", tank.id, 30, START)
    tank.handle_command(make_cmd(START, type_="pump", action="on"), START)
    assert acks(tank)[0]["reason"] == "UNSAFE_DRY_RUN"


def test_manual_pump_on_refused_when_it_would_overflow(tank):
    manual(tank, START)
    tank.level_l = 0.99 * tank.capacity_l
    tank.handle_command(make_cmd(START, type_="pump", action="on"), START)
    assert acks(tank)[0]["reason"] == "UNSAFE_OVERFLOW"


def test_manual_pump_stops_itself_at_98_percent(building, tank):
    manual(tank, START)
    tank.level_l = 0.97 * tank.capacity_l
    tank.handle_command(make_cmd(START, type_="pump", action="on"), START)
    run_for(building, START, 300)
    assert not tank.pump_on
    assert tank.level_pct <= 100


def test_thresholds_can_be_changed_but_must_make_sense(tank):
    tank.handle_command(make_cmd(START, type_="thresholds", lowPct=40, highPct=45), START)
    assert acks(tank)[0]["reason"] == "INVALID"  # gap smaller than 10 points
    tank.handle_command(make_cmd(START, type_="thresholds", lowPct=30, highPct=90), START)
    assert acks(tank)[0]["result"] == "ok"
    assert (tank.low_pct, tank.high_pct) == (30, 90)


def test_tank_sensor_failure_stops_pump_after_5_bad_readings(building, tank):
    tank.level_l = 0.1 * tank.capacity_l
    building.tick(START + 1)
    assert tank.pump_on
    building.add_scenario("tank_sensor_fail", tank.id, 10, START + 1)
    for second in range(2, 6):
        building.tick(START + second)
        assert tank.pump_on  # one or two bad readings must not switch the pump
    building.tick(START + 7)
    assert not tank.pump_on
    message = tank.telemetry(START + 7)
    assert message["levelPct"] is None and message["sensorOk"] is False


def test_homes_get_no_water_when_the_tank_is_empty():
    b = Building(Config(tank_start_pct=0.0), START, FlashStore(None))
    b.add_scenario("sump_empty", b.tank.id, 600, START)  # and it cannot refill
    run_for(b, START, 8 * 3600, step=10)
    assert all(home.total_l == 0 for home in b.homes)
