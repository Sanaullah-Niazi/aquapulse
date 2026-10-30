# infra/

Configuration for the services that run in Docker (see `docker-compose.yml` at the repo root).

| Folder | Service | Notes |
|---|---|---|
| `mosquitto/` | MQTT broker | `dev-insecure.conf` is for M1 to M4 only (plain MQTT, localhost). M5 adds TLS, per-device credentials and ACLs |
| `db/init/` | TimescaleDB | SQL that runs once on the first start. Tables come from Alembic migrations (M3) |
| `caddy/` | Web server / reverse proxy | Status page now; web app, API proxy and HTTPS later |

Guide: [docs/milestones/M1-infrastructure.md](../docs/milestones/M1-infrastructure.md)
