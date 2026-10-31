"""The AI module (M10): leak detection, unusual use, forecasts (FR-63 to FR-67).

Every detector here is plain Python on lists of readings, with no database,
so it can be tested with made-up numbers and scored offline on simulator
data whose leaks are known (`python -m app.ai.evaluate`).
"""
