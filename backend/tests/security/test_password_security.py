"""FR-68: password hashing, password rules, login rate limit, reset tokens (TC-68)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import db
from app.models import PasswordReset, User
from app.security.passwords import hash_password, needs_rehash, password_problem, verify_password

from ..conftest import PASSWORD, token_from_email

LOGIN = "/api/v1/auth/login"


async def test_database_contains_only_argon2id_hashes(make):
    await make.world()
    async with db.sessions()() as session:
        hashes = list(
            await session.scalars(select(User.password_hash).where(User.password_hash.is_not(None)))
        )
    assert hashes
    assert all(h.startswith("$argon2id$") for h in hashes)


def test_hash_and_verify():
    h = hash_password("a fine long passphrase")
    assert verify_password(h, "a fine long passphrase")
    assert not verify_password(h, "a fine long passphrasE")
    assert not verify_password(None, "anything at all")
    assert not verify_password("not-a-hash", "anything at all")
    assert not needs_rehash(h)


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("short1!", "at least 10"),
        ("x" * 129, "at most 128"),
        ("password123", "too common"),
        ("Password123!", "too common"),  # common word with decoration
        ("qwertyuiop", "too common"),
        ("aaaaaaaaaaaa", "too simple"),
        ("sana.niazi.rocks", "email name"),
    ],
)
def test_weak_passwords_are_refused(password, reason):
    problem = password_problem(password, "sana.niazi@example.com")
    assert problem is not None and reason in problem


def test_good_passphrase_is_accepted():
    assert password_problem("blue river lamp forty", "sana@example.com") is None


async def test_sixth_wrong_attempt_in_15_minutes_is_429(client, make):
    user = await make.user("owner")
    for _ in range(5):
        r = await client.post(LOGIN, json={"email": user.email, "password": "wrong-password-1"})
        assert r.status_code == 401
    r = await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 429  # even the right password waits now
    assert int(r.headers["retry-after"]) > 0


async def test_limit_also_counts_per_ip_across_accounts(client, make):
    for _ in range(5):
        await client.post(
            LOGIN, json={"email": "nobody@example.com", "password": "wrong-password-1"}
        )
    user = await make.user("owner")
    r = await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 429


async def test_successful_login_clears_the_account_counter(client, make):
    user = await make.user("owner")
    from app.routers import auth

    for _ in range(4):
        await client.post(LOGIN, json={"email": user.email, "password": "wrong-password-1"})
    assert (
        await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    ).status_code == 200
    auth.login_failures_by_ip.clear()  # this test is about the per-account counter
    for _ in range(4):
        r = await client.post(LOGIN, json={"email": user.email, "password": "wrong-password-1"})
        assert r.status_code == 401


async def _request_reset(client, make, outbox):
    user = await make.user("resident")
    r = await client.post("/api/v1/auth/password/forgot", json={"email": user.email})
    assert r.status_code == 202
    return user, token_from_email(outbox[-1][2])


async def test_reset_works_once_and_logs_out_old_sessions(client, make, outbox):
    user, token = await _request_reset(client, make, outbox)
    old_session = await make.headers(user)
    body = {"token": token, "new_password": "brand new river lamp"}
    assert (await client.post("/api/v1/auth/password/reset", json=body)).status_code == 200
    assert (await client.post("/api/v1/auth/password/reset", json=body)).status_code == 400
    assert (await client.get("/api/v1/me", headers=old_session)).status_code == 401
    assert (
        await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    ).status_code == 401
    r = await client.post(LOGIN, json={"email": user.email, "password": "brand new river lamp"})
    assert r.status_code == 200


async def test_reset_token_expires_after_30_minutes(client, make, outbox):
    user, token = await _request_reset(client, make, outbox)
    async with db.sessions()() as session:
        reset = await session.scalar(select(PasswordReset).where(PasswordReset.user_id == user.id))
    lifetime = reset.expires_at - datetime.now(UTC)
    assert timedelta(minutes=29) < lifetime <= timedelta(minutes=30)
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(PasswordReset)
            .where(PasswordReset.user_id == user.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    r = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": "brand new river lamp"}
    )
    assert r.status_code == 400


async def test_reset_refuses_a_weak_new_password(client, make, outbox):
    _, token = await _request_reset(client, make, outbox)
    r = await client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": "password1"}
    )
    assert r.status_code == 422


async def test_forgot_gives_the_same_answer_for_unknown_emails(client, make, outbox):
    user = await make.user("owner")
    known = await client.post("/api/v1/auth/password/forgot", json={"email": user.email})
    unknown = await client.post("/api/v1/auth/password/forgot", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [to for to, _, _ in outbox] == [user.email]  # only the real account got an email


async def test_change_password_needs_the_current_one(client, make):
    user = await make.user("owner")
    headers = await make.headers(user)
    wrong = await client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": "not-it-at-all", "new_password": "brand new river lamp"},
    )
    assert wrong.status_code == 400
    weak = await client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": "password1"},
    )
    assert weak.status_code == 422
    ok = await client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": "brand new river lamp"},
    )
    assert ok.status_code == 200
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 401  # log in again


async def test_old_weaker_hashes_are_upgraded_at_login(client, make):
    from argon2 import PasswordHasher

    user = await make.user("owner")
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(PASSWORD)
    async with db.sessions()() as session, session.begin():
        await session.execute(
            update(User).where(User.id == user.id).values(password_hash=weak_hash)
        )
    assert (
        await client.post(LOGIN, json={"email": user.email, "password": PASSWORD})
    ).status_code == 200
    async with db.sessions()() as session:
        new_hash = await session.scalar(select(User.password_hash).where(User.id == user.id))
    assert new_hash != weak_hash and not needs_rehash(new_hash)
