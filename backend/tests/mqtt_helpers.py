"""Connecting to the real broker in tests (M5: TLS + a password for everyone).

Tests that play a device log in as `home-test-01`, an account that exists only
for tests: broker-auth makes it from SIM_DEVICE_SECRET, the ACL lets it use
only its own topics, and the running stack's ingestor ignores it (it is not in
the main database). The tests' own database gets a device with that ID.
"""

from __future__ import annotations

import ssl
import uuid

import aiomqtt
from sqlalchemy import text

from app import db
from app.config import get_settings
from app.models import Device, Home
from app.security.mqtt_passwords import simulator_password

TEST_DEVICE = "home-test-01"


def tls() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=get_settings().mqtt_ca_file)


def broker(username: str | None, password: str | None = None, **options) -> aiomqtt.Client:
    """A TLS connection to the broker. A server account's password comes from .env."""
    settings = get_settings()
    if password is None and username in ("ingestor", "commander", "monitor"):
        password = getattr(settings, f"mqtt_{username}_password").get_secret_value()
    return aiomqtt.Client(
        settings.mqtt_host,
        settings.mqtt_port,
        identifier=f"test-{uuid.uuid4().hex[:8]}",  # never kick the stack's own clients
        username=username,
        password=password,
        tls_context=tls(),
        timeout=5,
        **options,
    )


def test_device_password() -> str:
    return simulator_password(get_settings().sim_device_secret.get_secret_value(), TEST_DEVICE)


def as_test_device(**options) -> aiomqtt.Client:
    return broker(TEST_DEVICE, test_device_password(), **options)


async def fresh_test_device(make, home: Home) -> Device:
    """Register home-test-01 in the tests' database (removing an older one first)."""
    async with db.sessions()() as session, session.begin():
        for table in ("device_commands", "devices"):
            column = "id" if table == "devices" else "device_id"
            await session.execute(
                text(f"DELETE FROM {table} WHERE {column} = :id"),  # noqa: S608 (fixed names)
                {"id": TEST_DEVICE},
            )
    return await make.device(home, device_id=TEST_DEVICE)
