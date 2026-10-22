"""What runs on a clock: the overdue sweep, the production-review timeout, follow-up reminders,
and the retry of mail the worker never got to.

Each is a plain function taking `now`, so a test can set the moment, and each is idempotent: run
twice at the same moment, the second run does nothing. The jobs in `records/jobs.py` are thin
wrappers that call them on a schedule. All act as a named system actor.

What automation may write is fixed (HANDOFF, non-negotiable 6): an overdue status when a deadline
lapses, `production_unreviewed` when nobody reviewed a production in time, and nothing else. A
lapsed deadline of a kind with no overdue status is marked missed and moves no status. Nothing
here sends anything: a follow-up is a TASK with a draft for a person to send (sending is P11).

No number of days is written in this file. The windows are `core.settings` rows; the dates
deadlines fall on were computed from the rules file when the request was filed.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from portal.core.services.settings import get_setting
from portal.records.actors import Actor
from portal.records.models import (
    Deadline,
    DeadlineDefinition,
    DeadlineFollowup,
    Request,
    Source,
    writable,
)
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import (
    NoCalendarMapped,
    ServiceError,
    agency_calendar,
    local_date,
    lock_request,
    rule_data,
)

logger = logging.getLogger(__name__)

SWEEP_ACTOR = "overdue_sweep"
REVIEW_ACTOR = "production_review_timeout"
FOLLOWUP_ACTOR = "auto_followups"
REVIEW_NOTE = "No review within the configured window."
# Mail the worker has had this long to extract and has not. Hours of an outage, not a legal clock.
STALLED_AFTER = timedelta(hours=1)


@dataclass
class SweepCounts:
    looked_at: int = 0
    missed: int = 0
    statuses_moved: int = 0
    no_calendar: int = 0


def sweep_overdue_deadlines(now: datetime | None = None) -> SweepCounts:
    """Mark as missed every deadline whose date has passed WHERE THE AGENCY IS.

    For each kind in `records.deadline_definitions`, and each request that is still running, only
    the EARLIEST active deadline of that kind is "the deadline" (two can be active while a
    conflict is unresolved; the later one is not a second, softer clock). It is missed when its
    date is before today on the agency's own calendar, so a deadline in Los Angeles is not missed
    because it is already tomorrow in UTC. An agency whose law has no calendar mapped is skipped
    and counted: with no calendar there is no "today" to compare with.
    """
    now = now or timezone.now()
    actor = Actor.for_system(SWEEP_ACTOR)
    counts = SweepCounts()
    data = rule_data()
    for kind in DeadlineDefinition.objects.order_by("sort_order", "code"):
        active = (
            Deadline.objects.filter(
                status="active",
                deadline_type=kind.code,
                request__current_status__is_terminal=False,
            )
            .select_related("request", "request__agency")
            .order_by("request_id", "due_at", "created_at")
        )
        seen: set = set()
        for deadline in active:
            if deadline.request_id in seen:
                continue  # a later active date of the same kind
            seen.add(deadline.request_id)
            counts.looked_at += 1
            try:
                today = local_date(now, agency_calendar(deadline.request.agency, data))
            except NoCalendarMapped:
                counts.no_calendar += 1
                continue
            if deadline.due_at >= today:
                continue
            change = deadlines_service.mark_deadline_missed(actor, deadline.pk)
            counts.missed += 1
            counts.statuses_moved += change is not None
    logger.info("overdue sweep: %s", counts)
    return counts


def production_review_timeout(now: datetime | None = None) -> int:
    """A production that has sat unreviewed past the configured window is marked as such."""
    now = now or timezone.now()
    window = timedelta(days=int(get_setting("records.production_review_window_days")))
    actor = Actor.for_system(REVIEW_ACTOR)
    waiting = Request.objects.filter(
        current_status="new_production", current_status_changed_at__lt=now - window
    ).values_list("pk", flat=True)
    moved = 0
    for request_id in list(waiting):
        with transaction.atomic():
            request = lock_request(request_id)
            # Re-read under the lock: a person may have reviewed it since the list was made.
            if (
                request.current_status_id != "new_production"
                or request.current_status_changed_at >= now - window
            ):
                continue
            events_service.record_event(
                actor,
                request_id,
                event_type="note",
                channel="system",
                direction="internal",
                occurred_at=now,
                display_body=REVIEW_NOTE,
                status="production_unreviewed",
            )
            moved += 1
    logger.info("production review timeout: %d request(s) marked unreviewed", moved)
    return moved


class _Placeholders(dict):
    """`str.format_map` with an unknown placeholder left as typed, never a KeyError: the
    template is a setting a person edits, and a typo must not stop the sweep."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def followup_placeholders(request: Request, kind: DeadlineDefinition | None) -> dict[str, str]:
    """What the subject and body templates may name (settings `records.followup_subject` and
    `records.followup_body`, P18a): facts from the ledger, nothing about the law. `kind` is None
    for a follow-up a person writes on a request with no deadline to name."""
    lapsed = (
        Deadline.objects.filter(request_id=request.pk, deadline_type=kind.code, status="missed")
        .order_by("-due_at")
        .first()
        if kind is not None
        else None
    )
    return {
        "tracking_number": request.tracking_number,
        "agency": request.agency.name,
        "subject": request.subject,
        "reference": (
            f" (your reference {request.agency_reference_number})"
            if request.agency_reference_number
            else ""
        ),
        "sent_on": (
            f" sent on {request.filed_at:%B} {request.filed_at.day}, {request.filed_at.year}"
            if request.filed_at
            else ""
        ),
        "expected_by": (
            f" of {lapsed.due_at:%B} {lapsed.due_at.day}, {lapsed.due_at.year}" if lapsed else ""
        ),
        "deadline_kind": kind.label.lower() if kind is not None else "",
        "reply_to": request.inbound_alias_email or "",
    }


def render_followup(template: str, placeholders: dict[str, str]) -> str:
    return str(template or "").format_map(_Placeholders(placeholders)).strip()


def _followup_text(request: Request, kind: DeadlineDefinition) -> str:
    """A draft for a person to send: the body template from settings, filled from the ledger. The
    first line is the task's own; only what follows "Draft:" goes out."""
    fields = followup_placeholders(request, kind)
    body = render_followup(str(get_setting("records.followup_body") or ""), fields)
    return (
        f"Follow up with {fields['agency']} on {fields['tracking_number']}: "
        f"the {fields['deadline_kind']} date{fields['expected_by']} has passed.\n\n"
        "Draft:\n"
        f"{body}\n\n"
        f"Reply to: {fields['reply_to']}"
    )


# P11: whoever can send a follow-up registers here (the mail app does, in its apps.ready). The
# sweep opens the task either way; a sender is told afterwards, with the draft text, and decides
# for itself whether sending is switched on. `records` knows nothing of mail.
FollowupSender = Callable[[Actor, Request, DeadlineDefinition, str], None]
_followup_senders: list[FollowupSender] = []


def on_followup_due(callback: FollowupSender) -> FollowupSender:
    if callback not in _followup_senders:
        _followup_senders.append(callback)
    return callback


def followup_draft(text: str) -> str:
    """The part of a follow-up task's text that is the draft to send, after the "Draft:" line."""
    _, _, draft = text.partition("Draft:\n")
    return draft.strip()


def send_auto_followups(now: datetime | None = None) -> int:
    """Open a follow-up task where one is due. Never sends itself: a registered sender may.

    For a request with follow-ups switched on whose status is an overdue one: the first reminder
    comes once `records.followup_first_delay_days` have passed since it became overdue, then one
    every `records.followup_interval_days`. The cadence is kept per (request, kind) in
    `deadline_followups`. The task carries a draft and goes to the request's assignee when it has
    one who can take it.
    """
    now = now or timezone.now()
    first_delay = timedelta(days=int(get_setting("records.followup_first_delay_days")))
    interval = timedelta(days=int(get_setting("records.followup_interval_days")))
    actor = Actor.for_system(FOLLOWUP_ACTOR)
    kinds = {
        kind.overdue_status_id: kind
        for kind in DeadlineDefinition.objects.exclude(overdue_status__isnull=True).order_by(
            "-sort_order"
        )
    }
    due = Request.objects.filter(
        auto_followups_enabled=True, current_status__in=list(kinds)
    ).values_list("pk", flat=True)
    opened = 0
    for request_id in list(due):
        with transaction.atomic():
            request = lock_request(request_id)
            kind = kinds.get(request.current_status_id)
            if kind is None or not request.auto_followups_enabled:
                continue
            with writable():
                cadence, _ = DeadlineFollowup.objects.get_or_create(
                    request_id=request_id, deadline_type=kind.code
                )
            if cadence.followup_count == 0 or cadence.last_sent_at is None:
                ready = now >= request.current_status_changed_at + first_delay
            else:
                ready = now >= cadence.last_sent_at + interval
            if not ready:
                continue
            request = Request.objects.select_related("agency").get(pk=request_id)
            text = _followup_text(request, kind)
            try:
                with transaction.atomic():
                    tasks_service.create_task(
                        actor,
                        request_id,
                        "manual_followup",
                        text,
                        assigned_to_user_id=request.assigned_to_id,
                    )
            except ServiceError:
                # The assignee cannot take work any more (deactivated, or off a privileged
                # request's list). The reminder still has to exist; it goes to nobody in particular.
                tasks_service.create_task(actor, request_id, "manual_followup", text)
            DeadlineFollowup.objects.filter(pk=cadence.pk).update(
                last_sent_at=now, followup_count=F("followup_count") + 1
            )
            opened += 1
            for sender in _followup_senders:
                try:
                    sender(actor, request, kind, followup_draft(text))
                except Exception:  # noqa: BLE001 -- the reminder exists; sending is extra
                    logger.exception("follow-up sender failed for %s", request.tracking_number)
    logger.info("auto follow-ups: %d task(s) opened", opened)
    return opened


def stalled_mail(now: datetime | None = None) -> list:
    """Delivered mail the worker has not extracted the files of, an hour on. The message itself is
    already on its request (the webhook placed it); what is owed is its attachments. Returned so
    the job can defer them again; a message whose extraction finally FAILED is flagged and is not
    retried here, or a file that cannot be read would be retried for ever."""
    now = now or timezone.now()
    return list(
        Source.objects.filter(
            source_type="postmark_email",
            ingested_at__isnull=True,
            flagged_for_review=False,
            captured_at__lt=now - STALLED_AFTER,
        ).values_list("pk", flat=True)
    )
