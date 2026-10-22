"""One happy path per fees endpoint."""

from tests.api.conftest import patch_json, post_json


def test_record_and_update_fee(staff_client, reserved):
    created = post_json(
        staff_client,
        f"/api/records/requests/{reserved.pk}/fees",
        {"status": "assessed", "amount_cents": 2500},
    )
    assert created.status_code == 201, created.content
    fee_id = created.json()["id"]
    resp = patch_json(
        staff_client,
        f"/api/records/fees/{fee_id}",
        {"status": "paid", "note": "check received and deposited"},
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["status"] == "paid"
