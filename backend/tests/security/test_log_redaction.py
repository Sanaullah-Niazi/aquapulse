"""NFR-15: no passwords, tokens or email addresses in application logs.

Runs the most sensitive flows with every logger switched to DEBUG and scans
everything that was logged.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import db
from app.emailer import send_email

from ..conftest import PASSWORD, token_from_email


async def test_sensitive_flows_log_no_secrets(client, make, outbox, caplog):
    caplog.set_level(logging.DEBUG)
    world = await make.world()
    tokens = await make.tokens(world.owner)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    wrong = "wrong-pass-123"
    await client.post("/api/v1/auth/login", json={"email": world.owner.email, "password": wrong})
    await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    await client.post("/api/v1/auth/password/forgot", json={"email": world.resident.email})
    reset_token = token_from_email(outbox[-1][2])
    invite_email = f"logscan-{world.home.id.hex[:6]}@example.com"
    await client.post(
        f"/api/v1/homes/{world.home.id}/invitations",
        headers=headers,
        json={"email": invite_email, "full_name": "Log Scan", "move_in": str(date.today())},
    )
    invite_token = token_from_email(outbox[-1][2])

    secrets = [
        PASSWORD,
        wrong,
        tokens["access_token"],
        tokens["refresh_token"],
        reset_token,
        invite_token,
        world.owner.email,
        world.resident.email,
        invite_email,
    ]
    logged = caplog.text
    assert logged  # we really captured something
    for secret in secrets:
        assert secret not in logged


async def test_failed_email_logs_only_the_error_type(caplog, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "smtp_port", 1)  # nothing listens: sending fails
    caplog.set_level(logging.DEBUG)
    assert await send_email("private.person@example.com", "Code", "secret-code-123") is False
    assert "could not be sent" in caplog.text
    assert "private.person" not in caplog.text
    assert "secret-code-123" not in caplog.text


async def test_database_errors_do_not_echo_the_query_values():
    async with db.engine().connect() as conn:
        with pytest.raises(DBAPIError) as err:
            # Fails on the duplicate code; without hide_parameters the error
            # text would list every value we sent, including the secret.
            await conn.execute(
                text("INSERT INTO currencies VALUES ('PKR', :name, 'x', 2)"),
                {"name": "secret-value-42"},
            )
    assert "secret-value-42" not in str(err.value)


async def test_crash_is_logged_without_its_message(client, make, monkeypatch, caplog):
    """Database errors can quote row data ("Key (email)=(...) exists"), so an
    unexpected crash is logged with its type and code lines, not its message."""
    from app.routers import me

    leaked = "leaky.person" + "@example.com"  # built at run time, so not in the code line

    async def crash(session, caller):
        raise RuntimeError(f"Key (email)=({leaked}) already exists")

    headers = await make.headers(await make.user("owner"))
    monkeypatch.setattr(me, "_me", crash)
    caplog.set_level(logging.DEBUG)
    r = await client.get("/api/v1/me", headers=headers)
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "RuntimeError" in caplog.text
    assert "leaky.person" not in caplog.text
