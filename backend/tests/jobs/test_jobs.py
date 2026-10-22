"""The jobs that run on a clock. Each test calls the function with the moment it wants.

Runs as the owner and again as portal_app, the role the worker really has.
"""

import io
from datetime import UTC, datetime, time, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from portal.core.models import AgreementAcceptance, Review, Setting, User
from portal.jobs.app import app
from portal.records import jobs
from portal.records.actors import Actor
from portal.records.models import (
    Deadline,
    DeadlineFollowup,
    Event,
    Request,
    Source,
    StatusChange,
    Task,
)
from portal.records.services import deadlines as deadlines_service
from portal.records.services import directory, sweeps
from portal.records.services import events as events_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import status as status_service

from tests.jobs.conftest import FILED_AT
from tests.p20h_helpers import give

pytestmark = pytest.mark.usefixtures("as_each_role")


def _set(key: str, value) -> None:
    Setting.objects.update_or_create(key=key, defaults={"value": value})


def _statutory(request) -> Deadline:
    return Deadline.objects.get(
        request_id=request.pk, deadline_type="statutory_determination", status="active"
    )


def _utc(day, hour) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


# ---- the overdue sweep --------------------------------------------------


def test_a_deadline_is_missed_only_when_its_date_has_passed_where_the_agency_is(make_filed) -> None:
    request = make_filed()
    deadline = _statutory(request)
    day_after = deadline.due_at + timedelta(days=1)

    # On its due date, all day: not missed.
    assert sweeps.sweep_overdue_deadlines(_utc(deadline.due_at, 23)).missed == 0
    # 03:00 UTC the next day is still the evening of the due date in Los Angeles: not missed.
    assert sweeps.sweep_overdue_deadlines(_utc(day_after, 3)).missed == 0
    request.refresh_from_db()
    assert request.current_status_id == "filed"

    # 10:00 UTC the next day is the next morning there: missed, and the status says so.
    counts = sweeps.sweep_overdue_deadlines(_utc(day_after, 10))
    assert (counts.missed, counts.statuses_moved) == (1, 1)
    deadline.refresh_from_db()
    request.refresh_from_db()
    assert deadline.status == "missed" and request.current_status_id == "determination_overdue"
    change = StatusChange.objects.filter(request_id=request.pk).order_by("-effective_at").first()
    assert (change.reason, change.triggering_deadline_id) == ("deadline_expired", deadline.pk)

    # Idempotent: the same moment again does nothing.
    again = sweeps.sweep_overdue_deadlines(_utc(day_after, 10))
    assert (again.missed, again.statuses_moved) == (0, 0)
    assert StatusChange.objects.filter(request_id=request.pk).count() == 3


def test_a_request_in_a_terminal_status_is_never_touched(make_filed, owner) -> None:
    request = make_filed()
    deadline = _statutory(request)
    status_service.override_status(owner, request.pk, "withdrawn", "Not needed.")
    counts = sweeps.sweep_overdue_deadlines(_utc(deadline.due_at + timedelta(days=30), 12))
    assert (counts.looked_at, counts.missed) == (0, 0)
    deadline.refresh_from_db()
    request.refresh_from_db()
    assert deadline.status == "active" and request.current_status_id == "withdrawn"


def test_the_sweep_follows_the_kinds_table_and_takes_only_the_earliest_of_a_kind(
    make_filed, owner
) -> None:
    request = make_filed()
    statutory = _statutory(request)

    def open_(kind, due):
        return deadlines_service.open_deadline(
            owner, request.pk, deadline_type=kind, due_at=due, basis="agency_commitment"
        )

    early_other = open_("other", statutory.due_at - timedelta(days=3))
    # Also already past when the sweep runs, but not the earliest of its kind.
    later_other = open_("other", statutory.due_at + timedelta(days=2))
    counts = sweeps.sweep_overdue_deadlines(_utc(statutory.due_at + timedelta(days=5), 12))

    # The statutory date lapsed and moved the status; "other" has no overdue status, so its
    # earliest date is missed and moves nothing; its later date is not the deadline yet.
    assert counts.missed == 2 and counts.statuses_moved == 1
    for row in (early_other, later_other, statutory):
        row.refresh_from_db()
    assert (early_other.status, later_other.status, statutory.status) == (
        "missed",
        "active",
        "missed",
    )
    request.refresh_from_db()
    assert request.current_status_id == "determination_overdue"
    # The next run: the later "other" date is now the earliest active one of its kind.
    assert (
        sweeps.sweep_overdue_deadlines(_utc(statutory.due_at + timedelta(days=6), 12)).missed == 1
    )
    later_other.refresh_from_db()
    assert later_other.status == "missed"


def test_an_agency_with_no_calendar_is_skipped_and_counted_not_guessed_at(
    make_filed, owner
) -> None:
    unmapped = directory.create_agency(
        owner, name="No Calendar Agency", jurisdiction="Testland", records_law="NOSUCHLAW"
    )
    request = make_filed(agency_=unmapped)
    deadline = deadlines_service.open_deadline(
        owner,
        request.pk,
        deadline_type="production",
        due_at=FILED_AT.date() - timedelta(days=5),
        basis="agency_commitment",
    )
    counts = sweeps.sweep_overdue_deadlines(FILED_AT + timedelta(days=30))
    assert counts.no_calendar == 1 and counts.missed == 0
    deadline.refresh_from_db()
    assert deadline.status == "active"


# ---- the production review timeout --------------------------------------------------


def test_an_unreviewed_production_times_out_after_the_window_in_settings(make_filed, owner) -> None:
    request = make_filed()
    events_service.record_event(
        owner,
        request.pk,
        event_type="production",
        channel="email",
        direction="inbound",
        occurred_at=FILED_AT,
        display_body="Records attached.",
        status="new_production",
    )
    request.refresh_from_db()
    since = request.current_status_changed_at

    _set("records.production_review_window_days", 10)
    assert sweeps.production_review_timeout(since + timedelta(days=9)) == 0
    # The number is the setting's: shorten it and the same moment is now past the window.
    _set("records.production_review_window_days", 5)
    assert sweeps.production_review_timeout(since + timedelta(days=9)) == 1

    request.refresh_from_db()
    assert request.current_status_id == "production_unreviewed"
    note = Event.objects.filter(request_id=request.pk, event_type_id="note").get()
    assert (
        note.recorded_by_system == sweeps.REVIEW_ACTOR and note.display_body == sweeps.REVIEW_NOTE
    )
    assert sweeps.production_review_timeout(since + timedelta(days=90)) == 0


# ---- follow-ups ---------------------------------------------------------------------------------


@pytest.fixture
def overdue(make_filed, owner):
    request = make_filed()
    deadline = _statutory(request)
    sweeps.sweep_overdue_deadlines(_utc(deadline.due_at + timedelta(days=1), 12))
    request.refresh_from_db()
    assert request.current_status_id == "determination_overdue"
    return request


def _followup_tasks(request):
    return Task.objects.filter(request_id=request.pk, task_type="manual_followup")


def test_a_follow_up_is_a_task_with_a_draft_on_the_cadence_in_settings_and_nothing_is_sent(
    overdue, staff_user, owner
) -> None:
    requests_service.assign_request(owner, overdue.pk, staff_user.pk)
    since = overdue.current_status_changed_at
    _set("records.followup_first_delay_days", 4)
    _set("records.followup_interval_days", 6)

    assert sweeps.send_auto_followups(since + timedelta(days=3)) == 0
    assert sweeps.send_auto_followups(since + timedelta(days=4, minutes=1)) == 1
    task = _followup_tasks(overdue).get()
    assert task.assigned_to_id == staff_user.pk and task.created_by_system == sweeps.FOLLOWUP_ACTOR
    assert overdue.tracking_number in task.description and "Draft:" in task.description
    assert overdue.inbound_alias_email in task.description
    cadence = DeadlineFollowup.objects.get(request_id=overdue.pk)
    assert (cadence.deadline_type, cadence.followup_count) == ("statutory_determination", 1)

    first = cadence.last_sent_at
    assert sweeps.send_auto_followups(first + timedelta(days=5)) == 0
    assert sweeps.send_auto_followups(first + timedelta(days=6, minutes=1)) == 1
    cadence.refresh_from_db()
    assert cadence.followup_count == 2 and _followup_tasks(overdue).count() == 2
    # Nothing went out: no outbound event, no new source.
    assert not Event.objects.filter(request_id=overdue.pk, event_type_id="follow_up").exists()
    assert not Source.objects.exists()


def test_follow_ups_are_only_for_requests_that_have_them_switched_on_and_are_overdue(
    overdue, make_filed, owner
) -> None:
    from portal.records.services import edits as edits_service

    not_overdue = make_filed("Still in time")
    edits_service.edit_with_audit(
        owner, "requests", overdue.pk, "auto_followups_enabled", False, "Handled by phone."
    )
    far = overdue.current_status_changed_at + timedelta(days=400)
    assert sweeps.send_auto_followups(far) == 0
    assert not _followup_tasks(overdue).exists() and not _followup_tasks(not_overdue).exists()


def test_a_follow_up_for_a_privileged_request_whose_assignee_lost_access_still_opens(
    make_filed, owner, staff_user, colleague_user
) -> None:
    """Work on a privileged request goes only to someone on its list. The reminder must not be
    lost because the person it would have gone to can no longer take it."""
    sealed = make_filed("Sealed", sensitivity="privileged")
    row = give(sealed.pk, colleague_user.pk)
    requests_service.assign_request(owner, sealed.pk, colleague_user.pk)
    sweeps.sweep_overdue_deadlines(_utc(_statutory(sealed).due_at + timedelta(days=1), 12))
    type(colleague_user).objects.filter(pk=colleague_user.pk).update(is_active=False)
    sealed.refresh_from_db()
    assert sealed.assigned_to_id == colleague_user.pk and row.revoked_at is None

    assert sweeps.send_auto_followups(sealed.current_status_changed_at + timedelta(days=400)) == 1
    assert _followup_tasks(sealed).get().assigned_to_id is None


# ---- mail the worker never got to --------------------------------------------------


def test_stalled_mail_is_delivered_mail_an_hour_old_that_was_never_extracted(db, owner) -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def message(reference, age, **extra):
        return sources_service.register_source(
            Actor.for_system("postmark_webhook"),
            source_type="postmark_email",
            raw_storage_url=f"test://{reference}.json",
            external_ref=reference,
            captured_at=now - age,
            **extra,
        )

    stalled = message("stalled", timedelta(hours=2))
    message("fresh", timedelta(minutes=10))
    done = message("done", timedelta(hours=2))
    Source.objects.filter(pk=done.pk).update(ingested_at=now)
    failed = message("failed", timedelta(hours=2))
    sources_service.flag_source(Actor.for_system("postmark_webhook"), failed.pk, "x", "other")
    sources_service.register_source(
        owner,
        source_type="scanned_mail",
        raw_storage_url="test://scan",
        captured_at=now - timedelta(days=3),
    )
    assert sweeps.stalled_mail(now) == [stalled.pk]


# ---- the jobs themselves, and the System screen -----------------------------------------------


def test_the_eight_periodic_jobs_are_registered_on_a_schedule_and_each_runs(db) -> None:
    registered = {p.task.name: p.cron for p in app.periodic_registry.periodic_tasks.values()}
    assert registered == {
        # P15b: deadline reminders once a day; the summary hour checked hourly, per time zone.
        "core.deadline_reminders": "0 6 * * *",
        "core.daily_summaries": "5 * * * *",
        "records.sweep_overdue_deadlines": "0 * * * *",
        "records.production_review_timeout": "15 3 * * *",
        "records.send_auto_followups": "30 3 * * *",
        "records.requeue_stalled_mail": "*/20 * * * *",
        # P11: a queued message whose window closed and that no worker delivered.
        "mail.requeue_stuck_messages": "*/15 * * * *",
        # P25a: the changes owed to Keycloak taken up again, and Settings' sign-in rules written
        # (the P23a provider token sweep it replaced at the same minute retired with the provider).
        "core.keycloak_sweep": "40 * * * *",
    }
    from portal.core import jobs as core_jobs
    from portal.mail import jobs as mail_jobs

    mail_jobs.requeue_stuck_messages(timestamp=0)
    core_jobs.deadline_reminders(timestamp=0)
    core_jobs.daily_summaries(timestamp=0)
    core_jobs.keycloak_sweep(timestamp=0)
    for job in (
        jobs.sweep_overdue_deadlines,
        jobs.production_review_timeout,
        jobs.send_auto_followups,
        jobs.requeue_stalled_mail,
    ):
        job(timestamp=0)


def test_the_system_screen_reads_mail_jobs_and_the_calendar_for_an_administrator(
    db, admin_user, staff_user
) -> None:
    system = Actor.for_system("postmark_webhook")
    for index, age in enumerate((timedelta(hours=1), timedelta(days=3), timedelta(days=40))):
        sources_service.register_source(
            system,
            source_type="postmark_email",
            raw_storage_url=f"test://{index}.json",
            external_ref=f"sys-{index}",
            captured_at=datetime.now(UTC) - age,
        )
    _set("core.system_mail_window_days", 7)
    client = Client()
    client.force_login(admin_user)
    body = client.get("/api/core/system").json()
    assert body["mail"]["received"] == 2 and body["mail"]["window_days"] == 7
    assert body["mail"]["last_received_at"] is not None and body["mail"]["awaiting_extraction"] == 3
    assert [j["name"] for j in body["jobs"]] == sorted(
        p.task.name for p in app.periodic_registry.periodic_tasks.values()
    )
    assert all(j["last_run_at"] is None and j["next_run_at"] > body["now"] for j in body["jobs"])
    assert any(c["key"] and c["last_holiday"] for c in body["calendars"])
    _set("core.system_mail_window_days", 60)
    assert client.get("/api/core/system").json()["mail"]["received"] == 3

    other = Client()
    other.force_login(staff_user)
    assert other.get("/api/core/system").status_code == 403
    assert Client().get("/api/core/system").status_code == 401


# ---- the synthetic seed --------------------------------------------------


@pytest.mark.unfinished_setup  # the seed's newcomer is unfinished, as the seed makes them
def test_the_seed_refuses_outside_development_and_fills_a_development_database_once(
    db, settings, media
) -> None:
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_synthetic", stdout=io.StringIO())
    assert not User.objects.exists()

    settings.DEBUG = True
    out = io.StringIO()
    call_command("seed_synthetic", stdout=out)
    assert "seed-admin@example.org" in out.getvalue()
    # Seven before P20h, and three an intern made: one filed, one waiting for approval, one sent
    # back (pending filing again).
    assert Request.objects.count() == 10
    assert Request.objects.filter(sensitivity="privileged").count() == 1
    assert Request.objects.filter(current_status="determination_overdue").exists()
    assert Request.objects.filter(current_status="pending_filing").count() == 3
    assert Request.objects.filter(current_status="waiting_for_approval").count() == 1
    roles = set(User.objects.values_list("app_roles__role", flat=True))
    assert roles == {"admin", "staff", "staff_attorney", "limited_attorney", "intern", "requester"}
    assert Review.objects.filter(decision__isnull=True, kind="approval").count() == 1
    assert Review.objects.filter(decision="sent_back").count() == 1
    assert set(
        Source.objects.exclude(flag_reason__isnull=True).values_list("flag_reason", flat=True)
    ) == set(sources_service.FLAG_REASONS)
    assert Source.objects.filter(resolution="automated_reply").exists()
    assert Task.objects.filter(created_by_system__isnull=False).exists()
    assert Task.objects.filter(assigned_to__isnull=True, created_by__isnull=False).exists()
    assert Deadline.objects.filter(flagged_for_review=True).exists(), "the conflicting pair"
    # Every name in it says what it is.
    assert all(
        "Test Agency" in name for name in Request.objects.values_list("agency__name", flat=True)
    )
    assert not User.objects.exclude(email__endswith="@example.org").exists()
    # P20i: every seed person but the newcomer has finished the new-user setup, signed with a
    # synthetic drawn signature; the newcomer, an intern, has not started it.
    from portal.core.services import setup as setup_service

    newcomer = User.objects.get(email="seed-newcomer@example.org")
    assert setup_service.required(newcomer) and newcomer.title == "Legal Intern"
    others = User.objects.exclude(pk=newcomer.pk).filter(is_active=True)
    assert others.exists() and not any(setup_service.required(user) for user in others)
    assert AgreementAcceptance.objects.filter(user__in=others).count() >= others.count()
    finished = io.StringIO()
    call_command("seed_synthetic", "--setup-only", stdout=finished)
    assert "people who finished the setup (P20i): 0" in finished.getvalue()

    # P22: synthetic team chat, every channel named seed-*, every message saying it is synthetic.
    from portal.core.models import ChatChannel, ChatMessage

    names = set(ChatChannel.objects.filter(kind="channel").values_list("name", flat=True))
    assert names == {"seed-lobby", "seed-workroom", "seed-private-room"}
    assert ChatChannel.objects.filter(kind="dm").count() == 3
    assert all("Seed" in body for body in ChatMessage.objects.values_list("body", flat=True))

    with pytest.raises(CommandError, match="already has"):
        call_command("seed_synthetic", stdout=io.StringIO())
    with pytest.raises(CommandError, match="already has"):
        call_command("seed_synthetic", "--roles-only", stdout=io.StringIO())
    with pytest.raises(CommandError, match="already has"):
        call_command("seed_synthetic", "--chat-only", stdout=io.StringIO())
