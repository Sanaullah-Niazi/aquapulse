# M10: AI (leaks, unusual use, forecasts)

> **Goal:** AquaPulse notices a leak by itself, says **why** in plain words, and can turn the water off. Later parts add unusual-use flags and a 7-day forecast with a month-end bill estimate.
> **Built in 3 parts:** part 1 (this page so far) is leak and burst detection plus automatic shut-off. Part 2 adds forecasts, unusual use and the model registry. Part 3 adds the screens in the web app and the Android app.

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
# (the owner's screen comes in part 3; until then, with the API)
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
