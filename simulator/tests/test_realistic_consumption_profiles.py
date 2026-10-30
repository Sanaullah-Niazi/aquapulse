"""FR-02 · Realistic consumption profiles (TC-02)."""

from datetime import datetime, timedelta

import pytest

from aquapulse_sim.config import Config
from aquapulse_sim.profile import HouseholdProfile

from .conftest import START


def week_of(profile: HouseholdProfile, days: int = 7):
    """Litres per (day, hour) for one home, computed exactly from its events."""
    start_day = datetime.fromtimestamp(START, profile.tz).date()
    table = {}
    for d in range(days):
        day = start_day + timedelta(days=d)
        midnight = datetime.combine(day, datetime.min.time(), profile.tz).timestamp()
        for hour in range(24):
            t0 = midnight + hour * 3600
            table[(day, hour)] = profile.litres_between(t0, t0 + 3600)
    return table


@pytest.fixture(scope="module")
def homes():
    cfg = Config()
    sizes = (1, 2, 3, 4, 5, 6, 2, 3, 4, 5)
    return [
        HouseholdProfile(f"home-b01-{n:02d}", persons, cfg.seed, cfg.tz, cfg.weekend)
        for n, persons in enumerate(sizes, start=1)
    ]


def test_daily_use_per_person_is_100_to_150_litres(homes):
    for profile in homes:
        litres = sum(week_of(profile).values())
        per_person_per_day = litres / 7 / profile.persons
        assert 100 <= per_person_per_day <= 150, profile.device_id


def test_morning_and_evening_peaks_are_at_least_twice_the_night(homes):
    hourly = [0.0] * 24
    for profile in homes:
        for (_, hour), litres in week_of(profile).items():
            hourly[hour] += litres
    night = sum(hourly[2:5]) / 3  # 02:00 to 05:00
    morning = max(hourly[6:9])
    evening = max(hourly[18:22])
    assert morning >= 2 * night
    assert evening >= 2 * night


def test_weekends_use_10_to_25_percent_more(homes):
    weekday, weekend = [], []
    for profile in homes:
        per_day = {}
        for (day, _), litres in week_of(profile, days=28).items():
            per_day[day] = per_day.get(day, 0.0) + litres
        for day, litres in per_day.items():
            (weekend if day.weekday() in profile.weekend else weekday).append(litres)
    ratio = (sum(weekend) / len(weekend)) / (sum(weekday) / len(weekday))
    assert 1.10 <= ratio <= 1.25


def test_same_seed_gives_exactly_the_same_data():
    cfg = Config()
    a = HouseholdProfile("home-b01-01", 4, 7, cfg.tz)
    b = HouseholdProfile("home-b01-01", 4, 7, cfg.tz)
    assert week_of(a, days=2) == week_of(b, days=2)
    c = HouseholdProfile("home-b01-01", 4, 8, cfg.tz)  # another seed: other data
    assert week_of(a, days=2) != week_of(c, days=2)


def test_events_crossing_midnight_are_counted_once():
    cfg = Config()
    profile = HouseholdProfile("home-b01-01", 4, 42, cfg.tz)
    day_total = sum(week_of(profile, days=3).values())
    assert profile.litres_between(START, START + 3 * 86400) == pytest.approx(day_total)
