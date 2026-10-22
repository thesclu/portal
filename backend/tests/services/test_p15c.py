"""P15c: reports (the year's figures over what the viewer may see), search across everything,
and the registry export."""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from portal.mail.services import compose, send, transport
from portal.records.models import Deadline
from portal.records.services import deadlines as deadlines_service
from portal.records.services import directory, reports, search
from portal.records.services import events as events_service
from portal.records.services import fees as fees_service
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service
from portal.records.services.access import Viewer

from .conftest import FILED_AT

YEAR = FILED_AT.year


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
    monkeypatch.setattr(send, "_defer", lambda message_id, send_after: None)
    return fake


def _filed(actor, user, agency, subject, *, sensitivity="internal"):
    request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject=subject,
        request_text="Body about widgets.",
        sensitivity=sensitivity,
    )
    filing_service.file_request(actor, request.pk, filed_at=FILED_AT, channel="email")
    return request


def _determination(actor, request, after: timedelta, *, status="determination_issued"):
    event = events_service.record_event(
        actor,
        request.pk,
        event_type="determination",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT + after,
        display_body="Determination.",
        status=status,
    )
    deadline = Deadline.objects.get(request_id=request.pk, deadline_type="statutory_determination")
    deadlines_service.satisfy_deadline(actor, deadline.pk, event.pk)
    return event


def _viewer(user) -> Viewer:
    return Viewer(user_id=user.pk, role="staff")


def test_the_report_counts_what_the_ledger_holds(staff_actor, staff_user, agency) -> None:
    other = directory.create_agency(
        staff_actor, name="Other Agency", jurisdiction="Testland", records_law="TESTLAW"
    )
    due = Deadline.objects.filter(deadline_type="statutory_determination")
    quick = _filed(staff_actor, staff_user, agency, "Quick")
    slow = _filed(staff_actor, staff_user, agency, "Slow")
    none = _filed(staff_actor, staff_user, other, "No answer yet")
    _determination(staff_actor, quick, timedelta(hours=24 * 2))
    # The slow one is answered after its due date, with no records.
    late = due.get(request_id=slow.pk).due_at - FILED_AT.date()
    _determination(staff_actor, slow, late + timedelta(hours=24 * 3), status="no_records_claimed")
    status_service.override_status(staff_actor, quick.pk, "production_received", "Everything came.")
    fee = fees_service.record_fee(staff_actor, quick.pk, status="assessed", amount_cents=1250)
    fees_service.update_fee(
        staff_actor, fee.pk, status="paid", paid_at=FILED_AT + timedelta(hours=48), note="Paid."
    )
    # A fee paid in another year does not count.
    other_fee = fees_service.record_fee(staff_actor, slow.pk, status="assessed", amount_cents=999)
    fees_service.update_fee(
        staff_actor,
        other_fee.pk,
        status="paid",
        paid_at=FILED_AT.replace(year=YEAR + 1),
        note="Later.",
    )

    found = reports.summary(_viewer(staff_user), year=YEAR)
    assert found.filed == 3 and found.determinations == 2
    assert found.on_time == 1 and found.judged == 2
    assert found.fees_paid_cents == 1250
    assert found.median_days_to_determination == round((2 + late.days + 3) / 2)
    lines = {line.agency_name: line for line in found.agencies}
    assert lines["Test Agency"].filed == 2 and lines["Test Agency"].produced == 1
    assert lines["Test Agency"].no_records == 1 and lines["Test Agency"].determinations == 2
    assert lines["Other Agency"].filed == 1 and lines["Other Agency"].determinations == 0
    assert lines["Other Agency"].median_days_to_determination is None
    assert found.years[0] >= YEAR and YEAR in found.years
    # Narrowed to one agency, and another year.
    narrowed = reports.summary(_viewer(staff_user), year=YEAR, agency_id=other.pk)
    assert narrowed.filed == 1 and narrowed.fees_paid_cents == 0 and narrowed.determinations == 0
    assert reports.summary(_viewer(staff_user), year=YEAR + 1).filed == 0
    text = reports.export(found)
    assert text.splitlines()[0].startswith("year,agency,filed")
    assert f"{YEAR},Test Agency,2,2," in text
    assert none.pk  # filed, never answered: counted as filed only


def test_the_report_leaves_out_what_the_viewer_cannot_see(
    staff_actor, staff_user, colleague_user, agency
) -> None:
    _filed(staff_actor, staff_user, agency, "Open")
    _filed(staff_actor, staff_user, agency, "Sealed", sensitivity="privileged")
    assert reports.summary(_viewer(staff_user), year=YEAR).filed == 2
    assert reports.summary(_viewer(colleague_user), year=YEAR).filed == 1


def test_the_reports_api_answers_staff_and_exports(staff_actor, staff_user, agency) -> None:
    _filed(staff_actor, staff_user, agency, "Something")
    client = Client()
    client.force_login(staff_user)
    body = client.get(f"/api/records/reports?year={YEAR}").json()
    assert body["filed"] == 1 and body["agencies"][0]["agency_name"] == "Test Agency"
    export = client.get(f"/api/records/reports/export?year={YEAR}")
    assert export.status_code == 200 and export["Content-Type"].startswith("text/csv")
    assert "Test Agency,1" in export.content.decode()
    assert client.get("/api/records/reports?year=1999").status_code == 422


def test_search_finds_each_kind_and_only_what_is_visible(
    staff_actor, staff_user, colleague_user, agency, provider, media
) -> None:
    open_request = _filed(staff_actor, staff_user, agency, "Widget audit logs")
    sealed = _filed(
        staff_actor, staff_user, agency, "Widget privileged matter", sensitivity="privileged"
    )
    # A message that went out, and a note on mail that came in.
    draft = compose.create_draft(
        staff_actor,
        open_request.pk,
        subject="About the widget request",
        body_text="Hello.",
        to=["records@agency.example.org"],
    )
    send.queue(staff_actor, draft.pk, delay_seconds=0)
    assert send.deliver(draft.pk).outcome == "sent"
    source = sources_service.register_source(
        staff_actor, source_type="postmark_email", raw_storage_url="test://x.eml"
    )
    sources_service.add_source_note(staff_actor, source.pk, "the widget reply came in")
    directory.create_agency(
        staff_actor, name="Widget County", jurisdiction="Testland", records_law="TESTLAW"
    )

    found = search.search(_viewer(staff_user), "widget")
    assert {r.pk for r in found.requests} == {open_request.pk, sealed.pk}
    assert found.sent_total == 1 and found.sent[0].pk == draft.pk
    assert found.received_total >= 1 and any(r.source.pk == source.pk for r in found.received)
    assert [a.name for a in found.agencies] == ["Widget County"]
    # The body of a request is searched too; the colleague does not see the sealed one.
    assert {r.pk for r in search.search(_viewer(staff_user), "widgets.").requests} == {
        open_request.pk,
        sealed.pk,
    }
    theirs = search.search(_viewer(colleague_user), "widget")
    assert [r.pk for r in theirs.requests] == [open_request.pk]
    assert search.search(_viewer(staff_user), "   ").requests_total == 0

    client = Client()
    client.force_login(colleague_user)
    body = client.get("/api/records/search?q=widget").json()
    assert body["requests_total"] == 1 and body["sent_total"] == 1
    assert body["agencies"][0]["name"] == "Widget County"
    assert sealed.tracking_number not in json.dumps(body)


def test_the_registry_export_is_the_docket_as_text(
    staff_actor, staff_user, colleague_user, agency
) -> None:
    shown = _filed(staff_actor, staff_user, agency, "Shown")
    sealed = _filed(staff_actor, staff_user, agency, "Sealed", sensitivity="privileged")
    requests_service.assign_request(staff_actor, shown.pk, colleague_user.pk)
    client = Client()
    client.force_login(colleague_user)
    export = client.get("/api/records/requests/export")
    assert export.status_code == 200 and export["Content-Type"].startswith("text/csv")
    lines = export.content.decode().splitlines()
    assert lines[0].startswith("tracking_number,agency,subject,status")
    assert len(lines) == 2 and shown.tracking_number in lines[1]
    assert "Colleague Person" in lines[1] and "Staff Person" in lines[1]
    assert sealed.tracking_number not in export.content.decode()
    # The same filters as the docket; the owner sees both.
    mine = Client()
    mine.force_login(staff_user)
    assert len(mine.get("/api/records/requests/export").content.decode().splitlines()) == 3
    assert len(mine.get("/api/records/requests/export?q=Sealed").content.decode().splitlines()) == 2
    assert timezone.now().year >= YEAR
