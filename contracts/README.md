# contracts/

**The device contract**: the exact format of every MQTT message between devices and the server (SRS section 7.3, FR-09, FR-10).

The simulator, the backend (M4) and the ESP32 firmware (M11) all follow these files. That is why a simulated home can later be replaced by a real ESP32 with **zero code changes**: both speak exactly the same language.

## Topics

`aquapulse/v1/dev/{deviceId}/{channel}`, where `deviceId` is like `home-b01-03` or `tank-b01-01`.

| Channel | Direction | QoS | Retained | Schema |
|---|---|:---:|:---:|---|
| `telemetry` | device → server, every 10 s | 0 | no | [`telemetry-home.v1`](schemas/telemetry-home.v1.json), [`telemetry-tank.v1`](schemas/telemetry-tank.v1.json) |
| `status` | device → server (`offline` is the MQTT Last Will) | 1 | **yes** | [`status.v1`](schemas/status.v1.json) |
| `state` | device → server, on connect and on change | 1 | **yes** | [`state-home.v1`](schemas/state-home.v1.json), [`state-tank.v1`](schemas/state-tank.v1.json) |
| `cmd` | server → device | 1 | **never** | [`cmd.v1`](schemas/cmd.v1.json) |
| `ack` | device → server, answer to a command | 1 | no | [`ack.v1`](schemas/ack.v1.json) |

Shared building blocks (timestamp, UUID, device ID ...) are in [`common.v1.json`](schemas/common.v1.json).

## Rules in plain words

- Every payload is JSON, at most **2 KB**, with `"v": 1`. Anything else is rejected.
- **Unknown fields are rejected** (`additionalProperties: false`), so the simulator and firmware cannot drift apart without a test failing.
- Times are UTC with a `Z`, e.g. `2026-10-30T08:15:20Z`.
- `totalL` is a **cumulative** counter that only goes up. That's why telemetry can use QoS 0: a lost message loses no litres, the next one has the new total.
- A new `bootId` (and `"boot": true`, `seq` back to 0) tells the server the device restarted.
- Commands carry a `cmdId` and an `expiresAt`. Devices refuse expired and repeated commands, and always answer with an `ack`:

| `result` | `reason` | Meaning |
|---|---|---|
| `ok` | `null` | Done |
| `rejected` | `INVALID` | Not valid JSON or does not match `cmd.v1` |
| `rejected` | `EXPIRED` | Arrived after `expiresAt` |
| `rejected` | `DUPLICATE` | This `cmdId` was already handled |
| `rejected` | `UNSUPPORTED` | Wrong device type (e.g. pump command to a home) |
| `rejected` | `BUSY` | The valve is still moving |
| `rejected` | `NOT_MANUAL_MODE` | Pump commands need the tank in manual mode |
| `rejected` | `UNSAFE_DRY_RUN` / `UNSAFE_OVERFLOW` | Pump ON refused: sump empty / tank (almost) full |
| `failed` | `VALVE_STUCK` | The valve motor did not reach its position in 8 s |

## Examples

[`examples/`](examples) holds one or more valid messages per schema. Tests check that every example passes its schema, and that every message the simulator sends does too.

Changing a schema is a contract change: bump the version (`.v2.json`, `"v": 2`, topic `aquapulse/v2/...`) instead of editing v1 in a way that breaks devices already in the field.
