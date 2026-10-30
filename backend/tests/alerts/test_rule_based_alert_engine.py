"""FR-58: the built-in rules, each fed with the simulator scenario it is meant
to catch, and 7 days of normal simulated water with (almost) no alerts.

These tests run the rules alone (no database): app/alerts/detect.py.
test_alert_lifecycle_... runs the same events through the worker's engine
into the database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.alerts.detect import Detector, Signal
from app.alerts.rules import RULES, settings_for

from .twin import Twin


def _run(twin: Twin, detector: Detector, seconds: float, step: float = 10.0):
    """Feed the twin's events to the rules; returns (time, signal) for each signal."""
    found: list[tuple[datetime, Signal]] = []
    for when, event in twin.run(seconds, step):
        found += [(when, s) for s in detector.see(event, when)]
    return found


def _first(found, rule: str, active: bool = True) -> tuple[datetime, Signal] | None:
    return next(((t, s) for t, s in found if s.rule == rule and s.active == active), None)


def test_every_rule_in_the_srs_is_built_in():
    assert set(RULES) == {
        "ph_unsafe", "temp_abnormal", "device_offline", "tank_low", "tank_overflow",
        "dry_run", "high_usage", "command_failed", "sensor_stuck",
    }  # fmt: skip
    severities = {r.id: r.severity for r in RULES.values()}
    assert severities["ph_unsafe"] == severities["tank_overflow"] == "critical"
    assert severities["dry_run"] == "critical" and severities["high_usage"] == "info"


def test_ph_drift_raises_a_critical_alert_after_5_readings():
    twin, detector = Twin(), Detector()
    device = twin.device(2)
    twin.inject("ph_drift", device, 30, direction="up")
    found = _run(twin, detector, 30 * 60)
    hit = _first(found, "ph_unsafe")
    assert hit is not None
    when, signal = hit
    assert signal.device_id == device and signal.severity == "critical"
    assert signal.value > 8.5 and "too high" in signal.message
    # ... the drift takes about 13 minutes to cross 8.5; 5 readings at 10 s later the
    # alert is there (well within 60 s).
    assert timedelta(minutes=12) < when - datetime.fromtimestamp(twin.now - 1800, UTC)
    assert all(s.device_id == device for _, s in found if s.rule == "ph_unsafe")


def test_alert_comes_within_60_s_once_the_ph_stays_unsafe():
    """Criterion 1. pH sensors are noisy: near 6.5 a reading may bounce back into
    the safe range, which restarts the count. Once readings stay unsafe, the
    5th one (40 s later at 10 s per reading) raises the alert."""
    twin, detector = Twin(), Detector()
    device = twin.device(8)
    twin.inject("ph_drift", device, 30, direction="down")
    first_bad = streak_start = alert_at = streak_at_alert = None
    for when, event in twin.run(30 * 60):
        if event["deviceId"] == device:
            bad = event["ph"] < 6.5
            first_bad = first_bad or (when if bad else None)
            if not bad:
                streak_start = None
            elif streak_start is None:
                streak_start = when
        for s in detector.see(event, when):
            if s.rule == "ph_unsafe" and s.active and alert_at is None:
                alert_at, streak_at_alert = when, streak_start
    assert None not in (first_bad, streak_at_alert, alert_at)
    assert alert_at - streak_at_alert == timedelta(seconds=40)
    assert alert_at - first_bad <= timedelta(minutes=2)


def test_temp_spike_raises_a_warning():
    twin, detector = Twin(), Detector()
    twin.inject("temp_spike", twin.device(6), 10)
    found = _run(twin, detector, 5 * 60)
    _, signal = _first(found, "temp_abnormal")
    assert signal.severity == "warning" and signal.value > 35 and "too hot" in signal.message


def test_one_odd_reading_is_not_enough():
    detector = Detector()
    twin = Twin()
    _, event = next(twin.run(10))
    now = datetime.now(UTC)
    for ph in (9.5, 9.5, 9.5, 9.5, 7.2, 9.5):  # never 5 in a row
        assert [s for s in detector.see(event | {"ph": ph}, now) if s.rule == "ph_unsafe"] == []


def test_stuck_sensor_after_60_minutes():
    twin, detector = Twin(), Detector()
    device = twin.device(4)
    twin.inject("sensor_stuck", device, 90)
    found = _run(twin, detector, 70 * 60)
    when, signal = _first(found, "sensor_stuck")
    assert signal.device_id == device
    # The values freeze at injection; 60 minutes later (not before) the alert comes.
    stuck_for = when - datetime.fromtimestamp(twin.now - 70 * 60, UTC)
    assert timedelta(minutes=60) <= stuck_for <= timedelta(minutes=61)


def test_empty_sump_raises_dry_run_protection():
    twin, detector = Twin(), Detector()
    twin.inject("sump_empty", twin.building.tank.id, 30)
    found = _run(twin, detector, 2 * 60)
    _, signal = _first(found, "dry_run")
    assert signal.severity == "critical" and signal.tank_id == twin.tank_id
    # The sump fills again: the problem is reported gone.
    found = _run(twin, detector, 40 * 60)
    assert _first(found, "dry_run", active=False) is not None


def _tank_event(level: float, pump: bool = False, dry: bool = False) -> dict:
    return {"type": "tank", "buildingId": "b", "tankId": "t1", "deviceId": "tank-b01-01",
            "levelPct": level, "pumpOn": pump, "dryRunProtect": dry}  # fmt: skip


def test_tank_low_and_overflow_risk():
    detector, now = Detector(tank_high={"t1": 95.0}), datetime.now(UTC)
    [low] = detector.see(_tank_event(12.0), now)
    assert (low.rule, low.active, low.severity) == ("tank_low", True, "warning")
    gone = Signal("tank_low", "tank_low:t1", False, "b", None, "t1", "tank-b01-01")
    assert detector.see(_tank_event(40.0), now) == [gone]
    [over] = detector.see(_tank_event(99.0), now)
    assert (over.rule, over.severity) == ("tank_overflow", "critical")
    assert detector.see(_tank_event(90.0), now)[0].active is False
    # Pump still ON above the high threshold (95 %) for 3 readings in a row.
    assert detector.see(_tank_event(96.0, pump=True), now) == []
    assert detector.see(_tank_event(96.5, pump=True), now) == []
    [over] = detector.see(_tank_event(97.0, pump=True), now)
    assert "Pump still ON" in over.message
    # A failed level sensor sends no level: nothing to compare, no crash.
    assert detector.see(_tank_event(None), now) == []


def test_device_offline_from_the_last_will_and_back_online():
    detector, now = Detector(), datetime.now(UTC)
    gone = {"type": "device", "buildingId": "b", "homeId": "h", "deviceId": "home-b01-05",
            "online": False}  # fmt: skip
    [signal] = detector.see(gone, now)
    assert (signal.rule, signal.active, signal.severity) == ("device_offline", True, "warning")
    _, reading = next(Twin().run(10))
    signals = detector.see(reading | {"deviceId": "home-b01-05"}, now)
    assert [(s.rule, s.active) for s in signals] == [("device_offline", False)]


def test_failed_or_timed_out_valve_command():
    detector, now = Detector(), datetime.now(UTC)
    command = {"type": "command", "buildingId": "b", "homeId": "h", "deviceId": "home-b01-03",
               "commandType": "valve", "action": "close"}  # fmt: skip
    [failed] = detector.see(command | {"state": "failed", "resultReason": "VALVE_STUCK"}, now)
    assert failed.active and failed.message == "Valve close command failed (VALVE_STUCK)"
    assert detector.see(command | {"state": "pending"}, now) == []
    [ok] = detector.see(command | {"state": "succeeded"}, now)
    assert not ok.active
    [timeout] = detector.see(command | {"state": "timeout"}, now)
    assert timeout.message == "Valve close command timeout"
    assert detector.see(command | {"commandType": "pump", "state": "failed"}, now) == []


def test_a_switched_off_rule_raises_nothing():
    """FR-62 criterion 2."""
    off = settings_for({"ph_unsafe": {"enabled": False}}, {})
    twin, detector = Twin(), Detector(settings=lambda _b: off)
    twin.inject("ph_drift", twin.device(2), 30, direction="up")
    found = _run(twin, detector, 30 * 60)
    assert _first(found, "ph_unsafe") is None


def test_owner_thresholds_are_used():
    strict = settings_for({"ph_unsafe": {"low": 7.0, "high": 7.5, "readings": 1}}, {})
    detector = Detector(settings=lambda _b: strict)
    _, event = next(Twin().run(10))
    [signal] = [s for s in detector.see(event | {"ph": 7.6}, datetime.now(UTC))
                if s.rule == "ph_unsafe"]  # fmt: skip
    assert signal.active and "safe: 7 to 7.5" in signal.message


def test_seven_normal_days_give_at_most_one_alert_per_home():
    """Criterion 2: no false alarms on normal simulated data (<= 1 per home per week).
    Readings every 60 s for speed; the rules count readings, so this is stricter
    than live (a 5-reading streak lasts 5 minutes instead of 50 s)."""
    twin = Twin()
    detector = Detector(tank_high={twin.tank_id: twin.building.tank.high_pct})
    on: set[str] = set()
    started: list[str] = []  # one entry each time a problem starts
    for when, event in twin.run(7 * 24 * 3600, step=60):
        for s in detector.see(event, when):
            if s.active and s.key not in on:
                started.append(s.key)
            (on.add if s.active else on.discard)(s.key)
    per_device: dict[str, int] = {}
    for key in started:
        device = key.split(":")[1]
        per_device[device] = per_device.get(device, 0) + 1
    assert all(count <= 1 for count in per_device.values()), per_device
