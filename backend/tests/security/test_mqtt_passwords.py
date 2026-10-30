"""MQTT password hashes in Mosquitto's own format (FR-11, FR-14)."""

from __future__ import annotations

import base64

from app.security.mqtt_passwords import (
    DEVICE_ID,
    mosquitto_hash,
    new_password,
    simulator_password,
    verify,
)


def test_hash_has_mosquittos_format_and_checks_the_password():
    stored = mosquitto_hash("s3cret")
    _, kind, rounds, salt, digest = stored.split("$")
    assert (kind, rounds) == ("7", "1000")  # PBKDF2-SHA512, like mosquitto_passwd
    assert len(base64.b64decode(salt)) == 12 and len(base64.b64decode(digest)) == 64
    assert verify("s3cret", stored) and not verify("S3cret", stored)


def test_same_password_gets_a_different_hash_each_time():
    assert mosquitto_hash("x") != mosquitto_hash("x")  # random salt


def test_known_answer():
    # This exact line logged in to Mosquitto 2.1.2 with password pw1 (checked by hand,
    # and every `make smoke` logs in with hashes made by the same function).
    stored = mosquitto_hash("pw1", salt=base64.b64decode("Ssz1gK89h+RUx2Du"))
    assert stored.endswith(
        "RMCvVysA7YHMLZPSwAjN70mH49247TDUKvubszeKmskNw3U6Uo7r8v2YJpjrKnQ83mcGrmlCqMfxBM/FXeXXyw=="
    )
    assert verify("pw1", stored)


def test_unknown_or_broken_hashes_never_match():
    assert not verify("x", "$6$1000$abc$def")
    assert not verify("x", "not a hash")
    assert not verify("x", "$7$many$abc$def")


def test_new_device_passwords_are_long_and_random():
    passwords = {new_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) == 43 for p in passwords)  # 32 bytes, URL-safe base64


def test_simulator_password_matches_the_simulator():
    """Same value as simulator/tests/test_virtual_home_devices.py checks."""
    assert simulator_password("s", "home-b01-01") == "7Xggzgm4S_E4vqrxNLpC7OFXTYfZnQ6KfOrBaPkSwaY"
    assert simulator_password("s", "home-b01-02") != simulator_password("s", "home-b01-01")


def test_device_id_rule_follows_the_contract():
    assert DEVICE_ID.match("home-b01-03") and DEVICE_ID.match("tank-3f9a2c1d-01")
    assert not DEVICE_ID.match("home-b01-03/#") and not DEVICE_ID.match("monitor")
