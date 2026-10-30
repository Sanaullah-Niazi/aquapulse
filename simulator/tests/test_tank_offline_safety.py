"""NFR-20 · The tank protects itself with no server and no network at all.

The tank's control runs inside the tank node (here: TankNode.control), not
in the backend. This test never creates an MQTT connection, which proves the
automation does not depend on one.
"""

from .conftest import START, run_for


def test_tank_control_works_without_any_connection(building):
    tank = building.tank
    assert not tank.connected
    run_for(building, START, 12 * 3600, step=5)
    assert 5 <= tank.level_pct <= 100
