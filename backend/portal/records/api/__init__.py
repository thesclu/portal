"""Every records router, mounted at one prefix, plus the ServiceError -> HTTP mapping.

Error mapping: NotFound -> 404, NotAllowed -> 403, InvalidTransition -> 409, any other
ServiceError -> 422 (django-ninja dispatches by walking `type(exc).__mro__`, so registering the
three subclasses plus the ServiceError base all coexist -- the most specific one wins). These are
OUR OWN messages, written for humans and safe to hand back verbatim, so `str(exc)` is the body.
They log at INFO with no traceback: a 403/404/409/422 from this family is an expected refusal, not
a fault -- something to see in the log, not something to page anyone about.

`django.db.IntegrityError` -> 422 too, but through its OWN handler, not `str(exc)`. Several inputs
are enforced only by the database, not by Python: `events.event_type` (FK to event_definitions),
`events.channel`/`events.direction` (CHECK), a fee's `status` (CHECK), a deadline's
`deadline_type` (FK to deadline_definitions since records_db.0007), and `tasks.create_task`'s
`request_id` when it names no row (FK). All five are P2's `notes` convention #9. Covering all
of them (plus anything else the schema's own vocabulary rejects) with one handler, instead of a
second Python copy of the same vocabulary, is still the right call -- but `str(exc)` on a raw
`IntegrityError` is Postgres's own error text, which includes a `DETAIL: Failing row contains
(...)` line naming every column of the row that failed, including ones this system deliberately
never puts in one response together (the internal tracking number and the agency-facing alias --
see `records/aliases.py`), plus table and constraint names. None of that is safe to return, and
none of it is something the four convention-9 callers above need to see to understand "that value
isn't valid." The client gets a stable, generic message instead; the server log gets
`logger.exception` (ERROR, full traceback) so a genuine bug -- a unique-violation race, or a
constraint added later that nobody taught this mapping about -- doesn't quietly become a tidy 422
that nobody is paged for (see the worker's thoughts file, F1/F2, for the reproduction).
"""

import logging

from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from portal.records.api import (
    access,
    agencies,
    challenges,
    deadlines,
    events,
    fees,
    inbox,
    logins,
    overview,
    reports,
    requests,
    review,
    search,
    sources,
    tasks,
)
from portal.records.api.permissions import PRIVILEGED_DETAIL, PrivilegedRefusal
from portal.records.services.common import (
    ChangedMeanwhile,
    InvalidTransition,
    NotAllowed,
    NotFound,
    ServiceError,
)

logger = logging.getLogger(__name__)

_STATUS = {
    NotFound: 404,
    NotAllowed: 403,
    InvalidTransition: 409,
    ServiceError: 422,
}

_INTEGRITY_ERROR_DETAIL = (
    "This request could not be completed because it violates a database constraint."
)


def register(api: NinjaAPI) -> None:
    routers = (
        agencies,
        requests,
        events,
        deadlines,
        sources,
        tasks,
        fees,
        inbox,
        overview,
        access,
        review,
        challenges,
        reports,
        search,
        logins,
    )
    for router in routers:
        api.add_router("/records", router.router)

    def handler_for(status: int):
        def handle(request: HttpRequest, exc: Exception) -> HttpResponse:
            logger.info("%s -> %s %s", type(exc).__name__, status, exc)
            return api.create_response(request, {"detail": str(exc)}, status=status)

        return handle

    def handle_integrity_error(request: HttpRequest, exc: Exception) -> HttpResponse:
        # Full traceback at ERROR, unconditionally -- this is convention #9's expected path AND
        # the only alarm a genuine server bug (a race, a forgotten mapping) will ever raise, so it
        # cannot be quieted the way the ServiceError family's expected refusals are above.
        logger.exception("IntegrityError mapped to 422")
        return api.create_response(request, {"detail": _INTEGRITY_ERROR_DETAIL}, status=422)

    def handle_privileged(request: HttpRequest, exc: PrivilegedRefusal) -> HttpResponse:
        # The whole body, by design: a code the client can branch on, the tracking number to ask
        # for access with, and a fixed sentence. No subject, agency or status (BUILD_PLAN P9).
        logger.info("PrivilegedRefusal -> 403 %s", exc.tracking_number or "(a message)")
        body = {
            "detail": PRIVILEGED_DETAIL,
            "code": "privileged",
            "tracking_number": exc.tracking_number,
        }
        return api.create_response(request, body, status=403)

    def handle_changed(request: HttpRequest, exc: ChangedMeanwhile) -> HttpResponse:
        # P23b (094): the refusal names who changed it and when, in the reader's own zone; the
        # parts are in the body too, for a client that words it itself.
        logger.info("ChangedMeanwhile -> 409 %s", exc)
        body = {
            "detail": changed_sentence(exc, getattr(request.user, "timezone", None)),
            "code": "changed",
            "changed_by": exc.changed_by,
            "changed_at": exc.changed_at.isoformat(),
            "status": exc.status,
        }
        return api.create_response(request, body, status=409)

    api.add_exception_handler(PrivilegedRefusal, handle_privileged)
    for exc_class, status in _STATUS.items():
        api.add_exception_handler(exc_class, handler_for(status))
    api.add_exception_handler(ChangedMeanwhile, handle_changed)
    api.add_exception_handler(IntegrityError, handle_integrity_error)


def changed_sentence(exc: ChangedMeanwhile, zone_name: str | None) -> str:
    """ "Maya Torres marked this filed at 3:32 PM. Refresh to see it." (094), with the date too when
    it was not today where the reader is. "You" when it was the reader, in another tab."""
    from django.utils import timezone

    from portal.records.models import StatusDefinition
    from portal.records.services.docket_summary import viewer_zone

    zone = viewer_zone(zone_name)
    at = exc.changed_at.astimezone(zone)
    clock = f"{at:%I:%M %p}".lstrip("0")
    when = (
        f"at {clock}"
        if at.date() == timezone.now().astimezone(zone).date()
        else f"on {at:%b} {at.day} at {clock}"
    )
    who = "You" if exc.mine else exc.changed_by
    if exc.status == "filed":
        did = "marked this filed"
    else:
        label = StatusDefinition.objects.filter(pk=exc.status).values_list(
            "label", flat=True
        ).first() or exc.status.replace("_", " ")
        did = f"changed its status to {label}"
    return f"{who} {did} {when}. Refresh to see it."
