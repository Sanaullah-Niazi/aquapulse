"""Which days a bill covers, and which tariff version applies to which days.

Pure date logic (no database), so every rule is easy to test:

* A billing month is a calendar month in the BUILDING's time zone (SRS A4).
* Each billing contact gets a bill for the days they lived there that month:
  move-in day and move-out day both count (move_out = the last night there).
  Moving in on the 16th of a 30-day month = 15 days (FR-35 criterion 2).
* If the tariff changes mid-month, the days are split into "runs", one per
  tariff version (FR-33 criterion 4).
"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo


class NoTariffError(LookupError):
    """Some days of the period have no tariff (the first version starts later)."""


@dataclass(frozen=True)
class Stay:
    """One resident's occupancy of a home (from the occupancies table)."""

    user_id: uuid.UUID
    move_in: date
    move_out: date | None  # None = still living there
    is_billing_contact: bool


@dataclass(frozen=True)
class Segment:
    """The days of a month one billing contact is billed for (both days included)."""

    user_id: uuid.UUID
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Version:
    id: uuid.UUID
    version: int
    effective_from: date


@dataclass(frozen=True)
class Run:
    """Consecutive days with the same tariff version."""

    version: Version
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def month_of(day: date) -> date:
    return day.replace(day=1)


def month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def month_days(month: date) -> int:
    return calendar.monthrange(month.year, month.month)[1]


def previous_month(day: date) -> date:
    return month_of(month_of(day) - timedelta(days=1))


def local_today(timezone: str, now: datetime | None = None) -> date:
    """Today's date in the building's time zone."""
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).date()


def local_midnight(day: date, timezone: str) -> datetime:
    """The moment `day` starts in the building's time zone, as a UTC time."""
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(timezone)).astimezone(UTC)


def segments(month: date, stays: list[Stay]) -> list[Segment]:
    """Billing contacts' days in the month, oldest first."""
    first, last = month, month_end(month)
    result = []
    for stay in stays:
        if not stay.is_billing_contact:
            continue
        start = max(stay.move_in, first)
        end = min(stay.move_out or last, last)
        if start <= end:
            result.append(Segment(stay.user_id, start, end))
    return sorted(result, key=lambda s: (s.start, str(s.user_id)))


def active_version(versions: list[Version], day: date) -> Version | None:
    """The version in force on `day`: the latest start date on or before it,
    and of two versions with the same start date, the newer one."""
    candidates = [v for v in versions if v.effective_from <= day]
    return max(candidates, key=lambda v: (v.effective_from, v.version), default=None)


def runs(start: date, end: date, versions: list[Version]) -> list[Run]:
    """Split start..end into runs of days with the same tariff version."""
    result: list[Run] = []
    day = start
    while day <= end:
        version = active_version(versions, day)
        if version is None:
            raise NoTariffError(f"No tariff covers {day.isoformat()}")
        if result and result[-1].version == version:
            result[-1] = Run(version, result[-1].start, day)
        else:
            result.append(Run(version, day, day))
        day += timedelta(days=1)
    return result
