# OWASP ASVS 4.0.3 checklist (NFR-11)

The SRS asks for the Level 2 controls of five chapters: V2 authentication, V3
sessions, V4 access control, V5 validation and V7 logging. This page lists the
controls that apply to AquaPulse, how each one is met, and the test or file
that shows it. Reviewed for v1.0 on 2026-10-31.

✅ met · ⚠️ partly met (reason given) · ➖ does not apply

## V2 Authentication

| ASVS | Control | Status | How, and the proof |
|---|---|---|---|
| 2.1.1 | Passwords of at least 12 characters | ⚠️ | The SRS (FR-68) sets **10** characters, plus a common-password check. Accepted for the demo; raise `MIN_LENGTH` in `backend/app/security/passwords.py` for real use |
| 2.1.2 | Passwords of at least 64 characters allowed | ✅ | Up to 128 characters (`Password` type in `schemas.py`) |
| 2.1.7 | Checked against breached or common passwords | ✅ | Top 10,000 common passwords (offline list, no outside service). `tests/security/test_password_security.py` |
| 2.1.9 | No composition rules (upper case, symbols…) | ✅ | Only length and the common list |
| 2.2.1 | Anti-automation against password guessing | ✅ | 5 failures per 15 min per account and per IP, then blocked (FR-68). `tests/security/test_input_validation_and_rate_limiting.py` |
| 2.2.3 | User told about security events | ⚠️ | Password changes, resets and 2FA changes are in the audit log and end other sessions, but no email notice is sent yet (only the reset code itself is emailed) |
| 2.4.1 | Passwords stored with a slow, salted hash | ✅ | Argon2id (19 MiB memory, 2 iterations at least). `tests/security/test_crypto_config.py` (NFR-08) |
| 2.5.1 | Reset codes are random and single use | ✅ | Opaque random token, hash stored, 30 minutes, used once. `tests/api/test_login_and_sessions.py` |
| 2.5.2 | No password hints or security questions | ✅ | None |
| 2.5.4 | No shared or default accounts | ✅ | Demo accounts only get the random `DEMO_PASSWORD` from `.env`; System Admins are made with `make admin` |
| 2.5.6 | Forgot-password does not reveal accounts | ✅ | Always "If that account exists, we sent an email" |
| 2.7 / 2.8 | Second factor (TOTP) | ✅ | Optional for owners and residents, required for System Admins; 10 single-use recovery codes. `tests/security/test_two_factor_authentication_totp.py` (FR-70) |
| 2.10 | Service accounts without default secrets | ✅ | Every MQTT account and device has its own random password (hash stored). `tests/security/test_mqtt_passwords.py` |

## V3 Session management

| ASVS | Control | Status | How, and the proof |
|---|---|---|---|
| 3.1.1 | Session tokens never in URLs | ✅ | Tokens only in the `Authorization` header. The WebSocket uses a one-time 30-second ticket instead (FR-26) |
| 3.2.1 | New token at every login | ✅ | New refresh token family per login |
| 3.2.3 | Tokens stored safely in the browser | ⚠️ | The web app keeps the refresh token in `localStorage` (the access token only in memory). The strict Content-Security-Policy blocks injected scripts that could read it. An HttpOnly cookie would be stronger, but it needs CSRF protection and does not work for the "host the app on your own website" feature (FR-56). The Android app uses the Keystore (`flutter_secure_storage`) |
| 3.3.1 | Logout ends the session on the server | ✅ | `POST /auth/logout` revokes the refresh token; `logout-all` revokes every session |
| 3.3.2 | Sessions expire | ✅ | Access token 15 min, refresh token 7 days (NFR-08) |
| 3.3.3 | "Log out everywhere" after a password change | ✅ | Every session ends after a password change or reset |
| 3.5.2 | Stolen refresh token detection | ✅ | Reusing an old refresh token ends the whole session family and writes `auth.refresh_reuse_detected` to the audit log. `tests/security/test_token_security.py` |
| 3.5.3 | Signed tokens checked properly | ✅ | Ed25519 (EdDSA) only; algorithm, expiry, issuer and audience checked |
| 3.4 | Cookie flags | ➖ | No session cookies are used |

## V4 Access control

| ASVS | Control | Status | How, and the proof |
|---|---|---|---|
| 4.1.1 | Checked on the server | ✅ | Every endpoint checks the role (`require(...)`) and then the object (`visible_home`, `visible_building`) |
| 4.1.3 | Least privilege | ✅ | Three roles; System Admins cannot control valves (separation of duties). The API's database role cannot change or delete audit rows |
| 4.1.5 | Fails closed | ✅ | No role match is a 403. `tests/security/test_rbac_matrix.py` calls **every** endpoint with every role, and fails when a new endpoint is missing from the table (NFR-09) |
| 4.2.1 | No access to other people's objects (IDOR) | ✅ | Residents see only their own home and only during their stay; owners only their buildings. `tests/api/test_role_based_and_object_level_access_contr.py`, plus the WebSocket, CSV and PDF tests (NFR-13) |
| 4.3.1 | Admin interfaces protected | ✅ | System Admin console needs 2FA; `/metrics` is not published outside the Docker network |
| 4.3.2 | No directory listing or hidden files | ✅ | Caddy serves only the built app; ZAP baseline in CI (`make zap`) |

## V5 Validation, sanitisation and encoding

| ASVS | Control | Status | How, and the proof |
|---|---|---|---|
| 5.1.1 | Protection against mass assignment | ✅ | Every request model forbids unknown fields (`extra="forbid"`) |
| 5.1.3 | All input validated (allow lists, ranges) | ✅ | Pydantic types with limits; device messages checked against JSON Schemas (`contracts/`) with a 2 KB limit. `tests/security/test_input_validation_and_rate_limiting.py`, `tests/ingest/test_telemetry_ingestion.py` |
| 5.2.6 / 12.x | Uploaded files checked | ✅ | Payment receipts: size limit in Caddy and the API, type checked from the file's first bytes (PDF, PNG, JPEG), never from its name |
| 5.3.3 | Output encoded for HTML | ✅ | React escapes all text; the only raw HTML is the QR code SVGs the API draws itself (2FA setup, device setup) |
| 5.3.4 | No SQL injection | ✅ | SQLAlchemy with bound parameters only; CodeQL checks it on every push |
| 5.5 | Safe deserialisation | ✅ | JSON only. The AI models are saved with joblib (pickle) **by the worker itself** into the database and are never accepted from users (Bandit B301 reviewed) |

## V7 Error handling and logging

| ASVS | Control | Status | How, and the proof |
|---|---|---|---|
| 7.1.1 | No passwords, tokens or secrets in logs | ✅ | A redaction filter, and a test that scans the logs after a full session (NFR-15). `tests/security/test_log_redaction.py` |
| 7.1.2 | No other sensitive data in logs | ✅ | Failed logins store a short email fingerprint, not the email (M12, FR-75) |
| 7.1.3 / 7.2 | Security events logged | ✅ | Logins, failures, 2FA changes, role and device changes, valve commands, bills and payments go to the audit log |
| 7.3.1 / 7.3.3 | Logs protected from tampering | ✅ | Append-only audit table with a SHA-256 hash chain, checked daily; a broken chain raises a critical alert. `tests/security/test_tamper_evident_audit_chain.py` (FR-73) |
| 7.4.1 | Errors show no internals | ✅ | Plain-language messages; no stack traces in responses |

## Outside these chapters, for completeness

- **Transport (V9):** TLS 1.2+ everywhere, TLS 1.3 preferred; port 1883 is closed; testssl.sh in CI (`make tls-check`, NFR-07).
- **Headers (V14.4):** HSTS, Content-Security-Policy, X-Content-Type-Options, Referrer-Policy, frame-ancestors none (Caddy).
- **Dependencies (V14.2):** pip-audit, pnpm audit, Trivy and Dependabot ([scan exceptions](scan-exceptions.md)).
