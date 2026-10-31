# M10: AI (leaks, unusual use, forecasts)

> **Goal:** AquaPulse notices a leak by itself, says **why** in plain words, and can turn the water off. Later parts add unusual-use flags and a 7-day forecast with a month-end bill estimate.
> **Built in 3 parts:** part 1 is leak and burst detection plus automatic shut-off. Part 2 adds forecasts, unusual use and the model registry. Part 3 adds the screens in the web app and the Android app. **M10 is complete.**

---

# M10 part 1: leak detection, explanations, automatic shut-off

> **Requirements covered:** FR-63 (leak detection), FR-66 (explainable insights), FR-61 (optional automatic shut-off).
> **Time:** about 15 minutes.

## The idea in simple words

A home's water normally **stops**. Taps, showers and toilets run for a few minutes, then the flow goes back to 0. A leak (a running toilet, a dripping pipe) never stops. So the detector follows the current **run**: the time since the flow was last 0.

| Detector | Fires when (defaults) | Alert |
|---|---|---|
| **Night flow** | the run covers 60 minutes of the quiet window, 02:00 to 05:00, without ever reaching 0 | `leak` "Possible leak" (warning) |
| **Continuous flow** | the run has lasted 2 hours, at any time of day | `leak` "Possible leak" (warning) |
| **Burst** | the flow stayed above the home's normal maximum for 5 minutes. Normal maximum = the 99.9th percentile of its flow over the last 30 days, at least 24 L/min | `burst` "Burst pipe suspected" (critical) |

Night flow and continuous flow combine into a **confidence** from 0 to 1. It grows the longer the water runs: about 0.5 after 2 hours, 0.8 after 4 hours. A burst is always high confidence.

Every alert says why, from the real numbers (FR-66). Two examples from the evaluation report:

> Water kept flowing between 02:00 and 03:00 (lowest 1.0 L/min). Normally this home uses 0.0 L/min at night.

> Very high flow, up to 39.3 L/min, for 5 min since 07:48. This home's normal maximum is 24.0 L/min. This looks like a broken pipe.

The alert also keeps the **evidence**: the readings of the run (a small chart, at most 60 points), the lowest flow, the litres and the confidence. `GET /api/v1/homes/{id}/insights` returns them for the screens of part 3.

Owners can change the numbers per building (Alert rules page, M8): night minutes, continuous hours, burst minutes. They can also switch the two rules off.

## How good is it? (real numbers, made by a script)

[docs/ai-evaluation.md](../ai-evaluation.md) is written by `make ai-eval`, never by hand:

1. The simulator makes a **labelled test set**: 10 homes, 60 days, a reading every minute, with 50 slow leaks and 12 bursts planted at random homes and times (`aquapulse-sim eval-set`).
2. The backend replays it through exactly the same detector code as the worker, without seeing the labels, and counts what it found (`python -m app.ai.evaluate`).
3. The report gives recall, precision and time to alert against the SRS targets (recall ≥ 90%, precision ≥ 80%, slow leaks ≤ 2 h, bursts ≤ 10 min).

CI runs it on every push. It fails if a target is missed, or if the committed report is not the one the code makes. So the numbers in the README can be trusted.

## Automatic shut-off (FR-61)

Off by default. The owner switches it on per home:

```bash
# (or on the screen from part 3: Homes → a home → Automatic shut-off)
curl -k -X PATCH https://localhost:8443/api/v1/homes/<HOME_ID> \
  -H "Authorization: Bearer <OWNER_TOKEN>" -H "Content-Type: application/json" \
  -d '{"auto_shutoff_enabled": true}'
```

When it is on and the detector is sure (a burst, or a leak with confidence 0.8 or more):

- the worker closes the valve as the user `system`, recorded in the audit log like any command;
- the owner and the resident get an email (and a push, if set up) "Water turned off";
- the resident can open the valve again in the app, unless the owner has **locked** it. A locked valve is never touched;
- it happens at most **once per hour** per home, so a leak that goes on after the resident opened the valve again does not switch it on and off all day.

## Get it

```bash
cd "/Users/niazi_127.0.0.1/Documents/Water Management System/aquapulse"
git switch main && git pull
make up            # the migration (0009) runs by itself
make demo-data     # if you have not loaded history yet
```

## Try it

1. **A burst:** `make problem TYPE=burst DEVICE=home-b01-03 MINUTES=15`. After about 5 minutes the alert "Burst pipe suspected" appears in the web app (Alerts) with its reason, and Mailpit (http://localhost:8025) has the email.
2. **A slow leak:** `make problem TYPE=leak DEVICE=home-b01-04 MINUTES=180`. After 2 hours "Possible leak" appears. It is quicker at night: started at 01:30, it shows at about 03:00.
3. **The evidence:** open `https://localhost:8443/api/docs`, log in as the owner, and try `GET /homes/{home_id}/insights` for that home.
4. **Automatic shut-off:** switch it on for unit 03 (above), run the burst again, and watch the valve close by itself in the app, with an email to the owner and the resident.
5. **The scores:** `make ai-eval` (about 3 minutes) prints the table and rewrites `docs/ai-evaluation.md`.

## Run the tests

```bash
make api-test      # includes tests/ai and tests/alerts/test_auto_shutoff.py
make ai-eval       # the evaluation (fails if a target is missed)
```

New tests: normal use is never a leak, night flow after 60 minutes, continuous flow after 2 hours, a zero reading or a long silence ends the run, bursts against the home's normal maximum, an earlier burst does not raise that maximum, owner limits, explanations that change with the data, evidence for a chart, scoring of found, missed and false alarms. With the database: the simulator's burst raises a critical alert with its reason; auto shut-off never happens when it is off; when on it closes the valve within 60 s and tells the owner and the resident; never twice in an hour; a leak closes the valve only when sure; a locked valve is left alone; the insights API shows only to who may see the home.

## What is new in the code

```text
backend/app/ai/leak.py          the three detectors, confidence and explanations (pure Python)
backend/app/ai/monitor.py       runs them on every live reading inside the worker; daily baselines
backend/app/ai/shutoff.py       automatic shut-off (FR-61)
backend/app/ai/evaluate.py      scores the detector, writes docs/ai-evaluation.md
backend/app/routers/ai.py       GET /homes/{id}/insights
simulator/.../evalset.py        the labelled test set (aquapulse-sim eval-set)
backend/migrations/versions/0009_ai_leak_detection.py   alerts.details (confidence + evidence)
```

## Done criteria (part 1)

- [ ] A burst made "Burst pipe suspected" appear within about 5 minutes, with a reason that has real numbers.
- [ ] With automatic shut-off on, the valve closed by itself and both people got an email.
- [ ] `make ai-eval` shows every target met.
- [ ] `make api-test` passes.

## Troubleshooting (part 1)

| Problem | Fix |
|---|---|
| No leak alert after 2 hours | Check that the `leak` rule is on (web app, Alert rules), and that the water was really running all the time (`make live`). One zero reading starts the count again |
| The burst is called "Possible leak" | The flow was below the home's normal maximum (at least 24 L/min). Use a stronger burst: `make problem TYPE=burst DEVICE=home-b01-03 MINUTES=15` picks 25 to 40 L/min |
| The valve did not close by itself | Automatic shut-off is off by default, a locked valve is never touched, and it happens at most once per hour per home |

---

# M10 part 2: forecasts, unusual use, the model registry

> **Requirements covered:** FR-64 (unusual use), FR-65 (forecast and month-end bill), FR-66 (reasons), FR-67 (retraining and the model registry).
> **Time:** about 15 minutes.

## The forecast (FR-65)

Water use has a weekly rhythm: weekends are higher. **Holt-Winters** (exponential smoothing with a 7-day season) learns a home's usual level and weekly pattern, giving recent days more weight, and forecasts the next 7 days with an **80% range**: on 8 days out of 10 the real litres should fall inside it. With less than 3 weeks of history it cannot learn the weekly pattern, so it uses the simple rule "same weekday last week" instead.

The **month-end estimate** adds the forecast for the rest of the month to the litres used so far, and runs that through the **same billing engine as the real bills**, with the building's own tariff and currency. So the estimate follows the slabs. It always says "Not a bill". Without water rates for this month, there is no estimate (and the reason says so).

- `GET /api/v1/homes/{id}/forecast`: owner, admin, and the home's resident (from their move-in day only).
- `GET /api/v1/buildings/{id}/forecast`: owner and admin. The bill is the sum of every home's own estimate (slabs depend on each home's litres, not on the building total).

## Unusual use (FR-64)

Every home-day becomes a few numbers: how its litres compare with the home's **usual day** in the 7 days before (the median, so one party does not hide the next unusual day), the weekday, and the lowest night flow. An **Isolation Forest** per building learns what normal days look like. A day is flagged when the forest finds it strange **and** its litres are at least 1.4 times the usual day (or at most half). Honest note: in the evaluation the litres check does most of the work; the forest keeps out days that are big but normal for that home.

Each flag becomes an "Unusual water use" alert (info) with its reason and the model version, for example:

> Used 530 L on Sat 28 Mar, 2.0 times this home's usual day in the 7 days before (267 L).

It is about a day that is over, so the alert closes again by itself; it stays in the alert history and the insights. Owners can switch the rule off on the Alert rules page.

## Retraining and the model registry (FR-67)

Every night from 01:00 building time, the worker:

1. reads the last 60 days of each home's water use;
2. trains a new unusual-use model and checks it on the last 14 days: how many real days it flags (probably false alarms) and how many copies of those days with the water doubled it catches;
3. back-tests the forecast on the last 3 weeks: Holt-Winters against "same weekday last week", and keeps the better method;
4. saves both as new **versions** in the `ai_models` table, with their scores. A version goes **live only if it is not worse** than the live one. A worse one is kept, marked "kept out", so you can see why;
5. scores yesterday with the live unusual-use model and raises the alerts.

With too little history (under about 2 weeks) it trains nothing and tries again the next night. `GET /api/v1/buildings/{id}/ai/models` lists every version with its scores (owner and admin).

## How good is it?

`make ai-eval` (now about 5 minutes) uses a 90-day test set with 30 planted unusual days ("guests": the water of 1 or 2 more households), and writes [docs/ai-evaluation.md](../ai-evaluation.md):

| Measure | Result | Target |
|---|---|---|
| Unusual days flagged | 100% (25 of 25 scored) | ≥ 80% |
| False flags per home per 2 weeks | 0.30 | ≤ 1 |
| Forecast error (MAPE), Holt-Winters | 16% | ≤ 20%, and lower than the simple rule (19%) |
| Real litres inside the 80% range | 85% | about 80% |

Leak detection is unchanged: 100% found, 100% real, slow leaks in 2 h, bursts in 5 min.

## Get it

```bash
cd "/Users/niazi_127.0.0.1/Documents/Water Management System/aquapulse"
git switch main && git pull
make up            # the migration (0010, the ai_models table) runs by itself
make demo-data     # 90 days of history, if you have not loaded it yet
```

## Try it

1. **The forecast:** open `https://localhost:8443/api/docs`, log in as the owner, and try `GET /homes/{home_id}/forecast`. You get 7 days with a low and a high, the month-end litres and the bill estimate in the building's currency. Try `GET /buildings/{building_id}/forecast` too.
2. **The models:** the worker trains after 01:00 building time. To see it now, restart the worker after 01:00 (`docker compose restart worker`) and try `GET /buildings/{building_id}/ai/models`: one forecast and one unusual-use version, live, with their scores.
3. **The scores:** `make ai-eval`.

The screens for all of this come in part 3.

## Run the tests

```bash
make api-test      # includes tests/ai
make ai-eval
```

New tests: the forecast learns the weekly pattern, falls back to the simple rule under 3 weeks, the back-test, month-end sums; unusual days are found and normal ones (even of a big family) are not, one unusual day does not hide the next, reasons use the real numbers, saved models load back the same; with the database: the nightly job trains and saves both models once per building day, a worse model is kept out and the live one stays, too little data trains nothing, yesterday's unusual day raises an explained alert, the owner can switch it off, the worker runs it once a day; the forecast API gives 7 days with a range and a month-end bill that matches the tariff, no estimate without rates, the building sum, and who may see what.

## What is new in the code (part 2)

```text
backend/app/ai/forecast.py      Holt-Winters, the simple rule, the back-test (pure Python)
backend/app/ai/unusual.py       the Isolation Forest, its reasons and its check
backend/app/ai/registry.py      nightly training, promotion, unusual-use alerts
backend/app/ai/projection.py    the month-end bill estimate (the real billing engine)
backend/app/routers/ai.py       GET /homes/{id}/forecast, /buildings/{id}/forecast, /buildings/{id}/ai/models
backend/migrations/versions/0010_ai_models.py   the model registry table
simulator/...                   the 'abnormal' (guests) scenario and the 90-day test set
```

## Done criteria (part 2)

- [ ] `GET /homes/{id}/forecast` shows 7 days with a range and a month-end bill in the building's currency.
- [ ] After a night (or a worker restart after 01:00), `GET /buildings/{id}/ai/models` lists live versions with scores.
- [ ] `make ai-eval` shows every target met.
- [ ] `make api-test` passes.

## Troubleshooting (part 2)

| Problem | Fix |
|---|---|
| The forecast says "same weekday last week" | Less than 3 weeks of history for that home. Load history with `make demo-data` |
| The bill estimate is empty | The owner has not set water rates that cover this month (Tariffs page) |
| `ai/models` is empty | Training runs after 01:00 building time and needs about 2 weeks of data. Check `docker compose logs worker` for "AI models trained" |

---

# M10 part 3: the AI screens (web and Android)

> **Requirements covered:** the screens for FR-61 and FR-63 to FR-67.
> **Time:** about 10 minutes.

## What you see

| Who | Where | What |
|---|---|---|
| Resident | Web: menu → **AI insights**. Android: menu ☰ → **AI insights** | The forecast for the next 7 days (dashed line, with the shaded 80% range), this month's litres and the **bill estimate** in your currency, and the **insights**: leaks, bursts and unusual days, each with its reason, its confidence and a small chart of the readings |
| Owner | Web: **AI insights** in the menu | The whole building's forecast and bill estimate (every home's estimate added up), and the **AI models** table: every trained version, its scores, and which one is live (or why it was kept out) |
| Owner | Web and Android: **Homes** → a home | The home's forecast and insights, and the **Automatic shut-off** switch (FR-61) |
| Owner | Android: menu ☰ → **AI insights** | The building forecast and the model list |

"How is this worked out?" under each forecast says which method was used (Holt-Winters or "same weekday last week") and why, in plain words.

## Get it

```bash
cd "/Users/niazi_127.0.0.1/Documents/Water Management System/aquapulse"
git switch main && git pull
make up                       # rebuilds the web app
make apk && make apk-install  # the Android app (needs the tools from M9)
```

## Try it

1. **Resident (web):** https://localhost:8443/app/ → log in as `resident01@example.com` → **AI insights**. You see the forecast chart and the bill estimate. Run `make problem TYPE=burst DEVICE=home-b01-01 MINUTES=15`: after about 5 minutes "Burst pipe suspected" appears there with its chart.
2. **Owner (web):** log in as `owner@example.com` → **AI insights**: the building forecast and the model list (empty until the first nightly training; see part 2's "Try it").
3. **Automatic shut-off:** owner → **Homes** → unit 03 → tick **Turn the water off by itself when a leak is certain**. Run a burst on unit 03: the valve closes by itself.
4. **Android:** the same screens, from the menu ☰.

## Run the tests

```bash
make web-test      # includes the forecast chart rows and the model scores
make app-test      # 31 widget tests; 4 new: resident forecast and insights, the no-rates message, the shut-off switch, the model list
```

## Done criteria (part 3)

- [ ] The resident's AI insights page shows a forecast with a shaded range and a bill estimate.
- [ ] A burst shows up there with its reason and a chart.
- [ ] The owner switched automatic shut-off on from the home page.
- [ ] The Android app shows the same.
