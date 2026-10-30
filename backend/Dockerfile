# AquaPulse backend image (M3): the API, and the one-shot `migrate` job.
# Built by `make up` (docker compose build). Build context is the repo root.
FROM python:3.12.13-slim-trixie

# uv installs exactly the versions in uv.lock (reproducible builds).
RUN pip install --no-cache-dir uv==0.11.32

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

ENV PATH=/app/.venv/bin:$PATH

# Run as a normal user, not root.
RUN useradd --uid 10001 --no-create-home api
USER api

EXPOSE 8000
# --proxy-headers: trust Caddy's X-Forwarded-For, so the audit log records the
# real client IP. Port 8000 is not published to the Mac: only containers on the
# Docker network (Caddy) can reach it, which is why "*" is safe here.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
