# ADR 0002: Core technology choices

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

Constraints from the SRS: free tools only, runs on a MacBook Pro M1 Max, one-command Docker setup, real-time IoT data, strong security, and Sana must understand every part.

## Decision

| Need | Choice | Main reason |
|---|---|---|
| Device messaging | MQTT, Eclipse Mosquitto 2 | IoT standard; Last Will; TLS; per-device ACLs |
| Database | PostgreSQL 16 + TimescaleDB | Normal SQL and fast time-series in one database |
| Backend | Python 3.12, FastAPI | Same language as the simulator and AI; automatic API docs |
| Web | React + TypeScript + Vite | Most widely used; static build can be hosted on any website |
| Mobile | Flutter | Real Android APK from one codebase (iOS later) |
| Live events | Postgres LISTEN/NOTIFY + WebSocket | No extra service such as Redis needed |
| Firmware | Arduino framework on ESP32 via PlatformIO | Huge community, Wokwi support |

## Consequences

Three languages (Python, TypeScript, Dart) plus C++ for firmware. Each is introduced in its own milestone with a guide.
