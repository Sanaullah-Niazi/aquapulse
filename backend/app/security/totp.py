"""Two-factor login with any authenticator app (FR-70).

TOTP ("time-based one-time password"): the app and the server share a secret.
Every 30 seconds both turn the secret plus the current time into the same
6-digit number. Someone who steals the password still needs the phone.

* The secret is stored encrypted (AES-GCM) with a key made from JWT_SECRET,
  so a copy of the database alone is not enough to make codes.
* A code works once: we remember the last 30-second step that was used.
* 10 recovery codes, for a lost phone. Only a keyed hash (HMAC-SHA256 with
  the server's key) is stored, so a stolen database cannot be used to guess
  them offline. Each works one time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import Settings

ISSUER = "AquaPulse"
STEP_S = 30
RECOVERY_CODES = 10


def _keys(settings: Settings) -> list[bytes]:
    """The current key first, then keys of older JWT secrets (after a rotation)."""
    secrets_ = [settings.jwt_secret.get_secret_value()]
    secrets_ += [s.strip() for s in settings.jwt_previous_secrets.split(",") if s.strip()]
    return [hashlib.sha256(b"aquapulse-totp:" + s.encode()).digest() for s in secrets_]


def encrypt(settings: Settings, secret: str) -> str:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_keys(settings)[0]).encrypt(nonce, secret.encode(), b"totp")
    return base64.b64encode(nonce + sealed).decode()


def decrypt(settings: Settings, stored: str) -> str:
    raw = base64.b64decode(stored)
    for key in _keys(settings):
        try:
            return AESGCM(key).decrypt(raw[:12], raw[12:], b"totp").decode()
        except Exception:  # noqa: S112 (wrong key: try the next one)
            continue
    raise ValueError("The two-factor secret cannot be read (was JWT_SECRET replaced?)")


def new_secret() -> str:
    return pyotp.random_base32()


def otpauth_url(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def matching_step(
    secret: str, code: str, last_step: int | None, now: float | None = None
) -> int | None:
    """The time step the code belongs to (now, or one step before or after: phone
    clocks are not perfect), or None. A step at or before `last_step` was used already."""
    now = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    current = int(now // STEP_S)
    for step in (current, current - 1, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if secrets.compare_digest(totp.at(step * STEP_S), code):
            return step
    return None


def new_recovery_codes() -> list[str]:
    """10 codes like 'k7m2-q9xd', easy to type, 40 bits of chance each."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
    return [
        "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
        for _ in range(RECOVERY_CODES)
    ]


def recovery_hashes(settings: Settings, code: str) -> list[bytes]:
    """The code's hash with the current key first (to store), then older keys (to check)."""
    clean = code.strip().lower().encode()
    return [hmac.new(key, clean, hashlib.sha256).digest() for key in _keys(settings)]
