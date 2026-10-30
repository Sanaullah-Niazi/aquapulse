"""Health checks (FR-31). Used by Docker, Caddy and later by monitoring."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from ..config import get_settings
from ..db import engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Alive: the API process answers."""
    return {"status": "ok"}


async def _database_ok() -> bool:
    try:
        async with asyncio.timeout(2):
            async with engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # any failure means "not ready"
        return False


async def _broker_ok() -> bool:
    settings = get_settings()
    try:
        async with asyncio.timeout(2):
            _, writer = await asyncio.open_connection(settings.mqtt_host, settings.mqtt_port)
            writer.close()
            await writer.wait_closed()
        return True
    except (OSError, TimeoutError):
        return False


@router.get("/readyz", responses={503: {}})
async def readyz(response: Response) -> dict[str, bool]:
    """Ready: the database and the MQTT broker can be reached. 503 if not."""
    db_ok, mqtt_ok = await asyncio.gather(_database_ok(), _broker_ok())
    if not (db_ok and mqtt_ok):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"database": db_ok, "broker": mqtt_ok}
