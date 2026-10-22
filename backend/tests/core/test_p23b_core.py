"""P23b, the core side: the date the Users screen draws (205's "Deactivated Aug 30"; 200's "Changed
Jun 2" is Keycloak's since P25a, `GET /core/auth/security` in test_p25a_keycloak.py), the registry
export's formats, range and activity line with System's "Last registry export" (043, 199), and the
Activity page naming what each line is on (190), as far as the reader may know."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.test import Client, RequestFactory
from portal.core.api.activity import on_names
from portal.core.models import Activity, User
from portal.core.services import activity as core_activity
from portal.records.actors import Actor
from portal.records.models import Request
from portal.records.services import common, directory
from portal.records.services import requests as requests_service

RULES = Path(__file__).resolve().parent.parent / "services" / "fixtures"


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _post(client: Client, path: str, body: dict):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def _reserve(actor, agency, who, subject="Synthetic", sensitivity="internal"):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=who.pk,
        subject=subject,
        request_text="Synthetic.",
        sensitivity=sensitivity,
    )


# ---- Users -------------------------------------------------------------------------------------


def test_deactivating_keeps_the_date_and_reactivating_clears_it(
    db, admin_user, colleague_user
) -> None:
    admin = _client(admin_user)
    off = admin.patch(
        f"/api/core/users/{colleague_user.pk}",
        data=json.dumps({"is_active": False}),
        content_type="application/json",
    )
    assert off.status_code == 200, off.content
    assert off.json()["deactivated_at"] is not None
    listed = {u["email"]: u for u in admin.get("/api/core/users").json()}
    assert listed[colleague_user.email]["deactivated_at"] is not None
    assert Activity.objects.filter(action="user_deactivated", subject_id=colleague_user.pk).exists()
    on = admin.patch(
        f"/api/core/users/{colleague_user.pk}",
        data=json.dumps({"is_active": True}),
        content_type="application/json",
    )
    assert on.json()["deactivated_at"] is None
    assert User.objects.get(pk=colleague_user.pk).deactivated_at is None
    assert Activity.objects.filter(action="user_reactivated", subject_id=colleague_user.pk).exists()


# ---- the registry export -----------------------------------------------------------------------


@pytest.fixture
def two_requests(db, staff_user, agency):
    actor = Actor.human(staff_user)
    older = _reserve(actor, agency, staff_user, subject="Older request")
    newer = _reserve(actor, agency, staff_user, subject="Newer request")
    Request.objects.filter(pk=older.pk).update(created_at=datetime(2026, 3, 2, 18, 0, tzinfo=UTC))
    Request.objects.filter(pk=newer.pk).update(created_at=datetime(2026, 3, 9, 18, 0, tzinfo=UTC))
    return older, newer


def test_the_export_as_json_over_a_range(db, staff_user, two_requests) -> None:
    older, newer = two_requests
    staff_user.timezone = "America/Los_Angeles"
    staff_user.save(update_fields=["timezone"])
    with common.using_rule_data(
        rules_path=RULES / "rules_test.toml", calendars_path=RULES / "calendars_test.toml"
    ):
        response = _client(staff_user).get(
            "/api/records/requests/export",
            {"format": "json", "since": "2026-03-01", "until": "2026-03-05"},
        )
    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("application/json")
    assert 'filename="registry.json"' in response["Content-Disposition"]
    body = json.loads(response.content)
    assert body["count"] == 1 and body["made_since"] == "2026-03-01"
    assert [r["tracking_number"] for r in body["requests"]] == [older.tracking_number]
    assert set(body["requests"][0]) == set(body["fields"])
    line = Activity.objects.get(action="registry_exported")
    assert line.actor_id == staff_user.pk and line.data["format"] == "json"
    assert line.data["count"] == 1 and line.data["range"] == "Mar 1, 2026 – Mar 5, 2026"
    assert older.tracking_number not in line.summary


def test_the_export_as_a_pdf_summary_and_an_unknown_format(db, staff_user, two_requests) -> None:
    from io import BytesIO

    from pypdf import PdfReader

    client = _client(staff_user)
    with common.using_rule_data(
        rules_path=RULES / "rules_test.toml", calendars_path=RULES / "calendars_test.toml"
    ):
        pdf = client.get("/api/records/requests/export", {"format": "pdf"})
        assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
        text = "".join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
        assert "Registry export" in text and two_requests[0].tracking_number in text
        assert client.get("/api/records/requests/export", {"format": "xls"}).status_code == 422


def test_the_export_leaves_out_what_the_caller_may_not_see(
    db, staff_user, colleague_user, agency
) -> None:
    sealed = _reserve(Actor.human(staff_user), agency, staff_user, sensitivity="privileged")
    with common.using_rule_data(
        rules_path=RULES / "rules_test.toml", calendars_path=RULES / "calendars_test.toml"
    ):
        response = _client(colleague_user).get("/api/records/requests/export", {"format": "json"})
    assert sealed.tracking_number not in response.content.decode()


def test_system_shows_the_last_export(db, admin_user, staff_user, two_requests) -> None:
    admin = _client(admin_user)
    assert admin.get("/api/core/system").json()["last_export"] is None
    with common.using_rule_data(
        rules_path=RULES / "rules_test.toml", calendars_path=RULES / "calendars_test.toml"
    ):
        _client(staff_user).get("/api/records/requests/export")
    last = admin.get("/api/core/system").json()["last_export"]
    assert last["who"] == staff_user.name and last["format"] == "csv"
    assert last["count"] == 2 and last["range"] == "all time"


# ---- the Activity page's "On" --------------------------------------------------------------------


def test_each_line_names_what_it_is_on(db, admin_user, staff_user, agency) -> None:
    actor = Actor.human(staff_user)
    request = _reserve(actor, agency, staff_user)
    core_activity.record(
        actor, "status_changed", "x", subject_type="request", request_id=request.pk
    )
    core_activity.record(
        actor, "agency_login_changed", "x", subject_type="agency", subject_id=agency.pk
    )
    core_activity.record(
        actor,
        "registry_exported",
        "Exported the registry",
        subject_type="export",
        data={"format": "csv", "range": "all time"},
    )
    core_activity.record(actor, "role_changed", "x", subject_type="user", subject_id=staff_user.pk)
    rows = {r["action"]: r for r in _client(admin_user).get("/api/core/activity").json()["items"]}
    assert rows["status_changed"]["on"] == request.tracking_number
    assert rows["status_changed"]["on_link"] == f"/requests/{request.pk}"
    assert rows["agency_login_changed"]["on"] == agency.name
    assert rows["registry_exported"]["on"] == "CSV · all time"
    assert rows["role_changed"]["on"] == "Users"


def test_a_request_the_reader_may_not_see_is_named_by_its_kind(
    db, staff_user, colleague_user, agency
) -> None:
    """The route is an administrator's, and administrators see every request (P20h); the rule is
    asked anyway, so a reader who may not see a request is told only that a request was involved."""
    actor = Actor.human(staff_user)
    sealed = _reserve(actor, agency, staff_user, sensitivity="privileged")
    line = core_activity.record(
        actor, "status_changed", "x", subject_type="request", request_id=sealed.pk
    )
    asking = RequestFactory().get("/api/core/activity")
    asking.user = colleague_user
    assert on_names(asking, [line])[line.pk] == ("Request", None)
    asking.user = staff_user  # the requester of record sees it
    assert on_names(asking, [line])[line.pk][0] == sealed.tracking_number


def test_a_letter_is_named_by_its_subject(db, admin_user, staff_user) -> None:
    from portal.drafting.services import drafts as drafts_service

    draft = drafts_service.create_draft(subject="ALPR request v3", created_by_user_id=staff_user.pk)
    core_activity.record(
        Actor.human(staff_user),
        "suggestion_applied",
        "x",
        subject_type="draft",
        subject_id=draft.pk,
    )
    rows = _client(admin_user).get("/api/core/activity").json()["items"]
    found = next(r for r in rows if r["action"] == "suggestion_applied")
    assert found["on"] == "ALPR request v3" and found["on_link"] == f"/drafts/{draft.pk}"


def test_an_imported_agency_line_names_the_file(db, admin_user, staff_user) -> None:
    core_activity.record(
        Actor.human(staff_user),
        "agencies_imported",
        "Imported agencies from list.csv: 1 agency added, 0 filled in",
        subject_type="agencies",
        data={"filename": "list.csv"},
    )
    directory.create_agency(Actor.human(staff_user), name="Unused", jurisdiction="Testland")
    rows = _client(admin_user).get("/api/core/activity").json()["items"]
    assert next(r for r in rows if r["action"] == "agencies_imported")["on"] == "list.csv"
