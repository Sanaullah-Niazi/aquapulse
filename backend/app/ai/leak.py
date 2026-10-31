"""Leak and burst detection (FR-63) with a plain-language reason (FR-66).

How it works, in simple words: a home's water normally stops. Taps, showers
and toilets run for minutes, then the flow goes back to 0. So we follow the
current "run": the time since the flow was last 0. Three detectors look at it:

1. Night flow: the run covers at least 60 minutes of the quiet window
   (02:00 to 05:00, building time) without ever reaching 0.
2. Continuous flow: the run has lasted 2 hours or more, at any time of day.
3. Burst: the flow has stayed above the home's normal maximum (its 99.9th
   percentile over the last 30 days, at least 24 L/min) for 5 minutes or more.
   Readings above twice the 99th percentile are left out of that percentile:
   they are not normal use (an earlier burst must not raise the bar).

Night and continuous flow combine into one confidence score from 0 to 1.
A burst is always high confidence. When the flow goes back to 0 the run
ends, and so does the finding.

`FlowWatch` takes readings one by one (oldest first), so the worker can
replay the last hours from the database, and the evaluation can replay
60 days of simulator data, with exactly the same code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# A gap longer than this between two readings breaks the run: we do not know
# what happened while the device was silent.
MAX_GAP = timedelta(minutes=15)
BURST_FLOOR_LPM = 24.0  # a shower, a washing machine and a tap together are not a burst
HIGH_CONFIDENCE = 0.8  # FR-61: automatic shut-off only from here up
EVIDENCE_POINTS = 60  # at most this many points in the evidence chart


@dataclass(frozen=True)
class LeakLimits:
    """The numbers of FR-63. The owner may change them per building (FR-62)."""

    night_from: int = 2  # quiet window start hour (building time)
    night_until: int = 5  # quiet window end hour
    night_minutes: float = 60  # flowing this long inside the window
    continuous_hours: float = 2  # flowing this long at any time
    burst_minutes: float = 5  # above the burst level this long


@dataclass(frozen=True)
class Baseline:
    """What is normal for one home, worked out once a day from 30 days of data."""

    burst_lpm: float = BURST_FLOOR_LPM  # the 99.9th percentile, at least the floor
    normal_night_lpm: float = 0.0  # the usual lowest flow in the quiet window


@dataclass(frozen=True)
class Finding:
    kind: str  # "leak" or "burst"
    confidence: float  # 0 to 1
    since: datetime  # when the run (or the burst) started
    minutes: float
    lowest_lpm: float
    explanation: str  # FR-66: made from the numbers, never fixed text
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def high(self) -> bool:
        return self.kind == "burst" or self.confidence >= HIGH_CONFIDENCE


def _hm(minutes: float) -> str:
    """135 -> "2 h 15 min"; 40 -> "40 min"."""
    whole = round(minutes)
    hours, rest = divmod(whole, 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h" if not rest else f"{hours} h {rest} min"


def _lpm(value: float) -> str:
    return f"{value:.1f} L/min"


class FlowWatch:
    """Follows one home's flow, reading by reading."""

    def __init__(
        self,
        tz: ZoneInfo,
        baseline: Baseline | None = None,
        limits: LeakLimits | None = None,
        keep_points: bool = True,
    ) -> None:
        self.tz = tz
        self.baseline = baseline or Baseline()
        self.limits = limits or LeakLimits()
        self.keep_points = keep_points
        self.last: datetime | None = None
        self.last_flow = 0.0
        self.run_start: datetime | None = None  # first reading of the current run
        self.lowest = 0.0
        self.litres = 0.0
        self.high_since: datetime | None = None  # first reading above the burst level
        self.peak = 0.0
        self.points: list[tuple[datetime, float]] = []
        self.truncated = False  # the run started before the first reading we have

    def add(self, when: datetime, flow: float) -> None:
        if self.last is not None and when - self.last > MAX_GAP:
            self._stop()  # silent device: start again
        if flow <= 0:
            self._stop()
        else:
            if self.run_start is None:
                self.run_start, self.lowest, self.litres = when, flow, 0.0
                self.truncated = self.last is None  # flowing in the very first reading
                self.points = []
            else:
                assert self.last is not None
                self.litres += (flow + self.last_flow) / 2 * (when - self.last).total_seconds() / 60
                self.lowest = min(self.lowest, flow)
            if flow > self.baseline.burst_lpm:
                if self.high_since is None:
                    self.high_since, self.peak = when, flow
                self.peak = max(self.peak, flow)
            else:
                self.high_since = None
            if self.keep_points:
                self.points.append((when, flow))
        self.last, self.last_flow = when, flow

    def _stop(self) -> None:
        self.run_start = None
        self.high_since = None
        self.points = []
        self.truncated = False

    # ----- the three detectors -----

    def _night_minutes(self) -> tuple[float, datetime, datetime]:
        """Minutes of the current run inside a quiet window (the latest one it touches)."""
        assert self.run_start is not None and self.last is not None
        day = self.last.astimezone(self.tz).date()
        for d in (day, day - timedelta(days=1)):
            start = datetime.combine(d, time(self.limits.night_from), self.tz)
            end = datetime.combine(d, time(self.limits.night_until), self.tz)
            overlap = (min(end, self.last) - max(start, self.run_start)).total_seconds() / 60
            if overlap > 0:
                return overlap, start, end
        return 0.0, self.last, self.last

    def assess(self) -> Finding | None:
        """Is the current run a leak or a burst? None when it looks normal."""
        if self.run_start is None or self.last is None:
            return None
        lim, base = self.limits, self.baseline
        run_minutes = (self.last - self.run_start).total_seconds() / 60

        if self.high_since is not None:
            burst_minutes = (self.last - self.high_since).total_seconds() / 60
            if burst_minutes >= lim.burst_minutes:
                confidence = min(1.0, 0.9 + 0.01 * (burst_minutes - lim.burst_minutes))
                return Finding(
                    "burst",
                    round(confidence, 2),
                    self.high_since,
                    burst_minutes,
                    self.lowest,
                    f"Very high flow, up to {_lpm(self.peak)}, for {_hm(burst_minutes)} since "
                    f"{self._clock(self.high_since)}. This home's normal maximum is "
                    f"{_lpm(base.burst_lpm)}. This looks like a broken pipe.",
                    self._evidence(burst_minutes, peak_lpm=round(self.peak, 2)),
                )

        night, night_start, night_end = self._night_minutes()
        night_score = 0.0
        if night >= lim.night_minutes and self.lowest > base.normal_night_lpm * 1.2:
            window = (night_end - night_start).total_seconds() / 60
            night_score = 0.5 + 0.4 * min(1.0, night / window)
        continuous_score = 0.0
        if run_minutes >= lim.continuous_hours * 60:
            continuous_score = min(0.95, 0.5 + 0.15 * (run_minutes / 60 - lim.continuous_hours))
        if not night_score and not continuous_score:
            return None
        confidence = 1 - (1 - night_score) * (1 - continuous_score)

        reasons = []
        if night_score:
            reasons.append(
                f"Water kept flowing between {self._clock(max(night_start, self.run_start))} and "
                f"{self._clock(min(night_end, self.last))} (lowest {_lpm(self.lowest)}). "
                f"Normally this home uses {_lpm(base.normal_night_lpm)} at night."
            )
        if continuous_score or not night_score:
            more = "more than " if self.truncated else ""
            reasons.append(
                f"The flow has not stopped for {more}{_hm(run_minutes)}, since "
                f"{self._clock(self.run_start)} (lowest {_lpm(self.lowest)}, about "
                f"{self.litres:.0f} L). Taps and showers stop; a running toilet or a "
                "dripping pipe does not."
            )
        return Finding(
            "leak",
            round(confidence, 2),
            self.run_start,
            run_minutes,
            self.lowest,
            " ".join(reasons),
            self._evidence(
                run_minutes,
                night_minutes=round(night, 1),
                night_score=round(night_score, 2),
                continuous_score=round(continuous_score, 2),
                litres=round(self.litres, 1),
            ),
        )

    def _clock(self, when: datetime) -> str:
        local = when.astimezone(self.tz)
        if self.last is not None and local.date() != self.last.astimezone(self.tz).date():
            return local.strftime("%a %H:%M")
        return local.strftime("%H:%M")

    def _evidence(self, minutes: float, **extra: Any) -> dict[str, Any]:
        step = max(1, len(self.points) // EVIDENCE_POINTS)
        points = self.points[::step][-EVIDENCE_POINTS:]
        if self.points and points[-1] != self.points[-1]:
            points.append(self.points[-1])
        return {
            "minutes": round(minutes, 1),
            "since": self.run_start.isoformat() if self.run_start else None,
            "lowest_lpm": round(self.lowest, 2),
            "burst_lpm": round(self.baseline.burst_lpm, 2),
            "normal_night_lpm": round(self.baseline.normal_night_lpm, 2),
            "points": [[t.isoformat(), round(f, 2)] for t, f in points],
            **extra,
        }


def baseline_from(p999: float | None, night_lows: list[float]) -> Baseline:
    """A home's normal values: the 99.9th percentile of its flow over the last
    30 days (None when there is too little data) and the lowest flow of each
    recent quiet window."""
    burst = max(BURST_FLOOR_LPM, p999) if p999 is not None else BURST_FLOOR_LPM
    night = sorted(night_lows)[len(night_lows) // 2] if night_lows else 0.0
    return Baseline(burst_lpm=burst, normal_night_lpm=night)


def percentile_999(flows: list[float]) -> float | None:
    """The 99.9th percentile of normal use, or None with fewer than 1000 readings.
    Readings above twice the 99th percentile (a burst) are left out first."""
    if len(flows) < 1000:
        return None
    ordered = sorted(flows)
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
    if p99 > 0:
        ordered = [f for f in ordered if f <= 2 * p99]
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.999))]
