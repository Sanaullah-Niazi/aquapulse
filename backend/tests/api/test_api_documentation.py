"""FR-29: OpenAPI document and Swagger UI (TC-29)."""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

COMMITTED = Path(__file__).resolve().parents[2] / "openapi.json"


async def test_swagger_ui_and_openapi_are_served(client):
    docs = await client.get("/api/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text
    spec = (await client.get("/api/openapi.json")).json()
    assert spec["openapi"].startswith("3.1")


def test_every_endpoint_has_a_summary_and_response_schema():
    for path, operations in app.openapi()["paths"].items():
        for method, op in operations.items():
            assert op.get("summary"), f"{method} {path} has no summary"
            ok = [code for code in op["responses"] if code.startswith("2")]
            assert ok, f"{method} {path} documents no success response"


def test_committed_openapi_file_is_up_to_date():
    """CI fails when the API changes but backend/openapi.json was not regenerated.
    Fix: `make openapi`, then commit the file."""
    assert COMMITTED.exists(), "backend/openapi.json is missing: run `make openapi`"
    assert json.loads(COMMITTED.read_text()) == app.openapi(), "run `make openapi`"
