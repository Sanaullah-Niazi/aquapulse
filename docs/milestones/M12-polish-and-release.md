# M12: privacy, security scans, polish and v1.0

> **Goal:** finish the project so it is safe to show: your data belongs to you, the code is scanned for security problems on every push, and v1.0.0 can be released.
> **Requirements covered in part 1:** FR-75 (privacy: download my data, delete my account, old raw data deleted after 13 months) and FR-74 completed (dependency audit, Bandit, CodeQL, Trivy, Dependabot for every part).
> **Part 2** (later): `make demo`, the README and docs polish, the test report and requirements matrix, and the v1.0.0 release.
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
