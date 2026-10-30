"""FR-04 · Valve command handling (TC-04). SRS diagram 10.5."""

import pytest

from .conftest import START, acks, drain, make_cmd, run_for


@pytest.fixture
def home(building):
    return building.device("home-b01-01")


def close_and_wait(building, home, now, **kw):
    home.handle_command(make_cmd(now, action="close", **kw), now)
    return run_for(building, now, 10)


def test_close_gives_ok_ack_with_same_cmd_id_within_6_seconds(building, home):
    now = START + 7 * 3600
    building.tick(now)
    drain(home)
    home.handle_command(
        make_cmd(now, action="close", cmd_id="0d6a6a2e-8f0e-4a8e-bb2a-4b9f2b7e0c11"), now
    )
    assert home.valve_report == "moving"
    t = now
    received = []
    while not received and t < now + 6:
        t += 1
        building.tick(t)
        received = acks(home)
    assert received, "no ack within 6 s"
    assert received[0]["result"] == "ok"
    assert received[0]["cmdId"] == "0d6a6a2e-8f0e-4a8e-bb2a-4b9f2b7e0c11"
    assert received[0]["state"] == "closed"


def test_closed_valve_means_zero_flow_and_counter_stops(building, home):
    now = START + 7 * 3600
    building.tick(now)
    t = close_and_wait(building, home, now)
    building.add_scenario("leak", home.id, 60, t)  # even a leak cannot pass a closed valve
    total = home.total_l
    for _ in range(360):
        t += 10
        building.tick(t)
        message = home.telemetry(t)
        assert message["valve"] == "closed"
        assert message["flowLpm"] == 0
    assert home.total_l == total

    home.handle_command(make_cmd(t, action="open"), t)
    run_for(building, t, 10)
    assert home.valve_report == "open"


def test_expired_command_is_rejected_and_valve_does_not_move(home):
    now = START + 100
    home.handle_command(make_cmd(now - 60, action="close", ttl=30), now)
    [ack] = acks(home)
    assert (ack["result"], ack["reason"]) == ("rejected", "EXPIRED")
    assert home.valve_report == "open"


def test_duplicate_command_is_rejected(building, home):
    now = START + 100
    cmd = make_cmd(now, action="close")
    home.handle_command(cmd, now)
    run_for(building, now, 10)
    drain(home)
    home.handle_command(cmd, now + 11)
    [ack] = acks(home)
    assert (ack["result"], ack["reason"]) == ("rejected", "DUPLICATE")


def test_stuck_valve_fails_with_valve_stuck(building, home):
    now = START + 100
    building.add_scenario("valve_stuck", home.id, 10, now)
    home.handle_command(make_cmd(now, action="close"), now)
    run_for(building, now, 10)
    ack = acks(home)[-1]
    assert (ack["result"], ack["reason"], ack["state"]) == ("failed", "VALVE_STUCK", "fault")
    assert home.valve_pos == "open"  # it never moved
    assert home.telemetry(now + 10)["valve"] == "fault"


def test_already_in_requested_state_is_ok_without_moving(home):
    now = START + 100
    home.handle_command(make_cmd(now, action="open"), now)
    [ack] = acks(home)
    assert (ack["result"], ack["state"]) == ("ok", "open")


def test_command_while_moving_is_busy(home):
    now = START + 100
    home.handle_command(make_cmd(now, action="close"), now)
    home.handle_command(make_cmd(now + 1, action="open"), now + 1)
    assert acks(home)[-1]["reason"] == "BUSY"


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[]", b'{"v": 1}', b'{"v": 2, "cmdId": "x"}'],
)
def test_broken_commands_are_rejected_as_invalid(home, raw):
    home.handle_command(raw, START)
    [ack] = acks(home)
    assert (ack["result"], ack["reason"]) == ("rejected", "INVALID")


def test_invalid_command_with_readable_id_echoes_the_id(home):
    raw = make_cmd(START, action="explode", cmd_id="7e2d4c6b-1a3f-4b5c-8d9e-0f1a2b3c4d5e")
    home.handle_command(raw, START)
    [ack] = acks(home)
    assert ack["cmdId"] == "7e2d4c6b-1a3f-4b5c-8d9e-0f1a2b3c4d5e"


def test_pump_command_is_unsupported_on_a_home(home):
    home.handle_command(make_cmd(START, type_="pump", action="on"), START)
    assert acks(home)[0]["reason"] == "UNSUPPORTED"


def test_interval_command_changes_the_reporting_interval(home):
    home.handle_command(make_cmd(START, type_="interval", seconds=2), START)
    out = drain(home)
    assert out[0].payload["result"] == "ok"
    assert home.interval_s == 2
    assert out[1].channel == "state" and out[1].payload["intervalS"] == 2
