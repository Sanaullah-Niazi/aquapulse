"""The labelled leak test set for M10 (FR-63): enough problems, never overlapping,
and the same every run."""

from __future__ import annotations

import io
import itertools
import json
from datetime import datetime

from aquapulse_sim.config import Config
from aquapulse_sim.evalset import plan, write


def test_the_plan_has_every_problem_and_no_overlaps():
    planned = plan(homes=10, days=60, leaks=50, bursts=12)
    assert sum(p.type == "leak" for p in planned) == 50
    assert sum(p.type == "burst" for p in planned) == 12
    assert {p.home for p in planned} == set(range(1, 11))  # spread over the 10 homes
    days: dict[int, list[int]] = {}
    for p in planned:
        days.setdefault(p.home, []).append(round(p.at * 60))
    for taken in days.values():
        taken.sort()
        assert all(b - a >= 3 for a, b in itertools.pairwise(taken))
    assert plan(10, 60, 50, 12) == planned  # fixed seed: the same set every run


def test_a_small_set_has_labels_then_home_readings_only():
    cfg = Config(homes=2)
    end = datetime(2026, 6, 1, tzinfo=cfg.tz).timestamp()
    out = io.StringIO()
    count = write(cfg, end, days=4, leaks=1, bursts=1, step_s=600, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    labels = [line["label"] for line in lines if "label" in line]
    assert sorted(label["kind"] for label in labels) == ["burst", "leak"]
    readings = [line for line in lines if "topic" in line]
    assert len(readings) == count == 2 * 4 * 24 * 6
    assert all("/home-" in r["topic"] for r in readings)  # no tank in the test set
