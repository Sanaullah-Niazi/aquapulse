"""FR-34: the bill calculation engine.

Criterion 1: worked examples, each one calculated by hand first (the comment
shows the sum), including slab boundaries exactly at 5,000 L, zero use and
3-decimal rounding. Criterion 2: property tests with Hypothesis, which tries
hundreds of random tariffs and litres. Criterion 3 (100% branch coverage of
the engine) is checked by `make api-test` (see the M6 guide).
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.billing.engine import Charge, Part, Slab, Tariff, TariffError, calculate, validate

# The demo tariff of SRS OQ-5: Rs 60 / 120 / 200 per 1,000 L.
PKR = Tariff(
    slabs=(Slab(0, 5000, D("60")), Slab(5000, 15000, D("120")), Slab(15000, None, D("200"))),
)
# The second demo building: KWD 0.250 / 0.500 / 0.800 per 1,000 L, plus sewerage 10%.
KWD = Tariff(
    slabs=(
        Slab(0, 5000, D("0.250")),
        Slab(5000, 15000, D("0.500")),
        Slab(15000, None, D("0.800")),
    ),
    charges=(Charge("Sewerage", "extra_percent", D("10")),),
)


def with_charges(tariff: Tariff, *charges: Charge, label: str = "") -> Tariff:
    return Tariff(tariff.slabs, charges, label)


def bill(tariff: Tariff, litres: str, days: int = 30, month_days: int = 30, minor: int = 2):
    return calculate([Part(tariff, D(litres), days)], month_days, minor)


# ----- criterion 1: worked examples (hand-calculated) -----

EXAMPLES = [
    # (name, tariff, litres, days, month_days, minor units, expected line amounts, total)
    ("zero use", PKR, "0", 30, 30, 2, [], "0.00"),
    ("one litre", PKR, "1", 30, 30, 2, ["0.06"], "0.06"),  # 1 x 60/1000
    ("just below 5,000", PKR, "4999", 30, 30, 2, ["299.94"], "299.94"),
    ("exactly 5,000: all in slab 1", PKR, "5000", 30, 30, 2, ["300.00"], "300.00"),
    ("5,001: one litre in slab 2", PKR, "5001", 30, 30, 2, ["300.00", "0.12"], "300.12"),
    ("exactly 15,000", PKR, "15000", 30, 30, 2, ["300.00", "1200.00"], "1500.00"),
    ("15,001", PKR, "15001", 30, 30, 2, ["300.00", "1200.00", "0.20"], "1500.20"),
    ("20,000", PKR, "20000", 30, 30, 2, ["300.00", "1200.00", "1000.00"], "2500.00"),
    # 7,345.678 x 0.12 = 881.48136 -> 881.48
    ("millilitres", PKR, "12345.678", 30, 30, 2, ["300.00", "881.48"], "1181.48"),
    # Moving in on the 16th of a 30-day month: slabs x 15/30 = 0-2,500 / 2,500-7,500.
    # 2,500 x 0.06 = 150; 4,500 x 0.12 = 540
    ("15 of 30 days", PKR, "7000", 15, 30, 2, ["150.00", "540.00"], "690.00"),
    # 16 of 31 days: slab 1 ends at 5,000 x 16/31 = 2,580.645 L.
    # 2,580.645 x 0.06 = 154.8387 -> 154.84; 419.355 x 0.12 = 50.3226 -> 50.32
    ("16 of 31 days", PKR, "3000", 16, 31, 2, ["154.84", "50.32"], "205.16"),
    ("February, full month", PKR, "5000", 28, 28, 2, ["300.00"], "300.00"),
    # KWD (3 decimals): 1.250 + 1.000 = 2.250 water, sewerage 10% = 0.225
    ("KWD 7,000", KWD, "7000", 30, 30, 3, ["1.250", "1.000", "0.225"], "2.475"),
    # 2,499 x 0.8/1000 = 1.9992 -> 1.999; water 8.249; sewerage 0.8249 -> 0.825
    ("KWD rounding", KWD, "17499", 30, 30, 3, ["1.250", "5.000", "1.999", "0.825"], "9.074"),
    (
        "KWD 4.375 (SRS example)",  # one flat slab: KWD 0.875 per 1,000 L
        Tariff((Slab(0, None, D("0.875")),)),
        "5000",  # 5,000 x 0.875/1000 = 4.375
        30, 30, 3, ["4.375"], "4.375",
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("tariff", "litres", "days", "month_days", "minor", "amounts", "total"),
    [example[1:] for example in EXAMPLES],
    ids=[example[0] for example in EXAMPLES],
)
def test_worked_examples(tariff, litres, days, month_days, minor, amounts, total):
    result = bill(tariff, litres, days, month_days, minor)
    assert [str(line.amount) for line in result.lines] == amounts
    assert str(result.total) == total


def test_sales_tax_is_charged_on_water():
    # 7,000 L = 300 + 240 = 540; 17% of 540 = 91.80
    result = bill(with_charges(PKR, Charge("Sales tax", "tax_percent", D("17"))), "7000")
    assert [(ln.kind, str(ln.amount)) for ln in result.lines] == [
        ("slab", "300.00"),
        ("slab", "240.00"),
        ("tax", "91.80"),
    ]
    assert (str(result.subtotal), str(result.tax_total), str(result.total)) == (
        "540.00",
        "91.80",
        "631.80",
    )


def test_tax_covers_extra_charges_too():
    # water 540; sewerage 10% = 54; tax 17% of 594 = 100.98; total 694.98
    tariff = with_charges(
        PKR,
        Charge("Sales tax", "tax_percent", D("17")),  # listed first, still calculated last
        Charge("Sewerage", "extra_percent", D("10")),
    )
    result = bill(tariff, "7000")
    assert [str(ln.amount) for ln in result.lines] == ["300.00", "240.00", "54.00", "100.98"]
    assert str(result.total) == "694.98"


def test_fixed_extra_charge_applies_even_with_zero_use():
    # The owner's own fixed charge (there is no built-in fixed fee).
    result = bill(with_charges(PKR, Charge("Meter rent", "extra_fixed", D("150"))), "0")
    assert [(ln.kind, ln.description, str(ln.amount)) for ln in result.lines] == [
        ("extra", "Meter rent (fixed)", "150.00")
    ]
    assert str(result.total) == "150.00"


def test_fixed_extra_charge_is_prorated_by_days():
    # 150 x 15/30 = 75.00; tax 5% of 75 = 3.75
    tariff = with_charges(
        PKR,
        Charge("Meter rent", "extra_fixed", D("150")),
        Charge("Tax", "tax_percent", D("5")),
    )
    result = bill(tariff, "0", days=15)
    assert [str(ln.amount) for ln in result.lines] == ["75.00", "3.75"]
    assert result.lines[0].description == "Meter rent (fixed x 15/30 days)"
    assert str(result.total) == "78.75"


def test_two_tariff_versions_in_one_month():
    # September (30 days). v1 for days 1-14 (3,000 L), v2 from the 15th (16 days, 4,000 L).
    # v1: slab 1 = 5,000 x 14/30 = 2,333.333 L x 0.06 = 139.99998 -> 140.00
    #     666.667 L x 0.12 = 80.00004 -> 80.00
    # v2: slab 1 = 5,000 x 16/30 = 2,666.667 L x 0.07 = 186.66669 -> 186.67
    #     1,333.333 L x 0.15 = 199.99995 -> 200.00
    v1 = Tariff(PKR.slabs, label="Tariff v1")
    v2 = Tariff((Slab(0, 5000, D("70")), Slab(5000, None, D("150"))), label="Tariff v2")
    result = calculate([Part(v1, D("3000"), 14), Part(v2, D("4000"), 16)], 30, 2)
    assert [(ln.part, str(ln.amount)) for ln in result.lines] == [
        (0, "140.00"),
        (0, "80.00"),
        (1, "186.67"),
        (1, "200.00"),
    ]
    assert result.lines[0].description.startswith("Tariff v1: Slab 1 (0-2,333.333 L, 14/30 days)")
    assert str(result.litres) == "7000.000"
    assert str(result.total) == "606.67"


def test_a_free_first_slab_still_shows_its_line():
    # "First 2,000 L free": 0 + 500 x 100/1000 = 50.00
    tariff = Tariff((Slab(0, 2000, D("0")), Slab(2000, None, D("100"))))
    result = bill(tariff, "2500")
    assert [(str(ln.quantity_l), str(ln.amount)) for ln in result.lines] == [
        ("2000.000", "0.00"),
        ("500.000", "50.00"),
    ]


def test_rounding_is_half_up_not_bankers():
    # 10 L x 0.5/1000 = 0.005 exactly: half up gives 0.01 (banker's rounding would give 0.00).
    assert str(bill(Tariff((Slab(0, None, D("0.5")),)), "10").total) == "0.01"
    # OMR: 500 L x 0.001/1000 = 0.0005 -> 0.001
    assert str(bill(Tariff((Slab(0, None, D("0.001")),)), "500", minor=3).total) == "0.001"


def test_usd_rate_with_three_decimals():
    tariff = Tariff((Slab(0, None, D("1.234")),))
    assert str(bill(tariff, "1000").total) == "1.23"  # 1.234 -> 1.23
    assert str(bill(tariff, "1005").total) == "1.24"  # 1.24017 -> 1.24


def test_bhd_vat_with_three_decimals():
    # 3,333 x 0.025/1000 = 0.083325 -> 0.083; VAT 10% = 0.0083 -> 0.008
    tariff = Tariff((Slab(0, None, D("0.025")),), (Charge("VAT", "tax_percent", D("10")),))
    result = bill(tariff, "3333", minor=3)
    assert [str(ln.amount) for ln in result.lines] == ["0.083", "0.008"]
    assert str(result.total) == "0.091"


def test_no_parts_is_an_empty_bill():
    result = calculate([], 30, 3)
    assert result.lines == () and str(result.total) == "0.000"


def test_line_details_for_the_pdf():
    line = bill(PKR, "6000").lines[1]
    assert line.description == "Slab 2 (5,000-15,000 L)"
    assert (line.quantity_l, line.rate) == (D("1000"), D("120"))
    assert bill(PKR, "16000").lines[2].description == "Slab 3 (15,000+ L)"
    sewerage = bill(KWD, "100", minor=3).lines[1]
    assert sewerage.description == "Sewerage (10% of water)"


# ----- FR-33 criteria 1 and 2: invalid tariffs are refused -----


@pytest.mark.parametrize(
    ("slabs", "charges", "message"),
    [
        ((), (), "1 to 10 slabs"),
        (tuple(Slab(n, n + 1, D(1)) for n in range(11)), (), "1 to 10 slabs"),
        ((Slab(100, None, D(1)),), (), "start at 0"),
        ((Slab(0, 5000, D(1)), Slab(6000, None, D(1))), (), "no gaps"),  # gap
        ((Slab(0, 5000, D(1)), Slab(4000, None, D(1))), (), "no gaps"),  # overlap
        ((Slab(0, 5000, D(1)),), (), "last slab must have no upper limit"),
        ((Slab(0, None, D(1)), Slab(0, None, D(1))), (), "only the last slab"),
        ((Slab(0, 0, D(1)), Slab(0, None, D(1))), (), "more than 'from'"),
        ((Slab(0, None, D(-1)),), (), "cannot be negative"),
        ((Slab(0, None, D("0.0001")),), (), "at most 3 decimals"),
        ((Slab(0, None, D(1)),), (Charge("VAT", "tax_percent", D(101)),), "0 to 100"),
        ((Slab(0, None, D(1)),), (Charge("Fee", "extra_percent", D(-5)),), "cannot be negative"),
        ((Slab(0, None, D(1)),), (Charge("Fee", "extra_fixed", D("1.234")),), "at most 2"),
        ((Slab(0, None, D(1)),), (Charge("VAT", "tax_percent", D("1.2345")),), "3 decimals"),
        ((Slab(0, None, D(1)),), tuple(Charge("x", "tax_percent", D(1)) for _ in range(11)), "10"),
    ],
)
def test_invalid_tariffs_are_refused(slabs, charges, message):
    with pytest.raises(TariffError, match=message):
        validate(Tariff(slabs, charges), minor_units=2)


def test_valid_tariffs_pass():
    validate(PKR, 2)
    validate(KWD, 3)
    validate(with_charges(PKR, Charge("Meter rent", "extra_fixed", D("150.50"))), 2)


# ----- criterion 2: property tests -----


@st.composite
def tariffs(draw) -> Tariff:
    """A random valid tariff: 1 to 5 contiguous slabs, random rates and charges."""
    limits = sorted(draw(st.sets(st.integers(1, 50_000), max_size=4)))
    bounds = [0, *limits, None]
    rate = st.decimals(min_value=0, max_value=500, places=3)
    slabs = tuple(Slab(bounds[i], bounds[i + 1], draw(rate)) for i in range(len(bounds) - 1))
    percent = st.decimals(min_value=0, max_value=100, places=3)
    charges = draw(
        st.lists(
            st.one_of(
                st.builds(Charge, st.just("Tax"), st.just("tax_percent"), percent),
                st.builds(Charge, st.just("Extra"), st.just("extra_percent"), percent),
                st.builds(
                    Charge,
                    st.just("Fixed"),
                    st.just("extra_fixed"),
                    st.decimals(min_value=0, max_value=1000, places=2),
                ),
            ),
            max_size=3,
        )
    )
    return Tariff(slabs, tuple(charges))


litres = st.decimals(min_value=0, max_value=100_000, places=3)
minor_units = st.sampled_from([2, 3])


@settings(max_examples=300, deadline=None)
@given(tariffs(), litres, st.integers(1, 31), minor_units)
def test_total_is_never_negative_and_lines_add_up(tariff, used, days, minor):
    validate(tariff, minor)
    result = calculate([Part(tariff, used, min(days, 30))], 30, minor)
    assert result.total >= 0
    assert sum(line.amount for line in result.lines) == result.total
    assert result.subtotal + result.tax_total == result.total
    assert all(line.amount.as_tuple().exponent == -minor for line in result.lines)


@settings(max_examples=300, deadline=None)
@given(tariffs(), litres, litres, st.integers(1, 31), minor_units)
def test_more_litres_never_cost_less(tariff, a, b, days, minor):
    low, high = sorted((a, b))
    month = 31
    assert (
        calculate([Part(tariff, low, days)], month, minor).total
        <= calculate([Part(tariff, high, days)], month, minor).total
    )


@settings(max_examples=200, deadline=None)
@given(tariffs(), litres, litres, st.integers(1, 29))
def test_two_parts_add_up_too(tariff, a, b, split):
    result = calculate([Part(tariff, a, split), Part(tariff, b, 30 - split)], 30, 2)
    assert result.total >= 0
    assert sum(line.amount for line in result.lines) == result.total
