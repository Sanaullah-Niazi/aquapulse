# Security scan exceptions (FR-74)

The [Security workflow](../../.github/workflows/security.yml) fails on any High or
Critical problem. Sometimes a problem cannot be fixed yet (no fixed version exists)
or does not affect AquaPulse. Then it may be accepted, but only if it is written
down here **and** added to the matching ignore file:

| Scanner | Ignore file / flag |
|---|---|
| Trivy (containers) | `.trivyignore` (one ID per line) |
| pip-audit (Python) | `--ignore-vuln <ID>` in `security.yml` |
| pnpm audit (web) | `pnpm.auditConfig.ignoreGhsas` in `web/package.json` |
| Bandit | `# nosec <rule>` on the line, with a comment saying why |

Every entry needs: the ID, the package, why it does not affect us, who accepted it,
and the date to check it again (at most 3 months later).

| ID | Package | Why it is accepted | Accepted by, on | Check again |
|---|---|---|---|---|
| CVE-2026-116587 | go-jose 4.1.5 inside Caddy | Denial of service through crafted RSA keys in a JWK. Caddy reads JWKs only for ACME, and AquaPulse uses its own local certificates (no ACME) | Claude for sana, 2026-10-31 | 2026-12-01 or the next Caddy release |
| CVE-2026-78669 | golang.org/x/net and Go stdlib inside Caddy | HTTP/2 SETTINGS flood (denial of service). The stack runs on your own computer or behind a tunnel; nothing is lost but the web page for a moment | Claude for sana, 2026-10-31 | same |
| CVE-2026-78667 | Go stdlib inside Caddy | Range header denial of service. Same reasoning | Claude for sana, 2026-10-31 | same |
| CVE-2026-97031 | Go stdlib inside Caddy | TLS ECH denial of service. Caddy does not enable ECH in our Caddyfile | Claude for sana, 2026-10-31 | same |
| CVE-2026-111943 | Go stdlib inside Caddy | Certificate chain check with untrusted intermediates. Caddy acts as a TLS client only when it talks to other servers; ours talks to the API over the private Docker network without TLS, and we do not use client certificates | Claude for sana, 2026-10-31 | same |

Why accepted and not fixed: Caddy 2.11.7 is the newest release and is built with
these versions. Building our own Caddy with a newer Go is possible (xcaddy) but makes
`make up` much slower for everyone. Dependabot opens a pull request as soon as a new
Caddy image is out; then these lines are removed.

## Fixed when the scans were added (2026-10-31)

The first run of the scans found real problems, all fixed by updating:

- `pypdf` 6.21.3 → 6.21.4 (backend tests only: reads PDF bills back), 3 CVEs.
- `react-router` 7.9.6 → 7.18.5, `vite` 7.1.12 → 7.3.7 (web), several High advisories.
- `vitest` 3.2.7 → 4.1.12 (web tests), which no longer uses `tinypool` (Critical advisory).
- `uv` 0.11.32 → 0.13.4 inside the backend and simulator images (Critical: its bundled CA list).
- The web image now runs `apk upgrade`, which picks up the Alpine fixes for curl and brotli.
- Third-party GitHub Actions are pinned to commit hashes (CodeQL).
