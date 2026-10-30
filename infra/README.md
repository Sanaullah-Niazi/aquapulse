# infra/

Configuration for the services that run in Docker (see `docker-compose.yml` at the repo root).

| Folder | Service | Notes |
|---|---|---|
| `mosquitto/` | MQTT broker | TLS only on 8883 (M5): `mosquitto.conf`, the topic rules in `acl`, and `start.sh`, which reloads the broker when broker-auth writes a new password list |
| `db/init/` | TimescaleDB | SQL that runs once on the first start. Tables come from Alembic migrations (M3) |
| `caddy/` | Web server / reverse proxy | Status page, `/api` proxy, HTTPS with security headers (M5); the web app later |
| `certs/` | Certificates | Made by `make certs`, never committed: `ca/` (the CA, its key never enters a container), `server/` (broker and Caddy), `public/` (just the CA certificate, for clients) |

Guides: [M1](../docs/milestones/M1-infrastructure.md), [M5](../docs/milestones/M5-secure-mqtt-https.md)
