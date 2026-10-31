"""Consumption forecast (FR-65): the next 7 days of litres, with an 80% range.

In simple words: water use has a weekly rhythm (weekends are higher). The
model, Holt-Winters exponential smoothing with a 7-day season, learns the
home's (or building's) usual level and weekly pattern, giving more weight to
recent days. With fewer than 21 days of history it cannot learn a weekly
pattern reliably, so it uses the simple "same weekday last week" forecast
(seasonal naive) instead.

The 80% range: on 8 days out of 10 the real litres should fall inside it.

Pure functions on lists of daily litres (oldest first), so they can be tested
and back-tested without a database.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

SEASON = 7
MIN_HOLT_WINTERS_DAYS = 21  # three weeks: below this, seasonal naive (FR-65)
HORIZON = 7
ALPHA = 0.2  # 80% prediction interval


@dataclass(frozen=True)
class Forecast:
    method: str  # "holt_winters" or "seasonal_naive"
    mean: list[float]
    low: list[float]
    high: list[float]
    history_days: int
    params: dict[str, float] = field(default_factory=dict)


def _clean(history: list[float]) -> list[float]:
    return [max(0.0, float(v)) for v in history]


def seasonal_naive(history: list[float], horizon: int = HORIZON) -> Forecast:
    """Tomorrow = the same weekday last week. The range comes from how wrong that
    rule was on the past days (the 10th and 90th percentile of its errors)."""
    h = _clean(history)
    if not h:
        return Forecast("seasonal_naive", [0.0] * horizon, [0.0] * horizon, [0.0] * horizon, 0)
    if len(h) < SEASON:  # not even a week: repeat the average day
        avg = sum(h) / len(h)
        base = [avg] * horizon
        spread = float(np.std(h)) if len(h) > 1 else avg * 0.3
        low = [max(0.0, v - 1.28 * spread) for v in base]
        high = [v + 1.28 * spread for v in base]
        return Forecast("seasonal_naive", base, low, high, len(h))
    base = [h[len(h) - SEASON + (i % SEASON)] for i in range(horizon)]
    errors = [h[i] - h[i - SEASON] for i in range(SEASON, len(h))]
    if len(errors) >= 4:
        lo_err, hi_err = np.quantile(errors, [ALPHA / 2, 1 - ALPHA / 2])
    else:
        lo_err, hi_err = -0.2 * np.mean(h), 0.2 * np.mean(h)
    low = [max(0.0, v + float(lo_err)) for v in base]
    high = [max(lo, v + float(hi_err)) for v, lo in zip(base, low, strict=True)]
    return Forecast("seasonal_naive", base, low, high, len(h))


def holt_winters(history: list[float], horizon: int = HORIZON) -> Forecast:
    """Additive Holt-Winters (ETS A,N,A) with a weekly season and its 80% interval."""
    import pandas as pd
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    h = pd.Series(_clean(history), dtype=float)  # a Series: the interval code needs an index
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # convergence chatter on flat series
        model = ETSModel(h, error="add", trend=None, seasonal="add", seasonal_periods=SEASON)
        fit = model.fit(disp=False, maxiter=200)
        frame = fit.get_prediction(start=len(h), end=len(h) + horizon - 1).summary_frame(
            alpha=ALPHA
        )
    mean = [max(0.0, float(v)) for v in frame["mean"]]
    low = [max(0.0, float(v)) for v in frame["pi_lower"]]
    high = [max(m, float(v)) for v, m in zip(frame["pi_upper"], mean, strict=True)]
    if not all(math.isfinite(v) for v in mean + low + high):
        raise ValueError("the model did not converge")
    params = {
        k: round(float(v), 4)
        for k, v in zip(fit.param_names, fit.params, strict=False)
        if k in ("smoothing_level", "smoothing_seasonal")
    }
    return Forecast("holt_winters", mean, low, high, len(h), params)


def forecast(history: list[float], horizon: int = HORIZON) -> Forecast:
    """Holt-Winters with 21 days or more of history, else seasonal naive."""
    if len(history) >= MIN_HOLT_WINTERS_DAYS:
        try:
            return holt_winters(history, horizon)
        except (ValueError, np.linalg.LinAlgError):
            pass  # a strange series: the simple rule is safer
    return seasonal_naive(history, horizon)


def month_end(
    actual_to_yesterday: float, today: date, fc: Forecast
) -> tuple[float, float, float, date]:
    """Projected month total (mean, low, high): litres so far plus the forecast
    for today up to the last day of the month. After the 7 forecast days, the
    forecast week is repeated (the weekly pattern)."""
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    last = next_month - timedelta(days=1)
    days = (last - today).days + 1
    mean = low = high = 0.0
    for i in range(days):
        j = i % len(fc.mean)
        mean += fc.mean[j]
        low += fc.low[j]
        high += fc.high[j]
    return actual_to_yesterday + mean, actual_to_yesterday + low, actual_to_yesterday + high, last


# ----- back-test (FR-65 criterion 1) -----


def mape(actual: list[float], predicted: list[float]) -> float:
    """Mean absolute percentage error, skipping days with no water."""
    pairs = [(a, p) for a, p in zip(actual, predicted, strict=True) if a > 0]
    if not pairs:
        return 0.0
    return sum(abs(a - p) / a for a, p in pairs) / len(pairs)


def backtest(daily: list[float], first_origin: int = 28, horizon: int = HORIZON) -> dict:
    """Rolling origin: at every day from `first_origin` on, forecast the next 7
    days from the days before, and compare with what really happened."""
    hw_errors, naive_errors, inside, total = [], [], 0, 0
    for origin in range(first_origin, len(daily) - horizon + 1):
        past, future = daily[:origin], daily[origin : origin + horizon]
        hw = forecast(past, horizon)
        naive = seasonal_naive(past, horizon)
        hw_errors.append(mape(future, hw.mean))
        naive_errors.append(mape(future, naive.mean))
        for a, lo, hi in zip(future, hw.low, hw.high, strict=True):
            inside += lo <= a <= hi
            total += 1
    return {
        "origins": len(hw_errors),
        "mape": float(np.mean(hw_errors)) if hw_errors else None,
        "naive_mape": float(np.mean(naive_errors)) if naive_errors else None,
        "coverage": inside / total if total else None,
    }
