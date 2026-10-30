"""FR-31: health checks and Prometheus metrics (TC-31).
"/metrics is not reachable from outside" is checked against Caddy in scripts/smoke-test.sh."""

from __future__ import annotations

from app.config import get_settings


async def test_healthz_always_answers(client):
    r = await client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_readyz_is_200_when_database_and_broker_are_up(client):
    r = await client.get("/api/readyz")
    assert r.status_code == 200, r.json()
    assert r.json() == {"database": True, "broker": True}


async def test_readyz_is_503_when_the_broker_is_down(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "mqtt_port", 1)  # nothing listens there
    r = await client.get("/api/readyz")
    assert r.status_code == 503
    assert r.json() == {"database": True, "broker": False}


async def test_readyz_is_503_when_the_database_is_down(client, monkeypatch):
    from app.routers import health

    class Broken:
        def connect(self):
            raise OSError("database is down")

    monkeypatch.setattr(health, "engine", lambda: Broken())
    r = await client.get("/api/readyz")
    assert r.status_code == 503
    assert r.json()["database"] is False


async def test_metrics_count_requests_by_route_not_by_id(client, make):
    world = await make.world()
    await client.get(f"/api/v1/homes/{world.home.id}", headers=await make.headers(world.owner))
    text = (await client.get("/metrics")).text
    assert 'aquapulse_http_requests_total{method="GET",route="get_home",status="200"}' in text
    assert str(world.home.id) not in text
    assert "aquapulse_http_request_seconds_bucket" in text
