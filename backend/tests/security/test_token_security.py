"""FR-69: access-token and refresh-token security (TC-69)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import get_settings
from app.security.tokens import (
    ALGORITHM,
    TokenError,
    create_access_token,
    current_key,
    decode_access_token,
)

from ..conftest import audit_actions


def _claims(**changes) -> dict:
    s = get_settings()
    now = datetime.now(UTC)
    base = {
        "sub": str(uuid.uuid4()),
        "role": "owner",
        "sid": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid.uuid4()),
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
    }
    return {k: v for k, v in (base | changes).items() if v is not None}


def _sign(claims: dict, **header) -> str:
    key = current_key(get_settings())
    return jwt.encode(claims, key.private, algorithm=ALGORITHM, headers={"kid": key.kid} | header)


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, default=str).encode()).rstrip(b"=").decode()


def test_a_good_token_has_every_required_claim():
    s = get_settings()
    token = create_access_token(s, uuid.uuid4(), "owner", uuid.uuid4())
    claims = decode_access_token(s, token)
    assert {"sub", "role", "iat", "exp", "jti", "aud", "iss"} <= set(claims)
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"


def test_alg_none_is_refused():
    kid = current_key(get_settings()).kid
    claims = _claims()
    claims["iat"] = int(claims["iat"].timestamp())
    claims["exp"] = int(claims["exp"].timestamp())
    token = f"{_b64({'alg': 'none', 'typ': 'JWT', 'kid': kid})}.{_b64(claims)}."
    with pytest.raises(TokenError):
        decode_access_token(get_settings(), token)


def test_algorithm_confusion_with_hs256_is_refused():
    # Classic attack: sign with HMAC, using the *public* key as the shared secret.
    key = current_key(get_settings())
    token = jwt.encode(
        _claims(),
        "attacker-secret-key-32-bytes-long!!",
        algorithm="HS256",
        headers={"kid": key.kid},
    )
    with pytest.raises(TokenError):
        decode_access_token(get_settings(), token)


def test_token_signed_by_another_key_is_refused():
    other = Ed25519PrivateKey.generate()
    token = jwt.encode(
        _claims(), other, algorithm="EdDSA", headers={"kid": current_key(get_settings()).kid}
    )
    with pytest.raises(TokenError):
        decode_access_token(get_settings(), token)


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "someone-else"},
        {"iss": "evil"},
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"jti": None},
        {"sid": None},
    ],
    ids=["wrong-aud", "wrong-iss", "expired", "no-jti", "no-sid"],
)
def test_wrong_or_missing_claims_are_refused(changes):
    with pytest.raises(TokenError):
        decode_access_token(get_settings(), _sign(_claims(**changes)))


def test_unknown_kid_and_garbage_are_refused():
    with pytest.raises(TokenError):
        decode_access_token(get_settings(), _sign(_claims(), kid="nope"))
    with pytest.raises(TokenError):
        decode_access_token(get_settings(), "garbage")


def test_key_rotation_keeps_old_tokens_valid_until_they_expire():
    old = get_settings()
    token = create_access_token(old, uuid.uuid4(), "owner", uuid.uuid4())
    rotated = old.model_copy(
        update={
            "jwt_secret": type(old.jwt_secret)("a-brand-new-secret"),
            "jwt_previous_secrets": old.jwt_secret.get_secret_value(),
        }
    )
    assert decode_access_token(rotated, token)["role"] == "owner"  # old token still fine
    new_token = create_access_token(rotated, uuid.uuid4(), "owner", uuid.uuid4())
    assert jwt.get_unverified_header(new_token)["kid"] != jwt.get_unverified_header(token)["kid"]
    forgotten = old.model_copy(update={"jwt_secret": type(old.jwt_secret)("a-brand-new-secret")})
    with pytest.raises(TokenError):
        decode_access_token(forgotten, token)  # once the old secret is removed, old tokens die


async def test_reusing_a_rotated_refresh_token_ends_the_whole_session(client, make):
    user = await make.user("owner")
    stolen = await make.tokens(user)
    # The real user refreshes first...
    fresh = (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen["refresh_token"]})
    ).json()
    # ...then the thief replays the old token: refused, and the session is ended.
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen["refresh_token"]})
    assert r.status_code == 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]})
    assert r.status_code == 401
    me = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {fresh['access_token']}"}
    )
    assert me.status_code == 401
    assert "auth.refresh_reuse_detected" in await audit_actions(actor_id=user.id)


async def test_refresh_tokens_are_stored_only_as_hashes(client, make):
    from sqlalchemy import select

    from app import db
    from app.models import RefreshToken
    from app.security.tokens import hash_opaque_token

    user = await make.user("owner")
    tokens = await make.tokens(user)
    async with db.sessions()() as session:
        stored = list(
            await session.scalars(
                select(RefreshToken.token_hash).where(RefreshToken.user_id == user.id)
            )
        )
    assert stored == [hash_opaque_token(tokens["refresh_token"])]
    assert tokens["refresh_token"].encode() not in stored
