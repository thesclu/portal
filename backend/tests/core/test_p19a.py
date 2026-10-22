"""P19a: who's online."""

import json
from datetime import timedelta

from django.test import Client
from django.utils import timezone
from portal.core.models import Presence
from portal.core.services import presence


def _post(client, payload):
    return client.post(
        "/api/core/presence", data=json.dumps(payload), content_type="application/json"
    )


def test_heartbeats_make_the_list_and_fade_to_away_then_gone(
    staff_user, colleague_user, admin_user
) -> None:
    staff, colleague = Client(), Client()
    staff.force_login(staff_user)
    colleague.force_login(colleague_user)
    body = _post(staff, {"path": "/docket", "label": "Docket"}).json()
    assert body["online"] == 1 and body["people"][0]["name"] == "Staff Person"
    assert body["people"][0]["initials"] == "SP" and body["people"][0]["label"] == "Docket"
    _post(colleague, {"path": "/inbox?bucket=flagged", "label": "Inbox › Flagged"})
    listed = staff.get("/api/core/presence").json()
    assert listed["online"] == 2 and [p["name"] for p in listed["people"]] == [
        "Colleague Person",
        "Staff Person",
    ]
    # Quiet for a while: away, listed last; quiet for longer: gone.
    Presence.objects.filter(user=colleague_user).update(
        seen_at=timezone.now() - timedelta(minutes=10)
    )
    listed = staff.get("/api/core/presence").json()
    assert listed["online"] == 1 and listed["people"][-1]["state"] == "away"
    Presence.objects.filter(user=colleague_user).update(
        seen_at=timezone.now() - timedelta(minutes=60)
    )
    assert [p["name"] for p in staff.get("/api/core/presence").json()["people"]] == ["Staff Person"]
    # A second heartbeat rewrites the one row; nothing accumulates.
    _post(staff, {"path": "/requests/x", "label": "REQ-2026-000001"})
    assert Presence.objects.filter(user=staff_user).count() == 1
    assert Presence.objects.get(user=staff_user).label == "REQ-2026-000001"
    assert presence.initials("Ada") == "A" and presence.initials("") == "?"


def test_a_requester_sends_a_heartbeat_but_sees_nobody(staff_user, db) -> None:
    from portal.core.services.identity import create_user

    requester = create_user(email="r@example.org", name="Req Person", records_role="requester")
    client = Client()
    client.force_login(requester)
    assert _post(client, {"path": "/my-requests", "label": "My requests"}).json() == {
        "online": 0,
        "people": [],
    }
    assert client.get("/api/core/presence").status_code == 403
    staff = Client()
    staff.force_login(staff_user)
    # Staff still see that the requester is signed in.
    assert [p["name"] for p in staff.get("/api/core/presence").json()["people"]] == ["Req Person"]
