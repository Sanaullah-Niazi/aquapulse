# simulator/ · the digital twin

A virtual building that behaves like the real hardware will: **10 home nodes** (water meter, motorised valve, pH and temperature sensors) and **1 tank node** (level sensor, pump, sump). Each device has its own MQTT connection (TLS, with its own password since M5) and follows the contract in [`/contracts`](../contracts/README.md).

Requirements: SRS FR-01 to FR-07, FR-09, FR-10, FR-17 (simulator part). Guide: [docs/milestones/M2-digital-twin.md](../docs/milestones/M2-digital-twin.md).

## Run it

It starts with the rest of the stack (`make up`). Useful commands:

| Command | What it does |
|---|---|
| `make mqtt-watch` | Watch every message live |
| `make sim-logs` | The simulator's own log |
| `make sim-profile` | A week of simulated water use per home, with an hour-by-hour bar chart |
| `make valve-close DEVICE=home-b01-03` / `make valve-open ...` | Close or open a valve through the API and print the device's answer (since M5 only the server's `commander` account may send commands) |
| `make leak DEVICE=home-b01-03` | Start a 2-hour leak |
| http://localhost:8090/docs | Control API (Swagger UI): devices, scenarios, stop/start |
| `make sim-test` | Unit tests (no Docker needed) |
| `make sim-test-live` | Live tests against the running stack |

Settings are in `.env` (`SIM_HOMES`, `SIM_INTERVAL_S`, `SIM_SEED` ...), explained in [`.env.example`](../.env.example). Each device's MQTT password is made from `SIM_DEVICE_SECRET` and its device ID (`config.device_password`); the broker's certificate is checked against the project CA (`MQTT_CA_FILE`).

## Code map

| File | Job |
|---|---|
| `config.py` | Reads and checks the settings |
| `contract.py` | Topic names, schema validation, timestamps |
| `profile.py` | How a household uses water over a day (events, peaks, weekends) |
| `home.py` | Home node: meter, valve commands, pH and temperature |
| `tank.py` | Tank node: level, pump, automatic control with safety checks |
| `device.py` | What all devices share: boot, seq, command checks, outbox |
| `scenarios.py` | Injected faults, kept as ground truth for the AI |
| `store.py` | The devices' "flash memory" (litre counters survive restarts) |
| `building.py` | Puts it together and moves time forward (1 tick per second) |
| `mqtt_link.py` | One MQTT connection per device: TLS + password, Last Will, reconnect, publish |
| `api.py` | Control API (FastAPI) |
| `app.py`, `cli.py` | Start-up and the `aquapulse-sim` command |

Design rule: devices never touch the network. They put messages in an outbox and `mqtt_link.py` sends them. So all the logic is tested with a fake clock in milliseconds, and the network part is tested separately against a real broker.
