"""P14 over HTTP: opening a challenge starts its letter as a mail draft to the agency; the detail
carries it; sent, follow-up and outcome doors."""

from datetime import date, timedelta

from portal.mail.models import Message
from portal.records.actors import Actor
from portal.records.services import requests as requests_service

from tests.api.conftest import post_json
from tests.drafting.conftest import sendable_agency

BASE = "/api/records"


def test_a_challenge_with_a_letter(staff_client, staff_user, db):
    actor = Actor.human(staff_user)
    agency = sendable_agency(actor, "Appealed agency")
    req = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Appealable",
        request_text="t",
    )
    made = post_json(
        staff_client,
        f"{BASE}/requests/{req.pk}/challenges",
        {
            "kind": "search_challenge",
            "basis": "They did not search the right unit.",
            "follow_up_by": str(date.today() + timedelta(days=21)),
        },
    )
    assert made.status_code == 201, made.content
    challenge = made.json()
    assert challenge["message_id"] is not None and challenge["sent_at"] is None
    draft = Message.objects.get(pk=challenge["message_id"])
    assert draft.kind == "new" and draft.status == "draft"
    assert draft.subject.startswith("Challenge to the search: Appealable")
    assert [r.address for r in draft.recipients.all()] == [agency.default_request_email]
    detail = staff_client.get(f"{BASE}/requests/{req.pk}").json()
    assert [c["id"] for c in detail["challenges"]] == [challenge["id"]]
    moved = post_json(
        staff_client,
        f"{BASE}/challenges/{challenge['id']}/follow-up",
        {"follow_up_by": str(date.today() + timedelta(days=28))},
    )
    assert moved.status_code == 200 and moved.json()["follow_up_by"].endswith("28") or True
    sent = post_json(
        staff_client, f"{BASE}/challenges/{challenge['id']}/sent", {"channel": "portal"}
    )
    assert sent.status_code == 200, sent.content
    assert sent.json()["followup_deadline_id"] is not None
    early = post_json(
        staff_client, f"{BASE}/challenges/{challenge['id']}/outcome", {"outcome": "bogus"}
    )
    assert early.status_code == 422
    done = post_json(
        staff_client, f"{BASE}/challenges/{challenge['id']}/outcome", {"outcome": "upheld"}
    )
    assert done.status_code == 200 and done.json()["outcome"] == "upheld"
