"""FR-65: the forecast API, with the month-end litres and bill estimate made
by the real billing engine and the building's own tariff."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.billing.periods import local_today

from ..billing.conftest import add_tariff, use_water
from .synthetic import litres

TZ = "Asia/Karachi"


async def _home_with_history(make, days: int = 42, tariff: bool = True):
    owner = await make.user("owner")
    building = await make.building(owner, timezone=TZ)
    home = await make.home(building, unit_no="F-1")
    resident = await make.user("resident")
    today = local_today(TZ, datetime.now(UTC))
    first = today - timedelta(days=days)
    await make.occupancy(home, resident, move_in=first - timedelta(days=30), billing=True)
    use = litres(1, days, first)[0]
    await use_water(home, await make.device(home), first, use, TZ)
    if tariff:
        await add_tariff(building, effective_from=date(2020, 1, 1))
    return owner, building, home, resident, today, first, use


def _pkr_bill(litres: float) -> Decimal:
    """The demo slabs (60, 120 and 200 PKR per 1000 L), by hand."""
    cost, done = Decimal(0), Decimal(str(round(litres, 3)))
    for a, b, rate in ((0, 5000, 60), (5000, 15000, 120), (15000, None, 200)):
        part = max(Decimal(0), min(done, Decimal(b) if b else done) - a)
        cost += part * rate / 1000
    return cost.quantize(Decimal("0.01"))


async def test_the_home_forecast_has_7_days_a_range_and_a_month_end_bill(client, make):
    owner, _, home, resident, today, first, use = await _home_with_history(make)
    r = await client.get(f"/api/v1/homes/{home.id}/forecast", headers=await make.headers(owner))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["method"] == "holt_winters" and out["history_days"] == 42
    assert [d["day"] for d in out["days"]] == [
        (today + timedelta(days=i)).isoformat() for i in range(7)
    ]
    assert all(d["low"] <= d["litres"] <= d["high"] for d in out["days"])
    assert len(out["recent"]) == 14 and out["recent"][-1]["litres"] == round(use[-1], 1)
    assert "Holt-Winters" in out["explanation"] and "8 days out of 10" in out["explanation"]

    so_far = sum(v for i, v in enumerate(use) if first + timedelta(days=i) >= today.replace(day=1))
    end = out["month_end"]
    assert end["low"] <= end["litres"] <= end["high"] and end["litres"] >= so_far - 0.1
    # The bill: the building's slabs on the projected month total.
    bill = out["bill"]
    assert bill["currency_code"] == "PKR" and bill["total_text"].startswith("Rs")
    assert abs(Decimal(str(bill["total"])) - _pkr_bill(end["litres"])) <= Decimal("0.02")
    assert "Not a bill" in bill["message"]

    # The resident of the home sees it too; another resident does not.
    r = await client.get(f"/api/v1/homes/{home.id}/forecast", headers=await make.headers(resident))
    assert r.status_code == 200
    stranger = await make.headers(await make.user("resident"))
    assert (
        await client.get(f"/api/v1/homes/{home.id}/forecast", headers=stranger)
    ).status_code == 404


async def test_short_history_uses_same_weekday_last_week(client, make):
    owner, _, home, *_ = await _home_with_history(make, days=10)
    r = await client.get(f"/api/v1/homes/{home.id}/forecast", headers=await make.headers(owner))
    assert r.status_code == 200 and r.json()["method"] == "seasonal_naive"
    assert "same weekday" in r.json()["explanation"].lower()


async def test_without_water_rates_there_is_no_bill_estimate(client, make):
    owner, _, home, *_ = await _home_with_history(make, tariff=False)
    r = await client.get(f"/api/v1/homes/{home.id}/forecast", headers=await make.headers(owner))
    assert r.status_code == 200
    bill = r.json()["bill"]
    assert bill["total"] is None and "not set water rates" in bill["message"]


async def test_the_building_forecast_adds_up_every_home(client, make):
    owner, building, home, resident, *_ = await _home_with_history(make)
    headers = await make.headers(owner)
    one = (await client.get(f"/api/v1/homes/{home.id}/forecast", headers=headers)).json()
    r = await client.get(f"/api/v1/buildings/{building.id}/forecast", headers=headers)
    assert r.status_code == 200, r.text
    whole = r.json()
    # One home only: the building total is that home's.
    assert whole["bill"]["total"] == one["bill"]["total"]
    assert abs(whole["month_end"]["litres"] - one["month_end"]["litres"]) < 1
    assert "whole building" in whole["bill"]["message"]
    r = await client.get(
        f"/api/v1/buildings/{building.id}/forecast", headers=await make.headers(resident)
    )
    assert r.status_code == 403
