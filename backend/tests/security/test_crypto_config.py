"""NFR-08: the security settings are what the SRS promises."""

from __future__ import annotations

from argon2 import Type
from argon2.low_level import ARGON2_VERSION

from app.config import get_settings
from app.security import passwords


def test_argon2id_with_at_least_19_mib_and_2_iterations():
    params = passwords._hasher  # the one hasher every password goes through
    assert params.type is Type.ID
    assert params.memory_cost >= 19 * 1024  # in KiB
    assert params.time_cost >= 2
    assert passwords.hash_password("check the settings").startswith(
        f"$argon2id$v={ARGON2_VERSION}$m=19456,t=2"
    )


def test_token_lifetimes():
    s = get_settings()
    assert s.access_token_minutes == 15
    assert s.refresh_token_days == 7
    assert s.invitation_hours == 48
    assert s.reset_token_minutes == 30


def test_password_length_limits():
    assert passwords.MIN_LENGTH == 10
    assert passwords.MAX_LENGTH == 128
