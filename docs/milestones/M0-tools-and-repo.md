# M0: Tools and repository

> **Goal:** install every tool you need on your Mac, and put the AquaPulse skeleton on GitHub with a green CI badge.
> **Requirements covered:** FR-74 (first part: secret protection with gitleaks, pre-commit and `.env.example`), NFR-12 (no secrets in git history), NFR-30 (everything free).
> **Time:** about 1 to 2 hours, mostly waiting for downloads.

## What you will have at the end

- All M0 tools installed, checked by one command (`make check-tools`).
- A public GitHub repo `Sanaullah-Niazi/aquapulse` with README, MIT license, SRS, folder structure and community files.
- A **green CI badge**: GitHub Actions checks every push for file problems and leaked secrets.
- A safety net on your Mac: if you ever try to commit a password or token, the commit is **blocked** before it leaves your computer.

---

## Part 1: Install the tools

Open the **Terminal** app (Cmd+Space, type "Terminal"). Copy each block, paste it, press Enter, and wait until it finishes before the next one.

### 1.1 Apple command line tools

```bash
xcode-select --install
```

**What it is:** Apple's compilers plus `git` and `make`. A window pops up; click **Install**. If it says "already installed", that's fine.

### 1.2 Homebrew (the Mac "app store" for developer tools)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

It asks for your Mac password (typing shows nothing, that's normal). At the end it prints **"Next steps"** with two commands. On an M1 Mac they look like this; run them:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Check: `brew --version` prints a version number.

### 1.3 Command line tools for this project

```bash
brew install git gh uv fnm gitleaks openssl@3 mosquitto
```

| Tool | What it does for us |
|---|---|
| `git` | Saves the history of every change to our code |
| `gh` | GitHub from the terminal: create the repo, releases, pull requests |
| `uv` | Installs Python and Python packages, very fast |
| `fnm` | Installs Node.js (needed for the React web app) |
| `gitleaks` | Finds passwords and API keys before they are committed |
| `openssl@3` | Makes TLS certificates (used in M5) |
| `mosquitto` | Gives us `mosquitto_pub` / `mosquitto_sub` to test MQTT from the terminal (the broker itself will run in Docker) |

### 1.4 Desktop apps

```bash
brew install --cask visual-studio-code docker-desktop mqtt-explorer bruno dbeaver-community
```

If Homebrew says `docker-desktop` is not found, your Homebrew is older: use `docker` instead of `docker-desktop`.

Then:

1. Open **Docker** from Applications. Accept the terms. You can skip signing in.
2. Docker → **Settings → Resources**: set CPUs to **4** and Memory to **8 GB**. Click **Apply & restart**.
3. Test it: `docker run --rm hello-world` should print "Hello from Docker!".

**Why Docker?** Each part of AquaPulse (database, MQTT broker, API ...) runs in its own small "container". Docker lets us start the whole system with one command, and it runs the same on your Mac and on any server.

### 1.5 Python 3.12, Node.js 22 and pnpm

```bash
uv python install 3.12
echo 'eval "$(fnm env --use-on-cd)"' >> ~/.zshrc
source ~/.zshrc
fnm install 22
fnm default 22
corepack enable pnpm
uv tool install pre-commit
uv tool update-shell
```

Close Terminal and open it again, so the new settings load.

| Tool | What it does for us |
|---|---|
| Python 3.12 | Language for the backend, simulator and AI |
| Node.js 22 + pnpm | Run and build the React web app |
| pre-commit | Runs our checks automatically before every commit |

### 1.6 VS Code setup

1. Open **Visual Studio Code**.
2. Press **Cmd+Shift+P**, type **"shell command"**, and choose **"Shell Command: Install 'code' command in PATH"**. Now `code .` in Terminal opens the current folder.
3. Extensions: you don't need to install them one by one now. When you open the AquaPulse folder, VS Code shows **"Install recommended extensions?"** Click **Install**. (The list is in `.vscode/extensions.json`.)

### 1.7 Git identity and GitHub login

```bash
git config --global user.name "Sanaullah Khan Niazi"
git config --global user.email "YOUR_GITHUB_EMAIL"
git config --global init.defaultBranch main
gh auth login
```

For `gh auth login`, choose: **GitHub.com → HTTPS → Yes (authenticate Git) → Login with a web browser**. Copy the code it shows, press Enter, and paste the code in the browser.

> 💡 Use the email GitHub knows you by. To keep your real email private, use the `...@users.noreply.github.com` address from GitHub → Settings → Emails.

---

## Part 2: Create the repository

### 2.1 Unpack the skeleton

Download `aquapulse-m0.zip` from our project thread and unzip it into a projects folder:

```bash
mkdir -p ~/Projects
cd ~/Projects
unzip ~/Downloads/aquapulse-m0.zip
cd aquapulse
code .
```

### 2.2 Check your tools

```bash
make check-tools
```

You should see a ✅ for every line and **"All M0 tools are ready. 🎉"**. If something shows ❌, go back to the matching step in Part 1.

### 2.3 First-time setup

```bash
git init
make setup
```

- `git init` turns the folder into a git repository.
- `make setup` copies `.env.example` to `.env` (your private settings file) and installs the **pre-commit hooks**.

### 2.4 First commit and push to GitHub

```bash
git add .
git commit -m "chore: M0 project skeleton, SRS and CI"
gh repo create aquapulse --public --source=. --remote=origin --push \
  --description "Open-source smart water management for multi-home buildings: ESP32, MQTT/TLS, FastAPI, TimescaleDB, React, Flutter, AI leak detection"
```

During `git commit` you'll see the checks run and all say **Passed**. The first run takes a minute while it downloads the check tools.

`gh repo create` creates the GitHub repo and uploads your code in one step.

### 2.5 Watch CI turn green

```bash
gh run watch
```

Or open `https://github.com/Sanaullah-Niazi/aquapulse/actions`. Both jobs (**File hygiene** and **Secret scan**) should get a green tick in about a minute. The badge at the top of your README turns green.

### 2.6 Turn on free GitHub security features

On GitHub, open your repo → **Settings → Code security** (called "Security" or "Advanced Security" in some layouts) and enable:

- **Dependabot alerts** and **Dependabot security updates**
- **Secret Protection / Push protection** (free for public repos): GitHub also blocks pushes that contain secrets
- **Private vulnerability reporting** (makes our `SECURITY.md` link work)

Then **Settings → General → Features**: tick **Issues**. In the **About** box on the repo home page (gear icon), add topics: `iot`, `esp32`, `mqtt`, `fastapi`, `timescaledb`, `react`, `flutter`, `smart-water`, `digital-twin`.

---

## Part 3: See the safety net work (the demo for this milestone)

Let's prove that a leaked secret is blocked. We make a **fake** GitHub token. It's random and harmless, but it has the same format as a real one.

```bash
echo "GITHUB_TOKEN=ghp_$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 36)" > leak-test.txt
git add leak-test.txt
git commit -m "test: try to leak a secret"
```

You should see **"Detect hardcoded secrets ... Failed"** and **`RuleID: github-pat`**. The commit did **not** happen. Clean up:

```bash
git reset leak-test.txt
rm leak-test.txt
```

📸 **Screenshot this** and the green CI run for your portfolio notes.

---

## How it works (in simple words)

**Git and GitHub.** Git keeps a history of every change (a "commit"). GitHub stores that history online, so it's backed up and others can see it.

**pre-commit.** A small program that runs checks every time you type `git commit`. Our checks are listed in `.pre-commit-config.yaml`:

- remove extra spaces at line ends, make files end with a newline, use Unix line endings;
- check that YAML, JSON and TOML files are valid;
- block files bigger than 1 MB (so nobody commits videos or databases by mistake);
- block private keys and, with **gitleaks**, any password, token or API key.

**gitleaks.** It knows the patterns of hundreds of secret types (GitHub, AWS, Stripe, Firebase keys and many more). It runs in two places: on your Mac before each commit, and in GitHub Actions over the **whole history**. That's why a secret can't sneak in from someone else's computer either.

**`.env` and `.env.example`.** Real settings and secrets go in `.env`, which is listed in `.gitignore`, so git never uploads it. `.env.example` is the public template that shows *which* settings exist, without real values.

**GitHub Actions (CI).** "Continuous Integration" means a fresh computer in the cloud runs our checks on every push. The file `.github/workflows/ci.yml` describes the jobs. Public repos get these minutes free.

**Makefile.** A list of short names for longer commands. `make help` shows them all.

**Dependabot.** A GitHub robot that opens a pull request when one of our dependencies has an update or a security fix.

---

## Files in this milestone

| File | Purpose |
|---|---|
| `README.md` | Project front page. It only describes what really works so far |
| `LICENSE` | MIT license |
| `docs/SRS.md` | The approved requirements |
| `docs/adr/` | Architecture decisions: monorepo, tech stack |
| `docs/milestones/M0-tools-and-repo.md` | This guide |
| `.gitignore` | Files git must never upload (secrets, caches, builds) |
| `.gitattributes`, `.editorconfig` | Consistent line endings and formatting |
| `.env.example` | Settings template |
| `.pre-commit-config.yaml`, `.gitleaks.toml` | Commit checks and secret scanning |
| `Makefile`, `scripts/check-tools.sh` | Handy commands and the tools checker |
| `.github/workflows/ci.yml` | CI pipeline |
| `.github/dependabot.yml` | Automatic dependency updates |
| `.github/pull_request_template.md`, `.github/ISSUE_TEMPLATE/` | Templates for PRs and issues |
| `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` | Community files that GitHub shows on the repo page |
| `.vscode/extensions.json` | Recommended VS Code extensions |
| `contracts/`, `simulator/`, `backend/`, `web/`, `mobile/`, `firmware/`, `infra/` | Empty for now, each with a README saying which milestone fills it |

## Done criteria

- [ ] `make check-tools` shows all ✅
- [ ] Repo is public at `github.com/Sanaullah-Niazi/aquapulse`
- [ ] CI is green (both jobs)
- [ ] The fake-secret commit was blocked on your Mac
- [ ] Dependabot alerts, push protection and private vulnerability reporting are on

When all five are ticked, reply **"OK, next"** and we start **M1: Infrastructure** (Docker Compose with Mosquitto, TimescaleDB, Caddy and Mailpit).

## Troubleshooting

| Problem | Fix |
|---|---|
| `brew: command not found` | Run the two "Next steps" lines from 1.2, then open a new Terminal |
| `make: command not found` | Run `xcode-select --install` |
| `pre-commit: command not found` | Run `uv tool update-shell`, then open a new Terminal |
| `node: command not found` | Check `~/.zshrc` has the `fnm env` line; open a new Terminal |
| ❌ Docker engine in `check-tools` | Open the Docker app and wait for "Engine running" |
| `gh repo create` says the name already exists | You already created it: run `git remote add origin https://github.com/Sanaullah-Niazi/aquapulse.git && git push -u origin main` |
| CI "File hygiene" fails | Run `make lint` locally; it fixes most problems automatically, then commit again |
