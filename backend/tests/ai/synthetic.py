"""Made-up but realistic daily water use for the AI tests: every home has its
own size, weekends use about 25% more, and every day has some noise."""

from __future__ import annotations

import random
from datetime import date, timedelta

from app.ai.unusual import Day


def litres(homes: int, days: int, first: date, seed: int = 7) -> dict[int, list[float]]:
    rng = random.Random(seed)
    out = {}
    for h in range(homes):
        base = 250 + 120 * h
        out[h] = [
            base
            * (1.25 if (first + timedelta(days=i)).weekday() >= 5 else 1.0)
            * rng.gauss(1.0, 0.07)
            for i in range(days)
        ]
    return out


def days_from(use: dict[int, list[float]], first: date) -> list[Day]:
    return [
        Day(f"home-{h}", first + timedelta(days=i), v, tuple([v / 24] * 24), 0.0)
        for h, values in use.items()
        for i, v in enumerate(values)
    ]
