"""FR-63: leak and burst detection. Pure tests on made-up readings (no database),
plus a small run of the evaluation script with planted problems."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.ai import evaluate
from app.ai.leak import (
    BURST_FLOOR_LPM,
    Baseline,
    FlowWatch,
    LeakLimits,
    baseline_from,
    percentile_999,
)

TZ = ZoneInfo("Asia/Karachi")


def local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def feed(watch: FlowWatch, start: datetime, minutes: int, flow) -> None:
    """One reading per minute; `flow` is a number or a function of the minute."""
    for m in range(minutes):
        watch.add(start + timedelta(minutes=m), flow(m) if callable(flow) else flow)


def normal_use(m: int) -> float:
    """A tap for 3 minutes every 20 minutes: water that always stops."""
    return 6.0 if m % 20 < 3 else 0.0


def test_normal_use_is_not_a_leak():
    watch = FlowWatch(TZ)
    feed(watch, local(7, 0), 24 * 60, normal_use)
    assert watch.assess() is None


def test_night_flow_is_found_after_an_hour_in_the_quiet_window():
    watch = FlowWatch(TZ)
    feed(watch, local(7, 1, 30), 30, 0.0)  # quiet until 02:00
    feed(watch, local(7, 2), 59, 0.6)
    assert watch.assess() is None  # 59 minutes: not yet
    feed(watch, local(7, 2, 59), 2, 0.6)
    found = watch.assess()
    assert found is not None and found.kind == "leak"
    assert 0.5 < found.confidence < 0.8  # an hour of night flow alone is not "sure"
    assert "between 02:00 and 03:00" in found.explanation
    assert "lowest 0.6 L/min" in found.explanation


def test_continuous_flow_at_any_time_after_two_hours():
    watch = FlowWatch(TZ)
    feed(watch, local(7, 11), 60, 0.0)
    feed(watch, local(7, 12), 119, lambda m: 1.0 + normal_use(m))
    assert watch.assess() is None
    feed(watch, local(7, 13, 59), 2, 1.0)
    found = watch.assess()
    assert found is not None and found.kind == "leak"
    assert "has not stopped for 2 h" in found.explanation
    assert found.since == local(7, 12)


def test_confidence_grows_with_time_and_a_long_leak_is_sure():
    watch = FlowWatch(TZ)
    feed(watch, local(7, 12), 2 * 60 + 1, 0.5)
    early = watch.assess()
    feed(watch, local(7, 14, 1), 3 * 60, 0.5)
    later = watch.assess()
    assert early and later and later.confidence > early.confidence
    assert later.high  # 5 hours without a stop


def test_one_zero_reading_ends_the_run():
    watch = FlowWatch(TZ)
    feed(watch, local(7, 12), 3 * 60, 0.5)
    assert watch.assess() is not None
    watch.add(local(7, 15, 1), 0.0)
    assert watch.assess() is None


def test_a_long_silence_breaks_the_run():
    watch = FlowWatch(TZ)
    feed(watch, local(7, 12), 60, 0.5)
    feed(watch, local(7, 13, 30), 70, 0.5)  # 30 minutes without readings in between
    assert watch.assess() is None


def test_burst_after_five_minutes_above_the_normal_maximum():
    watch = FlowWatch(TZ, Baseline(burst_lpm=18.0))
    feed(watch, local(7, 14), 4, 0.0)
    feed(watch, local(7, 14, 4), 5, 32.0)
    assert watch.assess() is None  # 4 minutes between first and last high reading
    watch.add(local(7, 14, 9), 33.5)
    found = watch.assess()
    assert found is not None and found.kind == "burst" and found.high
    assert "up to 33.5 L/min" in found.explanation
    assert "normal maximum is 18.0 L/min" in found.explanation


def test_a_shower_and_washing_machine_together_are_not_a_burst():
    watch = FlowWatch(TZ, Baseline(burst_lpm=BURST_FLOOR_LPM))
    feed(watch, local(7, 19), 15, 23.5)
    assert watch.assess() is None or watch.assess().kind != "burst"


def test_owner_limits_are_used():
    watch = FlowWatch(TZ, limits=LeakLimits(continuous_hours=1))
    feed(watch, local(7, 12), 61, 0.5)
    assert watch.assess() is not None


def test_a_home_whose_night_flow_is_always_above_zero_is_not_flagged_at_night():
    watch = FlowWatch(TZ, Baseline(normal_night_lpm=0.5))
    feed(watch, local(7, 2), 90, 0.55)
    assert watch.assess() is None  # its usual night flow (e.g. a filter), not a leak


def test_baseline():
    assert percentile_999([1.0] * 999) is None  # too little data: the floor is used
    flows = [0.0] * 9000 + [14.0] * 985 + [27.0] * 15
    assert percentile_999(flows) == 27.0  # 27 is under twice the 99th percentile (14)
    base = baseline_from(percentile_999(flows), [0.0, 0.2, 0.0])
    assert base.burst_lpm == 27.0 and base.normal_night_lpm == 0.0
    # An earlier burst (35 L/min for 30 readings) does not raise the bar.
    burst = [0.0] * 9000 + [8.0] * 970 + [35.0] * 30
    assert percentile_999(burst) == 8.0
    assert baseline_from(12.0, []).burst_lpm == BURST_FLOOR_LPM


def _line(device: str, when: datetime, flow: float) -> str:
    ts = when.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return json.dumps(
        {"topic": f"aquapulse/v1/dev/{device}/telemetry", "payload": {"ts": ts, "flowLpm": flow}}
    )


def test_the_evaluation_counts_found_missed_and_false_alarms():
    start = local(7, 0)
    leak_start, leak_end = local(7, 12), local(7, 16)
    burst_start = local(8, 15)
    lines = [
        json.dumps({"label": {"deviceId": "home-b01-01", "kind": "leak",
                              "start": leak_start.isoformat(), "end": leak_end.isoformat(),
                              "details": {"lpm": 0.8}}}),
        json.dumps({"label": {"deviceId": "home-b01-02", "kind": "burst",
                              "start": burst_start.isoformat(),
                              "end": (burst_start + timedelta(minutes=20)).isoformat(),
                              "details": {"lpm": 30}}}),
    ]  # fmt: skip
    for m in range(2 * 24 * 60):
        when = start + timedelta(minutes=m)
        leak = 0.8 if leak_start <= when < leak_end else 0.0
        burst = 30.0 if burst_start <= when < burst_start + timedelta(minutes=20) else 0.0
        # home 3 runs a garden hose for 3 hours: a false alarm (no label)
        hose = 4.0 if local(8, 9) <= when < local(8, 12) else 0.0
        lines += [
            _line("home-b01-01", when, leak + normal_use(m)),
            _line("home-b01-02", when, burst + normal_use(m + 7)),
            _line("home-b01-03", when, hose),
        ]
    scores = evaluate.evaluate(lines, TZ)
    assert scores["planted"] == 2 and scores["found"] == 2
    assert scores["recall"] == 1.0
    assert scores["alerts"] == 3 and scores["false_alarms"] == 1
    assert abs(scores["precision"] - 2 / 3) < 1e-9
    assert scores["slow_median_minutes"] == 120
    assert scores["burst_median_minutes"] == 5
    assert scores["passed"]["precision"] is False
    text = evaluate.report(scores)
    assert "| Recall (planted problems found) | 100.0% (2 of 2)" in text
    assert "home-b01-03" in text  # the false alarm is listed

    out = io.StringIO()
    assert evaluate.main(["--check"], stdin=io.StringIO("\n".join(lines))) == 1
    del out
