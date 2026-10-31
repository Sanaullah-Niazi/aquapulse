"""FR-67: nightly retraining, the model registry, and promotion only when a
new version is not worse. Also FR-64's alerts, made by the nightly job."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import db, worker
from app.ai import registry, unusual
from app.alerts.store import auto_resolve
from app.models import AiModel, Alert, Building

from ..billing.conftest import use_water
from .synthetic import litres

TZ = "Asia/Karachi"
FIRST = date(2026, 7, 1)
DAYS = 70  # 1 Jul to 8 Sep
NIGHT = datetime.combine(FIRST + timedelta(days=DAYS), time(3, 0), ZoneInfo(TZ))  # 9 Sep 03:00


@pytest.fixture(autouse=True)
async def _close_old_alerts():
    """The unusual-use alerts made here are for September: close them after
    each test (as the alert sweep would), so other tests start clean."""
    yield
    async with db.sessions()() as session, session.begin():
        await auto_resolve(session, datetime.now(UTC))


async def _building_with_history(make, planted=None, homes: int = 6):
    owner = await make.user("owner")
    building = await make.building(owner, timezone=TZ)
    use = litres(homes, DAYS, FIRST)
    for (h, day), factor in (planted or {}).items():
        use[h][day] *= factor
    made = []
    for h in range(homes):
        home = await make.home(building, unit_no=f"U-{h}")
        await use_water(home, await make.device(home), FIRST, use[h], TZ)
        made.append(home)
    return owner, building, made


async def _models(building) -> list[AiModel]:
    async with db.sessions()() as session:
        return list(
            await session.scalars(
                select(AiModel)
                .where(AiModel.building_id == building.id)
                .order_by(AiModel.kind, AiModel.version)
            )
        )


async def _nightly(building, now):
    async with db.sessions()() as session, session.begin():
        b = await session.get(Building, building.id)
        return await registry.nightly(session, b, now)


async def test_the_nightly_job_trains_checks_and_saves_both_models(make):
    _, building, _ = await _building_with_history(make)
    assert await _nightly(building, NIGHT - timedelta(hours=3)) == {}  # before 01:00
    done = await _nightly(building, NIGHT)
    assert done["models"] == 2
    forecast, unusual_use = await _models(building)
    assert (forecast.kind, forecast.version, forecast.live) == ("forecast", 1, True)
    assert forecast.metrics["origins"] > 0 and forecast.metrics["mape"] < 0.2
    assert forecast.params["method"] in ("holt_winters", "seasonal_naive")
    assert (unusual_use.kind, unusual_use.version, unusual_use.live) == ("unusual_use", 1, True)
    assert unusual_use.metrics["doubled_recall"] >= 0.8
    assert unusual_use.metrics["false_rate"] <= 0.1
    assert unusual_use.data_to == date(2026, 9, 8) and unusual_use.data_from == date(2026, 7, 11)
    assert unusual_use.blob and unusual_use.note == "live: first version"
    # Once per building day.
    assert await _nightly(building, NIGHT + timedelta(hours=5)) == {}
    # The next night: version 2 of each, and still one live version per kind.
    assert (await _nightly(building, NIGHT + timedelta(days=1)))["models"] == 2
    models = await _models(building)
    assert [m.version for m in models] == [1, 2, 1, 2]
    assert sum(m.live for m in models if m.kind == "unusual_use") == 1


async def test_a_worse_model_is_kept_out_and_the_live_one_stays(make, monkeypatch):
    _, building, _ = await _building_with_history(make)
    await _nightly(building, NIGHT)

    def broken(rows, seed=0, flag_quantile=0.2):
        model = real_train(rows, seed, flag_quantile)
        model.threshold = -1.0  # below every possible score: it never flags anything
        return model

    real_train = unusual.train
    monkeypatch.setattr(unusual, "train", broken)
    async with db.sessions()() as session, session.begin():
        b = await session.get(Building, building.id)
        last = date(2026, 9, 8)
        days = await registry.home_days(session, b, last - timedelta(days=66), last)
        bad = await registry.train_unusual(session, b, days, NIGHT, last - timedelta(days=59), last)
    assert bad is not None and bad.live is False and bad.version == 2
    assert bad.metrics["doubled_recall"] == 0
    assert bad.note == "kept out: worse than the live version"
    live = [m for m in await _models(building) if m.kind == "unusual_use" and m.live]
    assert [m.version for m in live] == [1]


async def test_too_little_history_trains_nothing(make):
    owner = await make.user("owner")
    building = await make.building(owner, timezone=TZ)
    home = await make.home(building)
    await use_water(home, await make.device(home), date(2026, 9, 1), [300.0] * 8, TZ)
    assert await _nightly(building, NIGHT) == {"models": 0, "unusual_days": 0}
    assert await _models(building) == []


async def test_yesterday_s_unusual_day_raises_an_explained_alert(make):
    _, building, homes = await _building_with_history(make, planted={(2, DAYS - 1): 2.2})
    done = await _nightly(building, NIGHT)
    assert done["unusual_days"] >= 1
    async with db.sessions()() as session:
        alerts = list(
            await session.scalars(
                select(Alert).where(Alert.building_id == building.id, Alert.rule == "unusual_use")
            )
        )
    [alert] = [a for a in alerts if a.home_id == homes[2].id]
    assert alert.severity == "info" and alert.title == "Unusual water use"
    assert re.search(r"Used \d+ L on Tue 08 Sep, 2\.\d times this home's usual day", alert.message)
    assert alert.details["model_version"] == 1 and alert.details["day"] == "2026-09-08"
    assert alert.details["top_features"][0]["distance"] > 2
    assert alert.cleared_at is not None  # a past day: it closes again by itself
    assert len(alerts) <= 2  # at most one other home flagged by chance


async def test_the_owner_can_switch_unusual_use_alerts_off(make):
    _, building, _ = await _building_with_history(make, planted={(2, DAYS - 1): 2.2})
    async with db.sessions()() as session, session.begin():
        b = await session.get(Building, building.id)
        b.alert_rules = {"unusual_use": {"enabled": False}}
    assert (await _nightly(building, NIGHT))["unusual_days"] == 0


async def test_the_worker_runs_it_once_a_day_per_building(make, monkeypatch):
    _, building, _ = await _building_with_history(make, homes=1)
    calls = []

    async def fake(session, b, now):
        calls.append(b.id)
        return {}

    monkeypatch.setattr(worker, "nightly", fake)
    monkeypatch.setattr(worker, "_ai_tried", {})
    async with db.sessions()() as session:
        b = await session.get(Building, building.id)
    await worker.nightly_ai([b], NIGHT)
    await worker.nightly_ai([b], NIGHT + timedelta(minutes=1))
    await worker.nightly_ai([b], NIGHT + timedelta(days=1))
    assert calls == [building.id, building.id]


@pytest.mark.parametrize("role", ["owner", "sysadmin", "resident"])
async def test_the_model_list_for_owners_and_admins(client, make, role):
    owner, building, _ = await _building_with_history(make, homes=6)
    await _nightly(building, NIGHT)
    who = owner if role == "owner" else await make.user(role)
    r = await client.get(
        f"/api/v1/buildings/{building.id}/ai/models", headers=await make.headers(who)
    )
    if role == "resident":
        assert r.status_code == 403
        return
    assert r.status_code == 200, r.text
    models = r.json()
    assert {m["kind"] for m in models} == {"forecast", "unusual_use"}
    assert all(m["live"] and m["version"] == 1 and m["metrics"] for m in models)
    assert "blob" not in models[0]
