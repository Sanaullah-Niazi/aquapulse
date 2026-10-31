"""FR-64: unusual days are flagged, with the model's reasons (FR-66)."""

from __future__ import annotations

from datetime import date, timedelta

from app.ai import unusual

from .synthetic import days_from, litres

FIRST = date(2026, 3, 2)


def _setup(planted: dict[tuple[int, int], float], days: int = 70):
    use = litres(8, days, FIRST)
    for (home, day), factor in planted.items():
        use[home][day] *= factor
    return unusual.rows_from(days_from(use, FIRST))


def test_a_doubled_day_is_flagged_and_normal_days_mostly_are_not():
    rows = _setup({(2, 64): 2.0, (5, 66): 2.5, (6, 68): 0.3})
    train = [r for r in rows if r.day < FIRST + timedelta(days=60)]
    test = [r for r in rows if r.day >= FIRST + timedelta(days=60)]
    model = unusual.train(train)
    flagged = {(r.home, (r.day - FIRST).days) for r, _ in model.flags(test)}
    assert {("home-2", 64), ("home-5", 66), ("home-6", 68)} <= flagged
    false = flagged - {("home-2", 64), ("home-5", 66), ("home-6", 68)}
    assert len(false) <= 2  # of 77 normal home-days


def test_a_home_s_own_rhythm_is_normal_even_if_it_uses_a_lot():
    rows = _setup({})
    model = unusual.train(rows)
    big_home = [r for r in rows if r.home == "home-7"]
    # 1090 L a day is normal for home 7 (four times home 0). At most a rare
    # noisy Saturday may cross the line, as for any home.
    assert len(model.flags(big_home)) <= 1 < len(big_home) // 20


def test_one_unusual_day_does_not_hide_the_next_one():
    # The usual day is the median of the last 7: a party on day 62 does not
    # make day 64's doubled water look normal.
    rows = _setup({(3, 62): 2.5, (3, 64): 2.0})
    model = unusual.train([r for r in rows if r.day < FIRST + timedelta(days=60)])
    flagged = {(r.home, (r.day - FIRST).days) for r, _ in model.flags(rows)}
    assert ("home-3", 64) in flagged


def test_the_reason_uses_the_real_numbers():
    rows = _setup({(1, 65): 2.0})
    model = unusual.train(rows)
    [(row, score)] = [(r, s) for r, s in model.flags(rows) if (r.day - FIRST).days == 65]
    top = model.top_features(row)
    text = unusual.explain(row, top)
    assert f"Used {row.get('litres'):.0f} L" in text
    assert f"{row.get('ratio_7d'):.1f} times" in text
    assert f"({row.usual_7d:.0f} L)" in text
    assert top[0]["feature"] in ("litres", "ratio_7d", "z_7d") and top[0]["distance"] > 2
    assert score < model.threshold


def test_a_low_day_is_explained_as_low():
    rows = _setup({(4, 65): 0.3})
    model = unusual.train(rows)
    [(row, _)] = [(r, s) for r, s in model.flags(rows) if (r.day - FIRST).days == 65]
    assert "Used only" in unusual.explain(row, model.top_features(row))


def test_a_saved_model_scores_the_same_after_loading():
    rows = _setup({})
    model = unusual.train(rows)
    again = unusual.UnusualModel.loads(model.dumps())
    assert list(again.scores(rows[:20])) == list(model.scores(rows[:20]))
    assert again.threshold == model.threshold


def test_too_little_data_is_refused():
    rows = unusual.rows_from(days_from(litres(1, 30, FIRST), FIRST))
    assert len(rows) < unusual.MIN_TRAIN_ROWS
    try:
        unusual.train(rows)
    except ValueError as err:
        assert "at least" in str(err)
    else:  # pragma: no cover
        raise AssertionError("trained on too little data")
