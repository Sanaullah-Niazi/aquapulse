"""A labelled test set for the leak detector (FR-63 acceptance criterion 1).

`aquapulse-sim eval-set` runs the digital twin over many days with lots of
leaks and bursts planted at random homes and times, and prints the same JSON
lines as `backfill` (a label line for each planted problem, then readings).
The backend scores its detector against the labels:

    aquapulse-sim eval-set | python -m app.ai.evaluate

The seed is fixed, so the set (and so the scores) are the same every run.
Readings come every minute (live devices: every 10 s; demo history: 5 min).
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
    homes: int, days: int, leaks: int, bursts: int, seed: int = EVAL_SEED
) -> tuple[Planned, ...]:
    """Random leaks and bursts, never two at the same home on the same days."""
    rng = random.Random(f"eval:{seed}")
    taken: dict[int, set[int]] = {h: set() for h in range(1, homes + 1)}
    planned: list[Planned] = []
    kinds = ["leak"] * leaks + ["burst"] * bursts
    rng.shuffle(kinds)
    for kind in kinds:
        for _ in range(1000):
            home = rng.randint(1, homes)
            day = rng.randint(1, days - 3)
            if all(abs(day - other) >= 3 for other in taken[home]):  # 30 h leaks: 3 days apart
                break
        else:
            raise ValueError("too many problems for these homes and days")
        taken[home].add(day)
        if kind == "leak":
            # A slow leak: a running toilet or a dripping pipe, 3 to 30 hours.
            minutes = rng.uniform(3, 30) * 60
            params = {"lpm": round(rng.uniform(0.2, 2.0), 2)}
        else:
            # A burst pipe, noticed by someone after 10 to 40 minutes.
            minutes = rng.uniform(10, 40)
            params = {"lpm": round(rng.uniform(25.0, 40.0), 1)}
        planned.append(
            Planned(kind, home, day / days, rng.randint(0, 23), minutes, params, rng.uniform(0, 59))
        )
    return tuple(planned)


def write(
    cfg: Config,
    end: float,
    days: int = 60,
    leaks: int = 50,
    bursts: int = 12,
    step_s: int = 60,
    out: TextIO = sys.stdout,
) -> int:
    count = 0
    lines = history(
        cfg, days, end, plan(cfg.homes, days, leaks, bursts), step_s=step_s, homes_only=True
    )
    for line in lines:
        out.write(json.dumps(line, separators=(",", ":")) + "\n")
        count += "topic" in line
    out.flush()
    return count
