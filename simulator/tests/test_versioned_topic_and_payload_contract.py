"""FR-10 · Versioned topic and payload contract (TC-10)."""

import pytest

from aquapulse_sim import contract


def test_topics_use_the_versioned_namespace():
    assert contract.topic("home-b01-03", "telemetry") == "aquapulse/v1/dev/home-b01-03/telemetry"
    for channel in ("telemetry", "status", "state", "cmd", "ack"):
        assert contract.parse_topic(contract.topic("tank-b01-01", channel)) == (
            "tank-b01-01",
            channel,
        )


@pytest.mark.parametrize(
    "bad",
    [
        "aquapulse/v2/dev/home-b01-03/telemetry",
        "aquapulse/v1/dev/home-b01-03/secret",
        "aquapulse/v1/dev/home-b01-03",
        "other/v1/dev/home-b01-03/telemetry",
    ],
)
def test_other_topics_are_not_part_of_the_contract(bad):
    with pytest.raises(contract.ContractError):
        contract.parse_topic(bad)


def test_unknown_channel_cannot_be_built():
    with pytest.raises(ValueError):
        contract.topic("home-b01-03", "debug")


def test_every_schema_requires_version_1():
    for name in contract.schema_names():
        if name == "common.v1":
            continue
        problems = contract.validate(name, {"v": 2})
        assert any(p.startswith("v:") for p in problems), name


def test_payloads_over_2_kb_are_refused(monkeypatch):
    big = {"v": 1, "state": "online", "ts": "2026-10-30T08:00:00Z"}
    monkeypatch.setattr(contract, "MAX_PAYLOAD_BYTES", 10)
    with pytest.raises(contract.ContractError, match="max 2048"):
        contract.encode("home-b01-01", "status", big)


def test_real_messages_are_far_below_2_kb(building):
    for device in building.devices.values():
        assert len(contract.encode(device.id, "telemetry", device.telemetry(0))) < 600
