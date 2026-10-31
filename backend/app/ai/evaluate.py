"""Score the AI on simulator data whose problems are known (FR-63, FR-64, FR-65).

    aquapulse-sim eval-set | python -m app.ai.evaluate --write ../docs/ai-evaluation.md

Reads the JSON lines of the simulator (label lines + readings), replays every
home through `FlowWatch` exactly like the worker does (a check every 5
reading, a baseline worked out again every night from the days before), and
compares what it found with what was planted:

* a planted problem is **found** when an alert starts between its start and
  15 minutes after its end;
* an alert that matches no planted problem is a **false alarm**.

recall = found / planted, precision = alerts that match / all alerts.
With --check, the exit code is 1 when a target of FR-63 is missed (CI).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

from . import forecast as fc
from . import unusual
from .leak import Baseline, FlowWatch, LeakLimits, baseline_from, percentile_999

MATCH_AFTER_END = timedelta(minutes=15)
HISTORY_DAYS = 30
NIGHTS = 14

TARGETS = {
    "recall": 0.90,
    "precision": 0.80,
    "slow_median_minutes": 120,
    "burst_median_minutes": 10,
    "unusual_recall": 0.80,  # FR-64: at least 80% of unusual days flagged
    "unusual_false_per_2_weeks": 1.0,  # at most 1 false flag per home per 2 weeks
    "forecast_mape": 0.20,  # FR-65: building 7-day MAPE at most 20%
}
RETRAIN_EVERY = 7  # days; the worker retrains nightly, weekly is enough to score
TRAIN_DAYS = 60


@dataclass
class Label:
    device: str
    kind: str
    start: datetime
    end: datetime
    lpm: float
    seen: datetime | None = None  # the first reading at or after the start
    found_at: datetime | None = None
    found_as: str | None = None


@dataclass
class Episode:
    device: str
    kind: str
    start: datetime
    confidence: float
    explanation: str
    label: Label | None = None


@dataclass
class Home:
    device: str
    watch: FlowWatch
    flows: list[tuple[datetime, float]] = field(default_factory=list)
    night_lows: list[float] = field(default_factory=list)
    night_low: float | None = None
    day: object = None
    open: Episode | None = None


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _new_day(home: Home, tz: ZoneInfo, now: datetime) -> None:
    """At local midnight: work out the baseline again from the days before."""
    cutoff = now - timedelta(days=HISTORY_DAYS)
    home.flows = [(t, f) for t, f in home.flows if t >= cutoff]
    home.watch.baseline = baseline_from(
        percentile_999([f for _, f in home.flows]), home.night_lows[-NIGHTS:]
    )


def evaluate(lines: Iterable[str], tz: ZoneInfo, limits: LeakLimits | None = None) -> dict:
    limits = limits or LeakLimits()
    labels: list[Label] = []
    by_device: dict[str, list[Label]] = defaultdict(list)
    homes: dict[str, Home] = {}
    episodes: list[Episode] = []
    readings = 0
    first = last = None
    # M10 part 2: daily litres per home, for unusual use and the forecast
    days: dict[tuple[str, object], dict] = {}
    totals: dict[str, float] = {}
    unusual_days: set[tuple[str, object]] = set()
    busy_days: set[tuple[str, object]] = set()  # leak or burst: left out of FR-64 scoring

    for raw in lines:
        item = json.loads(raw)
        if "label" in item:
            lab = item["label"]
            start_day = _ts(lab["start"]).astimezone(tz).date()
            end_day = _ts(lab["end"]).astimezone(tz).date()
            if lab["kind"] == "abnormal":
                unusual_days.add((lab["deviceId"], start_day))
            elif lab["kind"] in ("leak", "burst"):
                d = start_day
                while d <= end_day:
                    busy_days.add((lab["deviceId"], d))
                    d += timedelta(days=1)
            if lab["kind"] in ("leak", "burst"):
                labels.append(
                    Label(lab["deviceId"], lab["kind"], _ts(lab["start"]), _ts(lab["end"]),
                          float(lab["details"].get("lpm", 0)))
                )  # fmt: skip
                by_device[labels[-1].device].append(labels[-1])
            continue
        device = item["topic"].split("/")[3]
        if not device.startswith("home-"):
            continue
        payload = item["payload"]
        now, flow = _ts(payload["ts"]), float(payload["flowLpm"])
        readings += 1
        first, last = first or now, now
        home = homes.get(device)
        if home is None:
            home = homes[device] = Home(
                device, FlowWatch(tz, Baseline(), limits, keep_points=False)
            )
        local = now.astimezone(tz)
        if home.day != local.date():
            home.day = local.date()
            _new_day(home, tz, now)
        # the lowest flow in tonight's quiet window, kept for the baseline
        if limits.night_from <= local.hour < limits.night_until:
            home.night_low = flow if home.night_low is None else min(home.night_low, flow)
        elif home.night_low is not None:
            home.night_lows.append(home.night_low)
            home.night_low = None
        home.flows.append((now, flow))
        if "totalL" in payload:
            total = float(payload["totalL"])
            delta = max(0.0, total - totals.get(device, total))
            totals[device] = total
            stats = days.setdefault(
                (device, local.date()), {"litres": 0.0, "hourly": [0.0] * 24, "night": None}
            )
            stats["litres"] += delta
            stats["hourly"][local.hour] += delta
            if limits.night_from <= local.hour < limits.night_until:
                stats["night"] = flow if stats["night"] is None else min(stats["night"], flow)
        for lab in by_device[device]:
            if lab.seen is None and lab.start <= now:
                lab.seen = now
        home.watch.add(now, flow)

        finding = home.watch.assess()
        if finding is None:
            home.open = None
        elif home.open is None or home.open.kind != finding.kind:
            home.open = Episode(device, finding.kind, now, finding.confidence, finding.explanation)
            episodes.append(home.open)

    # Match alerts with planted problems.
    for ep in episodes:
        for lab in by_device[ep.device]:
            if lab.start <= ep.start <= lab.end + MATCH_AFTER_END:
                ep.label = lab
                if lab.found_at is None or ep.start < lab.found_at:
                    lab.found_at, lab.found_as = ep.start, ep.kind
                break
    scores = _scores(labels, episodes, readings, first, last, len(homes))
    day_list = [
        unusual.Day(device, d, v["litres"], tuple(v["hourly"]), v["night"] or 0.0)
        for (device, d), v in sorted(days.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]
    if day_list:
        scores["unusual"] = _unusual_scores(day_list, unusual_days, busy_days)
        scores["forecast"] = _forecast_scores(day_list)
        u, f = scores["unusual"], scores["forecast"]
        scores["passed"]["unusual_recall"] = u["recall"] >= TARGETS["unusual_recall"]
        scores["passed"]["unusual_false"] = (
            u["false_per_home_2_weeks"] <= TARGETS["unusual_false_per_2_weeks"]
        )
        scores["passed"]["forecast_mape"] = (f["mape"] or 1.0) <= TARGETS["forecast_mape"]
        scores["passed"]["forecast_beats_naive"] = (f["mape"] or 1.0) < (f["naive_mape"] or 0.0)
    return scores


def _unusual_scores(days: list[unusual.Day], labelled: set, busy: set) -> dict:
    """Train like the worker (on the 60 days before), score the next week, repeat."""
    rows = unusual.rows_from(days)
    all_days = sorted({r.day for r in rows})
    flagged: set[tuple[str, object]] = set()
    scored: list[unusual.Row] = []
    examples: list[str] = []
    start = 14  # two weeks of rows to learn from first
    for i in range(start, len(all_days), RETRAIN_EVERY):
        week = set(all_days[i : i + RETRAIN_EVERY])
        first_train = all_days[max(0, i - TRAIN_DAYS)]
        train = [r for r in rows if first_train <= r.day < all_days[i]]
        test = [r for r in rows if r.day in week]
        model = unusual.train(train)
        for row, _score in model.flags(test):
            flagged.add((row.home, row.day))
            if (row.home, row.day) in labelled and len(examples) < 1:
                examples.append(unusual.explain(row, model.top_features(row)))
        scored += test
    keys = {(r.home, r.day) for r in scored} - busy
    planted = {k for k in labelled if k in keys}
    normal = keys - labelled
    found = planted & flagged
    false = (normal & flagged) - planted
    homes = len({h for h, _ in keys}) or 1
    normal_days_per_home = len(normal) / homes
    return {
        "planted": len(planted),
        "found": len(found),
        "recall": len(found) / len(planted) if planted else 0.0,
        "false": len(false),
        "normal_days": len(normal),
        "false_per_home_2_weeks": (len(false) / homes) / (normal_days_per_home / 14)
        if normal_days_per_home
        else 0.0,
        "example": examples[0] if examples else "",
    }


def _forecast_scores(days: list[unusual.Day]) -> dict:
    """Building total per day, back-tested from day 28 on (FR-65 criterion 1)."""
    per_day: dict[object, float] = defaultdict(float)
    for d in days:
        per_day[d.day] += d.litres
    ordered = [per_day[d] for d in sorted(per_day)]
    return {"days": len(ordered), **fc.backtest(ordered)}


def _minutes(lab: Label) -> float:
    assert lab.found_at is not None
    return (lab.found_at - (lab.seen or lab.start)).total_seconds() / 60


def _scores(labels, episodes, readings, first, last, homes) -> dict:
    found = [lab for lab in labels if lab.found_at is not None]
    matched = [ep for ep in episodes if ep.label is not None]
    slow = [lab for lab in found if lab.kind == "leak"]
    bursts = [lab for lab in found if lab.kind == "burst"]
    scores = {
        "readings": readings,
        "homes": homes,
        "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None,
        "planted": len(labels),
        "planted_slow": sum(lab.kind == "leak" for lab in labels),
        "planted_burst": sum(lab.kind == "burst" for lab in labels),
        "found": len(found),
        "alerts": len(episodes),
        "false_alarms": len(episodes) - len(matched),
        "recall": len(found) / len(labels) if labels else 0.0,
        "precision": len(matched) / len(episodes) if episodes else 0.0,
        "slow_median_minutes": statistics.median(map(_minutes, slow)) if slow else None,
        "burst_median_minutes": statistics.median(map(_minutes, bursts)) if bursts else None,
        "bursts_named_burst": sum(lab.found_as == "burst" for lab in bursts),
        "missed": [
            {"device": lab.device, "kind": lab.kind, "start": lab.start.isoformat(),
             "hours": round((lab.end - lab.start).total_seconds() / 3600, 1), "lpm": lab.lpm}
            for lab in labels if lab.found_at is None
        ],
        "false": [
            {"device": ep.device, "kind": ep.kind, "start": ep.start.isoformat(),
             "explanation": ep.explanation}
            for ep in episodes if ep.label is None
        ],
        "example": next((ep.explanation for ep in matched if ep.kind == "leak"), ""),
        "example_burst": next((ep.explanation for ep in matched if ep.kind == "burst"), ""),
    }  # fmt: skip
    scores["passed"] = {
        "recall": scores["recall"] >= TARGETS["recall"],
        "precision": scores["precision"] >= TARGETS["precision"],
        "slow_median_minutes": (scores["slow_median_minutes"] or 1e9)
        <= TARGETS["slow_median_minutes"],
        "burst_median_minutes": (scores["burst_median_minutes"] or 1e9)
        <= TARGETS["burst_median_minutes"],
    }
    return scores


def _ok(passed: bool) -> str:
    return "✅ met" if passed else "❌ missed"


def report(s: dict) -> str:
    p = s["passed"]
    lines = [
        "# AI evaluation",
        "",
        "> This file is written by a script (`make ai-eval`), never by hand, so the numbers "
        "are real. CI runs the same script on every push and fails if a target is missed.",
        "",
        "## Leak detection (FR-63)",
        "",
        f"Test set: the simulator's digital twin, {s['homes']} homes, readings every minute "
        f"from {s['first'][:10]} to {s['last'][:10]} ({s['readings']:,} readings). "
        f"{s['planted']} problems were planted at random homes and times: "
        f"{s['planted_slow']} slow leaks (0.2 to 2 L/min, 3 to 30 hours) and "
        f"{s['planted_burst']} bursts (25 to 40 L/min, 10 to 40 minutes). "
        "The detector does not see the labels; it only gets the readings, like in real life.",
        "",
        "| Measure | Result | Target | |",
        "|---|---|---|---|",
        f"| Recall (planted problems found) | {s['recall']:.1%} ({s['found']} of {s['planted']}) "
        f"| ≥ 90% | {_ok(p['recall'])} |",
        f"| Precision (alerts that were real) | {s['precision']:.1%} "
        f"({s['alerts'] - s['false_alarms']} of {s['alerts']}) | ≥ 80% | {_ok(p['precision'])} |",
        f"| Median time to alert, slow leaks | {_fmt_min(s['slow_median_minutes'])} | ≤ 2 h "
        f"| {_ok(p['slow_median_minutes'])} |",
        f"| Median time to alert, bursts | {_fmt_min(s['burst_median_minutes'])} | ≤ 10 min "
        f"| {_ok(p['burst_median_minutes'])} |",
        f'| Bursts named "burst" (not just "leak") | {s["bursts_named_burst"]} of '
        f"{s['planted_burst']} | | |",
        "",
        "How it is counted: a planted problem is found when an alert starts between its "
        "start and 15 minutes after its end. An alert that matches nothing is a false alarm. "
        "The detector checks every reading, like the worker does with live readings. "
        "Time to alert is counted from the first reading that shows the problem (the "
        "detector cannot know earlier). Slow leaks are found after exactly 2 hours of "
        "flow by design (FR-63's continuous-flow rule), sooner when they flow at night.",
        "",
        "Example explanation (FR-66), made from the data:",
        "",
        f"> {s['example']}",
        "",
        f"> {s['example_burst']}",
        "",
    ]
    if s["missed"]:
        lines += [
            "### Missed",
            "",
            "| Home | Kind | Start | Hours | L/min |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {m['device']} | {m['kind']} | {m['start'][:16]} | {m['hours']} | {m['lpm']} |"
            for m in s["missed"]
        ]
        lines.append("")
    if s["false"]:
        lines += [
            "### False alarms",
            "",
            "| Home | Kind | Start | Explanation |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {f['device']} | {f['kind']} | {f['start'][:16]} | {f['explanation']} |"
            for f in s["false"]
        ]
        lines.append("")
    if "unusual" in s:
        u, f = s["unusual"], s["forecast"]
        lines += [
            "## Unusual use (FR-64)",
            "",
            "An Isolation Forest per building, trained on the 60 days before and retrained "
            "every week here (nightly in the worker), scores each home-day. "
            f"{u['planted']} whole days of unusual use (1.8 to 3 times normal) were planted. "
            "Days with a planted leak or burst are left out (the leak detector covers those).",
            "",
            "| Measure | Result | Target | |",
            "|---|---|---|---|",
            f"| Unusual days flagged | {u['recall']:.1%} ({u['found']} of {u['planted']}) "
            f"| ≥ 80% | {_ok(p['unusual_recall'])} |",
            f"| False flags per home per 2 weeks | {u['false_per_home_2_weeks']:.2f} "
            f"({u['false']} on {u['normal_days']:,} normal home-days) | ≤ 1 "
            f"| {_ok(p['unusual_false'])} |",
            "",
            f'Example explanation: "{u["example"]}"',
            "",
            "## Forecast (FR-65)",
            "",
            f"Building total litres per day over {f['days']} days. From day 28 on, every day "
            "the model forecasts the next 7 days from the days before, and the forecast is "
            f"compared with what really happened ({f['origins']} forecasts).",
            "",
            "| Measure | Result | Target | |",
            "|---|---|---|---|",
            f"| MAPE, Holt-Winters (weekly season) | {f['mape']:.1%} | ≤ 20% "
            f"| {_ok(p['forecast_mape'])} |",
            f"| MAPE, seasonal naive (same weekday last week) | {f['naive_mape']:.1%} "
            f"| Holt-Winters must be lower | {_ok(p['forecast_beats_naive'])} |",
            f"| Real litres inside the 80% range | {f['coverage']:.0%} | about 80% | |",
            "",
            "MAPE = mean absolute percentage error: on average, how far the forecast was "
            "from the real litres, in percent.",
            "",
        ]
    lines += [
        "## Limits of this test",
        "",
        "- The data is simulated. Real homes have things the twin does not, such as a "
        "water filter that flushes at night. The night baseline (the home's usual lowest "
        "night flow) is there for that, but only real data can prove it.",
        "- A leak shorter than 2 hours that is not at night cannot be found by design: "
        "it looks like normal use.",
        "",
    ]
    return "\n".join(lines)


def _fmt_min(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0f} min" if value <= 180 else f"{value / 60:.1f} h"


def main(argv: list[str] | None = None, stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", type=Path, help="write the report to this Markdown file")
    parser.add_argument("--check", action="store_true", help="exit 1 if a target is missed")
    parser.add_argument("--timezone", default="Asia/Karachi")
    args = parser.parse_args(argv)
    scores = evaluate(stdin, ZoneInfo(args.timezone))
    text = report(scores)
    if args.write:
        args.write.write_text(text, encoding="utf-8")
    print(text)
    if args.check and not all(scores["passed"].values()):
        print("A target of FR-63 was missed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
