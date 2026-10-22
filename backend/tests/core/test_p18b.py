"""P18b: the rules and calendars as tables an administrator edits, read by `rule_data()` and so
by filing; seeded once from the shipped files."""

import json
from datetime import UTC, date, datetime

from deadline_rules import load_calendars, load_rules
from django.test import Client
from portal.core.models import Activity, DeadlineRule
from portal.core.services import rules as rules_service
from portal.records.actors import Actor
from portal.records.models import Deadline
from portal.records.services import directory
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services.common import rule_data

FILED = datetime(2026, 3, 3, 15, 0, tzinfo=UTC)  # a Tuesday where the test calendar is


def _json(client: Client, method: str, path: str, payload=None):
    if payload is None:
        return getattr(client, method)(path)
    return getattr(client, method)(path, data=json.dumps(payload), content_type="application/json")


def test_the_tables_are_seeded_from_the_shipped_files_and_are_what_is_read(db) -> None:
    shipped_calendars, shipped_laws = load_calendars()
    shipped_rules = load_rules()
    data = rule_data()
    assert {r.id for r in data.rules} == {r.id for r in shipped_rules}
    assert data.law_calendar == shipped_laws
    assert set(data.calendars) == set(shipped_calendars)
    for key, calendar in shipped_calendars.items():
        assert data.calendars[key].holidays == calendar.holidays
        assert data.calendars[key].timezone == calendar.timezone
    by_id = {r.id: r for r in shipped_rules}
    for rule in data.rules:
        assert rule == by_id[rule.id]


def test_an_administrator_adds_a_calendar_a_law_and_a_rule_and_filing_uses_them(
    admin_user, staff_user
) -> None:
    admin, staff = Client(), Client()
    admin.force_login(admin_user)
    staff.force_login(staff_user)
    assert (
        _json(staff, "post", "/api/core/calendars", {"key": "x", "timezone": "UTC"}).status_code
        == 403
    )
    bad = _json(admin, "post", "/api/core/calendars", {"key": "elsewhere", "timezone": "Mars/Base"})
    assert bad.status_code == 422
    made = _json(
        admin,
        "post",
        "/api/core/calendars",
        {"key": "Elsewhere", "timezone": "America/Los_Angeles", "weekend": [6, 5]},
    )
    assert made.status_code == 201 and made.json() == {
        "key": "elsewhere",
        "timezone": "America/Los_Angeles",
        "weekend": [5, 6],
    }
    # A rule needs its law on a calendar first, a known unit and type, a count, a citation.
    rule = {
        "rule_id": "elselaw.determination.general.2026-01-01",
        "law": "ELSELAW",
        "deadline_type": "statutory_determination",
        "count": 7,
        "unit": "calendar_days",
        "effective_from": "2026-01-01",
        "citation": "Typed by a person",
    }
    assert _json(admin, "post", "/api/core/rules", rule).status_code == 422
    assert _json(admin, "put", "/api/core/laws/ELSELAW", {"calendar": "elsewhere"}).json() == {
        "law": "ELSELAW",
        "calendar": "elsewhere",
    }
    assert _json(admin, "post", "/api/core/rules", {**rule, "unit": "weeks"}).status_code == 422
    assert _json(admin, "post", "/api/core/rules", {**rule, "citation": " "}).status_code == 422
    assert _json(admin, "post", "/api/core/rules", {**rule, "count": 0}).status_code == 422
    added = _json(admin, "post", "/api/core/rules", rule)
    assert added.status_code == 201, added.content
    assert added.json()["roll_forward_on_non_business_day"] is True
    assert _json(admin, "post", "/api/core/rules", rule).status_code == 409
    assert "elselaw.determination.general.2026-01-01" in {r.id for r in rule_data().rules}
    # Holidays: added, listed through the records read, removed.
    holidays = {
        "holidays": [{"date": "2026-03-10", "name": "Elsewhere Day"}, {"date": "2026-03-10"}]
    }
    assert _json(admin, "post", "/api/core/calendars/elsewhere/holidays", holidays).json() == {
        "added": 1
    }
    read = staff.get("/api/records/rules").json()
    mine = next(c for c in read["calendars"] if c["key"] == "elsewhere")
    assert mine["weekend"] == [5, 6] and [h["date"] for h in mine["holidays"]] == ["2026-03-10"]
    assert any(
        r["id"] == rule["rule_id"] and r["roll_forward_on_non_business_day"] for r in read["rules"]
    )

    # Filing under the new law runs the new clock, over the new holiday: seven calendar days from
    # March 3 is March 10, the holiday, so it lands on the 11th.
    actor = Actor.human(staff_user)
    agency = directory.create_agency(
        actor, name="Elsewhere Agency", jurisdiction="Elsewhere", records_law="ELSELAW"
    )
    request = requests_service.reserve_request(
        actor, agency_id=agency.pk, requester_user_id=staff_user.pk, subject="x", request_text="y"
    )
    filing_service.file_request(actor, request.pk, filed_at=FILED, channel="email")
    deadline = Deadline.objects.get(request_id=request.pk, deadline_type="statutory_determination")
    assert deadline.rule_ref == rule["rule_id"] and deadline.due_at == date(2026, 3, 11)
    assert (
        _json(admin, "delete", "/api/core/calendars/elsewhere/holidays/2026-03-10").status_code
        == 204
    )
    assert (
        _json(admin, "delete", "/api/core/calendars/elsewhere/holidays/2026-03-10").status_code
        == 404
    )

    # Edits are audited in the activity log with the old and new values; a retire is an end date.
    changed = _json(admin, "patch", f"/api/core/rules/{rule['rule_id']}", {"count": 9})
    assert changed.status_code == 200 and changed.json()["count"] == 9
    line = Activity.objects.get(action="rule_changed")
    assert line.data["old"] == {"count": 7} and line.data["new"] == {"count": 9}
    assert (
        _json(
            admin, "patch", f"/api/core/rules/{rule['rule_id']}", {"effective_to": "2025-01-01"}
        ).status_code
        == 422
    )
    retired = _json(
        admin, "post", f"/api/core/rules/{rule['rule_id']}/retire", {"effective_to": "2027-01-01"}
    )
    assert retired.status_code == 200 and retired.json()["effective_to"] == "2027-01-01"
    assert DeadlineRule.objects.get(pk=rule["rule_id"]).updated_by_id == admin_user.pk
    # A law with rules keeps its calendar.
    assert _json(admin, "delete", "/api/core/laws/ELSELAW").status_code == 409
    assert (
        _json(admin, "patch", "/api/core/calendars/elsewhere", {"timezone": "UTC"}).json()[
            "timezone"
        ]
        == "UTC"
    )
    assert (
        Activity.objects.filter(
            action__in=[
                "calendar_added",
                "law_mapped",
                "rule_added",
                "holidays_added",
                "holiday_removed",
                "calendar_changed",
            ]
        ).count()
        == 6
    )


def test_the_service_refuses_what_the_loader_would(admin_user, db) -> None:
    import pytest
    from portal.records.services.common import InvalidTransition, NotFound, ServiceError

    rules_service.add_calendar(admin_user, key="k", timezone="UTC")
    with pytest.raises(ServiceError, match="weekday"):
        rules_service.add_calendar(admin_user, key="k2", timezone="UTC", weekend=[7])
    with pytest.raises(InvalidTransition):
        rules_service.add_calendar(admin_user, key="k", timezone="UTC")
    with pytest.raises(NotFound):
        rules_service.map_law(admin_user, "L", "nope")
    rules_service.map_law(admin_user, "L", "k")
    good = {
        "law": "L",
        "deadline_type": "production",
        "count": 3,
        "unit": "business_days",
        "effective_from": date(2026, 1, 1),
        "citation": "c",
    }
    row = rules_service.add_rule(admin_user, rule_id="l.production.general", **good)
    assert row.provision == "" and row.effective_to is None
    with pytest.raises(ServiceError, match="deadline type"):
        rules_service.add_rule(admin_user, rule_id="x", **{**good, "deadline_type": "appeal"})
    with pytest.raises(ServiceError, match="Not editable"):
        rules_service.update_rule(admin_user, "l.production.general", nonsense="other")
    with pytest.raises(NotFound):
        rules_service.update_rule(admin_user, "missing", count=1)
    assert rules_service.update_rule(admin_user, "l.production.general", count=3).count == 3
    with pytest.raises(ServiceError, match="at least one"):
        rules_service.add_holidays(admin_user, "k", [])
    with pytest.raises(NotFound):
        rules_service.remove_holiday(admin_user, "k", date(2026, 1, 1))
