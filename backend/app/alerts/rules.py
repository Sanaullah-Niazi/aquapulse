"""The built-in alert rules and their settings (FR-58, FR-62).

Each rule has a fixed severity (from the SRS) and a few numbers the owner may
change per building, always within safe limits. The owner's changes are kept
in `buildings.alert_rules`; anything not changed uses the default below.

Who gets an email for which severity is `buildings.alert_recipients`
("owner", "resident"). Everyone always sees alerts in the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Limit:
    default: float
    low: float  # smallest value the owner may choose
    high: float  # largest value the owner may choose
    help: str
    whole: bool = False  # a count of readings, minutes or hours


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    severity: str
    about: str  # "home", "tank" or "device"
    describe: str
    limits: dict[str, Limit] = field(default_factory=dict)


RULES: dict[str, Rule] = {r.id: r for r in [
    Rule("ph_unsafe", "pH unsafe", "critical", "home",
         "pH below 'low' or above 'high' for 'readings' readings in a row", {
             "low": Limit(6.5, 4.0, 7.0, "unsafe below this pH"),
             "high": Limit(8.5, 7.5, 10.0, "unsafe above this pH"),
             "readings": Limit(5, 1, 60, "readings in a row", whole=True)}),
    Rule("temp_abnormal", "Water temperature abnormal", "warning", "home",
         "temperature below 'low' or above 'high' (°C) for 'readings' readings in a row", {
             "low": Limit(2.0, -10.0, 15.0, "too cold below this (°C)"),
             "high": Limit(35.0, 20.0, 60.0, "too hot above this (°C)"),
             "readings": Limit(5, 1, 60, "readings in a row", whole=True)}),
    Rule("device_offline", "Device offline", "warning", "device",
         "no message for 'seconds' seconds, or the device's Last Will arrived", {
             "seconds": Limit(30, 20, 3600, "seconds without a message", whole=True)}),
    Rule("tank_low", "Tank low", "warning", "tank",
         "tank level below 'below_pct' %", {
             "below_pct": Limit(15.0, 1.0, 50.0, "low below this level (%)")}),
    Rule("tank_overflow", "Tank overflow risk", "critical", "tank",
         "level above 'above_pct' %, or the pump still ON above the tank's high threshold "
         "for 'readings' readings in a row", {
             "above_pct": Limit(98.0, 80.0, 100.0, "overflow risk above this level (%)"),
             "readings": Limit(3, 1, 20, "readings in a row (pump ON too high)", whole=True)}),
    Rule("dry_run", "Pump dry-run protection", "critical", "tank",
         "the tank device stopped the pump because the sump is empty"),
    Rule("high_usage", "Daily usage high", "info", "home",
         "after 'after_hour':00, today's litres are more than 'percent' % of the home's "
         "average day over the last 30 days", {
             "percent": Limit(150, 110, 500, "% of an average day", whole=True),
             "after_hour": Limit(18, 0, 23, "check from this hour (building time)", whole=True)}),
    Rule("command_failed", "Valve command failed", "warning", "device",
         "a valve command ended as 'failed' or 'timeout'"),
    Rule("sensor_stuck", "Sensor stuck", "warning", "home",
         "pH and temperature exactly the same for 'minutes' minutes", {
             "minutes": Limit(60, 15, 1440, "minutes without any change", whole=True)}),
    # M10 (FR-63): the AI leak detector, see app/ai/leak.py.
    Rule("leak", "Possible leak", "warning", "home",
         "water keeps flowing: 'night_minutes' minutes without stopping between 02:00 and "
         "05:00, or 'continuous_hours' hours without stopping at any time", {
             "night_minutes": Limit(60, 30, 180, "minutes of night flow", whole=True),
             "continuous_hours": Limit(2, 1, 12, "hours of flow without a stop")}),
    Rule("burst", "Burst pipe suspected", "critical", "home",
         "flow above the home's normal maximum (99.9th percentile, at least 24 L/min) for "
         "'minutes' minutes", {
             "minutes": Limit(5, 2, 30, "minutes of very high flow", whole=True)}),
    # M10 (FR-64): the nightly unusual-use model, see app/ai/unusual.py.
    Rule("unusual_use", "Unusual water use", "info", "home",
         "yesterday's water was unlike this home's normal days (Isolation Forest, retrained "
         "nightly)"),
]}  # fmt: skip

AI_RULES = ("leak", "burst", "unusual_use")
LIVE_AI_RULES = ("leak", "burst")  # found from live readings (app/ai/monitor.py)

# Not configurable: raised by the worker's daily audit check, for System Admins.
AUDIT_RULE = Rule("audit_chain", "Audit log changed", "critical", "system",
                  "the audit chain check found a changed or missing entry")  # fmt: skip

SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
RECIPIENT_KINDS = ("owner", "resident")
DEFAULT_RECIPIENTS: dict[str, list[str]] = {
    "info": ["owner"],
    "warning": ["owner", "resident"],
    "critical": ["owner", "resident"],
}


class RuleSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class RuleSettings:
    """One building's settings: is each rule on, and its numbers."""

    enabled: dict[str, bool]
    values: dict[str, dict[str, float]]
    recipients: dict[str, list[str]]

    def on(self, rule: str) -> bool:
        return self.enabled.get(rule, True)

    def get(self, rule: str, name: str) -> float:
        return self.values[rule][name]


def settings_for(stored_rules: dict[str, Any], stored_recipients: dict[str, Any]) -> RuleSettings:
    """Defaults, overridden by what the owner saved (already checked by `check`)."""
    enabled, values = {}, {}
    for rule in RULES.values():
        saved = stored_rules.get(rule.id, {})
        enabled[rule.id] = bool(saved.get("enabled", True))
        values[rule.id] = {name: saved.get(name, lim.default) for name, lim in rule.limits.items()}
    recipients = {sev: list(stored_recipients.get(sev, kinds))
                  for sev, kinds in DEFAULT_RECIPIENTS.items()}  # fmt: skip
    return RuleSettings(enabled, values, recipients)


DEFAULTS = settings_for({}, {})


def check(rules: dict[str, Any], recipients: dict[str, Any]) -> list[str]:
    """Problems with an owner's changes, in plain words (FR-62 criterion 1)."""
    problems: list[str] = []
    for rule_id, saved in rules.items():
        rule = RULES.get(rule_id)
        if rule is None:
            problems.append(f"Unknown rule '{rule_id}'")
            continue
        if not isinstance(saved, dict):
            problems.append(f"{rule.title}: settings must be an object")
            continue
        for name, value in saved.items():
            if name == "enabled":
                if not isinstance(value, bool):
                    problems.append(f"{rule.title}: 'enabled' must be true or false")
                continue
            limit = rule.limits.get(name)
            if limit is None:
                problems.append(f"{rule.title}: unknown setting '{name}'")
            elif isinstance(value, bool) or not isinstance(value, int | float):
                problems.append(f"{rule.title}: '{name}' must be a number")
            elif limit.whole and value != int(value):
                problems.append(f"{rule.title}: '{name}' must be a whole number")
            elif not limit.low <= value <= limit.high:
                problems.append(
                    f"{rule.title}: '{name}' must be between {limit.low:g} and {limit.high:g}"
                )
    for rule_id in ("ph_unsafe", "temp_abnormal"):
        if rule_id in rules and isinstance(rules[rule_id], dict):
            merged = {n: lim.default for n, lim in RULES[rule_id].limits.items()} | rules[rule_id]
            low, high = merged["low"], merged["high"]
            if isinstance(low, int | float) and isinstance(high, int | float) and low >= high:
                problems.append(f"{RULES[rule_id].title}: 'low' must be less than 'high'")
    for severity, kinds in recipients.items():
        if severity not in SEVERITY_ORDER:
            problems.append(f"Unknown severity '{severity}'")
        elif not isinstance(kinds, list) or not set(kinds) <= set(RECIPIENT_KINDS):
            problems.append(f"Recipients for '{severity}' must be a list of: owner, resident")
    return problems


def describe(settings: RuleSettings) -> list[dict[str, Any]]:
    """Every rule with its current values and limits, for the API."""
    return [
        {
            "rule": r.id,
            "title": r.title,
            "severity": r.severity,
            "about": r.about,
            "description": r.describe,
            "enabled": settings.on(r.id),
            "values": settings.values[r.id],
            "limits": {
                n: {"default": lim.default, "min": lim.low, "max": lim.high, "help": lim.help}
                for n, lim in r.limits.items()
            },
        }
        for r in RULES.values()
    ]
