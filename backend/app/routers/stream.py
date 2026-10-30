"""Live events over a WebSocket (FR-25).

1. The app asks POST /ws-ticket (with its normal access token) for a
   one-time ticket that is valid for 30 seconds.
2. It opens  ws://.../api/v1/ws?ticket=...  Browsers cannot put an
   Authorization header on a WebSocket, and a long-lived token in a URL
   would end up in logs; a 30-second, single-use ticket is safe there.
3. The server forwards every event the user may see: a resident only
   their own home, an owner only their own buildings (FR-19).

Every 30 seconds the server checks the login session again, and closes the
socket (code 4401) when the user logged out or was disabled. When the
socket closes for any reason, the app reconnects and reloads the current
state with the normal GET endpoints, so nothing is missed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .. import db
from .. import events as bus
from ..config import Settings, get_settings
from ..deps import Caller, active_occupancy_filter, require, session_alive
from ..models import Building, Occupancy, User
from ..schemas import WsTicketOut
from ..security.tokens import hash_opaque_token, new_opaque_token

log = logging.getLogger("aquapulse.ws")
router = APIRouter(tags=["live stream"])
AnyRole = Annotated[Caller, Depends(require("sysadmin", "owner", "resident"))]

TICKET_SECONDS = 30
RECHECK_SECONDS = 30
QUEUE_SIZE = 500  # a client that falls this far behind is disconnected (it reconnects)
CLOSE_LOGGED_OUT = 4401  # our own code: "log in again"
CLOSE_TRY_AGAIN = 1013  # standard code: "try again later"


# ----- tickets -----


@dataclass(frozen=True)
class _Ticket:
    user_id: uuid.UUID
    session_id: uuid.UUID
    expires: float


_tickets: dict[bytes, _Ticket] = {}  # hash of the ticket -> who it belongs to


@router.post("/ws-ticket", response_model=WsTicketOut)
async def ws_ticket(caller: AnyRole, settings: Annotated[Settings, Depends(get_settings)]):
    """A one-time ticket to open the live WebSocket, valid for 30 seconds."""
    now = time.monotonic()
    for key in [k for k, t in _tickets.items() if t.expires < now]:
        del _tickets[key]  # forget old ones so the dict stays small
    ticket, ticket_hash = new_opaque_token()
    _tickets[ticket_hash] = _Ticket(caller.id, caller.session_id, now + TICKET_SECONDS)
    base = settings.public_url.replace("https://", "wss://").replace("http://", "ws://")
    return WsTicketOut(
        ticket=ticket, expires_in=TICKET_SECONDS, url=f"{base}/api/v1/ws?ticket={ticket}"
    )


def _use_ticket(ticket: str) -> _Ticket | None:
    entry = _tickets.pop(hash_opaque_token(ticket), None)  # pop: a ticket works only once
    if entry is None or entry.expires < time.monotonic():
        return None
    return entry


# ----- who may see what -----


@dataclass
class _Scope:
    role: str
    buildings: set[str] = field(default_factory=set)
    homes: set[str] = field(default_factory=set)

    def allows(self, event: dict[str, Any]) -> bool:
        if self.role == "sysadmin":
            return True
        if self.role == "owner":
            return event.get("buildingId") in self.buildings
        # Residents: only events about their own home (never tank or building events).
        return event.get("homeId") is not None and event["homeId"] in self.homes


async def _load_scope(user: User) -> _Scope:
    scope = _Scope(user.role)
    async with db.sessions()() as session:
        if user.role == "owner":
            ids = await session.scalars(select(Building.id).where(Building.owner_id == user.id))
            scope.buildings = {str(i) for i in ids}
        elif user.role == "resident":
            ids = await session.scalars(
                select(Occupancy.home_id).where(*active_occupancy_filter(user.id, date.today()))
            )
            scope.homes = {str(i) for i in ids}
    return scope


async def _still_allowed(user_id: uuid.UUID, session_id: uuid.UUID) -> User | None:
    async with db.sessions()() as session:
        user = await session.get(User, user_id)
        if user is None or not await session_alive(session, user, session_id):
            return None
        return user


# ----- one shared database listener for all sockets -----


class EventHub:
    """Listens once to PostgreSQL and hands every event to every open socket."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict | None]] = set()
        self._task: asyncio.Task | None = None
        self.ready = asyncio.Event()

    def subscribe(self) -> asyncio.Queue[dict | None]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        queue: asyncio.Queue[dict | None] = asyncio.Queue(QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    def _deliver(self, payload: str) -> None:
        try:
            event = json.loads(payload)
        except ValueError:
            return
        for queue in list(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._drop(queue)

    def _drop(self, queue: asyncio.Queue) -> None:
        """Tell one socket to close (None), so the app reconnects and reloads."""
        self._queues.discard(queue)
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()  # make room for the None
        queue.put_nowait(None)

    async def _run(self) -> None:
        pause = 1.0
        while True:
            try:
                async with bus.listening(bus.EVENTS, self._deliver) as conn:
                    self.ready.set()
                    pause = 1.0
                    while True:  # a dead connection would never say so: check it
                        await asyncio.sleep(RECHECK_SECONDS)
                        await conn.execute("SELECT 1")
            except asyncio.CancelledError:
                raise
            except Exception as err:
                log.warning("event listener lost (%s), reconnecting", type(err).__name__)
            self.ready.clear()
            # Events may have been missed: every socket reconnects and reloads.
            for queue in list(self._queues):
                self._drop(queue)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 30)


hub = EventHub()


@router.websocket("/ws")
async def live_events(websocket: WebSocket, ticket: Annotated[str, Query(max_length=200)] = ""):
    """Live events: telemetry, valve and pump changes, command results, locks."""
    entry = _use_ticket(ticket)
    user = entry and await _still_allowed(entry.user_id, entry.session_id)
    if entry is None or user is None:
        await websocket.close(code=CLOSE_LOGGED_OUT)  # before accept: the client sees 403
        return
    await websocket.accept()
    scope = await _load_scope(user)
    queue = hub.subscribe()
    try:
        await asyncio.wait_for(hub.ready.wait(), 10)
        await websocket.send_json({"type": "ready", "role": scope.role})
        receiver = asyncio.create_task(_drain(websocket))
        sender = asyncio.create_task(_forward(websocket, queue, entry, scope))
        done, pending = await asyncio.wait({receiver, sender}, return_when="FIRST_COMPLETED")
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                task.result()
    except (WebSocketDisconnect, TimeoutError):
        pass
    finally:
        hub.unsubscribe(queue)


async def _drain(websocket: WebSocket) -> None:
    """Read (and ignore) what the client sends; returns when it disconnects."""
    while True:
        await websocket.receive_text()


async def _forward(websocket: WebSocket, queue: asyncio.Queue, entry: _Ticket, scope: _Scope):
    next_check = time.monotonic() + RECHECK_SECONDS
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), max(0.0, next_check - time.monotonic()))
        except TimeoutError:
            event = {}
        if event is None:
            await websocket.close(code=CLOSE_TRY_AGAIN)
            return
        if time.monotonic() >= next_check:
            user = await _still_allowed(entry.user_id, entry.session_id)
            if user is None:
                await websocket.close(code=CLOSE_LOGGED_OUT)
                return
            scope = await _load_scope(user)  # e.g. a resident who moved out
            next_check = time.monotonic() + RECHECK_SECONDS
        if event and scope.allows(event):
            await websocket.send_json(event)
