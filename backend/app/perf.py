"""Speed checks on the running demo (M12, NFR-01, NFR-04, NFR-05): `make perf`.

Runs inside the API container against the real stack: the simulated
devices, the real broker, ingestor, database and live WebSocket. It prints
the measured times; nothing here is estimated.

- Valve (NFR-01): residents 01 to 10 each close and open their own valve 3
  times (60 commands, all homes at once). Measured from sending the command
  to two moments on the live stream an app listens to:
    * "moving": the device reports the valve has started turning, and
    * "done": the device confirms the end position.
  The simulated motor takes 2 to 5 s to turn (like a real ball valve), so
  "done" can never be under 2 s; "moving" is the software's own share.
- Live updates (NFR-05): for PERF_LIVE_S seconds, the delay between a
  reading's own timestamp (made on the device) and its arrival on the stream.
- Reads (NFR-04): 11 users at once (10 residents and the owner), each
  making PERF_READS requests: simple reads (home, live values) and
  consumption over 400 days (the longest range FR-23 allows).

Calls go straight to the API inside the server (no TLS, no network), so
the numbers are the server's own time.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from websockets.asyncio.client import connect

API = "http://127.0.0.1:8000/api/v1"
PASSWORD = os.environ.get("DEMO_PASSWORD", "")
RESIDENTS = [f"resident{n:02d}@example.com" for n in range(1, 11)]
PAIRS = int(os.environ.get("PERF_VALVE_PAIRS", "3"))
LIVE_S = int(os.environ.get("PERF_LIVE_S", "60"))
READS = int(os.environ.get("PERF_READS", "60"))  # per user; the API allows 120/min


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    request = urllib.request.Request(  # noqa: S310 (a fixed local http:// URL)
        API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}
        | ({"Authorization": f"Bearer {token}"} if token else {}),
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def login(email: str) -> str:
    return call("POST", "/auth/login", body={"email": email, "password": PASSWORD})["access_token"]


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def summary(name: str, seconds: list[float], unit: str = "s") -> str:
    if not seconds:
        return f"{name}: no samples"
    k = 1000 if unit == "ms" else 1
    values = [s * k for s in seconds]
    return (
        f"{name}: n={len(values)}  median {statistics.median(values):.{0 if k > 1 else 2}f} {unit}"
        f"  p95 {pct(values, 95):.{0 if k > 1 else 2}f} {unit}"
        f"  p99 {pct(values, 99):.{0 if k > 1 else 2}f} {unit}"
        f"  max {max(values):.{0 if k > 1 else 2}f} {unit}"
    )


class Stream:
    """The owner's live stream: every event of the building, with arrival times."""

    def __init__(self) -> None:
        self.waiters: dict[tuple[str, str], asyncio.Future] = {}
        self.live: list[float] = []
        self.recording_live = False

    def wait(self, kind: str, key: str) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self.waiters[(kind, key)] = future
        return future

    def _done(self, kind: str, key: str, at: float) -> None:
        future = self.waiters.pop((kind, key), None)
        if future is not None and not future.done():
            future.set_result(at)

    async def run(self, ticket: str, ready: asyncio.Event) -> None:
        async with connect(f"ws://127.0.0.1:8000/api/v1/ws?ticket={ticket}") as socket:
            await socket.recv()  # "ready"
            ready.set()
            async for message in socket:
                at = time.monotonic()
                event = json.loads(message)
                kind = event.get("type")
                if kind == "device" and (event.get("state") or {}).get("valve") == "moving":
                    self._done("moving", str(event.get("homeId")), at)
                elif kind == "command" and event.get("state") not in ("queued", "pending"):
                    self._done("done", str(event.get("commandId")), at)
                elif kind == "telemetry" and self.recording_live:
                    made = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
                    self.live.append((datetime.now(UTC) - made).total_seconds())


async def valve_run(stream: Stream, email: str, moving: list, done: list, other: list) -> None:
    token = await asyncio.to_thread(login, email)
    me = await asyncio.to_thread(call, "GET", "/me", token)
    home = str(me["home_ids"][0])
    for _ in range(PAIRS):
        for action in ("close", "open"):
            moved = stream.wait("moving", home)
            start = time.monotonic()
            command = await asyncio.to_thread(
                call, "POST", f"/homes/{home}/valve", token,
                {"action": action, "reason": "speed test (make perf)"},
            )  # fmt: skip
            finished = stream.wait("done", str(command["id"]))
            try:
                end = await asyncio.wait_for(finished, 20)
            except TimeoutError:
                other.append(f"{email} {action}: no answer in 20 s")
                continue
            done.append(end - start)
            if moved.done():  # an already-closed valve does not move
                moving.append(moved.result() - start)
            stream.waiters.pop(("moving", home), None)


def read_run(email: str, building: str | None, home: str | None) -> dict[str, list[float]]:
    token = login(email)
    today = date.today()
    first = (today - timedelta(days=399)).isoformat()
    if home is None:
        home = str(call("GET", "/me", token)["home_ids"][0])
    paths = {
        "simple": [f"/homes/{home}", f"/homes/{home}/live"],
        "consumption": [
            f"/homes/{home}/consumption?granularity=day&from={first}&to={today}",
            f"/homes/{home}/consumption?granularity=month&from={first}&to={today}",
        ],
    }
    if building is not None:
        paths["consumption"].append(
            f"/buildings/{building}/consumption?granularity=day&from={first}&to={today}"
        )
        paths["simple"].append(f"/buildings/{building}/overview")
    times: dict[str, list[float]] = defaultdict(list)
    for i in range(READS):
        kind = "simple" if i % 2 else "consumption"
        options = paths[kind]
        path = options[(i // 2) % len(options)]
        start = time.monotonic()
        call("GET", urllib.parse.quote(path, safe="/?=&-"), token)
        times[kind].append(time.monotonic() - start)
    return times


async def main() -> None:
    if not PASSWORD:
        raise SystemExit("DEMO_PASSWORD is missing")
    owner = await asyncio.to_thread(login, "owner@example.com")
    buildings = await asyncio.to_thread(call, "GET", "/buildings", owner)
    building = next(b for b in buildings if b["name"] == "AquaPulse Demo Tower")
    homes = await asyncio.to_thread(call, "GET", f"/buildings/{building['id']}/homes", owner)
    ticket = (await asyncio.to_thread(call, "POST", "/ws-ticket", owner))["ticket"]
    stream, ready = Stream(), asyncio.Event()
    listener = asyncio.create_task(stream.run(ticket, ready))
    await asyncio.wait_for(ready.wait(), 10)

    print(f"Valve (NFR-01): {len(RESIDENTS)} homes x {PAIRS * 2} commands, all at once...")
    moving: list[float] = []
    done: list[float] = []
    problems: list[str] = []
    await asyncio.gather(*(valve_run(stream, e, moving, done, problems) for e in RESIDENTS))
    for problem in problems:
        print("  ! " + problem)
    print("  " + summary("valve starts moving, shown on the stream", moving))
    print("  " + summary("valve confirmed in its new position     ", done))

    print(f"Live updates (NFR-05): listening for {LIVE_S} s...")
    stream.recording_live = True
    await asyncio.sleep(LIVE_S)
    stream.recording_live = False
    print("  " + summary("device reading -> on the live stream", stream.live))
    listener.cancel()

    print(f"Reads (NFR-04): 11 users at once, {READS} requests each...")
    jobs = [asyncio.to_thread(read_run, e, None, None) for e in RESIDENTS]
    jobs.append(asyncio.to_thread(read_run, "owner@example.com", building["id"], homes[0]["id"]))
    results = await asyncio.gather(*jobs)
    for kind, label in (("simple", "simple reads (home, live)"),
                        ("consumption", "consumption over 400 days")):  # fmt: skip
        values = [t for r in results for t in r.get(kind, [])]
        print("  " + summary(f"{label:26}", values, "ms"))


if __name__ == "__main__":
    asyncio.run(main())
