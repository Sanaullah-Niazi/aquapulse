"""MQTT passwords for devices and server accounts (FR-11, FR-14).

Mosquitto checks passwords against a file of `username:hash` lines. We make
those hashes ourselves, in the exact format Mosquitto's own `mosquitto_passwd`
tool writes ("$7$" = PBKDF2-HMAC-SHA512), so the plain password never has to
be stored anywhere: not in the database, not in a file, not in a log.

Device passwords are 32 random bytes (256 bits), so nobody can guess them;
that is why a fast hash (PBKDF2 with 1,000 rounds, Mosquitto's default) is
enough here, while people's passwords use the slow Argon2id (passwords.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

ITERATIONS = 1000  # what mosquitto_passwd 2.1 uses
SALT_BYTES = 12
PASSWORD_BYTES = 32  # FR-11: at least 32 bytes of randomness

# Device IDs as the MQTT contract defines them (contracts/schemas/common.v1.json).
DEVICE_ID = re.compile(r"^(home|tank)-[a-z0-9]{1,16}-[0-9]{2,3}$")


def new_password() -> str:
    """A fresh device password: 32 random bytes as 43 URL-safe characters."""
    return secrets.token_urlsafe(PASSWORD_BYTES)


def mosquitto_hash(password: str, salt: bytes | None = None) -> str:
    """Hash a password the way Mosquitto expects: $7$rounds$salt$hash (base64)."""
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, ITERATIONS)
    return f"$7${ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify(password: str, stored: str) -> bool:
    """True if `password` matches a hash made by mosquitto_hash (or mosquitto_passwd)."""
    try:
        _, kind, rounds, salt, digest = stored.split("$")
        if kind != "7":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac(
            "sha512", password.encode(), base64.b64decode(salt), int(rounds)
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def simulator_password(secret: str, device_id: str) -> str:
    """The password of a *simulated* device, made from SIM_DEVICE_SECRET.

    The simulator and broker-auth both compute it, so the 11 virtual devices can
    log in without anybody copying 11 passwords around. It is as strong as the
    secret (HMAC-SHA256, 32 bytes). Real devices never use this: they get a
    random password when the owner registers them. Same function, same result as
    aquapulse_sim.config.device_password (a test in both projects checks it).
    """
    digest = hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()
