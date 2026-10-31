# Test report (v1.0)

What is tested, how, and the results. Numbers come from the CI run of the M12
part 1 pull request (#29, 2026-10-31, every job green) unless a row says
otherwise. Nothing here is estimated: a requirement that has not been measured
yet says so.

## Automated tests

| Part | Tool | Tests | Result |
|---|---|---|---|
| Backend: API, ingestor, billing, alerts, AI, security, migrations | pytest, real TimescaleDB and TLS broker | 868 | all pass, **95.4% line coverage** (gate: 80%) |
| Billing engine | pytest + Hypothesis (random tariffs) | 45 | **100% branch coverage** (gate: 100%, FR-34) |
| Simulator (digital twin) | pytest | 95, plus the live MQTT tests against the running stack | all pass |
| Web app units | Vitest | 16 | all pass |
| Web app in a real browser | Playwright + axe-core, 390 px and 1440 px | every resident, owner and System Admin screen | all pass |
| Android app | flutter test (widget tests) | 32 | all pass, `flutter analyze`: no issues |
| ESP32 firmware logic | g++ native tests + contract check | 132 checks, 6 message kinds | all pass; builds for 4 targets |
| AI models | `make ai-eval` on a labelled test set | see [ai-evaluation.md](ai-evaluation.md) | targets met; CI checks the report is current |
| Whole stack | Docker Compose smoke, live MQTT, TLS, restart | see below | all pass |

Which test proves which requirement: [requirements-traceability.md](requirements-traceability.md)
(all 75 functional requirements have at least one automated test or CI check).

Speed numbers (NFR-01, 04, 05, 06) come from `make perf` in the CI run of the merged
M12 part 3 (2026-10-31, run 41204929921), on a GitHub runner with 4 CPU cores. They are
measured inside the server, without the network between a phone and the server.

## Non-functional requirements (SRS section 6)

✅ measured and met · ⚠️ partly measured · ⏳ not measured yet · 👤 needs a person (see the end)

| NFR | Target | Status | Evidence |
|---|---|---|---|
| NFR-01 Valve round trip | p95 ≤ 2 s, p99 ≤ 4 s | ⚠️ | `make perf`, 60 commands from 10 homes at once. Command sent → the valve is seen moving on the live stream: **p95 0.26 s, p99 0.30 s**. → the device confirms the end position: **p95 5.06 s, p99 5.73 s**. The second number includes the simulated motor, which takes 2 to 5 s to turn like a real ball valve, so it can never be under 2 s. The software's own share meets the target |
| NFR-02 Page load | ≤ 2 s, Lighthouse ≥ 85 | ⏳ | Not measured yet |
| NFR-03 Ingest throughput | ≥ 500 msg/s for 10 min, 0 loss | ⏳ | No load test yet |
| NFR-04 Read API latency | p95 ≤ 300 ms (400 days), ≤ 100 ms simple | ⚠️ | `make perf`, 11 users at once, 330 requests of each kind. Consumption over a 400-day range: **p95 50 ms**. Simple reads: **p95 64 ms**. Partly: CI holds about 8 days of readings, not 400, and the SRS asks for 50 users (the demo has 11 accounts, and each is limited to 120 requests a minute) |
| NFR-05 Live update latency | p95 ≤ 2 s | ✅ | `make perf`: 60 readings, from the device's own timestamp to arrival on the live stream: **p95 0.11 s** |
| NFR-06 Resource use (idle) | ≤ 2 GB RAM, ≤ 15% of a core | ✅ | `make perf` (`docker stats` every 10 s): all containers together **at most 389 MiB**, CPU **6.2% of one core** on average. Sampled for 2 minutes in CI (the SRS says 10: `make perf IDLE=600`) |
| NFR-07 Encryption in transit | TLS 1.2+, no plain MQTT | ✅ | `make tls-check` in CI: port 1883 closed, TLS 1.3 negotiated, testssl.sh finds nothing |
| NFR-08 Authentication strength | Argon2id, 15 min / 7 days | ✅ | `backend/tests/security/test_crypto_config.py` |
| NFR-09 Authorization coverage | 100% of endpoints | ✅ | `backend/tests/security/test_rbac_matrix.py`: every endpoint × every role; fails if an endpoint is missing |
| NFR-10 Vulnerabilities | 0 open High/Critical | ✅ | Security workflow (pip-audit, pnpm audit, Trivy, CodeQL, Bandit) green; ZAP baseline in the stack job (`make zap`). Accepted exceptions: [scan-exceptions.md](security/scan-exceptions.md) |
| NFR-11 OWASP ASVS L2 (V2–V5, V7) | checked off | ✅ | [security/asvs-checklist.md](security/asvs-checklist.md): 4 controls partly met, with reasons |
| NFR-12 Secrets | 0 in git history | ✅ | gitleaks full-history scan in CI |
| NFR-13 Data isolation | 0 cross-home leaks | ✅ | Object-level access tests for the API, WebSocket, CSV exports and PDFs |
| NFR-14 Retention | raw 13 months, deletion on request | ✅ | 395-day retention policies and account deletion: `backend/tests/privacy/` |
| NFR-15 Logs | no secrets or full emails | ✅ | `backend/tests/security/test_log_redaction.py` |
| NFR-16 No lost litres | within 0.5% with 10% loss | ✅ | Ingest test with 10% of messages dropped and reboots (FR-23) |
| NFR-17 Recovery after restart | healthy within 90 s | ✅ | `make restart-check` in CI |
| NFR-18 Backups | daily backup, tested restore | ✅ | The `backup` service makes a daily `pg_dump` ([backups.md](backups.md)). The CI stack job (every push and every Monday) makes a backup, restores it into a test database and compares the tables, then really restores it and runs the smoke test again; it prints how long the restore took |
| NFR-19 Availability (public demo) | ≥ 99% monthly | 👤 | Needs the public demo and a free uptime monitor |
| NFR-20 Safe failure | tank safe with the server down | ✅ | `simulator/tests/test_tank_offline_safety.py`; the tank firmware's own pump rules (`firmware/test/native`) |
| NFR-21 Ease of use | 60 s task, SUS ≥ 75 | 👤 | Needs 5 test users |
| NFR-22 Accessibility | 0 serious axe issues | ✅ | axe-core on every web screen at phone and desktop size |
| NFR-23 Responsiveness | 360 px to 4K; 5"–7" phones | ✅ | Playwright at 390 px and 1440 px with a no-sideways-scroll check; Flutter tests at 360 px |
| NFR-24 Clear language | plain English | ⚠️ | Written that way and reviewed by hand; no formal readability score |
| NFR-25 Scale on one host | 2,000 devices | ⏳ | Not load-tested yet |
| NFR-26 Horizontal growth | 2 ingestor replicas, no duplicates | ⏳ | Not tested yet |
| NFR-27 Code quality | coverage ≥ 80%, linters clean | ⚠️ | Backend 95.4%, billing engine 100%, Ruff, `flutter analyze` and TypeScript strict all clean. Web unit coverage and mypy are not measured yet |
| NFR-28 Documentation | a guide per milestone, quick start ≤ 15 min | ⚠️ | Guides M0–M12 in `docs/milestones/`; the fresh-clone timing needs a person |
| NFR-29 Portability | arm64 and amd64 | ✅ | CI runs everything on amd64 (GitHub Actions); the same compose file runs on an Apple silicon (arm64) Mac |
| NFR-30 Cost | $0 | ✅ | Only free tools and free tiers; Stripe in test mode |

## Still to do (honest list)

1. Load tests: ingest throughput (NFR-03), 2,000 devices on one host (NFR-25), two ingestors
   (NFR-26), page load with Lighthouse (NFR-02), and read speed with 400 days of data (NFR-04).
2. Things only a person can do: the 5-user usability test (NFR-21), the fresh-clone timing (NFR-28),
   and the public demo with an uptime monitor (NFR-19).
