"""P15b: the bell (notifications), what is emailed, deadline reminders, the daily summary, and
the activity log, with a fake mail provider."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from portal.core.models import Activity, Review
from portal.core.services import activity, identity, notifications
from portal.core.services import review as core_review
from portal.mail.services import transport
from portal.records.actors import Actor
from portal.records.models import DeadlineBasis
from portal.records.services import deadlines as deadlines_service
from portal.records.services import filing as records_filing
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import tasks as tasks_service

WHEN = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


class FakeProvider:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __call__(self, token, payload):
        self.sent.append(payload)
        return 200, {"ErrorCode": 0, "MessageID": f"pm-{len(self.sent)}"}


@pytest.fixture
def provider(monkeypatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(transport, "_post", fake)
    return fake


@pytest.fixture
def director(db):
    """P20h: whoever approves an intern's submission. A staff attorney (the Director mark that
    used to decide this is retired)."""
    return identity.create_user(
        email="director@example.org",
        name="Dee Rector",
        records_role="staff_attorney",
    )


@pytest.fixture
def request_row(staff_user, agency):
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Bell test",
        request_text="All the bells.",
    )


def _json(client: Client, method: str, path: str, payload: dict | None = None):
    if payload is None:
        return getattr(client, method)(path)
    return getattr(client, method)(path, data=json.dumps(payload), content_type="application/json")


# ---- notify: who is told, and once ---------------------------------------------------------


def test_assigning_a_request_tells_the_assignee_once_and_never_the_actor(
    provider, staff_user, colleague_user, request_row
) -> None:
    actor = Actor.human(staff_user)
    requests_service.assign_request(actor, request_row.pk, colleague_user.pk, notify=True)
    rows = notifications.unread(colleague_user)
    assert [r.kind for r in rows] == ["assigned"]
    assert request_row.tracking_number in rows[0].text and rows[0].link.endswith(
        str(request_row.pk)
    )
    # Asked to tell them by email (P20b2's "Tell them by email"), and their preference for
    # assignments is on by default, so it is emailed; the mail names the portal link.
    assert len(provider.sent) == 1 and provider.sent[0]["To"] == colleague_user.email
    # Assigning the same thing again while the first is unread writes nothing more.
    requests_service.assign_request(actor, request_row.pk, None)
    requests_service.assign_request(actor, request_row.pk, colleague_user.pk)
    assert len(notifications.unread(colleague_user)) == 1
    # Assigning to yourself tells nobody.
    requests_service.assign_request(actor, request_row.pk, staff_user.pk)
    assert notifications.unread(staff_user) == []
    # A task assignment is the same door.
    tasks_service.create_task(
        actor, request_row.pk, "manual", "Look at this", assigned_to_user_id=colleague_user.pk
    )
    assert {r.kind for r in notifications.unread(colleague_user)} == {"assigned"}


def test_a_mention_in_a_note_tells_the_person_named(provider, staff_user, colleague_user) -> None:
    source = sources_service.register_source(
        Actor.human(staff_user), source_type="postmark_email", raw_storage_url="test://m.pdf"
    )
    actor = Actor.human(staff_user)
    sources_service.add_source_note(actor, source.pk, "@Colleague can you take this one?")
    sources_service.add_source_note(actor, source.pk, "@staff is me; @Colleague Person again")
    rows = notifications.unread(colleague_user)
    assert len(rows) == 2 and all(r.kind == "mention" for r in rows)
    assert "Staff Person mentioned you" in rows[0].text
    assert notifications.unread(staff_user) == []
    assert [u.pk for u in notifications.mentioned("nothing here")] == []


def test_flags_and_arrivals_go_only_to_people_who_opted_in(
    provider, staff_user, colleague_user
) -> None:
    assert notifications.staff_who_want("flagged") == []
    notifications.set_preferences(colleague_user, email={"flagged": True}, daily_summary=None)
    assert notifications.staff_who_want("flagged") == [colleague_user.pk]
    source = sources_service.register_source(
        Actor.human(staff_user), source_type="postmark_email", raw_storage_url="test://f.pdf"
    )
    sources_service.flag_source(Actor.human(staff_user), source.pk, "Odd", reason="other")
    assert [r.kind for r in notifications.unread(colleague_user)] == ["flagged"]
    assert notifications.unread(staff_user) == []


def test_review_asks_tell_approvers_and_decisions_tell_the_submitter(
    provider, staff_user, director, request_row
) -> None:
    from portal.mail.services import compose

    intern = identity.create_user(
        email="intern@example.org", name="An Intern", records_role="intern"
    )
    message = compose.create_draft(
        Actor.human(intern), request_row.pk, subject="A reply", to=["records@agency.example.org"]
    )
    submitted = core_review.submit(
        intern, "message", message.pk, request_id=request_row.pk, note="Look please."
    )
    told = notifications.unread(director)
    assert [r.kind for r in told] == ["review"] and told[0].link == f"/approvals/{submitted.pk}"
    assert notifications.unread(staff_user) == []  # staff do not approve (42b)
    core_review.decide(director, submitted.pk, Review.APPROVED)
    mine = notifications.unread(intern)
    assert len(mine) == 1 and "approved" in mine[0].text
    assert Activity.objects.filter(action="review_decided").count() == 1


# ---- the API: the bell, read, preferences --------------------------------------------------


def test_the_bell_lists_unread_and_marks_read(staff_user, colleague_user, request_row) -> None:
    requests_service.assign_request(Actor.human(staff_user), request_row.pk, colleague_user.pk)
    client = Client()
    client.force_login(colleague_user)
    body = _json(client, "get", "/api/core/notifications").json()
    assert body["unread"] == 1 and body["items"][0]["kind"] == "assigned"
    body = _json(client, "post", "/api/core/notifications/read", {"ids": None}).json()
    assert body["unread"] == 0 and body["items"][0]["read_at"] is not None
    other = Client()
    other.force_login(staff_user)
    assert _json(other, "get", "/api/core/notifications").json()["items"] == []


def test_preferences_are_per_person_and_turn_email_off(
    provider, staff_user, colleague_user, request_row
) -> None:
    client = Client()
    client.force_login(colleague_user)
    body = _json(client, "get", "/api/core/notifications/preferences").json()
    assert body["email"]["assigned"] is True and body["daily_summary"] is True
    assert body["kinds"] == list(notifications.KINDS)
    body = _json(
        client,
        "put",
        "/api/core/notifications/preferences",
        {"email": {"assigned": False}, "daily_summary": False},
    ).json()
    assert body["email"]["assigned"] is False and body["daily_summary"] is False
    requests_service.assign_request(
        Actor.human(staff_user), request_row.pk, colleague_user.pk, notify=True
    )
    assert len(notifications.unread(colleague_user)) == 1 and provider.sent == []
    with pytest.raises(ValueError):
        notifications.set_preferences(colleague_user, email={"nope": True}, daily_summary=None)


# ---- the clock -----------------------------------------------------------------------------


def test_deadline_reminders_go_to_the_assignee_within_the_window_once(
    provider, staff_user, colleague_user, request_row
) -> None:
    actor = Actor.human(staff_user)
    records_filing.file_request(actor, request_row.pk, filed_at=WHEN, channel="email")
    requests_service.assign_request(actor, request_row.pk, colleague_user.pk)
    notifications.mark_read(colleague_user)
    deadlines_service.open_deadline(
        actor,
        request_row.pk,
        deadline_type="challenge_response",
        due_at=(WHEN + timedelta(hours=48)).date(),
        basis=DeadlineBasis.INTERNAL,
    )
    deadlines_service.open_deadline(
        actor,
        request_row.pk,
        deadline_type="challenge_response",
        due_at=(WHEN + timedelta(hours=24 * 30)).date(),
        basis=DeadlineBasis.INTERNAL,
    )
    assert notifications.deadline_reminders(now=WHEN) == 1
    assert notifications.deadline_reminders(now=WHEN) == 0
    rows = notifications.unread(colleague_user)
    assert [r.kind for r in rows] == ["deadline"] and "Mar 4" in rows[0].text


def test_the_daily_summary_goes_once_at_the_summary_hour_in_the_persons_zone(
    provider, staff_user, colleague_user, request_row
) -> None:
    actor = Actor.human(staff_user)
    records_filing.file_request(actor, request_row.pk, filed_at=WHEN, channel="email")
    tasks_service.create_task(
        actor, request_row.pk, "manual", "Read the reply", assigned_to_user_id=colleague_user.pk
    )
    colleague_user.timezone = "America/Los_Angeles"
    colleague_user.save(update_fields=["timezone"])
    seven_la = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # 07:00 in Los Angeles
    assert notifications.send_daily_summaries(now=seven_la - timedelta(hours=1)) == 0
    assert notifications.send_daily_summaries(now=seven_la) == 1
    assert notifications.send_daily_summaries(now=seven_la + timedelta(minutes=30)) == 0
    mail = provider.sent[-1]
    assert mail["To"] == colleague_user.email and "Read the reply" in mail["TextBody"]
    assert "Good morning, Colleague" in mail["TextBody"]
    # Nothing to say, nothing sent; and a person who turned it off gets none.
    assert notifications.summary_text(staff_user, now=seven_la) is None
    notifications.set_preferences(colleague_user, email=None, daily_summary=False)
    assert notifications.send_daily_summaries(now=seven_la + timedelta(hours=24)) == 0


# ---- the activity log ----------------------------------------------------------------------


def test_the_services_write_activity_and_administrators_read_and_export_it(
    staff_user, admin_user, colleague_user, request_row
) -> None:
    actor = Actor.human(staff_user)
    records_filing.file_request(actor, request_row.pk, filed_at=WHEN, channel="email")
    actions = list(Activity.objects.order_by("at").values_list("action", flat=True))
    assert "filed" in actions and "status_changed" in actions
    filed = Activity.objects.get(action="filed")
    assert filed.actor_id == staff_user.pk and filed.request_id == request_row.pk
    assert request_row.tracking_number in filed.summary
    system = activity.record(Actor.for_system("sweep"), "swept", "Swept overdue deadlines")
    assert system.actor_id is None and system.system == "sweep"

    client = Client()
    client.force_login(admin_user)
    body = client.get("/api/core/activity").json()
    assert body["total"] == len(actions) + 1 and body["items"][0]["who"] == "sweep"
    assert set(body["actions"]) >= {"filed", "status_changed", "swept"}
    body = client.get(f"/api/core/activity?actor_id={staff_user.pk}&action=filed").json()
    assert body["total"] == 1 and body["items"][0]["who"] == "Staff Person"
    assert client.get("/api/core/activity?system=true").json()["total"] == 1
    assert client.get("/api/core/activity?since=2030-01-01").json()["total"] == 0
    export = client.get("/api/core/activity/export?action=filed")
    assert export.status_code == 200 and export["Content-Type"].startswith("text/csv")
    lines = export.content.decode().splitlines()
    assert lines[0].startswith("when,who,action") and len(lines) == 2

    staff = Client()
    staff.force_login(colleague_user)
    assert staff.get("/api/core/activity").status_code == 403
    assert staff.get("/api/core/activity/export").status_code == 403
