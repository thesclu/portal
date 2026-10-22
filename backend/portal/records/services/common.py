"""Errors, locking, the effective_at clock, and the calendar lookup every service shares."""

import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from deadline_rules import Calendar, Rule, load_calendars, load_rules
from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from portal.records.actors import Actor
from portal.records.models import RecordsRole, RecordsUser, Request, StatusChange

if TYPE_CHECKING:
    from portal.records.models import Agency


class ServiceError(Exception):
    """A service refused. Every other error in this package is one of these."""


class NotAllowed(ServiceError):
    """This actor may not do this -- the answer would differ for a different actor."""


class InvalidTransition(ServiceError):
    """The ledger is not in a state where this makes sense."""


class NotFound(ServiceError):
    pass


class ChangedMeanwhile(InvalidTransition):
    """The thing changed under the person between their reading it and their acting on it, and the
    ledger knows who changed it and when (P23b, 094 "Maya Torres marked this filed at 3:32 PM").
    A 409 like any other refusal of the state; the API words the sentence in the reader's own
    time zone from `changed_by`, `changed_at` and `status`."""

    def __init__(
        self, message: str, *, changed_by: str, changed_at: datetime, status: str, mine: bool
    ) -> None:
        super().__init__(message)
        self.changed_by = changed_by
        self.changed_at = changed_at
        self.status = status
        self.mine = mine


def last_status_change(request_id: UUID) -> tuple[str, datetime, UUID | None] | None:
    """Who moved this request to its current status and when: the newest `status_changed`
    activity line, which the status chokepoint writes inside every status change (P15b). Returns
    the person's name (or "The portal" for a process), the moment, and the person's id."""
    from portal.core.models import Activity

    row = (
        Activity.objects.filter(action="status_changed", request_id=request_id)
        .select_related("actor")
        .order_by("-at")
        .first()
    )
    if row is None:
        return None
    return (row.actor.name if row.actor_id else "The portal", row.at, row.actor_id)


class NoCalendarMapped(ServiceError):
    """`agency_calendar` found no calendar for the agency's records_law -- a data-entry gap, not
    a caller error. Its own subtype so callers (filing.py) can catch it specifically instead of
    swallowing every ServiceError."""


class Unset:
    """The type of `UNSET`: "the caller did not mention this field".

    A plain `None` default cannot do that job for a field that may legitimately be cleared. A
    PATCH that omits `due_at` must leave it alone, and one that sends `due_at: null` must clear it;
    only a value that no caller can pass by accident tells the two apart.
    """

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = Unset()


def require_human(actor: Actor, what: str) -> UUID:
    if not actor.is_human:
        raise NotAllowed(f"{what} requires a human actor; got the system actor {actor.system!r}.")
    assert actor.user_id is not None
    return actor.user_id


def require_assignable(user_id: UUID) -> RecordsUser:
    """Someone work can be handed to: a person who exists, is not a requester and can still sign in.

    A requester is who a request is FOR and cannot see staff work at all; a deactivated person
    would hold work nobody is looking at, which is the very state `deactivate_user` exists to
    prevent. Refusing here keeps both from arriving by another door.
    """
    # Not `select_related("account")`: records.users and core.users are both named `users`, and
    # Django aliases neither, so a join between the two dies with 'table reference "users" is
    # ambiguous'. Two queries, by primary key, is the price of that.
    person = RecordsUser.objects.filter(pk=user_id).first()
    if person is None:
        raise NotFound(f"No person {user_id}.")
    if person.role == RecordsRole.REQUESTER:
        raise ServiceError(f"{person.name} is a requester and cannot be assigned work.")
    if not person.account.is_active:
        raise ServiceError(f"{person.name} is deactivated and cannot be assigned work.")
    return person


def require_of_request(model: Any, pk: UUID | None, request_id: UUID, what: str) -> None:
    """An id written beside a request must name a row OF that request.

    A deadline that cites an event, a fee that cites one, an event that cites a deadline: each is
    stored as a bare foreign key, and one pointing into another request lets that request's rows
    act on this one (a void there writes its reason here) and lets this one be read as evidence
    about that one. A row that does not exist is left for the foreign key to refuse, as it always
    has been (HANDOFF convention 9).
    """
    if pk is None:
        return
    owner = model.objects.filter(pk=pk).values_list("request_id", flat=True).first()
    if owner is not None and owner != request_id:
        raise ServiceError(f"That {what} belongs to a different request.")


def lock_request(request_id: UUID) -> Request:
    """Serialise everything that touches one request's ledger, including its status cache."""
    request = Request.objects.select_for_update().filter(pk=request_id).first()
    if request is None:
        raise NotFound(f"No request {request_id}.")
    return request


# The smallest interval Postgres's timestamptz can distinguish, so the "strictly later than every
# row that exists" step below is also the smallest step that can achieve it.
_TICK = timedelta(microseconds=1)


def next_effective_at(request_id: UUID) -> datetime:
    """An `effective_at` strictly later than every status row this request already has.

    `recompute_request_current_status` picks the winner with `order by effective_at desc` and no
    tiebreaker, so two rows inserted in one transaction -- which share `now()` -- leave the cache
    free to keep the OLDER one. Voided rows are included in the maximum on purpose: a voided row
    can still hold the largest effective_at, and reusing its instant would recreate the tie the
    moment it is unvoided or compared against.
    """
    rows = StatusChange.objects.filter(request_id=request_id)
    latest = rows.aggregate(m=Max("effective_at"))["m"]
    now = timezone.now()
    if latest is None or latest < now:
        return now
    return latest + _TICK


@dataclass(frozen=True)
class RuleData:
    rules: tuple[Rule, ...]
    calendars: dict[str, Calendar]
    law_calendar: dict[str, str]


_override: RuleData | None = None


def rule_data() -> RuleData:
    """The rules and calendars as they stand: a test's substitute first; in development the
    fictional files the synthetic seed was filed under; otherwise the tables an administrator
    edits (P18b), seeded once from the shipped files; the shipped files only while the tables are
    empty (a database before its seed migration ran)."""
    if _override is not None:
        return _override
    if settings.DEBUG:
        # Development only: point a dev server at the fictional rule files the synthetic seed was
        # filed under (`just seed` prints the two lines). Ignored unless DEBUG is on, so no
        # environment variable can swap the real rules out in production.
        rules_path = _dev_path("DEADLINE_RULES_PATH")
        calendars_path = _dev_path("DEADLINE_CALENDARS_PATH")
        if rules_path or calendars_path:
            calendars, law_calendar = load_calendars(calendars_path)
            return RuleData(
                rules=load_rules(rules_path), calendars=calendars, law_calendar=law_calendar
            )
    from portal.core.services import rules as rules_tables

    loaded = rules_tables.load()
    if not loaded.empty:
        return RuleData(
            rules=loaded.rules, calendars=loaded.calendars, law_calendar=loaded.law_calendar
        )
    calendars, law_calendar = load_calendars()
    return RuleData(rules=load_rules(), calendars=calendars, law_calendar=law_calendar)


def _dev_path(variable: str) -> Path | None:
    value = os.environ.get(variable, "").strip()
    return Path(value) if value else None


@contextlib.contextmanager
def using_rule_data(*, rules_path: Path, calendars_path: Path) -> Iterator[RuleData]:
    """Substitute fictional rule data. The one supported seam for tests.

    `data/rules.toml` is owner's, carries real statutory numbers under citation, and will keep
    changing; a test that asserted against it would be asserting against the law rather than
    against this code. `file_request`'s signature takes no rules argument, so the substitution has
    to happen here rather than at the call site.
    """
    global _override
    previous = _override
    calendars, law_calendar = load_calendars(calendars_path)
    _override = RuleData(
        rules=load_rules(rules_path), calendars=calendars, law_calendar=law_calendar
    )
    try:
        yield _override
    finally:
        _override = previous


def agency_calendar(agency: "Agency", data: RuleData | None = None) -> Calendar:
    """`data` lets a caller that looks up many agencies load the rule files once, not once each."""
    if data is None:
        data = rule_data()
    key = data.law_calendar.get(agency.records_law or "")
    if key is None or key not in data.calendars:
        raise NoCalendarMapped(
            f"No calendar is mapped for records_law {agency.records_law!r} "
            f"(agency {agency.name!r}); add one to calendars.toml."
        )
    return data.calendars[key]


def date_after(when: date) -> date:
    """The calendar date after `when`, for turning an inclusive last date into the start of the
    next one (a date range a person picked). Written by ordinal so no span literal appears."""
    return date.fromordinal(when.toordinal() + 1)


def local_date(dt: datetime, calendar: Calendar) -> date:
    """The civil date `dt` fell on where the agency is -- the date a clock starts from."""
    return dt.astimezone(ZoneInfo(calendar.timezone)).date()


def calendar_with_closures(agency: "Agency", data: RuleData | None = None) -> Calendar:
    """The agency's calendar plus its own closure days (P16): what a clock is computed against.
    `agency_calendar` stays the bare jurisdiction calendar, which is all a local date needs."""
    from dataclasses import replace

    from portal.records.services import closures as closures_service

    calendar = agency_calendar(agency, data)
    closed = closures_service.closure_dates(agency.pk)
    if not closed:
        return calendar
    return replace(calendar, holidays=calendar.holidays | closed)


def calendars_for(agency: "Agency", data: RuleData) -> dict[str, Calendar]:
    """`data.calendars` with this agency's calendar replaced by the one with its closures, so
    `compute_deadlines` rolls over closed days too."""
    calendar = calendar_with_closures(agency, data)
    return {**data.calendars, calendar.key: calendar}
