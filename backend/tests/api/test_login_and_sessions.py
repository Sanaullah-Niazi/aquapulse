"""FR-18: login, refresh, logout, invitations (TC-18)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import update

from app import db
from app.config import get_settings
from app.models import Invitation, RefreshToken
from app.security.tokens import create_access_token, decode_access_token

from ..conftest import PASSWORD, audit_actions, token_from_email

LOGIN = "/api/v1/auth/login"


async def test_correct_password_returns_both_tokens(client, make):
    user = await make.user("owner")
    r = await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["access_token"] and body["refresh_token"]


async def test_email_is_not_case_sensitive(client, make):
    user = await make.user("owner")
    r = await client.post(LOGIN, json={"email": user.email.upper(), "password": PASSWORD})
    assert r.status_code == 200


async def test_wrong_password_and_unknown_email_get_the_same_answer(client, make):
    user = await make.user("owner")
    wrong_pw = await client.post(LOGIN, json={"email": user.email, "password": "nope-nope-nope"})
    no_user = await client.post(LOGIN, json={"email": "ghost@example.com", "password": PASSWORD})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json() == {"detail": "Wrong email or password"}


async def test_disabled_account_cannot_log_in(client, make):
    user = await make.user("owner", active=False)
    r = await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 401


async def test_swagger_form_login_works(client, make):
    user = await make.user("owner")
    r = await client.post("/api/v1/auth/token", data={"username": user.email, "password": PASSWORD})
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_no_token_or_garbage_token_is_401(client):
    assert (await client.get("/api/v1/me")).status_code == 401
    r = await client.get("/api/v1/me", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


async def test_expired_access_token_is_401_and_refresh_gives_a_new_one(client, make):
    user = await make.user("owner")
    tokens = await make.tokens(user)
    assert (await client.get("/api/v1/me", headers=_bearer(tokens))).status_code == 200
    # A token for the same session that expired a minute ago.
    sid = uuid.UUID(decode_access_token(get_settings(), tokens["access_token"])["sid"])
    expired_settings = get_settings().model_copy(update={"access_token_minutes": -1})
    expired = create_access_token(expired_settings, user.id, user.role, sid)
    r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    # The app then refreshes silently and carries on.
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    assert (await client.get("/api/v1/me", headers=_bearer(r.json()))).status_code == 200


async def test_each_refresh_token_works_once(client, make):
    user = await make.user("owner")
    tokens = await make.tokens(user)
    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert first.status_code == 200
    assert first.json()["refresh_token"] != tokens["refresh_token"]
    again = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert again.status_code == 401


async def test_unknown_and_expired_refresh_tokens_are_refused(client, make):
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": "x" * 43})
    assert r.status_code == 401
    user = await make.user("owner")
    tokens = await make.tokens(user)
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


async def test_logout_ends_the_session_at_once(client, make):
    user = await make.user("owner")
    tokens = await make.tokens(user)
    assert (await client.post("/api/v1/auth/logout", headers=_bearer(tokens))).status_code == 204
    # Refresh token refused, and the access token stops working too.
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401
    assert (await client.get("/api/v1/me", headers=_bearer(tokens))).status_code == 401


async def test_logout_keeps_other_devices_logged_in(client, make):
    user = await make.user("owner")
    phone, laptop = await make.tokens(user), await make.tokens(user)
    await client.post("/api/v1/auth/logout", headers=_bearer(phone))
    assert (await client.get("/api/v1/me", headers=_bearer(laptop))).status_code == 200


async def test_logout_all_ends_every_session(client, make):
    user = await make.user("owner")
    phone, laptop = await make.tokens(user), await make.tokens(user)
    assert (await client.post("/api/v1/auth/logout-all", headers=_bearer(phone))).status_code == 204
    assert (await client.get("/api/v1/me", headers=_bearer(laptop))).status_code == 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": laptop["refresh_token"]})
    assert r.status_code == 401


async def test_login_and_logout_are_audited(client, make):
    user = await make.user("owner")
    tokens = await make.tokens(user)
    await client.post("/api/v1/auth/logout", headers=_bearer(tokens))
    assert await audit_actions(actor_id=user.id) == ["auth.login", "auth.logout"]


# ----- invitations -----


async def _invite(client, make, outbox, email=None, billing=False):
    world = await make.world()
    home = await make.home(world.building)
    email = email or f"new-{home.id.hex[:8]}@example.com"
    r = await client.post(
        f"/api/v1/homes/{home.id}/invitations",
        headers=await make.headers(world.owner),
        json={
            "email": email,
            "full_name": "New Resident",
            "move_in": str(date.today()),
            "is_billing_contact": billing,
        },
    )
    assert r.status_code == 201, r.text
    return home, email, token_from_email(outbox[-1][2]), r.json()


async def test_invitation_creates_a_resident_who_can_log_in(client, make, outbox):
    home, email, token, invite = await _invite(client, make, outbox)
    assert invite["email_sent"] is True
    assert outbox[-1][0] == email
    r = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 200
    me = await client.get("/api/v1/me", headers=_bearer(r.json()))
    assert me.json()["role"] == "resident"
    assert me.json()["home_ids"] == [str(home.id)]


async def test_invitation_works_only_once(client, make, outbox):
    _, _, token, _ = await _invite(client, make, outbox)
    body = {"token": token, "password": PASSWORD}
    assert (await client.post("/api/v1/auth/invitations/accept", json=body)).status_code == 200
    assert (await client.post("/api/v1/auth/invitations/accept", json=body)).status_code == 400


async def test_invitation_expires_after_48_hours(client, make, outbox):
    _, _, token, invite = await _invite(client, make, outbox)
    expires = datetime.fromisoformat(invite["expires_at"])
    assert timedelta(hours=47, minutes=59) < expires - datetime.now(UTC) <= timedelta(hours=48)
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(Invitation)
            .where(Invitation.id == invite["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    r = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 400


async def test_invitation_needs_a_strong_password(client, make, outbox):
    _, _, token, _ = await _invite(client, make, outbox)
    r = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": "password1"}
    )
    assert r.status_code == 422


async def test_existing_resident_joins_a_second_home_with_their_password(client, make, outbox):
    resident = await make.user("resident")
    _, _, token, _ = await _invite(client, make, outbox, email=resident.email)
    wrong = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": "not-my-password-1"}
    )
    assert wrong.status_code == 409
    ok = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert ok.status_code == 200


async def test_an_owner_email_cannot_accept_a_resident_invitation(client, make, outbox):
    owner = await make.user("owner")
    _, _, token, _ = await _invite(client, make, outbox, email=owner.email)
    r = await client.post(
        "/api/v1/auth/invitations/accept", json={"token": token, "password": PASSWORD}
    )
    assert r.status_code == 409


def _bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
