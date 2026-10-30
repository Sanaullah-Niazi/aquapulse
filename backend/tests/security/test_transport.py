"""FR-71 (CORS) and FR-12 criterion 3 (no client skips the certificate check).

HTTPS itself, the HTTP->HTTPS redirect and the security headers are Caddy's
job; `make smoke` and `make tls-check` check them on the running stack.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

ALLOWED = "https://localhost:8443"  # the default CORS_ORIGINS


async def test_cors_allows_the_configured_site(client):
    r = await client.options(
        "/api/v1/me",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED


async def test_cors_blocks_any_other_site(client):
    """Criterion 3: a page on another site cannot call the API from a browser."""
    evil = "https://evil.example.com"
    r = await client.options(
        "/api/v1/me", headers={"Origin": evil, "Access-Control-Request-Method": "GET"}
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    r = await client.get("/api/healthz", headers={"Origin": evil})
    assert "access-control-allow-origin" not in r.headers  # the browser hides the answer


# Ways to switch off certificate checking in the languages and tools we use.
INSECURE = re.compile(
    r"CERT_NONE|check_hostname\s*=\s*False|tls_insecure|verify\s*=\s*False"
    r"|curl\s+(-\w*k\b|--insecure)|--no-check-certificate|--insecure\b|setInsecure\(\)"
)
SCANNED = ["backend/app", "simulator/src", "scripts", "infra", "Makefile", "docker-compose.yml",
           ".github"]  # fmt: skip


@pytest.mark.parametrize("place", SCANNED)
def test_no_client_skips_the_certificate_check(place):
    root = REPO / place
    files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    problems = []
    for path in files:
        if path.suffix in {".pyc", ".crt", ".key", ".png"} or "certs" in path.parts:
            continue
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if INSECURE.search(line):
                problems.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert problems == []
