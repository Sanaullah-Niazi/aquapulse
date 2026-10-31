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
| (none) | | | | |

## Fixed when the scans were added (2026-10-31)

The first run of the scans found real problems, all fixed by updating:

- `pypdf` 6.21.3 → 6.21.4 (backend tests only: reads PDF bills back), 3 CVEs.
- `react-router` 7.9.6 → 7.18.5, `vite` 7.1.12 → 7.3.7 (web), several High advisories.
- `vitest` 3.2.7 → 4.1.12 (web tests), which no longer uses `tinypool` (Critical advisory).
