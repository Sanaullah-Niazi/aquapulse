"""FR-03 · Telemetry publishing (TC-03). The timing on a real broker is in test_live_mqtt.py."""

from aquapulse_sim import contract

from .conftest import START, run_for


def test_telemetry_follows_the_contract(building):
    run_for(building, START, 8 * 3600, step=10)
    for device in building.devices.values():
        message = device.telemetry(START + 8 * 3600)
        schema = contract.SCHEMA_FOR[(device.kind, "telemetry")]
        assert contract.validate(schema, message) == [], device.id


def test_seq_goes_up_by_one_and_resets_only_after_reboot(building):
    home = building.homes[0]
    seqs = [home.telemetry(START + i * 10)["seq"] for i in range(5)]
    assert seqs == [0, 1, 2, 3, 4]

    first = home.telemetry(START)  # boot flag was already used by the first message
    assert first["boot"] is False

    old_boot = home.boot_id
    building.add_scenario("reboot", home.id, 0, START + 60)
    after = home.telemetry(START + 61)
    assert after["seq"] == 0
    assert after["boot"] is True
    assert after["bootId"] != old_boot
    assert home.telemetry(START + 71)["boot"] is False


def test_first_message_after_start_says_boot(building):
    assert building.homes[0].telemetry(START)["boot"] is True


def test_ph_in_normal_range_and_temperature_follows_the_day(building):
    home = building.homes[0]
    temps = {}
    for minute in range(0, 24 * 60, 5):
        ph, temp = home.water_quality(START + minute * 60)
        assert 6.8 <= ph <= 7.8
        temps[minute // 60] = temp
    assert 17 <= min(temps.values()) <= 20  # coolest early morning
    assert 26 <= max(temps.values()) <= 29  # warmest in the afternoon
    assert temps[15] > temps[3] + 8
