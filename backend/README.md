# backend/

The AquaPulse API (M3), the MQTT ingestor (M4), broker-auth (M5) and the worker for bills and alerts (M6, M7): one image with four start commands.
Guides: [M3](../docs/milestones/M3-backend-core.md), [M4](../docs/milestones/M4-live-data.md), [M5](../docs/milestones/M5-secure-mqtt-https.md), [M6](../docs/milestones/M6-billing.md), [M7](../docs/milestones/M7-alerts.md), [M8](../docs/milestones/M8-web-app.md).

## Code map

| File | What it does |
|---|---|
| `app/main.py` | Builds the FastAPI app: URL layout, Swagger at `/api/docs`, `/metrics`, crash logging |
| `app/config.py` | Settings from environment variables (`.env`) |
| `app/models.py` | Database tables (SQLAlchemy) |
| `app/schemas.py` | Shapes of requests and responses (Pydantic); unknown fields are refused |
| `app/db.py` | Database connection, one session per request |
| `app/deps.py` | Who is calling and may they: token check, role check, object check |
| `app/audit.py` | Append-only audit log with a SHA-256 hash chain |
| `app/emailer.py` | Sends invitation and reset emails (Mailpit locally) |
| `app/security/passwords.py` | Argon2id hashing and password rules |
| `app/security/tokens.py` | Ed25519 JWT access tokens, random one-time tokens |
| `app/security/ratelimit.py` | Sliding-window rate limits |
| `app/security/mqtt_passwords.py` | Device passwords, Mosquitto-format hashes, simulator passwords (M5) |
| `app/security/totp.py` | Two-factor login: authenticator codes, the secret stored encrypted, one-time recovery codes (M8) |
| `app/brokerauth.py` | broker-auth (`python -m app.brokerauth`): writes the broker's password list from the database (M5) |
| `app/routers/` | The endpoints: `auth`, `me`, `admin`, `buildings`, `residents`, `health`; M4: `live`, `valves`, `tanks`, `stream` (WebSocket); M5: `devices` (register, rotate, revoke); M6: `tariffs`, `bills` (bills, payments, Stripe checkout and webhook); M7: `alerts` (alerts, rule settings, email choice); M8: `myhome` (comparison, bill so far), 2FA in `me`, email choices per alert type and quiet hours |
| `app/billing/` | M6: `money.py` rounding and formatting, `engine.py` the bill calculation (pure, no database), `periods.py` months and stays, `generate.py` monthly bills from readings, `lifecycle.py` issue/void/payments/overdue, `pdf.py` + `templates/bill.html` PDF bills, `stripe_pay.py` Stripe test mode; M7: `reminders.py` bill reminders |
| `app/alerts/` | M7: `rules.py` the 9 rules and safe limits, `detect.py` readings in, problems out (pure, no database), `store.py` alerts and queued emails, `engine.py` runs it all in the worker, `deliver.py` sends emails with retries |
| `app/worker.py` | The worker (`python -m app.worker`): last month's bills on the 1st, overdue bills every minute (M6); the alert engine, reminders, email sending and the daily audit chain check (M7) |
| `app/bills.py` | `python -m app.bills`: make draft bills now (`make bills`) |
| `app/ingest/` | The ingestor (`python -m app.ingest`): `counter.py` litres from the meter counter, `handler.py` checks and stores messages, `commands.py` sends commands and marks timeouts, `service.py` the MQTT connection |
| `app/contract.py` | Loads the shared JSON Schemas from `contracts/` |
| `app/events.py` | PostgreSQL LISTEN/NOTIFY between ingestor and API |
| `app/control.py` | Queues device commands, 10 per minute per user |
| `app/backfill.py` | `python -m app.backfill`: loads demo history (`make demo-data`) |
| `app/watch.py` | `python -m app.watch`: prints the live stream (`make live`) |
| `app/valve.py` | `python -m app.valve`: valve close/open through the API (`make valve-close`) |
| `app/migrate.py` | `python -m app.migrate`: database role, migrations, permissions |
| `app/seed.py` | `python -m app.seed`: demo accounts, building, tank and devices |
| `app/export_openapi.py` | Writes `openapi.json` |
| `migrations/` | Alembic migrations |
| `tests/` | `api/`, `security/`, `ingest/`, `billing/`, `alerts/` and `broker/` tests, one file per requirement, against a real database (and the real TLS broker for `broker/` and `test_ingestor_service.py`) |
| `bruno/` | Request collection for the free Bruno API client |

## Run without Docker (optional)

The normal way is `make up`. To run the API on your Mac for quick changes:

```bash
make up                               # database, broker and mail still run in Docker
cd backend
set -a; source ../.env; set +a        # load the settings
export DB_HOST=localhost MQTT_HOST=localhost SMTP_HOST=localhost
export MQTT_CA_FILE="$PWD/../infra/certs/public/ca.crt"
uv run uvicorn app.main:app --reload  # http://localhost:8000/api/docs (plain HTTP, only on your Mac)
```

## Database changes

1. Change `app/models.py`.
2. `uv run alembic revision --autogenerate -m "what changed"` (with the settings loaded as above), then read the new file in `migrations/versions/` and fix anything Alembic guessed wrong.
3. `make migrate`. `tests/test_migrations.py` fails if the models and migrations disagree.
