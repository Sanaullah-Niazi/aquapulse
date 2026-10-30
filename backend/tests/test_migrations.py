"""Database migrations: they match the models, and they undo cleanly."""

from __future__ import annotations

import asyncio

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import migrate
from app.config import get_settings
from app.models import Base

from .conftest import TEST_DB

SCRATCH_DB = "aquapulse_migrations_test"


async def _sql(database: str, *statements: str) -> list:
    engine = create_async_engine(
        get_settings().db_url(owner=True, database=database), isolation_level="AUTOCOMMIT"
    )
    results = []
    async with engine.connect() as conn:
        for statement in statements:
            result = await conn.execute(text(statement))
            results.append(result.fetchall() if result.returns_rows else None)
    await engine.dispose()
    return results


def _tables(database: str) -> set[str]:
    rows = asyncio.run(
        _sql(database, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )[0]
    return {r[0] for r in rows}


def test_models_and_migrations_agree():
    """If someone changes app/models.py but forgets a migration, this fails."""

    async def diff():
        engine = create_async_engine(get_settings().db_url(owner=True, database=TEST_DB))
        async with engine.connect() as conn:
            changes = await conn.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
            )
        await engine.dispose()
        return changes

    assert asyncio.run(diff()) == []


def test_migrations_upgrade_downgrade_and_upgrade_again():
    asyncio.run(
        _sql(
            "postgres",
            f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)',
            f'CREATE DATABASE "{SCRATCH_DB}"',
        )
    )
    try:
        migrate.run(SCRATCH_DB)
        assert {"users", "buildings", "homes", "occupancies", "audit_log", "currencies"} <= _tables(
            SCRATCH_DB
        )
        currencies = asyncio.run(_sql(SCRATCH_DB, "SELECT code, minor_units FROM currencies"))[0]
        assert dict(currencies) == {
            "PKR": 2, "USD": 2, "GBP": 2, "EUR": 2, "AUD": 2, "AED": 2,
            "SAR": 2, "QAR": 2, "KWD": 3, "BHD": 3, "OMR": 3,
        }  # fmt: skip
        migrate.run(SCRATCH_DB, downgrade_to="base")
        assert _tables(SCRATCH_DB) == {"alembic_version"}
        migrate.run(SCRATCH_DB)  # and it can be run again (idempotent)
        migrate.run(SCRATCH_DB)
        assert "audit_log" in _tables(SCRATCH_DB)
    finally:
        asyncio.run(_sql("postgres", f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
