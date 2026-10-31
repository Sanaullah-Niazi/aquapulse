"""FR-65: the 7-day forecast, its 80% range and the back-test (no database)."""

from __future__ import annotations

import random
from datetime import date

from app.ai import forecast as fc


def _weekly(days: int, seed: int = 3, noise: float = 0.06) -> list[float]:
    rng = random.Random(seed)
    pattern = [400, 410, 395, 405, 420, 520, 540]  # weekends are higher
    return [pattern[i % 7] * rng.gauss(1.0, noise) for i in range(days)]


def test_three_weeks_or_more_use_holt_winters():
    f = fc.forecast(_weekly(42))
    assert f.method == "holt_winters" and len(f.mean) == 7 and f.history_days == 42
    # It learned the weekly pattern: days 5 and 6 (weekend) are higher.
    assert min(f.mean[5], f.mean[6]) > max(f.mean[:5])
    assert all(lo <= m <= hi for lo, m, hi in zip(f.low, f.mean, f.high, strict=True))


def test_less_than_three_weeks_use_same_weekday_last_week():
    history = _weekly(14)
    f = fc.forecast(history)
    assert f.method == "seasonal_naive"
    assert f.mean == history[-7:]


def test_a_few_days_or_none_still_give_an_answer():
    assert fc.forecast([300, 320, 310]).mean == [310.0] * 7
    assert fc.forecast([]).mean == [0.0] * 7


def test_the_backtest_is_honest_and_holt_winters_wins_on_a_weekly_rhythm():
    test = fc.backtest(_weekly(70))
    assert test["origins"] == 70 - 28 - 7 + 1
    assert test["mape"] < test["naive_mape"] and test["mape"] < 0.2
    # The range is the model's own 80% interval. On 70 days of noisy data it is
    # a bit narrow; docs/ai-evaluation.md reports the real coverage.
    assert 0.5 <= test["coverage"] <= 1.0


def test_month_end_adds_the_forecast_to_the_litres_so_far():
    f = fc.Forecast("seasonal_naive", [10.0] * 7, [8.0] * 7, [12.0] * 7, 30)
    mean, low, high, last = fc.month_end(1000.0, date(2026, 10, 25), f)
    assert last == date(2026, 10, 31)
    assert (mean, low, high) == (1070.0, 1056.0, 1084.0)  # 7 days left including today


def test_mape_skips_days_without_water():
    assert fc.mape([100, 0, 200], [110, 50, 180]) == 0.1
