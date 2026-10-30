"""Alembic runs this file to connect to the database and apply migrations.

It connects as the database *owner* (POSTGRES_USER), because creating
tables needs more rights than the API's limited role has.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base

config = context.config
target_metadata = Base.metadata  # used by `alembic revision --autogenerate`


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    # A test or app.migrate can hand over a URL (for example the test database).
    url = config.attributes.get("url") or get_settings().db_url(owner=True)
    engine = create_async_engine(url, hide_parameters=True)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not used in AquaPulse.")
asyncio.run(_run_async())
