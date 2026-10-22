"""P20f: what the Users screen reads: a person's last activity. (Since when two-step sign-in has
been on is Keycloak's since P25a: `GET /core/auth/security`, test_p25a_keycloak.py.)"""

from datetime import UTC, datetime

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from portal.core.models import Presence, User

EARLIER = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
LATER = datetime(2026, 3, 4, 9, 30, tzinfo=UTC)


def _by_email(body: list[dict]) -> dict[str, dict]:
    return {row["email"]: row for row in body}


def test_last_active_is_the_newer_of_the_heartbeat_and_the_sign_in(
    db, staff_user, colleague_user, admin_user
) -> None:
    User.objects.filter(pk=staff_user.pk).update(last_login=EARLIER)
    Presence.objects.create(user=staff_user, path="/docket", seen_at=LATER)
    User.objects.filter(pk=colleague_user.pk).update(last_login=LATER)
    Presence.objects.create(user=colleague_user, path="/inbox", seen_at=EARLIER)
    User.objects.filter(pk=admin_user.pk).update(last_login=None)

    client = Client()
    client.force_login(staff_user)
    User.objects.filter(pk=staff_user.pk).update(last_login=EARLIER)  # signing in moved it
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/api/core/users")
    assert response.status_code == 200
    people = _by_email(response.json())
    assert people[staff_user.email]["last_active_at"] == "2026-03-04T09:30:00Z"
    assert people[colleague_user.email]["last_active_at"] == "2026-03-04T09:30:00Z"
    assert people[colleague_user.email]["last_login"] == "2026-03-04T09:30:00Z"
    assert people[admin_user.email]["last_active_at"] is None
    # The heartbeat comes with the list in one read, not one query per person.
    assert sum('"presence"' in q["sql"] for q in queries.captured_queries) == 1
