"""FR-01 · Virtual home devices (TC-01)."""

from aquapulse_sim.building import Building
from aquapulse_sim.config import Config
from aquapulse_sim.store import FlashStore

from .conftest import START, run_for


def test_ten_homes_and_one_tank_with_distinct_ids(building):
    ids = [home.id for home in building.homes]
    assert ids == [f"home-b01-{n:02d}" for n in range(1, 11)]
    assert building.tank.id == "tank-b01-01"
    assert len(set(building.devices)) == 11


def test_number_of_homes_is_configurable_up_to_500():
    big = Building(Config(homes=500), START, FlashStore(None))
    assert len(big.homes) == 500
    assert big.homes[-1].id == "home-b01-500"


def test_each_home_has_its_own_state(building):
    # Households differ in size, so they use different amounts of water.
    run_for(building, START, 24 * 3600, step=10)
    totals = {home.id: home.total_l for home in building.homes}
    assert len(set(totals.values())) == 10
    assert {home.persons for home in building.homes} <= set(range(1, 7))


def test_counter_only_goes_up(building):
    previous = {home.id: home.total_l for home in building.homes}
    t = START
    for _ in range(2 * 24 * 360):  # two days in 10-second steps
        t += 10
        building.tick(t)
        for home in building.homes:
            assert home.total_l >= previous[home.id]
            previous[home.id] = home.total_l


def test_stopping_one_device_does_not_affect_the_others(building):
    building.device("home-b01-03").stopped = True
    run_for(building, START, 12 * 3600, step=10)
    assert all(not h.stopped for h in building.homes if h.id != "home-b01-03")
    assert all(h.total_l > 0 for h in building.homes)
