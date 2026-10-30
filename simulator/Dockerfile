# AquaPulse simulator image. Built by `make up` (docker compose build).
# Build context is the repo root, so the shared /contracts folder can be copied in.
FROM python:3.12.13-slim-trixie

# uv installs exactly the versions in uv.lock (reproducible builds).
RUN pip install --no-cache-dir uv==0.11.32

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Dependencies first: this layer is cached until uv.lock changes.
COPY simulator/pyproject.toml simulator/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Then our code and the device contract.
COPY simulator/src ./src
COPY contracts /app/contracts
RUN uv sync --frozen --no-dev

ENV PATH=/app/.venv/bin:$PATH \
    AQUAPULSE_CONTRACTS_DIR=/app/contracts \
    SIM_DATA_DIR=/data

# Run as a normal user, not root. /data is the devices' "flash" (a volume).
RUN useradd --uid 10001 --no-create-home sim && mkdir /data && chown sim /data
USER sim

EXPOSE 8090
CMD ["aquapulse-sim", "run"]
