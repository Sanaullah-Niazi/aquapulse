# M12: privacy, security scans, polish and v1.0

> **Goal:** finish the project so it is safe to show: your data belongs to you, the code is scanned for security problems on every push, and v1.0.0 can be released.
> **Requirements covered in part 1:** FR-75 (privacy: download my data, delete my account, old raw data deleted after 13 months) and FR-74 completed (dependency audit, Bandit, CodeQL, Trivy, Dependabot for every part).
> **Part 2** ([jump](#part-2-make-demo-scans-and-reports)): `make demo`, the ZAP scan, the test report, the requirements matrix, the ASVS checklist and the README. The v1.0.0 release comes last.
> **Time:** about 15 minutes.

## Part 1a: privacy (FR-75)

| What | Where | Simple explanation |
|---|---|---|
| **Download my data** | Web: Profile → Your data. App: Profile → Download my data | One JSON file with your details, your homes (with move-in and move-out dates), your daily water use and your bills with their payments. Anyone can download their own data |
| **Delete my account** | Web: Profile → Your data. App: Profile → Delete my account | Residents only. You type your password and the word `DELETE`. Your name, email and phone are removed and you are logged out on every device. Bills stay with the home, with "Deleted resident" instead of your name, because the owner must keep them for accounting |
| **13-month limit** | Database | Raw readings (`telemetry`, `tank_readings`) older than 395 days are deleted by TimescaleDB every day. The 15-minute totals are kept, so old charts and bills still work |
| **No email in failed-login logs** | Audit log | A failed login now stores a short fingerprint of the email (part of a SHA-256 hash), not the email itself. You can still see repeated attempts on the same account |

Why owners and System Admins cannot delete themselves here: an owner's buildings and bills would be left without anyone responsible. A System Admin disables an owner instead (Admin → Owners).

### Try it

```bash
cd "/Users/niazi_127.0.0.1/Documents/Water Management System/aquapulse"
git switch main && git pull
make up
```

1. Open https://localhost:8443/app/, log in as `resident01@example.com`, go to **Profile** → **Download my data**. Open the file: you see your details, your home and a line per day of water use.
2. Delete an account you do not need for the demo: log out, log in as `gulf01@example.com` (second demo building, same password). **Profile** → under **Delete my account**, type your password and `DELETE`, then press the red button. You are logged out, and logging in again fails.
3. Log in as `owner@example.com`, switch to the Gulf building → **Bills**: unit 1's bills are still there, billed to "Deleted resident".
4. `make psql` then `SELECT hypertable_name, config FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention';` shows the 395-day rule on both tables (type `\q` to quit).

Deleting cannot be undone. The only way to get `gulf01` back is to start from empty with `make reset && make up && make demo-data` (this deletes all data).

## Part 1b: security scans (FR-74)

A new GitHub workflow, **Security**, runs on every push, every pull request, and every Monday (new problems are found in old packages all the time).

| Job | Tool | What it checks | Fails when |
|---|---|---|---|
| Dependency audit | `pip-audit`, `pnpm audit` | Every Python and web package we use, at the exact locked version, against public lists of known vulnerabilities | Python: any known problem. Web: High or Critical |
| Python code check | Bandit | Risky Python code (for example running shell commands or weak random numbers). Ruff's security rules also run in the normal CI | High severity |
| CodeQL | GitHub CodeQL | Deeper analysis of the Python, TypeScript and workflow code, for example user input reaching a database query | Results show in the repository's **Security** tab |
| Container scan | Trivy | The Docker images we build (backend, simulator, web), including the Linux packages inside | High or Critical problems that have a fix |
| Secret scan | gitleaks (already in CI since M0) | The full git history for passwords and keys | Any secret |

**Dependabot** now also watches the web app's packages, its build image and the Android app's Dart packages. It opens a pull request when an update or a security fix is out; CI then tells you whether it is safe to merge.

The first run already found real problems (old `pypdf`, `react-router`, `vite` and `vitest` versions) and they are updated in this milestone. If a problem ever cannot be fixed yet, it may be accepted only with a written reason in [docs/security/scan-exceptions.md](../security/scan-exceptions.md).

### See it

On GitHub: **Actions** → **Security** shows the latest run. **Security** → **Code scanning** shows CodeQL's results.

## Part 2: `make demo`, scans and reports

### One command for the whole demo

```bash
cd "/Users/niazi_127.0.0.1/Documents/Water Management System/aquapulse"
git switch main && git pull
make demo
```

It runs `make up`, `make seed` and `make demo-data` (90 days), then makes the bills of the last two
months from the real readings, issues them and records cash payments: the older month is fully paid,
last month only for every second home. So the unpaid list, the reminders and "Pay online" have
something to show. It is safe to run again: issued bills never change and nothing is paid twice.
At the end it prints the logins.

The first run takes longest, because Docker builds the images and the history is loaded.

### New checks

| Command | What it does | In CI |
|---|---|---|
| `make zap` | OWASP ZAP looks at the running web app like an attacker's scanner would (passive, nothing is attacked): security headers, cookies, leaked versions. Fails on any High risk finding and lists the rest. Report: `infra/zap/zap-report.html` | yes, in the stack job |
| `make rtm` | Rewrites [docs/requirements-traceability.md](../requirements-traceability.md): for every requirement, the test files that name it. All 75 have at least one | yes: CI fails if the file is out of date |

### The reports

- [docs/test-report.md](../test-report.md): how many tests each part has, the coverage, and every
  non-functional requirement marked measured, partly, not yet, or "needs a person". The not-yet ones
  are listed honestly at the end.
- [docs/security/asvs-checklist.md](../security/asvs-checklist.md): the OWASP ASVS Level 2 controls for
  login, sessions, access control, validation and logging, each with how it is met and the test that
  shows it. Four are only partly met, and the reason is written next to each.

### Also in part 2

- The Android setup script now also installs the NDK and CMake. Without them, the first `make apk`
  downloads about 1 GB in the middle of the build without showing progress, and a slow connection
  can hang it.
- The app's "change password" help said 12 characters; the rule is 10 (FR-68). Fixed.

## Part 3: daily database backups (NFR-18)

A new service, `backup`, copies the database into the `backups/` folder every day
and keeps the last 7. The System Admin's health page shows when the last one was made.
Full guide: [docs/backups.md](../backups.md).

### Try it

```bash
git pull
make up              # starts the new backup service; it makes the first backup straight away
make backups         # you should see one file
make restore-check   # restores it into a test database and compares: all ✅
```

Then log in as admin@example.com: **System health** shows the time under **Last backup**.

| Command | What it does | In CI |
|---|---|---|
| `make backup` | A backup now | yes |
| `make backups` | List the backups | |
| `make restore-check` | Restore the newest backup into a separate test database, compare the row counts, delete it again. Changes nothing | yes |
| `make restore FILE=...` | Replace all data with a backup (asks first, and makes one more backup before it starts) | yes: a real restore, then the smoke test again |

CI now also runs every Monday, so the restore test runs at least weekly even when nothing is pushed.
