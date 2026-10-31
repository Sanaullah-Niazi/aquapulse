# AI evaluation

> This file is written by a script (`make ai-eval`), never by hand, so the numbers are real. CI runs the same script on every push and fails if a target is missed.

## Leak detection (FR-63)

Test set: the simulator's digital twin, 10 homes, readings every minute from 2026-04-01 to 2026-05-31 (864,000 readings). 62 problems were planted at random homes and times: 50 slow leaks (0.2 to 2 L/min, 3 to 30 hours) and 12 bursts (25 to 40 L/min, 10 to 40 minutes). The detector does not see the labels; it only gets the readings, like in real life.

| Measure | Result | Target | |
|---|---|---|---|
| Recall (planted problems found) | 100.0% (62 of 62) | ≥ 90% | ✅ met |
| Precision (alerts that were real) | 100.0% (62 of 62) | ≥ 80% | ✅ met |
| Median time to alert, slow leaks | 120 min | ≤ 2 h | ✅ met |
| Median time to alert, bursts | 5 min | ≤ 10 min | ✅ met |
| Bursts named "burst" (not just "leak") | 12 of 12 | | |

How it is counted: a planted problem is found when an alert starts between its start and 15 minutes after its end. An alert that matches nothing is a false alarm. The detector checks every reading, like the worker does with live readings. Time to alert is counted from the first reading that shows the problem (the detector cannot know earlier). Slow leaks are found after exactly 2 hours of flow by design (FR-63's continuous-flow rule), sooner when they flow at night.

Example explanation (FR-66), made from the data:

> Water kept flowing between 02:00 and 03:00 (lowest 1.0 L/min). Normally this home uses 0.0 L/min at night.

> Very high flow, up to 39.3 L/min, for 5 min since 07:48. This home's normal maximum is 24.0 L/min. This looks like a broken pipe.

### Limits of this test

- The data is simulated. Real homes have things the twin does not, such as a water filter that flushes at night. The night baseline (the home's usual lowest night flow) is there for that, but only real data can prove it.
- A leak shorter than 2 hours that is not at night cannot be found by design: it looks like normal use.
