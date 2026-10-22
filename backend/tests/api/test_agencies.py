"""One happy path per agencies/contacts endpoint."""

from tests.api.conftest import patch_json, post_json


def test_list_agencies(staff_client, agency):
    resp = staff_client.get("/api/records/agencies")
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert agency.name in names


def test_create_agency(staff_client):
    resp = post_json(
        staff_client,
        "/api/records/agencies",
        {"name": "Second Test Agency", "jurisdiction": "Testland", "records_law": "TESTLAW"},
    )
    assert resp.status_code == 201, resp.content
    assert resp.json()["name"] == "Second Test Agency"


def test_update_agency(staff_client, agency):
    resp = patch_json(
        staff_client,
        f"/api/records/agencies/{agency.pk}",
        {"note": "corrected the URL", "portal_url": "https://example.org/records"},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["portal_url"] == "https://example.org/records"


def test_create_and_list_contact(staff_client, agency):
    created = post_json(
        staff_client,
        f"/api/records/agencies/{agency.pk}/contacts",
        {"name": "Records Officer", "email": "records@example.org"},
    )
    assert created.status_code == 201, created.content
    resp = staff_client.get(f"/api/records/agencies/{agency.pk}/contacts")
    assert resp.status_code == 200
    assert any(c["name"] == "Records Officer" for c in resp.json())
