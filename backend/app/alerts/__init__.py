"""Alerts and notifications (M7).

* `rules.py`   the built-in rules, their defaults and the owner's limits (FR-58, FR-62)
* `detect.py`  the fast rules, one live event at a time, no database (FR-58)
* `store.py`   signals -> alerts, de-duplication, auto-resolve, who is told (FR-59, FR-60)
* `deliver.py` sending emails, with retries (FR-60)
* `engine.py`  the loop in the worker that ties them together
* `replay.py`  run the rules over stored history: the false-positive check
"""
