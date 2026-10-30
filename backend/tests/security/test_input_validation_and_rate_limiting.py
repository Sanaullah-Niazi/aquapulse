"""FR-72: strict input validation and the general API rate limit (TC-72).
The login limit is tested in test_password_security.py (FR-68)."""

from __future__ import annotations

import pytest

from app.deps import api_limiter
from app.security.ratelimit import SlidingWindow


async def test_unknown_fields_are_rejected(client, make):
    headers = await make.headers(await make.user("owner"))
    r = await client.post(
        "/api/v1/buildings", headers=headers, json={"name": "x", "is_admin": True}
    )
    assert r.status_code == 422
    r = await client.patch("/api/v1/me", headers=headers, json={"role": "sysadmin"})
    assert r.status_code == 422  # nobody can make themselves admin


@pytest.mark.parametrize(
    "body",
    [
        {"unit_no": ""},
        {"unit_no": "x" * 21},
        {"unit_no": "A1", "floor": 999},
        {"unit_no": "A1", "occupants": -1},
        {"unit_no": "A1", "notes": "x" * 501},
        {"unit_no": 101},
    ],
    ids=["empty", "too-long", "floor-range", "negative", "notes-long", "wrong-type"],
)
async def test_home_fields_are_checked(client, make, body):
    world = await make.world()
    r = await client.post(
        f"/api/v1/buildings/{world.building.id}/homes",
        headers=await make.headers(world.owner),
        json=body,
    )
    assert r.status_code == 422


async def test_bad_email_and_phone_are_rejected(client, make):
    r = await client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422
    headers = await make.headers(await make.user("owner"))
    r = await client.patch("/api/v1/me", headers=headers, json={"phone": "call me maybe"})
    assert r.status_code == 422
    r = await client.patch("/api/v1/me", headers=headers, json={"phone": "+92 300 1234567"})
    assert r.status_code == 200
    assert r.json()["phone"] == "+92 300 1234567"


async def test_sql_injection_in_input_is_just_text(client, make):
    world = await make.world()
    headers = await make.headers(world.owner)
    evil = "x'); DROP TABLE homes; --"
    r = await client.patch(
        f"/api/v1/buildings/{world.building.id}", headers=headers, json={"name": evil}
    )
    assert r.status_code == 200
    assert r.json()["name"] == evil  # stored as plain text; the homes table is fine:
    assert (await client.get(f"/api/v1/homes/{world.home.id}", headers=headers)).status_code == 200


async def test_more_than_120_requests_a_minute_get_429(client, make):
    headers = await make.headers(await make.user("owner"))
    for _ in range(api_limiter.limit):
        assert (await client.get("/api/v1/me", headers=headers)).status_code == 200
    r = await client.get("/api/v1/me", headers=headers)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    other = await make.headers(await make.user("owner"))  # the limit is per user
    assert (await client.get("/api/v1/me", headers=other)).status_code == 200


def test_sliding_window_forgets_old_events_and_bounds_memory():
    now = [0.0]
    window = SlidingWindow(limit=3, window_s=10, clock=lambda: now[0], max_events=5)
    for _ in range(3):
        window.hit("k")
    assert window.blocked("k")
    assert window.retry_after("k") == 11
    for _ in range(100):  # an attacker hammering one key
        window.hit("k")
    assert len(window._events["k"]) == 5  # never more than max_events kept
    now[0] = 10.5
    assert not window.blocked("k")
    assert "k" not in window._events  # quiet keys are forgotten
    assert window.retry_after("k") == 0
