"""FR-23: litres come from the cumulative counter, so lost messages lose nothing."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from app.ingest.counter import Reading, litres_since

T0 = datetime(2026, 10, 5, tzinfo=UTC)
BOOT = uuid.uuid4()


def _reading(seconds: float, total: float, seq: int, boot=BOOT) -> Reading:
    return Reading(T0 + timedelta(seconds=seconds), boot, seq, total)


def _week_of_readings(seed: int) -> tuple[list[Reading], list[float]]:
    """7 days of 10-second readings with random bursts of use.
    Returns the readings and the true litres of each day."""
    rng = random.Random(seed)
    total, readings, per_day = 1000.0, [], [0.0] * 7
    for seq in range(7 * 8640):
        if seq and rng.random() < 0.08:  # a tap, toilet or shower running in these 10 s
            used = rng.uniform(0.5, 2.0)
            total += used
            per_day[seq // 8640] += used
        readings.append(_reading(seq * 10, total, seq))
    return readings, per_day


def _daily(readings: list[Reading]) -> list[float]:
    """Litres per day, the way the database adds them up (delta_l by reading time)."""
    last, days = None, [0.0] * 7
    for reading in readings:
        step = litres_since(last, reading)
        days[int((reading.time - T0).total_seconds() // 86400)] += step.delta_l
        last = step.baseline
    return days


def test_no_loss_counts_every_litre():
    readings, truth = _week_of_readings(1)
    assert all(abs(a - b) < 1e-6 for a, b in zip(_daily(readings), truth, strict=True))


def test_ten_percent_of_messages_lost_daily_totals_differ_by_less_than_half_a_percent():
    """FR-23 criterion 1: drop 10% of the readings at random, compare every day."""
    for seed in range(5):
        readings, truth = _week_of_readings(seed)
        rng = random.Random(1000 + seed)
        kept = [r for r in readings if rng.random() >= 0.10]
        assert len(kept) < 0.92 * len(readings)
        for counted, true in zip(_daily(kept)[1:], truth[1:], strict=True):
            # Day 0 starts with the first kept reading, so it is skipped.
            assert abs(counted - true) / true < 0.005


def test_duplicate_and_late_messages_add_nothing():
    a, b, c = _reading(0, 100, 0), _reading(10, 105, 1), _reading(20, 109, 2)
    assert sum(_daily([a, b, b, c, b])) == 9
    step = litres_since(b, _reading(5, 104, 1))
    assert step.delta_l == 0 and step.note == "duplicate_or_late" and step.baseline == b


def test_reboot_with_counter_from_flash_never_goes_negative_or_jumps():
    """FR-23 criterion 2: after a reboot the device continues from its last
    flash save, a few litres lower. Nothing negative, no jump, and counting
    goes on normally from the new value."""
    before = [_reading(0, 500, 10), _reading(10, 506, 11)]
    new_boot = uuid.uuid4()
    after = [_reading(60, 498, 0, new_boot), _reading(70, 503, 1, new_boot)]
    steps, last = [], None
    for reading in before + after:
        step = litres_since(last, reading)
        steps.append(step)
        last = step.baseline
    assert [s.delta_l for s in steps] == [0, 6, 0, 5]
    assert steps[2].note == "counter_down"


def test_reboot_with_higher_counter_counts_the_difference():
    """Water used between the last report and the reboot is not lost."""
    step = litres_since(_reading(0, 500, 10), _reading(30, 504, 0, uuid.uuid4()))
    assert step.delta_l == 4


def test_impossible_jump_is_not_counted():
    """More than 200 L/min (e.g. a replaced meter): new starting point, 0 litres."""
    step = litres_since(_reading(0, 500, 1), _reading(10, 900, 2))
    assert step.delta_l == 0 and step.note == "counter_jump"
    assert litres_since(step.baseline, _reading(20, 902, 3)).delta_l == 2


def test_first_reading_counts_nothing():
    step = litres_since(None, _reading(0, 123_456, 0))
    assert step.delta_l == 0 and step.note == "first_reading"
