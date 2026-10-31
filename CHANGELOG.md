# Changelog

## v1.0.0 (2026-10-31)

The first complete version: all 12 milestones of the [SRS](docs/SRS.md).

**What it does**

- Live water use, pH and temperature for every home, and remote valve control
  with locks for the building owner (M4).
- Secure devices: MQTT over TLS only, one password per device that can be rotated
  or revoked, and HTTPS everywhere (M5).
- Billing from real readings with slab tariffs in 11 currencies, PDF bills, cash,
  bank and Stripe (test mode) payments, reminders (M6).
- Alerts by email and push: leaks, hot water, bad pH, tank problems, devices offline (M7).
- A web app (installable PWA) for residents, owners and the System Admin (M8).
- An Android app (M9).
- AI: leak and unusual-use detection and a usage forecast, scored on a labelled test set (M10).
- Firmware for real ESP32 home and tank nodes, wiring and parts lists (M11).
- A digital twin that simulates a whole building, so everything runs without hardware (M2).

**Quality (M12)**

- 75 of 75 functional requirements have automated tests or CI checks
  ([traceability matrix](docs/requirements-traceability.md)).
- Security scans on every push: pip-audit, pnpm audit, Bandit, CodeQL, Trivy, gitleaks,
  testssl.sh and an OWASP ZAP baseline ([exceptions](docs/security/scan-exceptions.md),
  [ASVS checklist](docs/security/asvs-checklist.md)).
- Privacy: download your own data, residents can delete their account, raw readings
  are kept for 13 months.
- Daily database backups with a restore that CI tests ([docs/backups.md](docs/backups.md)).
- Measured speeds and what is not measured yet: [test report](docs/test-report.md).

**Known limits** (also in the test report)

- Not load-tested yet: ingest throughput, 2,000 devices, two ingestors, page load (NFR-02, 03, 25, 26).
- The 5-user usability test, the fresh-clone timing and a public demo with an uptime
  monitor need a person.
- Passwords need 10 characters (as in the SRS); OWASP ASVS asks for 12.
