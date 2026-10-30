# backend/

The AquaPulse API (M3) and the MQTT ingestor (M4), one image with two start commands.
Guides: [M3](../docs/milestones/M3-backend-core.md), [M4](../docs/milestones/M4-live-data.md).

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
| `app/routers/` | The endpoints: `auth`, `me`, `admin`, `buildings`, `residents`, `health`; M4: `live`, `valves`, `tanks`, `stream` (WebSocket) |
| `app/ingest/` | The ingestor (`python -m app.ingest`): `counter.py` litres from the meter counter, `handler.py` checks and stores messages, `commands.py` sends commands and marks timeouts, `service.py` the MQTT connection |
| `app/contract.py` | Loads the shared JSON Schemas from `contracts/` |
| `app/events.py` | PostgreSQL LISTEN/NOTIFY between ingestor and API |
| `app/control.py` | Queues device commands, 10 per minute per user |
| `app/backfill.py` | `python -m app.backfill`: loads demo history (`make demo-data`) |
| `app/watch.py` | `python -m app.watch`: prints the live stream (`make live`) |
| `app/migrate.py` | `python -m app.migrate`: database role, migrations, permissions |
| `app/seed.py` | `python -m app.seed`: demo accounts, building, tank and devices |
| `app/export_openapi.py` | Writes `openapi.json` |
| `migrations/` | Alembic migrations |
| `tests/` | `api/`, `security/` and `ingest/` tests, one file per requirement, against a real database (and the real broker for `test_ingestor_service.py`) |
| `bruno/` | Request collection for the free Bruno API client |

## Run without Docker (optional)

The normal way is `make up`. To run the API on your Mac for quick changes:

```bash
make up                               # database, broker and mail still run in Docker
cd backend
set -a; source ../.env; set +a        # load the settings
export DB_HOST=localhost MQTT_HOST=localhost SMTP_HOST=localhost
uv run uvicorn app.main:app --reload  # http://localhost:8000/api/docs
```

## Database changes

1. Change `app/models.py`.
2. `uv run alembic revision --autogenerate -m "what changed"` (with the settings loaded as above), then read the new file in `migrations/versions/` and fix anything Alembic guessed wrong.
3. `make migrate`. `tests/test_migrations.py` fails if the models and migrations disagree.
