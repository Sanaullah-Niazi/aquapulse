# AquaPulse threat model (STRIDE)

> **In simple words:** a list of the ways someone could attack or misuse
> AquaPulse, and what stops them. The full model is in the SRS, section 11.4.
> This file records the **reviews**: what is really in place at each
> milestone, checked against the code and tests, not just planned.
> New features must add their threats here before they are merged.

**STRIDE** = **S**poofing (pretending to be someone else), **T**ampering
(changing data), **R**epudiation (denying you did something), **I**nformation
disclosure (seeing what you shouldn't), **D**enial of service (stopping it
working), **E**levation of privilege (doing more than you are allowed).

## Review 1: milestone M5 (2026-10-30)

Scope: everything built in M0 to M5: the Docker stack, simulator, API,
ingestor, the MQTT broker with TLS and per-device passwords, and Caddy with
HTTPS. The web app, mobile app, billing, payments and firmware do not exist yet.

Status: **Done** = in place and tested · **Partly** = some of it in place ·
**Later** = the milestone that brings it.

| ID | Threat (short) | Status at M5 | Evidence (what proves it) |
|----|----------------|--------------|---------------------------|
| T-01 | Fake device sends fake readings | **Done**: own password per device (32 random bytes, only a hash stored), TLS, a device may only publish on its own topics. Anomaly alerts **Later** (M7). | `tests/broker/test_broker_acl.py`, smoke "home-test-01 may not publish to home-b01-02's topic" |
| T-02 | Someone sends "close valve" to many homes | **Done**: only the `commander` account may publish commands; the ingestor's reading account cannot; devices cannot. Commands expire after 30 s. | `test_only_the_command_account_can_send_commands`, M4 command tests |
| T-03 | Sniffing MQTT on the Wi-Fi | **Done**: TLS 1.2+ only (1.3 negotiated), modern ciphers only, port 1883 closed. | `tests/broker/test_broker_tls.py`, `make tls-check` (testssl.sh) |
| T-04 | Recorded command replayed | **Done**: cmdId dedupe + expiry (M4), and TLS stops recording. | M4 command tests, simulator FR-13 tests |
| T-05 | Password guessing on login | **Partly**: Argon2id, rate limits, common-password check (M3). 2FA **Later** (M8). | `tests/security/test_password_security.py` |
| T-06 | Forged or stolen API tokens | **Done** (M3): fixed algorithm, aud/iss, 15 min expiry, refresh rotation with reuse detection. | `tests/security/test_token_security.py` |
| T-07 | Resident opens another home (IDOR) | **Done**: object checks, 404 for foreign objects, every endpoint x role tested, including the new device endpoints. | `tests/security/test_rbac_matrix.py` |
| T-08 | Mass assignment (`role: owner`) | **Done**: request models forbid unknown fields. | `tests/security/test_input_validation_and_rate_limiting.py` |
| T-09 | SQL injection | **Done**: parameterised SQL only. Schemathesis fuzzing **Later** (M12). | code review, ruff/bandit rules |
| T-10 | XSS in the web app | **Partly**: strict CSP and nosniff on every page from Caddy (M5). The web app itself is **Later** (M8). | smoke "security headers", `infra/caddy/Caddyfile` |
| T-11 | Bills changed afterwards | **Done** (M6): issued bills are never edited, only voided with a reason (audited) and replaced; the API's database role cannot DELETE bills or payments; tariff changes are new versions and cannot reach back into issued months. | `tests/billing/test_bill_lifecycle.py`, `test_tariff_configuration_with_versioning.py` |
| T-12 | Fake "payment success" | **Done** (M6): only a Stripe webhook with a valid signature marks a bill paid (the return page changes nothing); bad signatures are refused and logged; each Stripe session is recorded once; only test keys are accepted. | `tests/billing/test_stripe.py` |
| T-13 | Owner denies cutting off water | **Partly**: reason required, audit log with user, IP, time (M3, M4). Hash chain **Later** (M7). | `tests/api/test_audit_log.py`, valve-lock tests |
| T-14 | Admin deletes audit entries | **Partly**: the API's database role cannot UPDATE/DELETE audit rows. Hash chain **Later** (M7). | smoke "the API's database role cannot delete history" |
| T-15 | Flooding the broker | **Partly**: 2 KB message limit, max 1000 connections, max keepalive 120 s, login required before anything, the ingestor queue pushes back instead of growing. Docker resource limits **Later** (M12). | `infra/mosquitto/mosquitto.conf` |
| T-16 | Expensive API queries | **Done**: 7 days raw / 400 days aggregates, rate limits. | `tests/api/test_live_data_and_consumption.py` |
| T-17 | Water cut off wrongly | **Partly**: owner lock needs a reason and emails the residents, everything audited (M4). Non-payment shut-off only with the owner's policy, an overdue bill and a 48-hour warning (M6). Auto shut-off limits **Later** (M7). | `tests/api/test_valve_control.py`, `tests/billing/test_nonpayment_shutoff.py` |
| T-18 | Pump run with empty sump | **Done** in the simulator (device-side safety overrides commands); real firmware **Later** (M11). | simulator tank tests |
| T-19 | Building average reveals a neighbour | **Later** (M9). | |
| T-20 | Secrets in logs or backups | **Partly**: logs never contain payloads, passwords or tokens; device passwords are never stored in plain text or logged (M5). Encrypted backups **Later** (M12). | `tests/security/test_log_redaction.py`, `test_plain_password_is_never_stored_or_logged` |
| T-21 | Device password leaked from DB or screen | **Done**: shown once (API response + QR), only a hash stored, rotate and revoke in one call, broker applies it within about a second. | `tests/api/test_device_provisioning_and_credential_rotation.py`, smoke "Devices" checks |
| T-22 | Malicious dependency or image | **Partly**: lock files, pinned images, Dependabot, gitleaks. Image scanning **Later** (M12). | `.github/` |
| T-23 | Stolen ESP32 | **Done** for the server side: a stolen device's password only works for its own topics and can be revoked in one call. Flash encryption **Later** (M11). | revoke tests |
| T-24 | Simulator control API misused | **Done**: published on 127.0.0.1 only; the simulator has no rights beyond its own devices' topics. | `docker-compose.yml` |
| T-25 | Compromised System Admin | **Partly**: SA cannot control valves or register devices; all SA actions audited. Mandatory 2FA **Later** (M8). | RBAC matrix |

### New in M5, and what was decided

* **Project CA instead of a public CA on the Mac.** `make certs` makes our
  own small certificate authority. Its private key stays in
  `infra/certs/ca/` and is mounted into **no** container. Online (M12), Caddy
  gets a free Let's Encrypt certificate instead.
* **Simulated devices** log in with passwords made from `SIM_DEVICE_SECRET`
  (HMAC-SHA256). Risk: whoever has that secret can act as any *simulated*
  device. Accepted for the demo; on a server with only real devices the
  setting is left empty and those accounts do not exist. Real devices always
  get a random password.
* **`home-test-01`**, a device account for automated tests, follows the same
  rule. It can only use its own topics, and the server ignores its messages
  because it is not a registered device.
* **Password list reload.** The broker re-reads its password list on SIGHUP
  and disconnects clients whose password is no longer valid. Measured in the
  smoke test: rotation and revoke take effect in about 1 s (FR-14 asks for 10 s).
* **Swagger UI** needs a wider CSP (inline script, jsDelivr CDN). Only
  `/api/docs` gets it; all other pages get the strict policy.
* **HSTS** is 1 hour on the Mac, so other projects on `localhost` are not
  forced to HTTPS for long. The public server will use one year (M12).

### New in M6 (billing and payments)

* **Webhook is public, but signed.** `POST /api/v1/webhooks/stripe` needs no
  login (Stripe calls it), so the Stripe signature is the only proof. It is
  checked before anything is read, and the bill is updated with the database
  row locked, so two copies of the same message cannot pay twice.
* **No card data here.** Card numbers are typed on Stripe's own page; our
  server only sees "session X paid amount Y".
* **Receipt uploads** (bank slips): 2 MB max (Caddy allows 3 MB on that path
  only, 1 MB elsewhere), the first bytes must really be JPEG, PNG or PDF, and
  they are served back with `nosniff`, so a browser never runs them as a page.
* **Residents see only their own issued bills**; a previous or next tenant of
  the same home gets 404 (tested).

### Left open after this review

| Risk | Why it is acceptable now | When |
|------|--------------------------|------|
| One server = single point of failure (T-15) | Portfolio project, documented | M12 (backups, monitoring) |
| A DB superuser could rewrite the audit log | Hash chain not built yet | M7 |
| No 2FA yet | No public deployment yet | M8 |

Next review: **M12**, before the public demo.
