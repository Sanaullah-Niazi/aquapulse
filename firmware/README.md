# firmware/

ESP32 firmware for the AquaPulse device nodes (M11). Guide: [docs/milestones/M11-firmware.md](../docs/milestones/M11-firmware.md).

| Path | What |
|---|---|
| `lib/aquapulse_core/` | The device rules in plain C++ (contract messages, command checks, counter saving, back-off, pH calibration, pump rules). No Arduino code, so it is tested on a computer |
| `src/common/` | What both nodes share: Wi-Fi, clock, TLS MQTT with Last Will and back-off, command checks, acks, flash |
| `src/home/main.cpp` | The home node (FR-15): flow meter, pH, temperature, valve |
| `src/tank/main.cpp` | The tank node (FR-16): level sensor, sump switch, pump run locally |
| `include/config.h` | Pins and sensor settings |
| `include/secrets.example.h` | The settings each device needs. Copy it to `secrets.h` (git-ignored) or run `make firmware-secrets` |
| `test/native/` | 132 checks for `aquapulse_core`, run with `make firmware-test` |
| `test/check_contract.py` | Validates every message the firmware makes against `/contracts` |
| `wokwi/home-node/`, `wokwi/tank-node/` | The Wokwi simulator diagrams and settings |

Parts lists and wiring: [docs/hardware](../docs/hardware).

```bash
make firmware-test      # tests on your computer
make firmware-build     # builds for the ESP32 (real board and Wokwi)
make firmware-secrets   # writes include/secrets.h
make firmware-upload    # flashes a real ESP32 over USB (NODE=tank for the tank node)
```
