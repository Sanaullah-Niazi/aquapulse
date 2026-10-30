"""Shared test setup.

The tests run the real API against a real PostgreSQL database (no mocks for
the database), so they prove what users will actually get. They use their
own database, `aquapulse_test`, which is dropped and created again at the
start of every run, and their own app role, so the running stack is never
touched.

Needs: the database container running (`make up`), or any PostgreSQL 16.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

TEST_DB = "aquapulse_test"


def _load_dotenv() -> None:
    """Use the repo's .env for the database password, like `make` does."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip())


# Settings are read from the environment, so set them before importing the app.
_load_dotenv()
os.environ["POSTGRES_DB"] = TEST_DB
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("MQTT_HOST", "localhost")
os.environ.setdefault("SMTP_HOST", "localhost")
os.environ["DB_APP_USER"] = "aquapulse_app_test"
os.environ["APP_DB_PASSWORD"] = "test-app-" + uuid.uuid4().hex
os.environ["JWT_SECRET"] = "test-jwt-" + uuid.uuid4().hex
os.environ["JWT_PREVIOUS_SECRETS"] = ""

from httpx2 import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app import db, migrate  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.deps import api_limiter  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Building, Home, Occupancy, User  # noqa: E402
from app.routers import auth  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402

PASSWORD = "correct-horse-battery-staple"
_PASSWORD_HASH = hash_password(PASSWORD)  # hash once: Argon2id is slow on purpose


async def _recreate_test_database() -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.db_url(owner=True, database="postgres"), isolation_level="AUTOCOMMIT"
    )
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    await engine.dispose()


def pytest_sessionstart(session: pytest.Session) -> None:
    asyncio.run(_recreate_test_database())
    migrate.run(TEST_DB)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Every test starts with empty rate-limit counters."""
    for limiter in (api_limiter, auth.login_failures_by_account, auth.login_failures_by_ip):
        limiter.clear()


@pytest.fixture(scope="session")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.dispose()


@pytest.fixture
def outbox(monkeypatch):
    """Catch emails instead of sending them. Returns the list of (to, subject, body)."""
    sent: list[tuple[str, str, str]] = []

    async def fake_send(to: str, subject: str, body: str) -> bool:
        sent.append((to, subject, body))
        return True

    from app.routers import residents

    monkeypatch.setattr(auth, "send_email", fake_send)
    monkeypatch.setattr(residents, "send_email", fake_send)
    return sent


def token_from_email(body: str) -> str:
    """The emails put the code on its own indented line."""
    return next(line.strip() for line in body.splitlines() if line.startswith("    "))


class Factory:
    """Creates test data straight in the database. Every email is unique, so
    tests never clash even though they share one database."""

    def __init__(self, client: AsyncClient) -> None:
        self.client = client

    async def _add(self, *objects):
        async with db.sessions()() as session, session.begin():
            session.add_all(objects)
        return objects[0] if len(objects) == 1 else objects

    async def user(self, role: str = "owner", active: bool = True) -> User:
        email = f"{role}-{uuid.uuid4().hex[:10]}@example.com"
        return await self._add(
            User(
                email=email,
                full_name=f"Test {role}",
                role=role,
                password_hash=_PASSWORD_HASH,
                is_active=active,
            )
        )

    async def building(self, owner: User, **values) -> Building:
        values = {"name": "Test Tower", "timezone": "Asia/Karachi", "currency_code": "PKR"} | values
        return await self._add(Building(owner_id=owner.id, **values))

    async def home(self, building: Building, unit_no: str | None = None) -> Home:
        return await self._add(
            Home(building_id=building.id, unit_no=unit_no or uuid.uuid4().hex[:8])
        )

    async def occupancy(
        self,
        home: Home,
        user: User,
        move_in: date | None = None,
        move_out: date | None = None,
        billing: bool = False,
    ) -> Occupancy:
        return await self._add(
            Occupancy(
                home_id=home.id,
                user_id=user.id,
                move_in=move_in or date.today() - timedelta(days=30),
                move_out=move_out,
                is_billing_contact=billing,
            )
        )

    async def tokens(self, user: User) -> dict:
        r = await self.client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def headers(self, user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {(await self.tokens(user))['access_token']}"}

    async def world(self) -> World:
        """A small, typical setup: an owner with one building, a home and its resident."""
        owner = await self.user("owner")
        building = await self.building(owner)
        home = await self.home(building)
        resident = await self.user("resident")
        await self.occupancy(home, resident, billing=True)
        return World(owner, building, home, resident)


class World:
    def __init__(self, owner: User, building: Building, home: Home, resident: User) -> None:
        self.owner, self.building, self.home, self.resident = owner, building, home, resident


@pytest.fixture
def make(client) -> Factory:
    return Factory(client)


async def audit_actions(**where) -> list[str]:
    """Audit actions matching simple equality filters, oldest first."""
    from sqlalchemy import select

    from app.models import AuditEntry

    async with db.sessions()() as session:
        query = select(AuditEntry.action).order_by(AuditEntry.id)
        for field, value in where.items():
            query = query.where(getattr(AuditEntry, field) == value)
        return list(await session.scalars(query))
