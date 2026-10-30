"""FR-06 · Online / offline status (TC-06), unit part. Live Last Will: test_live_mqtt.py."""

from aquapulse_sim import contract
from aquapulse_sim.mqtt_link import KEEPALIVE_S


def test_last_will_and_online_messages_follow_the_contract():
    contract.encode("home-b01-01", "status", {"v": 1, "state": "offline"})
    contract.encode("home-b01-01", "status", {"v": 1, "state": "online", "ts": contract.iso(0)})


def test_keepalive_is_30_seconds():
    # The broker gives up on a silent device after 1.5 x keep-alive = 45 s (FR-06).
    assert KEEPALIVE_S == 30
