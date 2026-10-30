# backend/

The AquaPulse API (M3). The ingestor and worker join in M4 and later.
Guide: [docs/milestones/M3-backend-core.md](../docs/milestones/M3-backend-core.md).

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
| `app/routers/` | The endpoints: `auth`, `me`, `admin`, `buildings`, `residents`, `health` |
| `app/migrate.py` | `python -m app.migrate`: database role, migrations, permissions |
| `app/seed.py` | `python -m app.seed`: demo accounts and building |
| `app/export_openapi.py` | Writes `openapi.json` |
| `migrations/` | Alembic migrations |
| `tests/` | `api/` and `security/` tests, one file per requirement, against a real database |
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
