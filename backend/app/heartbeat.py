"""Heartbeats: background services say "I am alive" in the database (FR-55).

The API cannot look inside the ingestor or worker containers, but all of them
share the database. Each service upserts its row in `service_heartbeats`
every few seconds; the System Admin health page shows a service as down when
its row is older than its limit (GET /admin/health).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from . import db
from .models import ServiceHeartbeat

STARTED = datetime.now(UTC)  # when this process started


async def beat(service: str, details: dict[str, Any] | None = None) -> None:
    """Write (or refresh) this service's row."""
    now = datetime.now(UTC)
    values = {"seen_at": now, "started_at": STARTED, "details": details or {}}
    statement = insert(ServiceHeartbeat).values(service=service, **values)
    async with db.sessions()() as session, session.begin():
        await session.execute(
            statement.on_conflict_do_update(index_elements=["service"], set_=values)
        )
