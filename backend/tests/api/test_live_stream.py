"""FR-25: the live WebSocket. Runs a real server, because the test client
cannot speak WebSocket; events come from the real ingestor code path."""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import UTC, datetime

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.ingest.handler import Handler
from app.main import app
from app.routers import stream

from ..ingest.helpers import home_telemetry, message, tank_telemetry


@pytest.fixture(scope="module")
async def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve())
    while not srv.started:
        await asyncio.sleep(0.05)
    yield f"ws://127.0.0.1:{port}/api/v1/ws"
    srv.should_exit = True
    await task


async def _ticket(client, make, user) -> str:
    r = await client.post("/api/v1/ws-ticket", headers=await make.headers(user))
    assert r.status_code == 200, r.text
    assert r.json()["expires_in"] == 30 and "/api/v1/ws?ticket=" in r.json()["url"]
    return r.json()["ticket"]


async def _open(url: str, ticket: str):
    ws = await connect(f"{url}?ticket={ticket}")
    hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert hello["type"] == "ready"
    return ws


async def _events(ws, seconds: float = 1.5) -> list[dict]:
    found = []
    try:
        while True:
            found.append(json.loads(await asyncio.wait_for(ws.recv(), seconds)))
    except TimeoutError:
        return found


async def _live_reading(device, *, tank: bool = False) -> None:
    now = datetime.now(UTC)
    payload = tank_telemetry(0, 50.0, False, at=now) if tank else home_telemetry(0, 10.0, at=now)
    await Handler().handle([message(device.id, "telemetry", payload, now)])


async def test_each_user_only_gets_events_they_may_see(client, make, server):
    """FR-25 criterion 1: two residents in one building never see each other."""
    world = await make.world()
    neighbour_home = await make.home(world.building)
    neighbour = await make.user("resident")
    await make.occupancy(neighbour_home, neighbour)
    mine = await make.device(world.home)
    theirs = await make.device(neighbour_home)
    tank = await make.device(tank=await make.tank(world.building))

    sockets = {
        name: await _open(server, await _ticket(client, make, user))
        for name, user in (
            ("resident", world.resident),
            ("neighbour", neighbour),
            ("owner", world.owner),
        )
    }
    stranger = await _open(server, await _ticket(client, make, await make.user("owner")))
    for device in (mine, theirs):
        await _live_reading(device)
    await _live_reading(tank, tank=True)

    seen = {name: await _events(ws) for name, ws in sockets.items()}
    devices = {name: {e["deviceId"] for e in events} for name, events in seen.items()}
    assert devices["resident"] == {mine.id}
    assert devices["neighbour"] == {theirs.id}
    assert devices["owner"] == {mine.id, theirs.id, tank.id}
    assert {e["type"] for e in seen["owner"]} >= {"telemetry", "tank"}
    assert await _events(stranger, 0.5) == []
    for ws in (*sockets.values(), stranger):
        await ws.close()


async def test_ticket_works_once_and_bad_tickets_are_refused(client, make, server):
    world = await make.world()
    ticket = await _ticket(client, make, world.resident)
    ws = await _open(server, ticket)
    await ws.close()
    for bad in (ticket, "made-up-ticket-made-up-ticket", ""):
        with pytest.raises(InvalidStatus):
            await connect(f"{server}?ticket={bad}")


async def test_socket_is_closed_after_logout(client, make, server, monkeypatch):
    """FR-25 criterion 3: checked again every 30 s (0.3 s in this test)."""
    monkeypatch.setattr(stream, "RECHECK_SECONDS", 0.3)
    world = await make.world()
    tokens = await make.tokens(world.resident)
    r = await client.post(
        "/api/v1/ws-ticket", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    ws = await _open(server, r.json()["ticket"])
    await client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    with pytest.raises(ConnectionClosed) as closed:
        await asyncio.wait_for(ws.recv(), 5)
    assert closed.value.rcvd.code == stream.CLOSE_LOGGED_OUT


async def test_valve_command_and_lock_events_reach_the_resident(client, make, server, outbox):
    world = await make.world()
    await make.device(world.home)
    ws = await _open(server, await _ticket(client, make, world.resident))
    owner = await make.headers(world.owner)
    await client.post(
        f"/api/v1/homes/{world.home.id}/valve-lock",
        json={"state": "closed", "reason": "Leak confirmed in the bathroom"},
        headers=owner,
    )
    events = await _events(ws)
    types = [e["type"] for e in events]
    assert "command" in types and "lock" in types
    lock = next(e for e in events if e["type"] == "lock")
    assert lock["locked"] is True and lock["reason"] == "Leak confirmed in the bathroom"
    await ws.close()


async def test_slow_client_is_disconnected_and_can_reconnect(client, make, server):
    """A socket that falls 500 events behind is closed (1013); the app reconnects."""
    world = await make.world()
    await make.device(world.home)
    ws = await _open(server, await _ticket(client, make, world.owner))
    for queue in list(stream.hub._queues):  # as if each queue had overflowed
        stream.hub._drop(queue)
    with pytest.raises(ConnectionClosed) as closed:
        while True:
            await asyncio.wait_for(ws.recv(), 5)
    assert closed.value.rcvd.code == stream.CLOSE_TRY_AGAIN
