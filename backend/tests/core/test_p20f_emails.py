"""P20f: the daily summary as HTML as well as text (design 228), built from plain data by
`core.services.emails`, and sent with both parts. (The invite, 229, is Keycloak's email since P25a,
in the sclu email theme.) Every row here is synthetic."""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.utils.html import escape
from portal.core.services import emails, notifications
from portal.mail.services import system_mail, transport
from portal.records.actors import Actor
from portal.records.models import DeadlineBasis
from portal.records.services import deadlines as deadlines_service
from portal.records.services import directory
from portal.records.services import requests as requests_service
from portal.records.services import tasks as tasks_service

# 07:00 in Los Angeles (daylight time) on a Tuesday: the summary hour.
NOW = datetime(2026, 3, 10, 14, 0, tzinfo=UTC)
TODAY = date(2026, 3, 10)
SCRIPT = "<script>alert(1)</script>"


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


def _request(actor, agency, staff_user, subject: str):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject=subject,
        request_text="All of it.",
    )


@pytest.fixture
def morning(db, staff_user, agency):
    """Two overdue deadlines, one due today and one later in the window; a task due today and
    one with no date. One agency's name is markup."""
    staff_user.timezone = "America/Los_Angeles"
    staff_user.save(update_fields=["timezone"])
    actor = Actor.human(staff_user)
    marked = directory.create_agency(
        actor, name=f"{SCRIPT} Agency", jurisdiction="Testland", records_law="TESTLAW"
    )
    late = _request(actor, agency, staff_user, "Late one")
    later = _request(actor, marked, staff_user, "Late two")
    today = _request(actor, agency, staff_user, "Due today")
    soon = _request(actor, agency, staff_user, "Due soon")
    for row, kind, due in (
        (late, "production", TODAY - timedelta(hours=24 * 5)),
        (later, "other", TODAY - timedelta(hours=24)),
        (today, "challenge_response", TODAY),
        (soon, "other", TODAY + timedelta(hours=24 * 3)),
    ):
        deadlines_service.open_deadline(
            actor, row.pk, deadline_type=kind, due_at=due, basis=DeadlineBasis.INTERNAL
        )
    tasks_service.create_task(
        actor,
        late.pk,
        "manual",
        "Call the records office\nabout the delay",
        due_at=datetime(2026, 3, 10, 20, 0, tzinfo=UTC),  # early afternoon in Los Angeles
        assigned_to_user_id=staff_user.pk,
    )
    tasks_service.create_task(
        actor, soon.pk, "manual", "Read the reply", assigned_to_user_id=staff_user.pk
    )
    return {"late": late, "later": later, "today": today, "soon": soon, "marked": marked}


def test_the_summary_splits_home_into_the_four_lists_by_the_readers_date(
    staff_user, agency, morning
) -> None:
    summary = notifications.summary_data(staff_user, now=NOW)
    assert summary is not None
    assert summary.subject == "Your day · Tuesday, March 10"
    assert summary.first_name == "Staff"
    assert [(d.tracking_number, d.late_by) for d in summary.overdue] == [
        (morning["late"].tracking_number, 5),
        (morning["later"].tracking_number, 1),
    ]
    assert [d.tracking_number for d in summary.due_today] == [morning["today"].tracking_number]
    assert [d.tracking_number for d in summary.due_soon] == [morning["soon"].tracking_number]
    assert [t.text.splitlines()[0] for t in summary.tasks_today] == ["Call the records office"]
    assert [t.text for t in summary.other_tasks] == ["Read the reply"]

    text, html = emails.daily_summary(summary, emails.brand())
    portal = emails.brand().portal
    # The HTML, as drawn: the counts line always has the three; each list only when it has rows.
    assert html.startswith("<!doctype html>") and "<style" not in html
    assert "display:flex" not in html and "grid" not in html
    assert f">{escape(portal)}</span><br>Your day · Tuesday, March 10<" in html
    assert "Good morning, Staff" in html and "2 overdue · 2 due today · 0 flagged" in html
    for heading in ("Overdue", "Due today", "Due soon", "Assigned to you"):
        assert f">{heading}</div>" in html
    assert f"{agency.name} · production</td>" in html and ">5 days</td>" in html
    assert ">1 day</td>" in html
    assert f"{agency.name} · challenge response</td>" in html
    assert f">{morning['today'].tracking_number}</td>" in html
    assert f"Call the records office · {agency.name}</td>" in html
    assert "Read the reply · " in html and ">Task</td>" in html
    assert ">Mar 13</td>" in html
    assert f'href="{emails.brand().home_url}"' in html and f">Open {escape(portal)}</a>" in html
    assert 'src="http://' in html and "/brand/sclu-letters-blue.png" in html
    assert "Change what you get in Settings → Notifications." in html
    # Every interpolated string is escaped: the agency named with markup stays text.
    assert SCRIPT not in html and escape(SCRIPT) in html
    # The text keeps its words and carries the same four lists.
    assert text.startswith("Good morning, Staff\n2 overdue · 2 due soon · 2 assigned to you\n")
    for heading in ("\nOverdue\n", "\nDue today\n", "\nDue soon\n", "\nAssigned to you\n"):
        assert heading in text
    assert f"{agency.name} · Production · {morning['late'].tracking_number} · 5 days overdue" in (
        text
    )
    assert f"Task: Call the records office · {agency.name}" in text
    assert f"Mar 13 · {morning['soon'].tracking_number} · {agency.name}" in text
    assert "  Read the reply" in text and SCRIPT in text  # plain text is not HTML
    assert text.endswith("Change what you get in Settings → Notifications.")
    assert notifications.summary_text(staff_user, now=NOW) == text


def test_the_summary_goes_with_both_parts_to_each_person_with_their_own_tasks(
    provider, staff_user, colleague_user, morning
) -> None:
    colleague_user.timezone = "America/Los_Angeles"
    colleague_user.save(update_fields=["timezone"])
    # Both may see the same deadlines; only the staff member has the tasks.
    assert notifications.send_daily_summaries(now=NOW) == 2
    mails = {m["To"]: m for m in provider.sent}
    mine = mails[staff_user.email]
    assert mine["Subject"] == "Your day · Tuesday, March 10"
    assert mine["TextBody"].startswith("Good morning, Staff")
    assert mine["HtmlBody"].startswith("<!doctype html>") and ">Due today</div>" in mine["HtmlBody"]
    assert "Call the records office" in mine["HtmlBody"]
    theirs = mails[colleague_user.email]
    assert "2 overdue · 1 due today · 0 flagged" in theirs["HtmlBody"]
    assert "Call the records office" not in theirs["HtmlBody"]
    assert "Assigned to you" not in theirs["TextBody"]
    # Once a day.
    assert notifications.send_daily_summaries(now=NOW + timedelta(minutes=30)) == 0


def test_nothing_to_say_is_no_summary_and_no_mail(provider, staff_user) -> None:
    staff_user.timezone = "America/Los_Angeles"
    staff_user.save(update_fields=["timezone"])
    assert notifications.summary_data(staff_user, now=NOW) is None
    assert notifications.send_daily_summaries(now=NOW) == 0 and provider.sent == []


def test_the_builders_are_pure_and_escape_everything() -> None:
    brand = emails.Brand(
        short_name="T&T",
        portal="T&T Portal",
        logo_url="https://portal.example.org/brand/x.png?a=1&b=2",
        home_url="https://portal.example.org/",
    )
    empty = emails.DailySummary(
        first_name="A",
        subject="s",
        overdue=[],
        due_today=[],
        due_soon=[],
        tasks_today=[],
        other_tasks=[],
        flagged=0,
    )
    assert empty.is_empty
    flagged = emails.DailySummary(**{**empty.__dict__, "flagged": 4})
    text, html = emails.daily_summary(flagged, brand)
    assert 'src="https://portal.example.org/brand/x.png?a=1&amp;b=2"' in html
    assert 'alt="T&amp;T"' in html
    assert "0 overdue · 0 due today · 4 flagged" in html and "Overdue" not in html
    assert "4 flagged in the Inbox" in text


def test_system_mail_adds_an_html_part_only_when_given_one(provider, db) -> None:
    system_mail.send_system_email("a@example.org", "Plain", "Just text.")
    system_mail.send_system_email("b@example.org", "Both", "Text too.", "<!doctype html><p>Hi</p>")
    plain, both = provider.sent
    assert plain["TextBody"] == "Just text." and "HtmlBody" not in plain
    assert both["TextBody"] == "Text too." and both["HtmlBody"] == "<!doctype html><p>Hi</p>"
