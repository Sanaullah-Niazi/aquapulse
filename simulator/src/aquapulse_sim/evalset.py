"""A labelled test set for the leak detector (FR-63 acceptance criterion 1).

`aquapulse-sim eval-set` runs the digital twin over many days with lots of
leaks and bursts planted at random homes and times, and prints the same JSON
lines as `backfill` (a label line for each planted problem, then readings).
The backend scores its detector against the labels:

    aquapulse-sim eval-set | python -m app.ai.evaluate

The seed is fixed, so the set (and so the scores) are the same every run.
Readings come every minute (live devices: every 10 s; demo history: 5 min).
The 'abnormal' days (whole days with guests: about 2 or 3 times the normal use) score the
unusual-use model (FR-64); the readings also feed the forecast back-test (FR-65).
"""

from __future__ import annotations

import json
import random
import sys
from typing import TextIO

from .backfill import Planned, history
from .config import Config

EVAL_SEED = 2026


def plan(
    homes: int, days: int, leaks: int, bursts: int, abnormal: int = 0, seed: int = EVAL_SEED
) -> tuple[Planned, ...]:
    """Random leaks, bursts and unusual days, never two at the same home within 3 days."""
    rng = random.Random(f"eval:{seed}")
    taken: dict[int, set[int]] = {h: set() for h in range(1, homes + 1)}
    planned: list[Planned] = []
    kinds = ["leak"] * leaks + ["burst"] * bursts + ["abnormal"] * abnormal
    rng.shuffle(kinds)
    for kind in kinds:
        for _ in range(1000):
            home = rng.randint(1, homes)
            day = rng.randint(1 if kind != "abnormal" else 15, days - 3)  # 2 weeks to learn
            if all(abs(day - other) >= 3 for other in taken[home]):  # 30 h leaks: 3 days apart
                break
        else:
            raise ValueError("too many problems for these homes and days")
        taken[home].add(day)
        if kind == "leak":
            # A slow leak: a running toilet or a dripping pipe, 3 to 30 hours.
            minutes = rng.uniform(3, 30) * 60
            params = {"lpm": round(rng.uniform(0.2, 2.0), 2)}
        elif kind == "burst":
            # A burst pipe, noticed by someone after 10 to 40 minutes.
            minutes = rng.uniform(10, 40)
            params = {"lpm": round(rng.uniform(25.0, 40.0), 1)}
        else:
            # A whole day of guests: 1 or 2 more households' worth of water.
            params = {"extra": rng.choice([1, 2])}
            planned.append(Planned(kind, home, day / days, 0, 24 * 60, params))
            continue
        planned.append(
            Planned(kind, home, day / days, rng.randint(0, 23), minutes, params, rng.uniform(0, 59))
        )
    return tuple(planned)


def write(
    cfg: Config,
    end: float,
    days: int = 90,
    leaks: int = 50,
    bursts: int = 12,
    abnormal: int = 30,
    step_s: int = 60,
    out: TextIO = sys.stdout,
) -> int:
    count = 0
    lines = history(
        cfg,
        days,
        end,
        plan(cfg.homes, days, leaks, bursts, abnormal),
        step_s=step_s,
        homes_only=True,
    )
    for line in lines:
        out.write(json.dumps(line, separators=(",", ":")) + "\n")
        count += "topic" in line
    out.flush()
    return count
