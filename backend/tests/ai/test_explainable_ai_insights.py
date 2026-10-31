"""FR-66: every AI finding has a reason made from the data, and its evidence."""

from __future__ import annotations

from datetime import timedelta

from app.ai.leak import Baseline, FlowWatch

from .test_leak_detection import TZ, feed, local


def _night_leak(flow: float, start_hour: int, minutes: int):
    watch = FlowWatch(TZ)
    feed(watch, local(7, start_hour) - timedelta(minutes=5), 5, 0.0)
    feed(watch, local(7, start_hour), minutes, flow)
    return watch.assess()


def test_explanations_change_with_the_data():
    a = _night_leak(0.4, 2, 70)
    b = _night_leak(1.7, 3, 100)
    assert a and b
    assert "lowest 0.4 L/min" in a.explanation and "between 02:00 and 03:09" in a.explanation
    assert "lowest 1.7 L/min" in b.explanation and "between 03:00 and 04:39" in b.explanation
    assert a.explanation != b.explanation


def test_the_usual_night_flow_is_part_of_the_reason():
    watch = FlowWatch(TZ, Baseline(normal_night_lpm=0.1))
    feed(watch, local(7, 2), 90, 0.9)
    found = watch.assess()
    assert found and "Normally this home uses 0.1 L/min at night" in found.explanation


def test_every_finding_has_evidence_for_a_chart():
    long_leak = FlowWatch(TZ)
    feed(long_leak, local(7, 9), 10 * 60, 0.7)
    burst = FlowWatch(TZ)
    feed(burst, local(7, 9), 8, 35.0)
    for watch in (long_leak, burst):
        found = watch.assess()
        assert found is not None and found.explanation.strip()
        ev = found.evidence
        assert ev["minutes"] > 0 and ev["lowest_lpm"] > 0 and ev["burst_lpm"] >= 20
        assert 2 <= len(ev["points"]) <= 61  # a small chart, not every reading
        assert ev["points"][-1][1] == (0.7 if watch is long_leak else 35.0)


def test_a_leak_that_began_before_the_data_says_more_than():
    watch = FlowWatch(TZ)
    feed(watch, local(7, 12), 3 * 60 + 1, 0.5)  # flowing in the very first reading
    found = watch.assess()
    assert found and "for more than 3 h" in found.explanation
