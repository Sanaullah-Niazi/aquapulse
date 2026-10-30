"""Access tokens (JWT) and one-time tokens (FR-18, FR-69).

* Access token: a JWT signed with Ed25519 ("EdDSA"), valid 15 minutes. The
  server only ever accepts EdDSA, so tricks like "alg": "none" or switching
  to a symmetric algorithm are refused.
* Refresh, invitation and reset tokens: 32 random bytes. The database keeps
  only their SHA-256 hash, so a database leak does not leak usable tokens.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..config import Settings

ALGORITHM = "EdDSA"


@dataclass(frozen=True)
class SigningKey:
    kid: str  # key ID, written in the token header so we know which key to check with
    private: Ed25519PrivateKey

    @property
    def public_pem(self) -> bytes:
        return self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )


@cache
def _key_from_secret(secret: str) -> SigningKey:
    # Ed25519 keys are 32 bytes; derive them from the .env secret with SHA-256.
    private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(secret.encode()).digest())
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return SigningKey(hashlib.sha256(public_raw).hexdigest()[:12], private)


def current_key(settings: Settings) -> SigningKey:
    return _key_from_secret(settings.jwt_secret.get_secret_value())


def verification_keys(settings: Settings) -> dict[str, SigningKey]:
    """The current key plus older keys that are still accepted (key rotation)."""
    keys = [current_key(settings)]
    keys += [_key_from_secret(s) for s in settings.jwt_previous_secrets.split(",") if s.strip()]
    return {k.kid: k for k in keys}


def create_access_token(settings: Settings, user_id: uuid.UUID, role: str, sid: uuid.UUID) -> str:
    now = datetime.now(UTC)
    key = current_key(settings)
    claims = {
        "sub": str(user_id),
        "role": role,
        "sid": str(sid),  # session (refresh-token family), so logout ends this session
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(claims, key.private, algorithm=ALGORITHM, headers={"kid": key.kid})


class TokenError(Exception):
    pass


def decode_access_token(settings: Settings, token: str) -> dict:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        raise TokenError("malformed token") from None
    key = verification_keys(settings).get(kid)
    if key is None:
        raise TokenError("unknown signing key")
    try:
        return jwt.decode(
            token,
            key.private.public_key(),
            algorithms=[ALGORITHM],  # fixed: the token's own "alg" is never trusted
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "role", "sid", "iat", "exp", "jti", "iss", "aud"]},
        )
    except jwt.PyJWTError as err:
        raise TokenError(str(err)) from None


def new_opaque_token() -> tuple[str, bytes]:
    """A random token for the user, and the hash we store."""
    token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    return token, hash_opaque_token(token)


def hash_opaque_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
