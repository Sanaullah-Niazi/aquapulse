"""The AquaPulse API: builds the FastAPI app and plugs in every router.

URL layout (Caddy forwards everything under /api to this app):
  /api/v1/...        the API itself (versioned, so v2 can live next to it later)
  /api/docs          Swagger UI: try every endpoint in the browser
  /api/healthz       alive?          /api/readyz   database + broker reachable?
  /metrics           Prometheus counters (FR-31). Not under /api, so Caddy
                     does not expose it to the internet.

Run locally:  uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import time
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from . import __version__, db
from .routers import admin, auth, buildings, health, me, residents

log = logging.getLogger("aquapulse.api")

API_PREFIX = "/api/v1"

REQUESTS = Counter(
    "aquapulse_http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
LATENCY = Histogram(
    "aquapulse_http_request_seconds", "Time to answer an HTTP request", ["method", "route"]
)


def _route_name(request: Request) -> str:
    """The name of the endpoint function (e.g. "get_home"), never the real URL.

    Real URLs contain IDs, and a new metric for every home ID would make
    Prometheus slow (the "high cardinality" problem). FastAPI puts the matched
    route in the request scope once routing is done.
    """
    route = request.scope.get("route")
    return getattr(route, "name", None) or "unmatched"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("AquaPulse API %s starting", __version__)
    yield
    await db.dispose()  # close database connections cleanly on shutdown


def create_app() -> FastAPI:
    app = FastAPI(
        title="AquaPulse API",
        version=__version__,
        description=(
            "Smart water management: buildings, homes, residents, login and audit log.\n\n"
            "Click **Authorize** and log in with a demo account (see `make seed`)."
        ),
        docs_url="/api/docs",
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def metrics_and_errors(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # Error messages can contain personal data (a database error may
            # quote an email address), so log the type and the code lines only
            # (NFR-15). That is still enough to find the bug.
            log.error(
                "unhandled %s in %s %s\n%s",
                type(exc).__name__,
                request.method,
                _route_name(request),
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
        route = _route_name(request)
        LATENCY.labels(request.method, route).observe(time.perf_counter() - start)
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        return response

    app.include_router(health.router, prefix="/api")
    for module in (auth, me, admin, buildings, residents):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
