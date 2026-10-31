"""Unusual-use detection (FR-64) with an Isolation Forest, one per building.

In simple words: every home-day becomes a few numbers (litres, how that
compares with the last 7 days, the lowest night flow, how the water was
spread over the day ...). An Isolation Forest learns what the building's
normal days look like. A day it can "isolate" with very few questions is
unlike the others: an unusual day, such as use suddenly doubling.

The model's own score decides; on top of that, a day is only flagged when
its litres really differ from the home's usual day in the last 7 days (the
median: at least 1.4 times or at most half), so a day that is merely shaped
differently is not an alarm. In the evaluation this litres check does most of
the work; the forest keeps out days that are big but normal for that weekday
and home.

Each flag keeps the model version and the features that made it unusual,
and its explanation is written from those numbers (FR-66).
"""

from __future__ import annotations

import io
import pickle
import statistics
from dataclasses import dataclass, field
from datetime import date

import numpy as np

FEATURES = (
    "litres",
    "ratio_7d",
    "z_7d",
    "weekend",
    "night_min_lpm",
    "peak_hour_share",
    "active_hours",
)
# The forest sees how the day compares with the home's own last 7 days (ratio
# and z-score), the weekday and the night flow: not the raw litres, because a
# big family's normal day must not look like a small home's party. The shape
# of the day (busiest hour, hours with use) is kept for the explanation only:
# in tests it added noise and hid real unusual days.
MODEL = [1, 2, 3, 4]
FLAG_QUANTILE = 0.2  # the strangest 20% of training days set the bar
MIN_RATIO_UP, MAX_RATIO_DOWN = 1.4, 0.5
MIN_TRAIN_ROWS = 50


@dataclass(frozen=True)
class Day:
    """What one home used on one day (building time)."""

    home: str
    day: date
    litres: float
    hourly: tuple[float, ...]  # 24 values
    night_min_lpm: float


@dataclass(frozen=True)
class Row:
    home: str
    day: date
    values: tuple[float, ...]  # in FEATURES order
    usual_7d: float
    spread_7d: float

    def get(self, name: str) -> float:
        return self.values[FEATURES.index(name)]


def rows_from(days: list[Day]) -> list[Row]:
    """Feature rows. A home's first 7 days have no 'last 7 days' yet: skipped."""
    by_home: dict[str, list[Day]] = {}
    for d in sorted(days, key=lambda d: (d.home, d.day)):
        by_home.setdefault(d.home, []).append(d)
    rows = []
    for home, items in by_home.items():
        for i in range(7, len(items)):
            d, last7 = items[i], [x.litres for x in items[i - 7 : i]]
            # The median and its spread (MAD), not the mean: one leak or party
            # in the last 7 days must not make the next unusual day look normal.
            usual = statistics.median(last7)
            mad = statistics.median(abs(v - usual) for v in last7) * 1.4826
            spread = max(mad, 0.05 * usual, 1.0)
            total = d.litres
            values = (
                total,
                total / usual if usual > 0 else 1.0,
                (total - usual) / spread,
                1.0 if d.day.weekday() >= 5 else 0.0,
                d.night_min_lpm,
                max(d.hourly) / total if total > 0 else 0.0,
                float(sum(1 for h in d.hourly if h > 1.0)),
            )
            rows.append(Row(home, d.day, values, usual, spread))
    return rows


@dataclass
class UnusualModel:
    """A trained model: the forest, its bar, and what normal looks like."""

    forest: object
    threshold: float
    medians: tuple[float, ...]
    spreads: tuple[float, ...]
    trained_rows: int
    metrics: dict[str, float] = field(default_factory=dict)

    def scores(self, rows: list[Row]) -> np.ndarray:
        return self.forest.decision_function(np.array([r.values for r in rows])[:, MODEL])

    def flags(self, rows: list[Row]) -> list[tuple[Row, float]]:
        """The unusual rows with their score (lower = stranger)."""
        if not rows:
            return []
        out = []
        for row, score in zip(rows, self.scores(rows), strict=True):
            ratio = row.get("ratio_7d")
            different = ratio >= MIN_RATIO_UP or ratio <= MAX_RATIO_DOWN
            if score < self.threshold and different:
                out.append((row, float(score)))
        return out

    def top_features(self, row: Row, count: int = 3) -> list[dict]:
        """The features furthest from a normal day, in spreads (robust z-scores)."""
        ranked = sorted(
            (
                (abs(v - m) / s, name, v, m)
                for name, v, m, s in zip(
                    FEATURES, row.values, self.medians, self.spreads, strict=True
                )
                if name != "weekend"
            ),
            reverse=True,
        )
        return [
            {"feature": name, "value": round(v, 2), "normal": round(m, 2), "distance": round(d, 1)}
            for d, name, v, m in ranked[:count]
        ]

    def dumps(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump(self, buf)
        return buf.getvalue()

    @staticmethod
    def loads(data: bytes) -> UnusualModel:
        # Only ever our own model, read back from our own database.
        return pickle.loads(data)  # noqa: S301


def train(rows: list[Row], seed: int = 0, flag_quantile: float = FLAG_QUANTILE) -> UnusualModel:
    from sklearn.ensemble import IsolationForest

    if len(rows) < MIN_TRAIN_ROWS:
        raise ValueError(f"need at least {MIN_TRAIN_ROWS} home-days to learn, got {len(rows)}")
    x = np.array([r.values for r in rows])
    forest = IsolationForest(n_estimators=200, random_state=seed).fit(x[:, MODEL])
    threshold = float(np.quantile(forest.decision_function(x[:, MODEL]), flag_quantile))
    medians = tuple(float(v) for v in np.median(x, axis=0))
    q1, q3 = np.quantile(x, [0.25, 0.75], axis=0)
    spreads = tuple(
        max(float(b - a), 1e-6, 0.05 * abs(m)) for a, b, m in zip(q1, q3, medians, strict=True)
    )
    return UnusualModel(forest, threshold, medians, spreads, len(rows))


_WORDS = {
    "litres": "litres in the day",
    "ratio_7d": "times the usual day of the last 7 days",
    "z_7d": "standard deviations from the last 7 days",
    "night_min_lpm": "L/min lowest night flow",
    "peak_hour_share": "share of the day's water in its busiest hour",
    "active_hours": "hours with water use",
}


def explain(row: Row, top: list[dict]) -> str:
    """A plain-language reason made from the numbers (FR-66)."""
    ratio = row.get("ratio_7d")
    litres = row.get("litres")
    if ratio >= 1:
        head = (
            f"Used {litres:.0f} L on {row.day:%a %d %b}, {ratio:.1f} times this home's "
            f"usual day in the 7 days before ({row.usual_7d:.0f} L)."
        )
    else:
        head = (
            f"Used only {litres:.0f} L on {row.day:%a %d %b}, {ratio:.0%} of this home's "
            f"usual day in the 7 days before ({row.usual_7d:.0f} L)."
        )
    others = [
        f"{t['value']:g} {_WORDS[t['feature']]} (normal {t['normal']:g})"
        for t in top
        if t["feature"] not in ("litres", "ratio_7d", "z_7d")
    ]
    if others:
        head += " Also unusual: " + "; ".join(others[:2]) + "."
    return head


# ----- checking a new model before it goes live (FR-67) -----


def synthetic_check(model: UnusualModel, holdout: list[Row]) -> dict[str, float]:
    """Labels-free check on recent real days: how many real days it flags
    (fewer is better: those are probably false alarms), and how many copies of
    the same days with the water doubled it catches (more is better)."""
    if not holdout:
        return {"false_rate": 0.0, "doubled_recall": 0.0, "days": 0}
    doubled = []
    for r in holdout:
        v = list(r.values)
        litres = v[0] * 2
        v[0] = litres
        v[1] = litres / r.usual_7d if r.usual_7d > 0 else 2.0
        v[2] = (litres - r.usual_7d) / r.spread_7d
        doubled.append(Row(r.home, r.day, tuple(v), r.usual_7d, r.spread_7d))
    return {
        "false_rate": round(len(model.flags(holdout)) / len(holdout), 4),
        "doubled_recall": round(len(model.flags(doubled)) / len(doubled), 4),
        "days": len(holdout),
    }


def better_or_equal(candidate: dict[str, float], live: dict[str, float] | None) -> bool:
    """A new version goes live only if it is not worse than the current one."""
    if live is None:
        return candidate["doubled_recall"] >= 0.5 and candidate["false_rate"] <= 0.15
    return (
        candidate["doubled_recall"] >= live["doubled_recall"] - 0.05
        and candidate["false_rate"] <= live["false_rate"] + 0.02
    )
