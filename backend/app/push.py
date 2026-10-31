"""Push notifications to the Android app through Firebase Cloud Messaging (M9, FR-60).

Free and optional. With a Firebase project (the free "Spark" plan), you download
a *service account* key file and point FCM_SERVICE_ACCOUNT_FILE at it. Without
it, push is simply off: alerts still show in the app and come by email.

How one push is sent (the "HTTP v1" API):
1. We sign a short JSON Web Token with the service account's private key and
   swap it at Google for an access token (valid 1 hour, kept and reused).
2. We POST the message to
   https://fcm.googleapis.com/v1/projects/<project>/messages:send
   with that access token. Firebase delivers it to the phone.

Only the standard library and PyJWT (already used for our own tokens) are
needed. The phone's token and the text are never logged (NFR-15).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import jwt

from .config import get_settings

log = logging.getLogger("aquapulse.push")

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (an address, not a password)
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
Result = Literal["sent", "gone", "error"]


@dataclass(frozen=True)
class Account:
    project_id: str
    client_email: str
    private_key: str
    token_uri: str


@lru_cache(maxsize=4)
def _load(path: str) -> Account | None:
    try:
        data = json.loads(Path(path).read_text())
        return Account(
            project_id=data["project_id"],
            client_email=data["client_email"],
            private_key=data["private_key"],
            token_uri=TOKEN_URL,  # always Google's; never an address from the file
        )
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("push is off: the Firebase service account file could not be read")
        return None


def account() -> Account | None:
    path = get_settings().fcm_service_account_file
    return _load(path) if path else None


def available() -> bool:
    """Is push set up on this server?"""
    return account() is not None


_access: dict[str, tuple[str, float]] = {}  # client_email -> (token, expires at)


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    if not url.startswith("https://"):  # only Google's fixed HTTPS addresses, never file:
        raise ValueError("push only talks HTTPS")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}


def _access_token(acc: Account) -> str:
    cached = _access.get(acc.client_email)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": acc.client_email,
            "scope": SCOPE,
            "aud": acc.token_uri,
            "iat": now,
            "exp": now + 3600,
        },
        acc.private_key,
        algorithm="RS256",
    )
    body = (
        "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=" + assertion
    ).encode()
    status, answer = _post(
        acc.token_uri, body, {"Content-Type": "application/x-www-form-urlencoded"}
    )
    if status != 200 or "access_token" not in answer:
        raise OSError(f"Google refused the service account (HTTP {status})")
    _access[acc.client_email] = (answer["access_token"], now + int(answer.get("expires_in", 3600)))
    return answer["access_token"]


def _send(token: str, title: str, body: str, data: dict[str, str], critical: bool) -> Result:
    acc = account()
    if acc is None:
        return "error"
    message = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": data,
            "android": {
                "priority": "high" if critical else "normal",
                # One notification per alert: a repeat replaces it instead of piling up.
                "notification": {"tag": data.get("alertId", "")},
            },
        }
    }
    url = f"https://fcm.googleapis.com/v1/projects/{acc.project_id}/messages:send"
    status, answer = _post(
        url,
        json.dumps(message).encode(),
        {"Authorization": f"Bearer {_access_token(acc)}", "Content-Type": "application/json"},
    )
    if status == 200:
        return "sent"
    # The app was removed or the token replaced: forget this phone.
    code = json.dumps(answer.get("error", {}))
    if status == 404 or "UNREGISTERED" in code:
        return "gone"
    log.warning("push not accepted (HTTP %s)", status)
    return "error"


async def send(
    token: str, title: str, body: str, data: dict[str, str] | None = None, critical: bool = False
) -> Result:
    """Send one push in a worker thread (urllib blocks). Never raises."""
    try:
        return await asyncio.to_thread(_send, token, title, body, data or {}, critical)
    except (OSError, ValueError, jwt.PyJWTError) as err:
        log.warning("push could not be sent: %s", type(err).__name__)
        return "error"
