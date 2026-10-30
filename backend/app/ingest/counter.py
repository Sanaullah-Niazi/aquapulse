"""Litres used, from the meter's cumulative counter (FR-23).

Each home node reports `totalL`, a counter that only goes up, like the
numbers on a mechanical water meter. The litres used since the previous
reading are simply `new total - previous total`. Because of that:

* a lost message loses no litres: the next reading's difference covers the gap;
* a duplicate or late (out-of-order) message adds nothing (difference 0);
* after a reboot the device continues from the value saved in its flash, which
  can be a few litres *lower* than what it reported last (at most 10 L, FR-17).
  A counter that goes down becomes the new starting point; those few litres
  were already counted before the reboot.
* a counter that jumps up more than water can physically flow (200 L/min) is
  not trusted either (e.g. a replaced meter): new starting point, 0 litres.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

MAX_FLOW_LPM = 200.0  # the contract's maximum flow rate
SLACK_L = 1.0  # rounding in the device's report


@dataclass(frozen=True)
class Reading:
    time: datetime
    boot_id: uuid.UUID
    seq: int
    total_l: float


@dataclass(frozen=True)
class Step:
    delta_l: float  # litres to count for this reading
    baseline: Reading  # what the next reading is compared with
    note: str | None = None  # why nothing was counted, if something is odd


def litres_since(last: Reading | None, new: Reading) -> Step:
    if last is None:
        return Step(0.0, new, "first_reading")  # nothing to compare with yet
    same_boot = new.boot_id == last.boot_id
    if (same_boot and new.seq <= last.seq) or new.time <= last.time:
        return Step(0.0, last, "duplicate_or_late")  # already counted; keep the baseline
    difference = new.total_l - last.total_l
    if difference < 0:
        return Step(0.0, new, "counter_down")
    elapsed_min = (new.time - last.time).total_seconds() / 60.0
    if difference > MAX_FLOW_LPM * elapsed_min + SLACK_L:
        return Step(0.0, new, "counter_jump")
    return Step(difference, new)
