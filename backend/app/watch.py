"""Print the live event stream, like an app would see it: `make live`.

Logs in as a demo account (owner by default), asks for a WebSocket ticket
and prints every event that arrives. Ctrl+C to stop. Useful to see FR-25
working before the web app exists (M8).

    python -m app.watch [email]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

from websockets.sync.client import connect

API = "http://127.0.0.1:8000/api/v1"


def _post(path: str, body: dict, token: str | None = None) -> dict:
    request = urllib.request.Request(  # noqa: S310 (a fixed local http:// URL)
        API + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}
        | ({"Authorization": f"Bearer {token}"} if token else {}),
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.load(response)


def main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else "owner@example.com"
    tokens = _post("/auth/login", {"email": email, "password": os.environ["DEMO_PASSWORD"]})
    ticket = _post("/ws-ticket", {}, tokens["access_token"])["ticket"]
    print(f"Live events for {email} (Ctrl+C to stop):", flush=True)
    with connect(f"ws://127.0.0.1:8000/api/v1/ws?ticket={ticket}") as socket:
        try:
            for message in socket:
                print(message, flush=True)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
