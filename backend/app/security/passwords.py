"""Password hashing and password rules (FR-68, NFR-08).

Argon2id is the winner of the Password Hashing Competition. It is slow and
needs a lot of memory on purpose, so guessing passwords from a stolen
database is very expensive. Settings follow NFR-08 / OWASP: 19 MiB, 2 passes.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 10
MAX_LENGTH = 128  # long enough for passphrases, short enough to stop "hash a 1 MB password" DoS

_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

# A hash to check against when the email does not exist, so a wrong email and a
# wrong password take the same time (no hint whether an account exists, FR-18).
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


@cache
def _common_passwords() -> frozenset[str]:
    # 10,000 most common passwords (SecLists, MIT licence).
    path = Path(__file__).with_name("common-passwords.txt")
    return frozenset(line.strip().lower() for line in path.read_text().splitlines() if line)


def _letters_core(text: str) -> str:
    """Cut digits and symbols off both ends: "!!password123" -> "password".

    A plain loop, not a regular expression: a pattern like "[^a-z]+$" can take
    a very long time on some inputs (CodeQL: polynomial regular expression).
    """
    start, end = 0, len(text)
    while start < end and not text[start].isalpha():
        start += 1
    while end > start and not text[end - 1].isalpha():
        end -= 1
    return text[start:end]


def password_problem(password: str, email: str = "") -> str | None:
    """Return why a new password is not allowed, or None if it is fine."""
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Password must be at most {MAX_LENGTH} characters."
    lowered = password.lower()
    # "Password123!" is just "password" with decoration, so check the core too.
    core = _letters_core(lowered)
    common = _common_passwords()
    if lowered in common or core in common or len(set(lowered)) < 4:
        return "This password is too common or too simple. Try a few unrelated words."
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in lowered:
        return "Password must not contain your email name."
    return None
