"""Bring the database up to date: `python -m app.migrate`.

The `migrate` service in docker-compose.yml runs this once before the API
starts. It is safe to run any number of times. Three steps:

1. Make sure the API's own database role (aquapulse_app) exists and has
   the password from .env (APP_DB_PASSWORD).
2. Apply any new Alembic migrations (migrations/versions/).
3. Give the API role only the rights it needs ("least privilege", FR-72):
   read and write the normal tables, but on audit_log only SELECT and
   INSERT, so a bug or an attacker in the API cannot rewrite history (FR-28).
   Bills and payments cannot be deleted either (FR-20, FR-40): bills are
   voided and payments reversed, and both stay on record.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from .config import Settings, get_settings

log = logging.getLogger("aquapulse.migrate")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


async def _sql(conn: AsyncConnection, template: str, *args: str) -> None:
    """Run DDL that needs names or a password in it.

    DDL statements cannot take normal query parameters, so Postgres' own
    format() builds the statement: %I quotes a name, %L quotes a value.
    That way a password with quotes in it cannot break (or inject) SQL.
    """
    params = {f"a{i}": arg for i, arg in enumerate(args)}
    placeholders = ", ".join(f"CAST(:a{i} AS text)" for i in range(len(args)))
    statement = await conn.scalar(text(f"SELECT format('{template}', {placeholders})"), params)
    await conn.execute(text(statement))


def _engine(settings: Settings, database: str):
    # hide_parameters: if a statement fails, the error message must not
    # contain the password we were setting.
    return create_async_engine(settings.db_url(owner=True, database=database), hide_parameters=True)


async def ensure_app_role(settings: Settings, database: str) -> None:
    engine = _engine(settings, database)
    user = settings.db_app_user
    password = settings.db_app_password.get_secret_value()
    async with engine.begin() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :u"), {"u": user})
        if not exists:
            await _sql(conn, "CREATE ROLE %I LOGIN PASSWORD %L", user, password)
            log.info("created database role %s", user)
        else:
            # Re-applied every time, so changing APP_DB_PASSWORD in .env just works.
            await _sql(conn, "ALTER ROLE %I LOGIN PASSWORD %L", user, password)
    await engine.dispose()


async def grant_rights(settings: Settings, database: str) -> None:
    engine = _engine(settings, database)
    user = settings.db_app_user
    async with engine.begin() as conn:
        await _sql(conn, "GRANT CONNECT ON DATABASE %I TO %I", database, user)
        await _sql(conn, "GRANT USAGE ON SCHEMA public TO %I", user)
        await _sql(
            conn, "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I", user
        )
        await _sql(conn, "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I", user)
        # The exceptions: history may only grow, and the schema version is not the API's business.
        await _sql(conn, "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM %I", user)
        await _sql(conn, "REVOKE DELETE, TRUNCATE ON bills, payments, alerts FROM %I", user)
        await _sql(conn, "REVOKE ALL ON alembic_version FROM %I", user)
    await engine.dispose()


def alembic_config(settings: Settings, database: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["url"] = settings.db_url(owner=True, database=database)
    return cfg


def run(database: str | None = None, downgrade_to: str | None = None) -> None:
    """Steps 1 to 3. Alembic runs its own event loop, so the steps run one after another."""
    settings = get_settings()
    database = database or settings.db_name
    asyncio.run(ensure_app_role(settings, database))
    cfg = alembic_config(settings, database)
    if downgrade_to:
        # Used by tests/test_migrations.py to prove every migration can be undone.
        command.downgrade(cfg, downgrade_to)
        return
    command.upgrade(cfg, "head")
    asyncio.run(grant_rights(settings, database))
    log.info("database %s is up to date", database)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Apply database migrations.")
    parser.add_argument("--database", help="database name (default: POSTGRES_DB)")
    parser.add_argument("--downgrade", metavar="REVISION", help="undo migrations down to REVISION")
    args = parser.parse_args()
    run(args.database, args.downgrade)


if __name__ == "__main__":
    main()
