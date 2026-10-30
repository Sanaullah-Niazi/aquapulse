"""Live events between services, using PostgreSQL LISTEN/NOTIFY (free, no extra server).

* The ingestor NOTIFYs `aquapulse_events` when it stores a reading, a device
  changes state or a command finishes. The API LISTENs and forwards each event
  to the WebSockets allowed to see it (FR-25).
* The API NOTIFYs `aquapulse_commands` when it queues a command, so the
  ingestor sends it at once instead of at its next 1-second check.
* The API NOTIFYs `aquapulse_devices` when a device is registered, gets a new
  password or is revoked, so broker-auth updates the broker's password list (M5).

A NOTIFY is only delivered when its transaction commits, so a listener never
hears about data that was rolled back.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings

log = logging.getLogger("aquapulse.events")

EVENTS = "aquapulse_events"
COMMANDS = "aquapulse_commands"
DEVICES = "aquapulse_devices"


async def notify(session: AsyncSession, channel: str, payload: dict[str, Any] | str) -> None:
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": body}
    )


@asynccontextmanager
async def listening(
    channel: str, callback: Callable[[str], None]
) -> AsyncIterator[asyncpg.Connection]:
    """Call `callback(payload)` for every NOTIFY on `channel` while inside the block.
    Yields the connection, so a long-running listener can check it is still alive."""
    settings = get_settings()
    conn = await asyncpg.connect(
        user=settings.db_app_user,
        password=settings.db_app_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )

    def on_notify(_conn, _pid, _channel, payload: str) -> None:
        try:
            callback(payload)
        except Exception:  # one bad listener must not stop the others
            log.exception("event listener failed")

    try:
        await conn.add_listener(channel, on_notify)
        yield conn
    finally:
        await asyncio.shield(conn.close())
