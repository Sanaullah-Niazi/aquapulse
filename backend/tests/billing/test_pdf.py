"""FR-36: PDF bills. The test reads the text back out of the PDF and compares
it with the bill record (criterion 1), and checks it is one A4 page."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from .conftest import add_tariff, bills_of, generate, issued_bill

pytestmark = pytest.mark.usefixtures("outbox")

A4_POINTS = (595, 842)  # 210 x 297 mm


async def _pdf(client, make, user, bill) -> tuple[int, bytes, dict]:
    r = await client.get(f"/api/v1/bills/{bill['id']}/pdf", headers=await make.headers(user))
    return r.status_code, r.content, dict(r.headers)


def _text(pdf: bytes) -> tuple[str, PdfReader]:
    reader = PdfReader(io.BytesIO(pdf))
    return " ".join(" ".join(page.extract_text().split()) for page in reader.pages), reader


async def test_pdf_text_matches_the_bill(client, make, world):
    bill = await issued_bill(client, make, world)  # 7,000 L, Rs 540.00
    await client.post(
        f"/api/v1/bills/{bill['id']}/payments",
        json={"amount": "40", "method": "bank", "reference": "TRX-889"},
        headers=await make.headers(world.owner),
    )
    status, pdf, headers = await _pdf(client, make, world.resident, bill)
    assert status == 200 and headers["content-type"] == "application/pdf"
    assert f'filename="{bill["bill_no"]}.pdf"' in headers["content-disposition"]
    text, reader = _text(pdf)

    # One A4 page (criterion 2).
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == A4_POINTS

    for expected in (
        world.building.name,
        bill["bill_no"],
        world.resident.full_name,
        "Unit A-1",
        "01 Sep 2026 to 30 Sep 2026",
        "7,000 L",  # litres
        "1,000",  # meter reading at the start
        "8,000",  # ... and at the end
        "Slab 1 (0-5,000 L)",
        "Rs 300.00",
        "Slab 2 (5,000-15,000 L)",
        "Rs 240.00",
        "Total Rs 540.00",
        f"Due {bill['due_date']}",
        "PARTLY PAID",
        "Bank transfer TRX-889 Rs 40.00",
        "Still to pay Rs 500.00",
        "Pakistani Rupee (PKR)",
        "last 6 months",
    ):
        assert expected in text, f"{expected!r} not in PDF text: {text}"


async def test_kwd_pdf_shows_three_decimals(client, make):
    from datetime import date

    from .conftest import SEPT, use_water

    owner = await make.user("owner")
    building = await make.building(owner, currency_code="KWD", timezone="Asia/Kuwait")
    await add_tariff(
        building,
        slabs=[(0, 5000, "0.250"), (5000, None, "0.500")],
        charges=[("Sewerage", "extra_percent", "10")],
    )
    home = await make.home(building)
    resident = await make.user("resident")
    await make.occupancy(home, resident, move_in=date(2026, 8, 1), billing=True)
    device = await make.device(home)
    await use_water(home, device, SEPT, [7000.0 / 30] * 30, building.timezone)

    class W:
        pass

    world = W()
    world.owner, world.building = owner, building
    await generate(client, make, world)
    [bill] = await bills_of(client, make, world)
    status, pdf, _ = await _pdf(client, make, owner, bill)
    text, _ = _text(pdf)
    assert status == 200
    # 1.250 + 1.000 = 2.250, sewerage 0.225, total 2.475
    for expected in ("KWD 1.250", "KWD 1.000", "Sewerage (10% of water)", "KWD 0.225",
                     "Total KWD 2.475", "DRAFT", "Draft (not issued yet)"):  # fmt: skip
        assert expected in text, f"{expected!r} not in PDF text: {text}"


async def test_only_allowed_people_get_the_pdf(client, make, world):
    """Criterion 3: residents only their own bills, owners only their buildings'."""
    bill = await issued_bill(client, make, world)
    assert (await _pdf(client, make, world.owner, bill))[0] == 200
    for user in (await make.user("resident"), await make.user("owner")):
        assert (await _pdf(client, make, user, bill))[0] == 404


async def test_a_resident_never_gets_a_draft_pdf(client, make, world):
    await generate(client, make, world)
    [draft] = await bills_of(client, make, world)
    assert (await _pdf(client, make, world.resident, draft))[0] == 404
    status, _, headers = await _pdf(client, make, world.owner, draft)
    assert status == 200 and "draft-" in headers["content-disposition"]


async def test_names_cannot_inject_html(client, make, world):
    """The template escapes every value (Jinja2 autoescape)."""
    from app import db
    from app.models import User

    async with db.sessions()() as session, session.begin():
        user = await session.get(User, world.resident.id)
        user.full_name = '<img src="x"> Mallory'
    bill = await issued_bill(client, make, world)
    text, _ = _text((await _pdf(client, make, world.owner, bill))[1])
    assert '<img src="x"> Mallory' in text  # shown as text, not turned into an image
