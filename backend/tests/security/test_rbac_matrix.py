"""NFR-09 / FR-19: every endpoint, called by every role, against the permission table.

EXPECTED below is the permission table of SRS sections 3 and 8, written out
by hand. The first test makes sure it lists *every* endpoint the app has, so
a new endpoint without an entry here fails CI (100% coverage, NFR-09).
"""

from __future__ import annotations

import uuid

import pytest

from app.main import app

SA, OW, RE = "sysadmin", "owner", "resident"
ALL = {SA, OW, RE}
PUBLIC = "public"

EXPECTED: dict[tuple[str, str], set[str] | str] = {
    ("GET", "/api/healthz"): PUBLIC,
    ("GET", "/api/readyz"): PUBLIC,
    ("POST", "/api/v1/auth/login"): PUBLIC,
    ("POST", "/api/v1/auth/token"): PUBLIC,
    ("POST", "/api/v1/auth/refresh"): PUBLIC,
    ("POST", "/api/v1/auth/password/forgot"): PUBLIC,
    ("POST", "/api/v1/auth/password/reset"): PUBLIC,
    ("POST", "/api/v1/auth/invitations/accept"): PUBLIC,
    ("POST", "/api/v1/auth/logout"): ALL,
    ("POST", "/api/v1/auth/logout-all"): ALL,
    ("GET", "/api/v1/me"): ALL,
    ("PATCH", "/api/v1/me"): ALL,
    ("POST", "/api/v1/me/password"): ALL,
    ("GET", "/api/v1/admin/owners"): {SA},
    ("POST", "/api/v1/admin/owners"): {SA},
    ("PATCH", "/api/v1/admin/owners/{owner_id}"): {SA},
    ("GET", "/api/v1/admin/audit"): {SA},
    ("POST", "/api/v1/admin/audit/verify"): {SA},
    ("GET", "/api/v1/buildings"): {SA, OW},
    ("POST", "/api/v1/buildings"): {SA, OW},
    ("GET", "/api/v1/buildings/{building_id}"): {SA, OW},
    ("PATCH", "/api/v1/buildings/{building_id}"): {SA, OW},
    ("GET", "/api/v1/buildings/{building_id}/audit"): {OW},
    ("GET", "/api/v1/buildings/{building_id}/homes"): {SA, OW},
    ("POST", "/api/v1/buildings/{building_id}/homes"): {SA, OW},
    ("GET", "/api/v1/homes/{home_id}"): ALL,
    ("PATCH", "/api/v1/homes/{home_id}"): {SA, OW},
    ("DELETE", "/api/v1/homes/{home_id}"): {SA, OW},
    ("POST", "/api/v1/homes/{home_id}/invitations"): {OW},
    ("GET", "/api/v1/homes/{home_id}/occupancies"): {OW},
    ("PATCH", "/api/v1/occupancies/{occupancy_id}"): {OW},
    # M4: live data, consumption and control (SRS 8.1 and 8.4)
    ("POST", "/api/v1/ws-ticket"): ALL,
    ("GET", "/api/v1/homes/{home_id}/live"): {OW, RE},
    ("GET", "/api/v1/homes/{home_id}/telemetry"): {OW, RE},
    ("GET", "/api/v1/homes/{home_id}/consumption"): {OW, RE},
    ("GET", "/api/v1/buildings/{building_id}/consumption"): {OW},
    ("POST", "/api/v1/homes/{home_id}/valve"): {OW, RE},
    ("GET", "/api/v1/commands/{command_id}"): {OW, RE},
    ("POST", "/api/v1/homes/{home_id}/valve-lock"): {OW},
    ("POST", "/api/v1/homes/{home_id}/valve-unlock"): {OW},
    ("GET", "/api/v1/buildings/{building_id}/tank"): {OW},
    ("GET", "/api/v1/tanks/{tank_id}/history"): {OW},
    ("PATCH", "/api/v1/tanks/{tank_id}/settings"): {OW},
    ("POST", "/api/v1/tanks/{tank_id}/pump"): {OW},
    ("GET", "/api/v1/buildings/{building_id}/devices"): {OW},
    ("POST", "/api/v1/homes/{home_id}/device"): {OW},
    ("POST", "/api/v1/tanks/{tank_id}/device"): {OW},
    ("POST", "/api/v1/devices/{device_id}/password"): {OW},
    ("POST", "/api/v1/devices/{device_id}/revoke"): {OW},
    # M6: billing and payments (SRS 3.2 and 8.5). System Admins read bills only.
    ("GET", "/api/v1/buildings/{building_id}/tariffs"): {OW},
    ("POST", "/api/v1/buildings/{building_id}/tariffs"): {OW},
    ("GET", "/api/v1/tariffs/{tariff_id}"): {OW},
    ("POST", "/api/v1/tariffs/preview"): {OW},
    ("POST", "/api/v1/buildings/{building_id}/bills/generate"): {OW},
    ("GET", "/api/v1/buildings/{building_id}/bills"): {SA, OW},
    ("POST", "/api/v1/buildings/{building_id}/bills/issue-all"): {OW},
    ("GET", "/api/v1/me/bills"): {RE},
    ("GET", "/api/v1/bills/{bill_id}"): ALL,
    ("GET", "/api/v1/bills/{bill_id}/pdf"): ALL,
    ("POST", "/api/v1/bills/{bill_id}/issue"): {OW},
    ("POST", "/api/v1/bills/{bill_id}/void"): {OW},
    ("POST", "/api/v1/bills/{bill_id}/payments"): {OW},
    ("POST", "/api/v1/bills/{bill_id}/shutoff-warning"): {OW},
    ("POST", "/api/v1/bills/{bill_id}/checkout"): {RE},
    ("POST", "/api/v1/payments/{payment_id}/reverse"): {OW},
    ("PUT", "/api/v1/payments/{payment_id}/receipt"): {OW},
    ("GET", "/api/v1/payments/{payment_id}/receipt"): ALL,
    ("POST", "/api/v1/webhooks/stripe"): PUBLIC,  # Stripe itself; the signature is checked
    ("GET", "/api/v1/bills/unpaid"): {SA, OW},
    # M7: alerts and notifications (SRS 8.6). Residents acknowledge, owners resolve.
    ("GET", "/api/v1/alerts"): ALL,
    ("GET", "/api/v1/alerts/{alert_id}"): ALL,
    ("POST", "/api/v1/alerts/{alert_id}/acknowledge"): ALL,
    ("POST", "/api/v1/alerts/{alert_id}/resolve"): {SA, OW},
    ("GET", "/api/v1/buildings/{building_id}/alert-rules"): {SA, OW},
    ("PATCH", "/api/v1/buildings/{building_id}/alert-rules"): {OW},
    ("GET", "/api/v1/me/notifications"): ALL,
    ("PATCH", "/api/v1/me/notifications"): ALL,
    # M8: two-factor login (FR-70) and the resident's screens (FR-48, FR-49)
    ("POST", "/api/v1/me/2fa/setup"): ALL,
    ("POST", "/api/v1/me/2fa/enable"): ALL,
    ("POST", "/api/v1/me/2fa/recovery-codes"): ALL,
    ("POST", "/api/v1/me/2fa/disable"): ALL,
    ("GET", "/api/v1/homes/{home_id}/comparison"): {OW, RE},
    ("GET", "/api/v1/homes/{home_id}/bill-estimate"): {OW, RE},
    ("GET", "/api/v1/admin/audit/checks"): {SA},
    ("GET", "/api/v1/admin/notifications"): {SA},
}


def _app_endpoints() -> set[tuple[str, str]]:
    # The OpenAPI document lists every endpoint (FR-29 checks nothing is hidden from it).
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def test_every_endpoint_is_in_the_permission_table():
    assert _app_endpoints() == set(EXPECTED)


def _url(path: str) -> str:
    # Random IDs: the role check must happen before anything is looked up.
    return path.format(
        owner_id=uuid.uuid4(),
        building_id=uuid.uuid4(),
        home_id=uuid.uuid4(),
        occupancy_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        tank_id=uuid.uuid4(),
        device_id=f"home-{uuid.uuid4().hex[:8]}-01",
        tariff_id=uuid.uuid4(),
        bill_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        alert_id=uuid.uuid4(),
    )


CASES = [
    pytest.param(method, path, role, id=f"{role}-{method}-{path}")
    for (method, path), allowed in EXPECTED.items()
    if allowed != PUBLIC
    for role in sorted(ALL)
]


@pytest.mark.parametrize(("method", "path", "role"), CASES)
async def test_role_gets_exactly_the_access_in_the_table(client, make, method, path, role):
    user = await make.user(role)
    headers = await make.headers(user)
    r = await client.request(method, _url(path), headers=headers, json={})
    if role in EXPECTED[(method, path)]:
        assert r.status_code not in (401, 403), r.text
    else:
        assert r.status_code == 403, r.text


@pytest.mark.parametrize(
    ("method", "path"), [k for k, v in EXPECTED.items() if v != PUBLIC], ids=lambda x: str(x)
)
async def test_every_private_endpoint_needs_a_login(client, method, path):
    r = await client.request(method, _url(path), json={})
    assert r.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"), [k for k, v in EXPECTED.items() if v == PUBLIC], ids=lambda x: str(x)
)
async def test_public_endpoints_need_no_login(client, method, path):
    r = await client.request(method, path, json={})
    assert r.status_code not in (401, 403)
