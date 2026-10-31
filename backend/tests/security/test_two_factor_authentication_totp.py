"""FR-70: two-factor login with any authenticator app. A valid 6-digit code
(one 30-second step early or late is fine) is needed; each code and each of
the 10 recovery codes works only once."""

from __future__ import annotations

import time

import pyotp
import pytest
from sqlalchemy import select

from app import db
from app.models import User

from ..conftest import PASSWORD, audit_actions

LOGIN = "/api/v1/auth/login"


@pytest.fixture
def wait(monkeypatch):
    """Move the clock forward, so the next 30-second code exists (on both sides)."""
    real, offset = time.time, [0.0]
    monkeypatch.setattr(time, "time", lambda: real() + offset[0])

    def later(seconds: float = 60) -> None:
        offset[0] += seconds

    return later


async def _turn_on(client, make, user) -> tuple[str, list[str]]:
    headers = await make.headers(user)
    r = await client.post("/api/v1/me/2fa/setup", json={"password": PASSWORD}, headers=headers)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_url"].startswith("otpauth://totp/AquaPulse:")
    assert r.json()["qr_svg"].startswith("<svg")
    r = await client.post("/api/v1/me/2fa/enable", json={"code": pyotp.TOTP(secret).now()},
                          headers=headers)  # fmt: skip
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _code(secret: str, steps: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + 30 * steps)


async def test_login_needs_the_code(client, make, wait):
    owner = await make.user("owner")
    secret, codes = await _turn_on(client, make, owner)
    assert len(codes) == 10 and len(set(codes)) == 10

    # The right password alone is not enough any more; the app is told to ask.
    r = await client.post(LOGIN, json={"email": owner.email, "password": PASSWORD})
    assert r.status_code == 401 and r.headers["X-Two-Factor"] == "required"
    assert r.json()["detail"] == "Two-factor code required"
    r = await client.post(LOGIN, json={"email": owner.email, "password": PASSWORD,
                                       "code": "000000"})  # fmt: skip
    assert r.status_code == 401 and r.json()["detail"] == "Wrong two-factor code"
    # A wrong password never reveals that 2FA is on.
    r = await client.post(LOGIN, json={"email": owner.email, "password": "wrong password!!"})
    assert "X-Two-Factor" not in r.headers

    # One step later (a phone clock a bit fast) works; the same code twice does not.
    code = _code(secret, +1)
    r = await client.post(LOGIN, json={"email": owner.email, "password": PASSWORD, "code": code})
    assert r.status_code == 200 and "access_token" in r.json()
    r = await client.post(LOGIN, json={"email": owner.email, "password": PASSWORD, "code": code})
    assert r.status_code == 401

    wait()
    bearer = await _bearer(client, owner, secret)
    me = await client.get("/api/v1/me", headers={"Authorization": bearer})
    assert me.json()["two_factor"] is True
    assert "auth.2fa_enabled" in await audit_actions(actor_id=owner.id)


async def _bearer(client, user, secret) -> str:
    """Log in with the current code (call wait() first if a code was just used)."""
    r = await client.post(LOGIN, json={"email": user.email, "password": PASSWORD,
                                       "code": _code(secret)})  # fmt: skip
    assert r.status_code == 200, r.text
    return "Bearer " + r.json()["access_token"]


async def test_a_recovery_code_works_once(client, make):
    owner = await make.user("owner")
    _, codes = await _turn_on(client, make, owner)
    body = {"email": owner.email, "password": PASSWORD, "code": codes[3].upper() + " "}
    assert (await client.post(LOGIN, json=body)).status_code == 200  # typed sloppily: still OK
    assert (await client.post(LOGIN, json=body)).status_code == 401  # used
    assert "auth.recovery_code_used" in await audit_actions(actor_id=owner.id)


async def test_switching_off_needs_password_and_code(client, make, wait):
    resident = await make.user("resident")
    secret, _ = await _turn_on(client, make, resident)
    wait()
    headers = {"Authorization": await _bearer(client, resident, secret)}
    wait()
    r = await client.post("/api/v1/me/2fa/disable",
                          json={"password": "wrong password!!", "code": _code(secret, 1)},
                          headers=headers)  # fmt: skip
    assert r.status_code == 400
    r = await client.post("/api/v1/me/2fa/disable",
                          json={"password": PASSWORD, "code": _code(secret, 1)},
                          headers=headers)  # fmt: skip
    assert r.status_code == 200, r.text
    r = await client.post(LOGIN, json={"email": resident.email, "password": PASSWORD})
    assert r.status_code == 200  # back to password only


async def test_new_recovery_codes_replace_the_old_ones(client, make, wait):
    owner = await make.user("owner")
    secret, old = await _turn_on(client, make, owner)
    wait()
    headers = {"Authorization": await _bearer(client, owner, secret)}
    wait()
    r = await client.post("/api/v1/me/2fa/recovery-codes",
                          json={"password": PASSWORD, "code": _code(secret, 1)},
                          headers=headers)  # fmt: skip
    assert r.status_code == 200, r.text
    new = r.json()["recovery_codes"]
    assert (await client.post(LOGIN, json={"email": owner.email, "password": PASSWORD,
                                           "code": old[0]})).status_code == 401  # fmt: skip
    assert (await client.post(LOGIN, json={"email": owner.email, "password": PASSWORD,
                                           "code": new[0]})).status_code == 200  # fmt: skip


async def test_setup_rules(client, make):
    owner = await make.user("owner")
    headers = await make.headers(owner)
    r = await client.post("/api/v1/me/2fa/setup", json={"password": "wrong password!!"},
                          headers=headers)  # fmt: skip
    assert r.status_code == 400
    r = await client.post("/api/v1/me/2fa/enable", json={"code": "123456"}, headers=headers)
    assert r.status_code == 400  # no setup yet
    r = await client.post("/api/v1/me/2fa/setup", json={"password": PASSWORD}, headers=headers)
    r = await client.post("/api/v1/me/2fa/enable", json={"code": "12345"}, headers=headers)
    assert r.status_code == 422
    r = await client.post("/api/v1/me/2fa/disable", json={"password": PASSWORD, "code": "123456"},
                          headers=headers)  # fmt: skip
    assert r.status_code == 409  # not on
    # The secret is not stored readable.
    async with db.sessions()() as session:
        stored = await session.scalar(select(User.totp_secret).where(User.id == owner.id))
    assert stored and len(stored) > 40 and stored.upper() != stored


async def test_swagger_authorize_takes_the_code_as_client_secret(client, make):
    owner = await make.user("owner")
    secret, _ = await _turn_on(client, make, owner)
    form = {"username": owner.email, "password": PASSWORD, "client_secret": _code(secret, 1)}
    r = await client.post("/api/v1/auth/token", data=form)
    assert r.status_code == 200


async def test_system_admins_must_turn_it_on(client, make, wait):
    """FR-70: "System Admins must". Until they do, only their own account works."""
    admin = await make.user("sysadmin", two_factor=False)
    headers = await make.headers(admin)  # the password alone still logs in...
    r = await client.get("/api/v1/admin/owners", headers=headers)
    assert r.status_code == 403  # ...but nothing else works yet
    assert r.headers["X-Two-Factor"] == "setup-required"
    assert (await client.get("/api/v1/buildings", headers=headers)).status_code == 403
    me = await client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200 and me.json()["two_factor"] is False

    secret, _ = await _turn_on(client, make, admin)  # the same session goes on working
    assert (await client.get("/api/v1/admin/owners", headers=headers)).status_code == 200

    # And it stays on: a System Admin cannot switch it off.
    wait()
    r = await client.post("/api/v1/me/2fa/disable", headers=headers,
                          json={"password": PASSWORD, "code": _code(secret)})  # fmt: skip
    assert r.status_code == 409
    assert "must keep" in r.json()["detail"]


async def test_admins_are_made_and_reset_from_the_command_line(client, make, wait):
    """`make admin` and `make admin-reset-2fa` (app/admins.py): the way to make a
    System Admin, and the way back in for one who lost their phone."""
    from app import admins

    email = f"cli-admin-{time.time_ns()}@example.com"
    assert "created" in await admins.add(email, "System Admin", PASSWORD)
    assert "already has an account" in await admins.add(email, "System Admin", PASSWORD)
    async with db.sessions()() as session:
        admin = await session.scalar(select(User).where(User.email == email))
    assert admin.role == "sysadmin" and admin.totp_enabled_at is None

    await _turn_on(client, make, admin)
    login = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login.status_code == 401  # a code is needed now

    assert "reset" in await admins.reset_two_factor(email)
    login = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200  # the password alone logs in again, to set it up
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/admin/owners", headers=headers)).status_code == 403
    assert "No System Admin" in await admins.reset_two_factor("nobody@example.com")
    actions = await audit_actions(target_id=str(admin.id))
    assert actions[0] == "admin.sysadmin_created" and "admin.2fa_reset" in actions
