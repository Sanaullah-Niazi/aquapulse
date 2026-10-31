# AquaPulse backend image: the API and the one-shot `migrate` job (M3), the
# ingestor (M4), broker-auth (M5) and the worker (M6). One image, different
# start commands.
# Built by `make up` (docker compose build). Build context is the repo root.
FROM python:3.12.16-slim-trixie

# Pango (text layout) and a font for WeasyPrint, which prints the PDF bills (M6).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# uv installs exactly the versions in uv.lock (reproducible builds).
RUN pip install --no-cache-dir uv==0.13.4

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Dependencies first: this layer is cached until uv.lock changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# 2) Then our code and the database migrations.
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/app ./app
# The device contract (JSON Schemas): the ingestor checks every MQTT message with it.
COPY contracts /app/contracts

ENV PATH=/app/.venv/bin:$PATH \
    AQUAPULSE_CONTRACTS_DIR=/app/contracts

# Run as a normal user, not root.
# /broker-auth: where broker-auth writes the MQTT password list (M5). Made here so
# the shared volume belongs to this user, not root.
RUN useradd --uid 10001 --no-create-home api && mkdir /broker-auth && chown api /broker-auth
USER api

EXPOSE 8000
# --proxy-headers: trust Caddy's X-Forwarded-For, so the audit log records the
# real client IP. Port 8000 is not published to the Mac: only containers on the
# Docker network (Caddy) can reach it, which is why "*" is safe here.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
