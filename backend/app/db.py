"""Database connection: one engine for the app, one session per request."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine, _sessions
    if _engine is None:
        _engine = create_async_engine(
            get_settings().db_url(),
            pool_size=10,
            pool_pre_ping=True,
            hide_parameters=True,  # error messages never contain user data (NFR-15)
        )
        _sessions = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessions() -> async_sessionmaker[AsyncSession]:
    engine()
    assert _sessions is not None
    return _sessions


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session that commits if the request succeeds.

    Use it with scope="function": the commit then happens before the response
    is sent, so a client that gets "201 Created" can use the new row at once.
    (With FastAPI's default scope the commit ran after the response, and a
    quick next request could get 404.)"""
    async with sessions()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def dispose() -> None:
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
    _engine = _sessions = None
