"""P20b2: the backend the Home, Docket and Calendar screens read.

The list's new filters and the export by ids; the stat strip's summary (its numbers must be the
ones the screen used to compute from the list route, so a parity test computes them that way);
the sparklines' history, rebuilt from the status ledger; the calendar's finished deadlines;
holiday names; the agency's reference on the request; the follow-up draft; the assign dialog's
"Tell them by email" and its per-person counts. Every count is checked against a privileged
request the viewer is not on the list of, and the reads are repeated as portal_app so row-level
security is in play.
"""

import contextlib
import json
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test import Client
from portal.core import rls
from portal.core.models import CalendarDefinition, CalendarHoliday, Notification, Setting
from portal.core.services import notifications
from portal.mail.models import Message
from portal.mail.services import transport
from portal.records.actors import Actor
from portal.records.models import Agency, StatusChange, writable
from portal.records.services import common, directory
from portal.records.services import deadlines as deadlines_service
from portal.records.services import docket_summary as summary_service
from portal.records.services import edits as edits_service
from portal.records.services import events as events_service
from portal.records.services import requests as requests_service
from portal.records.services import status as status_service

from tests.api.test_p8b_api import FIXTURES

BASE = "/api/records"
LA = ZoneInfo("America/Los_Angeles")
TODAY = date(2026, 3, 11)  # a Wednesday
WEEK = [date(2026, 3, d) for d in (9, 10, 11, 12, 13)]


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
def rules():
    with common.using_rule_data(
        rules_path=FIXTURES / "rules_test.toml", calendars_path=FIXTURES / "calendars_test.toml"
    ) as data:
        yield data


@contextlib.contextmanager
def as_app_role():
    """Run the reads as portal_app (row-level security on), inside the test's transaction."""
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ROLE portal_app")
    try:
        yield
    finally:
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post(client: Client, path: str, payload: dict | None = None):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json")


def _reserve(actor, agency, subject, *, sensitivity="internal", requester=None):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=(requester or actor.user_id),
        subject=subject,
        request_text=f"{subject} text",
        sensitivity=sensitivity,
    )


def _status(actor, request, code):
    status_service.override_status(actor, request.pk, code, "set for the test")


def _deadline(actor, request, due, kind="production"):
    return deadlines_service.open_deadline(
        actor, request.pk, deadline_type=kind, due_at=due, basis="agency_commitment"
    )


@pytest.fixture
def docket(staff_user, colleague_user, agency):
    """Five requests the colleague may see and one privileged one they may not (staff_user
    reserved it, so staff_user is on its list)."""
    actor = Actor.human(staff_user)
    r1 = _reserve(actor, agency, "Filed and overdue")
    _status(actor, r1, "filed")
    _deadline(actor, r1, date(2026, 3, 2))
    requests_service.assign_request(actor, r1.pk, colleague_user.pk)
    r2 = _reserve(actor, agency, "New production due today")
    _status(actor, r2, "new_production")
    _deadline(actor, r2, TODAY)
    r3 = _reserve(actor, agency, "Partial production due Friday")
    _status(actor, r3, "partial_production")
    _deadline(actor, r3, date(2026, 3, 13))
    r4 = _reserve(actor, agency, "Closed")
    _status(actor, r4, "closed")
    r5 = _reserve(actor, agency, "Denied, still assigned")
    _status(actor, r5, "denied")
    requests_service.assign_request(actor, r5.pk, colleague_user.pk)
    sealed = _reserve(actor, agency, "SEALED in progress", sensitivity="privileged")
    _status(actor, sealed, "in_progress")
    _deadline(actor, sealed, date(2026, 3, 1))
    _deadline(actor, sealed, date(2026, 3, 10), kind="statutory_determination")
    requests_service.assign_request(actor, sealed.pk, staff_user.pk)
    return {"r1": r1, "r2": r2, "r3": r3, "r4": r4, "r5": r5, "sealed": sealed}


def _ids(page: dict) -> set[str]:
    return {row["id"] for row in page["items"]}


# ---- the list's filters and the export by ids --------------------------------------------------


def test_the_list_takes_several_statuses_a_span_of_dates_and_open_only(colleague_user, docket):
    client = _client(colleague_user)
    get = lambda **q: client.get(f"{BASE}/requests", q).json()  # noqa: E731
    r = {k: str(v.pk) for k, v in docket.items()}
    assert _ids(get(status="new_production,partial_production")) == {r["r2"], r["r3"]}
    assert _ids(get(status=" filed , ")) == {r["r1"]}
    assert _ids(get(due_after="2026-03-11")) == {r["r2"], r["r3"]}
    assert _ids(get(due_after="2026-03-09", due_before="2026-03-12")) == {r["r2"]}
    # Open is "not terminal": denied, closed and withdrawn are left out.
    assert _ids(get(open="true")) == {r["r1"], r["r2"], r["r3"]}
    assert _ids(get()) == {r["r1"], r["r2"], r["r3"], r["r4"], r["r5"]}
    # The sealed request moves nothing for someone not on its list.
    assert r["sealed"] not in _ids(get(status="in_progress"))


def test_the_export_takes_exactly_the_selected_ids(colleague_user, staff_user, docket):
    client = _client(colleague_user)
    picked = f"{docket['r1'].pk},{docket['r3'].pk},{docket['sealed'].pk}"
    resp = client.get(f"{BASE}/requests/export", {"ids": picked})
    assert resp.status_code == 200
    lines = resp.content.decode().strip().splitlines()
    assert len(lines) == 3  # the header and the two the colleague may see
    body = "\n".join(lines)
    assert docket["r1"].tracking_number in body and docket["r3"].tracking_number in body
    assert docket["sealed"].tracking_number not in body
    assert docket["r2"].tracking_number not in body
    # The insider exports the sealed one when they pick it.
    mine = _client(staff_user).get(f"{BASE}/requests/export", {"ids": str(docket["sealed"].pk)})
    assert docket["sealed"].tracking_number in mine.content.decode()
    bad = client.get(f"{BASE}/requests/export", {"ids": "not-an-id"})
    assert bad.status_code == 422


# ---- the summary -----------------------------------------------------------------------------


def test_the_summary_counts_what_the_viewer_may_see(colleague_user, staff_user, docket):
    figures = summary_service.summary(colleague_user.pk, TODAY)
    assert figures.today == TODAY
    assert figures.overdue == 1  # r1; the sealed one is hidden
    assert [(d.date, d.count) for d in figures.due_by_date] == list(
        zip(WEEK, [0, 0, 1, 0, 1], strict=True)
    )
    assert figures.producing == 2 and figures.producing_unreviewed == 1
    assert figures.waiting_on_agency == 1
    assert figures.assigned_to_me == 2  # r1 and r5, whatever their status
    assert figures.all_open == 3
    insider = summary_service.summary(staff_user.pk, TODAY)
    assert insider.overdue == 2 and insider.waiting_on_agency == 2 and insider.all_open == 4
    assert [d.count for d in insider.due_by_date] == [0, 0, 1, 0, 1]  # next deadline is Mar 1
    assert insider.assigned_to_me == 1


def test_the_summary_is_the_number_the_screen_computed_from_the_list(colleague_user, docket):
    """`frontend/src/screens/docket/stats.ts` before P20b2: one `limit=0` read per figure."""
    client = _client(colleague_user)

    def total(**query) -> int:
        return client.get(f"{BASE}/requests", {**query, "limit": 0}).json()["total"]

    def statuses(codes) -> int:
        return sum(total(status=code) for code in codes)

    terminal = ["denied", "closed", "withdrawn"]
    before = {d: total(due_before=d.isoformat()) for d in [*WEEK, date(2026, 3, 14), TODAY]}
    week = [before[day] - before[WEEK[i]] for i, day in enumerate([*WEEK[1:], date(2026, 3, 14)])]
    figures = summary_service.summary(colleague_user.pk, TODAY)
    assert figures.overdue == before[TODAY]
    assert [d.count for d in figures.due_by_date] == week
    assert figures.producing == statuses(summary_service.PRODUCING_STATUSES)
    assert figures.producing_unreviewed == statuses(summary_service.UNREVIEWED_STATUSES)
    assert figures.waiting_on_agency == statuses(summary_service.WAITING_ON_AGENCY_STATUSES)
    assert figures.assigned_to_me == total(assignee="me")
    assert figures.all_open == total() - statuses(terminal)


def test_the_summary_route_reads_today_in_the_viewers_zone(colleague_user, docket):
    with as_app_role():
        body = _client(colleague_user).get(f"{BASE}/requests/summary").json()
    expected_today = datetime.now(UTC).astimezone(ZoneInfo(colleague_user.timezone)).date()
    assert body["today"] == expected_today.isoformat()
    assert [d["date"] for d in body["due_by_date"]] == [
        d.isoformat() for d in summary_service.work_week(expected_today)
    ]
    assert body["all_open"] == 3 and body["assigned_to_me"] == 2
    assert body["producing_statuses"] == list(summary_service.PRODUCING_STATUSES)
    assert body["waiting_statuses"] == ["filed", "acknowledged", "in_progress"]


def test_the_work_week_is_monday_to_friday_and_a_sunday_looks_ahead():
    assert summary_service.work_week(date(2026, 3, 11)) == WEEK
    assert summary_service.work_week(date(2026, 3, 9)) == WEEK
    assert summary_service.work_week(date(2026, 3, 14))[0] == date(2026, 3, 9)  # Saturday
    assert summary_service.work_week(date(2026, 3, 15))[0] == date(2026, 3, 16)  # Sunday


def test_the_summary_is_staff_only(requester_user, client):
    client.force_login(requester_user)
    assert client.get(f"{BASE}/requests/summary").status_code == 403
    assert client.get(f"{BASE}/requests/history").status_code == 403
    assert client.get(f"{BASE}/requests/assignee-counts").status_code == 403
    assert Client().get(f"{BASE}/requests/summary").status_code == 401


# ---- the history -----------------------------------------------------------------------------


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=LA)


def _set_effective(change: StatusChange, when: datetime) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE records.status_changes SET effective_at = %s WHERE id = %s", [when, change.pk]
        )


def _set_created(request, when: datetime) -> None:
    with connection.cursor() as cur:
        cur.execute("UPDATE records.requests SET created_at = %s WHERE id = %s", [when, request.pk])


def _changes(request) -> list[StatusChange]:
    return list(StatusChange.objects.filter(request_id=request.pk).order_by("effective_at"))


def test_the_history_is_rebuilt_from_the_status_ledger(staff_user, colleague_user, agency):
    actor = Actor.human(staff_user)
    a = _reserve(actor, agency, "A")
    _status(actor, a, "filed")
    _status(actor, a, "determination_overdue")
    _status(actor, a, "closed")
    reserved, filed, overdue, closed = _changes(a)
    _set_created(a, _at(1, 9))
    _set_effective(reserved, _at(1, 9))
    _set_effective(filed, _at(7, 10))
    _set_effective(overdue, _at(8, 23, 30))  # still the 8th where the viewer is
    _set_effective(closed, _at(9, 0, 30))

    b = _reserve(actor, agency, "B")
    _status(actor, b, "withdrawn")
    b_reserved, withdrawn = _changes(b)
    _set_created(b, _at(8, 12))
    _set_effective(b_reserved, _at(8, 12))
    _set_effective(withdrawn, _at(9, 8))
    with connection.cursor() as cur:  # a voided row never counts
        cur.execute(
            "UPDATE records.status_changes SET voided_at = now() WHERE id = %s", [withdrawn.pk]
        )

    sealed = _reserve(actor, agency, "SEALED", sensitivity="privileged")
    (s_reserved,) = _changes(sealed)
    _set_created(sealed, _at(1, 9))
    _set_effective(s_reserved, _at(1, 9))

    points = summary_service.history(colleague_user.pk, date(2026, 3, 10), 5, LA)
    assert [(p.date.day, p.open, p.overdue) for p in points] == [
        (6, 1, 0),
        (7, 1, 0),
        (8, 2, 1),
        (9, 1, 0),
        (10, 1, 0),
    ]
    insider = summary_service.history(staff_user.pk, date(2026, 3, 10), 5, LA)
    assert [p.open for p in insider] == [2, 2, 3, 2, 2]
    with as_app_role():
        rls.set_web_context(colleague_user)  # what the API's session auth sets for a request
        again = summary_service.history(colleague_user.pk, date(2026, 3, 10), 5, LA)
    assert again == points


def test_the_history_route_spans_the_setting_and_is_clamped(colleague_user, docket):
    client = _client(colleague_user)
    body = client.get(f"{BASE}/requests/history").json()
    assert body["span"] == 11 and len(body["points"]) == 11
    today = datetime.now(UTC).astimezone(ZoneInfo(colleague_user.timezone)).date()
    assert body["points"][-1]["date"] == today.isoformat()
    # Everything in the docket fixture was made just now, so only the last point counts it.
    assert body["points"][-1]["open"] == 3 and body["points"][0]["open"] == 0
    Setting.objects.filter(key="records.docket_history_span").update(value=4)
    assert client.get(f"{BASE}/requests/history").json()["span"] == 4
    assert client.get(f"{BASE}/requests/history", {"span": 2}).json()["span"] == 2
    huge = client.get(f"{BASE}/requests/history", {"span": 100000}).json()
    assert huge["span"] == summary_service.MAX_HISTORY_SPAN
    assert client.get(f"{BASE}/requests/history", {"span": 0}).json()["span"] == 1


# ---- the calendar ----------------------------------------------------------------------------


def test_the_calendar_shows_finished_deadlines_with_their_status(staff_user, agency, rules):
    actor = Actor.human(staff_user)
    req = _reserve(actor, agency, "Calendar")
    active = _deadline(actor, req, date(2026, 3, 16))
    satisfied = _deadline(actor, req, date(2026, 3, 17), kind="statutory_determination")
    event = events_service.record_event(
        actor,
        req.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=datetime(2026, 3, 17, 17, tzinfo=UTC),
        display_body="The answer",
    )
    deadlines_service.satisfy_deadline(actor, satisfied.pk, event.pk)
    waived = _deadline(actor, req, date(2026, 3, 18), kind="fee_response")
    deadlines_service.waive_deadline(actor, waived.pk, "Not needed")
    missed = _deadline(actor, req, date(2026, 3, 2), kind="other")
    deadlines_service.mark_deadline_missed(Actor.for_system("overdue_sweep"), missed.pk)
    old = _deadline(actor, req, date(2026, 3, 19), kind="challenge_response")
    deadlines_service.open_deadline(
        actor,
        req.pk,
        deadline_type="challenge_response",
        due_at=date(2026, 3, 20),
        basis="agency_commitment",
        supersedes=old.pk,
        note="moved",
    )
    view = (
        _client(staff_user)
        .get(f"{BASE}/calendar", {"start": "2026-03-01", "end": "2026-03-31"})
        .json()
    )
    by_id = {r["deadline"]["id"]: r["status"] for r in view["deadlines"]}
    assert by_id[str(active.pk)] == "active"
    assert by_id[str(satisfied.pk)] == "satisfied"
    assert by_id[str(waived.pk)] == "waived"
    assert by_id[str(missed.pk)] == "missed"
    assert str(old.pk) not in by_id  # superseded: the replacement is shown instead
    assert all(r["status"] == r["deadline"]["status"] for r in view["deadlines"])
    # An active date on a closed request is still left out, as before.
    _status(actor, req, "closed")
    after = (
        _client(staff_user)
        .get(f"{BASE}/calendar", {"start": "2026-03-01", "end": "2026-03-31"})
        .json()
    )
    statuses = {r["status"] for r in after["deadlines"]}
    assert "active" not in statuses and {"satisfied", "waived", "missed"} <= statuses


# ---- holiday names ---------------------------------------------------------------------------


def test_an_administrator_names_a_holiday_and_the_calendar_shows_it(admin_user, staff_user, rules):
    admin = _client(admin_user)
    calendar = CalendarDefinition.objects.create(key="testland", timezone="America/Los_Angeles")
    CalendarHoliday.objects.create(calendar=calendar, date=date(2026, 1, 13))
    path = "/api/core/calendars/testland/holidays/2026-01-13"
    staff = _client(staff_user)
    assert (
        staff.patch(
            path, data=json.dumps({"name": "x"}), content_type="application/json"
        ).status_code
        == 403
    )
    resp = admin.patch(
        path, data=json.dumps({"name": "  Founders' Holiday  "}), content_type="application/json"
    )
    assert resp.status_code == 200 and resp.json() == {
        "date": "2026-01-13",
        "name": "Founders' Holiday",
    }
    january = staff.get(f"{BASE}/calendar", {"start": "2026-01-01", "end": "2026-01-31"}).json()
    assert january["calendars"][0]["holidays"] == [
        {"date": "2026-01-13", "name": "Founders' Holiday"}
    ]
    rules_view = staff.get(f"{BASE}/rules").json()
    (testland,) = [c for c in rules_view["calendars"] if c["key"] == "testland"]
    named = {h["date"]: h["name"] for h in testland["holidays"]}
    assert named["2026-01-13"] == "Founders' Holiday" and named["2026-11-11"] is None
    cleared = admin.patch(path, data=json.dumps({"name": ""}), content_type="application/json")
    assert cleared.json()["name"] is None
    missing = admin.patch(
        "/api/core/calendars/testland/holidays/2026-01-14",
        data=json.dumps({"name": "x"}),
        content_type="application/json",
    )
    assert missing.status_code == 404


def test_no_holiday_has_a_name_nobody_typed(db):
    assert not CalendarHoliday.objects.exclude(name="").exists()


# ---- the request read ------------------------------------------------------------------------


def test_the_request_read_carries_the_agencys_reference(staff_user, reserved, staff_client):
    assert (
        staff_client.get(f"{BASE}/requests/{reserved.pk}").json()["agency_reference_number"] is None
    )
    edits_service.edit_with_audit(
        Actor.human(staff_user),
        "requests",
        reserved.pk,
        "agency_reference_number",
        "TA-2026-0001",
        "From their acknowledgment",
    )
    body = staff_client.get(f"{BASE}/requests/{reserved.pk}").json()
    assert body["agency_reference_number"] == "TA-2026-0001"


# ---- the follow-up draft ---------------------------------------------------------------------


def test_the_follow_up_draft_is_rendered_from_the_settings_and_saves_nothing(
    staff_user, agency, requester_user
):
    actor = Actor.human(staff_user)
    with writable():
        Agency.objects.filter(pk=agency.pk).update(default_request_email="records@example.org")
    directory.create_contact(
        actor, agency.pk, name="Clerk", email="clerk@example.org", copy_on_every_request=True
    )
    directory.create_contact(actor, agency.pk, name="Other", email="other@example.org")
    req = _reserve(actor, agency, "Use of force reports")
    edits_service.edit_with_audit(
        actor, "requests", req.pk, "agency_reference_number", "TA-7", "From their letter"
    )
    lapsed = _deadline(actor, req, date(2026, 3, 2), kind="statutory_determination")
    deadlines_service.mark_deadline_missed(Actor.for_system("overdue_sweep"), lapsed.pk)
    _deadline(actor, req, date(2026, 4, 2))
    Setting.objects.filter(key="records.followup_subject").update(
        value="{tracking_number}: {deadline_kind} {unknown}"
    )
    before = Message.objects.count()
    resp = _post(_client(staff_user), f"/api/mail/requests/{req.pk}/followup-draft")
    assert resp.status_code == 200, resp.content
    draft = resp.json()
    assert draft["to"] == ["records@example.org"]
    assert draft["cc"] == ["clerk@example.org"]
    # The most recently missed kind wins over the next active one; an unknown placeholder stays.
    assert draft["subject"] == f"{req.tracking_number}: determination {{unknown}}"
    assert "(your reference TA-7)" in draft["body"]
    assert "of March 2, 2026" in draft["body"]
    assert Message.objects.count() == before
    requester = _client(requester_user)
    assert _post(requester, f"/api/mail/requests/{req.pk}/followup-draft").status_code == 403


def test_the_follow_up_draft_with_no_deadline_and_no_address(staff_user, reserved):
    resp = _post(_client(staff_user), f"/api/mail/requests/{reserved.pk}/followup-draft")
    assert resp.status_code == 200
    draft = resp.json()
    assert draft["to"] == [] and draft["cc"] == []
    assert draft["subject"] == f"Follow-up on our records request {reserved.tracking_number}"
    assert "{" not in draft["body"]


def test_a_person_may_draft_a_follow_up_message_but_not_a_filing(staff_user, reserved):
    client = _client(staff_user)
    base = {
        "request_id": str(reserved.pk),
        "subject": "Following up",
        "body_text": "Any news?",
        "to": [{"address": "records@example.org"}],
    }
    made = _post(client, "/api/mail/messages", {**base, "kind": "followup"})
    assert made.status_code == 201 and made.json()["kind"] == "followup"
    assert _post(client, "/api/mail/messages", {**base, "kind": "filing"}).status_code == 422


# ---- assigning, by email or not, and the per-person counts ------------------------------------


def test_assign_tells_by_email_only_when_asked_and_the_person_allows_it(
    provider, staff_user, colleague_user, reserved, own_request
):
    client = _client(staff_user)
    path = f"{BASE}/requests/{reserved.pk}/assign"
    assert _post(client, path, {"user_id": str(colleague_user.pk)}).status_code == 200
    assert [n.kind for n in notifications.unread(colleague_user)] == ["assigned"]
    assert provider.sent == []  # in the portal only
    notifications.mark_read(colleague_user)
    _post(client, path, {"user_id": None})
    _post(client, path, {"user_id": str(colleague_user.pk), "notify": True})
    assert len(provider.sent) == 1 and provider.sent[0]["To"] == colleague_user.email
    assert Notification.objects.filter(user=colleague_user, emailed_at__isnull=False).count() == 1
    # Their own preference still decides.
    notifications.set_preferences(colleague_user, email={"assigned": False}, daily_summary=None)
    other = f"{BASE}/requests/{own_request.pk}/assign"
    _post(client, other, {"user_id": str(colleague_user.pk), "notify": True})
    assert len(provider.sent) == 1


def test_assignee_counts_are_open_requests_the_viewer_may_see(staff_user, colleague_user, docket):
    with as_app_role():
        outsider = _client(colleague_user).get(f"{BASE}/requests/assignee-counts").json()
    # r1 is open; r5 is denied, so it is not counted; the sealed one is hidden.
    assert outsider == {str(colleague_user.pk): 1}
    insider = _client(staff_user).get(f"{BASE}/requests/assignee-counts").json()
    assert insider == {str(colleague_user.pk): 1, str(staff_user.pk): 1}


def test_the_viewer_zone_falls_back_when_theirs_is_not_a_zone(db):
    assert summary_service.viewer_zone("Mars/Olympus").key == "America/Los_Angeles"
    assert summary_service.viewer_zone("Europe/Paris").key == "Europe/Paris"
    assert summary_service.viewer_today(LA, datetime.combine(TODAY, time(6), tzinfo=UTC)) == date(
        2026, 3, 10
    )
