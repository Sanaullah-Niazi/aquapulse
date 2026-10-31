"""M9 (FR-60, FR-52): push notifications to the Android app.

The phone registers its Firebase token; alerts are pushed to the right people
by their own push choice (like email), quiet hours apply to non-critical
ones, a phone Firebase says is gone is forgotten, and failures are retried.
Firebase itself is replaced by a fake here; app/push.py's message format and
token exchange are tested at the end with a fake HTTP layer.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app import db, push
from app.alerts import deliver
from app.config import get_settings
from app.models import PushDevice

from .conftest import alerts_of, feed, notifications_of

TOKEN = "fcm-token-" + "x" * 40


@pytest.fixture
def firebase(monkeypatch):
    """Push is set up, and every push lands in this list instead of Firebase."""
    sent: list[tuple[str, str, str, dict, bool]] = []
    answers: dict[str, str] = {}

    async def fake_send(token, title, body, data=None, critical=False):
        sent.append((token, title, body, data or {}, critical))
        return answers.get(token, "sent")

    monkeypatch.setattr(push, "available", lambda: True)
    monkeypatch.setattr(push, "send", fake_send)
    fake_send.answers = answers  # type: ignore[attr-defined]
    return sent, answers


async def _register(client, make, user, token=TOKEN):
    r = await client.post(
        "/api/v1/me/push-devices", json={"token": token}, headers=await make.headers(user)
    )
    assert r.status_code == 204, r.text


async def _phones(user_id) -> list[str]:
    async with db.sessions()() as session:
        return list(
            await session.scalars(select(PushDevice.token).where(PushDevice.user_id == user_id))
        )


async def test_a_phone_registers_once_and_moves_with_the_login(client, make):
    one, two = await make.user("resident"), await make.user("owner")
    token = "phone-a-" + "y" * 40
    await _register(client, make, one, token)
    await _register(client, make, one, token)  # again after a restart: still one row
    assert await _phones(one.id) == [token]
    await _register(client, make, two, token)  # someone else logs in on the same phone
    assert await _phones(one.id) == [] and await _phones(two.id) == [token]

    r = await client.post("/api/v1/me/push-devices/forget", json={"token": token},
                          headers=await make.headers(two))  # fmt: skip
    assert r.status_code == 204 and await _phones(two.id) == []
    short = await client.post("/api/v1/me/push-devices", json={"token": "x"},
                              headers=await make.headers(one))  # fmt: skip
    assert short.status_code == 422
    anonymous = await client.post("/api/v1/me/push-devices", json={"token": token})
    assert anonymous.status_code == 401


async def test_push_choices_are_saved_and_checked(client, make, monkeypatch):
    user = await make.user("resident")
    headers = await make.headers(user)
    r = await client.get("/api/v1/me/notifications", headers=headers)
    assert r.json()["push_min"] == "warning" and r.json()["push_muted_rules"] == []
    assert r.json()["push_available"] is False  # no Firebase file in the tests

    monkeypatch.setattr(push, "available", lambda: True)
    choice = {"push_min": "critical", "push_muted_rules": ["high_usage", "high_usage"]}
    r = await client.patch("/api/v1/me/notifications", headers=headers, json=choice)
    assert r.status_code == 200
    assert r.json()["push_min"] == "critical"
    assert r.json()["push_muted_rules"] == ["high_usage"]
    assert r.json()["push_available"] is True
    bad = await client.patch("/api/v1/me/notifications", headers=headers,
                             json={"push_muted_rules": ["no_such_rule"]})  # fmt: skip
    assert bad.status_code == 422


async def test_a_critical_alert_is_pushed_to_owner_and_resident(
    site, engine, client, make, firebase
):
    sent, _ = firebase
    await _register(client, make, site.owner, "owner-phone-" + "o" * 30)
    await _register(client, make, site.resident, "resident-phone-" + "r" * 30)
    site.twin.inject("ph_drift", site.twin.device(1), 30, direction="up")
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    pushes = [n for n in await notifications_of(alert.id) if n.channel == "push"]
    assert {n.user_id for n in pushes} == {site.owner.id, site.resident.id}

    await deliver.deliver_due(max(n.next_attempt_at for n in pushes))
    mine = [s for s in sent if s[3].get("alertId") == str(alert.id)]
    assert {s[0][:5] for s in mine} == {"owner", "resid"}
    assert mine[0][1] == "pH unsafe (unit 01, Alert Tower)"
    assert all(s[4] for s in mine)  # critical: high priority, wakes the phone
    assert {n.status for n in await notifications_of(alert.id) if n.channel == "push"} == {"sent"}


async def test_each_person_chooses_push_by_severity_and_type(site, engine, client, make, firebase):
    await _register(client, make, site.owner, "owner2-" + "o" * 30)
    await _register(client, make, site.resident, "resident2-" + "r" * 30)
    r = await client.patch("/api/v1/me/notifications", headers=await make.headers(site.owner),
                           json={"push_min": "off"})  # fmt: skip
    assert r.status_code == 200
    r = await client.patch("/api/v1/me/notifications", headers=await make.headers(site.resident),
                           json={"push_muted_rules": ["temp_abnormal"]})  # fmt: skip
    assert r.status_code == 200
    site.twin.inject("temp_spike", site.twin.device(1), 10)  # a warning, type temp_abnormal
    await feed(engine, site.twin, 5 * 60)
    [alert] = await alerts_of(site.building)
    assert [n for n in await notifications_of(alert.id) if n.channel == "push"] == []
    # Email is chosen separately and still goes out.
    assert [n for n in await notifications_of(alert.id) if n.channel == "email"]


async def test_no_phone_or_no_firebase_means_no_push(site, engine, client, make, monkeypatch):
    monkeypatch.setattr(push, "available", lambda: True)
    site.twin.inject("ph_drift", site.twin.device(2), 30, direction="up")  # nobody has a phone
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    assert [n for n in await notifications_of(alert.id) if n.channel == "push"] == []


async def test_a_gone_phone_is_forgotten_and_errors_are_retried(
    site, engine, client, make, firebase
):
    _, answers = firebase
    gone, flaky = "gone-phone-" + "g" * 30, "flaky-phone-" + "f" * 30
    await _register(client, make, site.owner, gone)
    answers[gone] = "gone"
    site.twin.inject("ph_drift", site.twin.device(2), 30, direction="up")  # owner only
    await feed(engine, site.twin, 30 * 60)
    [alert] = await alerts_of(site.building)
    [note] = [n for n in await notifications_of(alert.id) if n.channel == "push"]
    await deliver.deliver_due(note.next_attempt_at)
    [note] = [n for n in await notifications_of(alert.id) if n.channel == "push"]
    assert note.status == "failed" and note.last_error == deliver.NO_PHONE
    assert await _phones(site.owner.id) == []

    # A phone that Firebase does not accept for now: tried again later.
    await _register(client, make, site.owner, flaky)
    answers[flaky] = "error"
    async with db.sessions()() as session, session.begin():
        retry = await session.get(type(note), note.id)
        retry.status, retry.attempts = "pending", 0
    await deliver.deliver_due(note.next_attempt_at)
    [note] = [n for n in await notifications_of(alert.id) if n.channel == "push"]
    assert note.status == "pending" and note.last_error == "Firebase did not accept it"
    answers[flaky] = "sent"
    await deliver.deliver_due(note.next_attempt_at + timedelta(seconds=10))
    [note] = [n for n in await notifications_of(alert.id) if n.channel == "push"]
    assert note.status == "sent"


# ----- app/push.py itself, with Google's two addresses faked -----


@pytest.fixture
def service_account(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    path = tmp_path / "firebase-service-account.json"
    path.write_text(json.dumps({
        "type": "service_account", "project_id": "aquapulse-test",
        "client_email": "push@aquapulse-test.iam.gserviceaccount.com", "private_key": pem,
        "token_uri": "file:///etc/passwd",  # ignored: always Google's own address
    }))  # fmt: skip
    monkeypatch.setattr(get_settings(), "fcm_service_account_file", str(path))
    push._load.cache_clear()
    push._access.clear()
    yield key
    push._load.cache_clear()
    push._access.clear()


async def test_push_sends_the_http_v1_message(service_account, monkeypatch):
    calls: list[tuple[str, bytes, dict]] = []

    def fake_post(url, body, headers):
        calls.append((url, body, headers))
        if url == push.TOKEN_URL:
            return 200, {"access_token": "google-access", "expires_in": 3600}
        return 200, {"name": "projects/aquapulse-test/messages/1"}

    monkeypatch.setattr(push, "_post", fake_post)
    assert push.available()
    assert await push.send(TOKEN, "pH unsafe", "pH 9.1", {"alertId": "a1"}, critical=True) == "sent"
    assert await push.send(TOKEN, "Again", "x") == "sent"
    token_calls = [c for c in calls if c[0] == push.TOKEN_URL]
    assert len(token_calls) == 1  # the access token is reused for an hour
    url, body, headers = calls[1]
    assert url == "https://fcm.googleapis.com/v1/projects/aquapulse-test/messages:send"
    assert headers["Authorization"] == "Bearer google-access"
    message = json.loads(body)["message"]
    assert message["token"] == TOKEN and message["notification"] == {
        "title": "pH unsafe",
        "body": "pH 9.1",
    }
    assert message["android"]["priority"] == "high" and message["data"] == {"alertId": "a1"}
    assert b"assertion=" in token_calls[0][1]


async def test_push_reports_gone_phones_and_errors(service_account, monkeypatch):
    answer = {"code": 404, "body": {"error": {"status": "NOT_FOUND"}}}

    def fake_post(url, body, headers):
        if url == push.TOKEN_URL:
            return 200, {"access_token": "t"}
        return answer["code"], answer["body"]

    monkeypatch.setattr(push, "_post", fake_post)
    assert await push.send(TOKEN, "t", "b") == "gone"
    answer.update(code=400, body={"error": {"details": [{"errorCode": "UNREGISTERED"}]}})
    assert await push.send(TOKEN, "t", "b") == "gone"
    answer.update(code=503, body={})
    assert await push.send(TOKEN, "t", "b") == "error"


async def test_push_is_off_without_a_readable_file(tmp_path, monkeypatch):
    push._load.cache_clear()
    monkeypatch.setattr(get_settings(), "fcm_service_account_file", "")
    assert not push.available()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    monkeypatch.setattr(get_settings(), "fcm_service_account_file", str(broken))
    assert not push.available()
    assert await push.send(TOKEN, "t", "b") == "error"
    with pytest.raises(ValueError):
        push._post("file:///etc/passwd", b"", {})
    push._load.cache_clear()
