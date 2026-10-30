"""Billing (M6): money, tariffs, the bill engine, bills, PDFs and payments.

* money.py     amounts in a currency's minor unit (2 or 3 decimals), Decimal only
* engine.py    the pure bill calculation (FR-34): no database, no clock, 100% tested
* periods.py   which days, tariff versions and residents a monthly bill covers
* generate.py  monthly bill generation from meter readings (FR-35)
* lifecycle.py states, bill numbers and payments (FR-37, FR-40)
* pdf.py       PDF bills from an HTML template (FR-36)
* stripe_pay.py online payment in Stripe test mode (FR-39)

No floats anywhere in this package: 0.1 + 0.2 is 0.30000000000000004 in
floating point, which is fine for a chart and wrong for money. A test reads
every file here and fails if it finds a float (tests/billing/test_no_floats.py).
"""
