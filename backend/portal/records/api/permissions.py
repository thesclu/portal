"""Who is asking, and whether they may see or touch this.

**Who may see a request is decided in this file and nowhere else** (HANDOFF convention 12). Every
route that takes a request, or an id that resolves to one, passes through one of the functions
below, and `tests/api/test_p9_census.py` fails for any route in the registry that it has not been
told about. The rule itself is one predicate, `services/access.py::_visible`; this file is the
only place that asks it on behalf of an HTTP caller.

The rule (BUILD_PLAN P9, rewritten for six roles by P20h; `services/access.py` states it):
  1. An administrator sees every request. Staff and staff attorneys see every request that is not
     privileged. A limited-scope attorney sees only what they were given (a grant); an intern sees
     what interns made and what they were given. A requester sees their own.
  2. A `privileged` request is seen by an administrator, by the people on its access list and by
     its requester of record. Nobody else.
  3. A message (a source) is seen when a request it is attached to is seen. **Unattached mail is
     triage, and admins, staff and staff attorneys see it. That is the one place a privileged
     message can be read before it is filed** (`access.source_visible_q`).

Who may reach a door at all, before visibility: `require_staff` admits FULL_STAFF (admin, staff,
staff attorney) and guards the organisation's own tables; `require_team` admits every role that
works requests (FULL_STAFF, limited-scope attorney, intern) and every request-scoped gate below
uses it, so what those two narrower roles reach is decided by the visibility rule. A role a door
does not name is refused (`portal/records/roles.py`).

What a refusal says. A member of staff opening a privileged request they are not on the list of
gets 403 with `{"code": "privileged", "tracking_number": ...}` and a fixed sentence: enough for the
"You don't have access" page to name what to ask for, and no subject, agency or status. A requester,
a limited-scope attorney and an intern never get that shape: for them a random id and a real
request they may not see stay the same 403 with the same body, so the response cannot confirm that
an id names a request (P3, F3).

How to use it from a handler:
  - a request id in the path of a write   -> `require_request_scope`
  - GET /requests/{id}                    -> `require_viewable_request`
  - an event, deadline, task, fee, item   -> `require_event_scope` and its siblings
  - a source, attachment or source note   -> `require_source_scope` and its siblings
  - a list of requests already in hand    -> `scope_visible_requests`
  - a list built by a service             -> pass `viewer(request)` to the service
  - request ids named by something else   -> `visible_request_ids` (a draft's, for instance)

Who work may be ASSIGNED to is not decided here. It is a rule about the assignee, not the caller,
and it has to hold for a job and for the Users screen too, so the assigning services keep it
(`access.require_assignee_can_view`).
"""

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from django.db.models import QuerySet
from django.http import HttpRequest
from ninja.errors import HttpError

from portal.core.services.identity import records_role_of
from portal.records import roles
from portal.records.actors import Actor
from portal.records.models import (
    Attachment,
    Deadline,
    Event,
    Fee,
    Request,
    RequestAccess,
    Source,
    SourceNote,
    Task,
)
from portal.records.services import access
from portal.records.services import edits as edits_service
from portal.records.services.access import Viewer
from portal.records.services.common import NotFound

_NOT_ALLOWED_TO_VIEW = "Not allowed to view this request."
PRIVILEGED_DETAIL = "You don't have access to this."


class PrivilegedRefusal(Exception):
    """A staff member reached for something privileged that has not been shared with them.

    Rendered by the handler in `records/api/__init__.py` as 403 with `code: "privileged"` and, when
    the thing is one request, its tracking number. Nothing else about the request is in it.
    """

    def __init__(self, tracking_number: str | None = None) -> None:
        super().__init__(PRIVILEGED_DETAIL)
        self.tracking_number = tracking_number


def records_role(request: HttpRequest) -> str | None:
    return records_role_of(request.user)


def viewer(request: HttpRequest) -> Viewer:
    """The caller, in the shape the visibility predicate and the list services take."""
    return Viewer(user_id=request.user.id, role=records_role(request))


def require_staff(request: HttpRequest) -> str:
    """Admin, staff or staff attorney: the organisation's own tables (the directory, templates,
    saved replies, suggestions), and anything not scoped to the requests one may see."""
    role = records_role(request)
    if role not in roles.FULL_STAFF:
        raise HttpError(403, "Staff only.")
    assert role is not None
    return role


def require_team(request: HttpRequest) -> str:
    """Anyone who works requests: FULL_STAFF, a limited-scope attorney, an intern. What they then
    see is the visibility rule's; every request-scoped gate in this file starts here."""
    role = records_role(request)
    if role not in roles.TEAM:
        raise HttpError(403, "Staff only.")
    assert role is not None
    return role


def require_records_admin(request: HttpRequest) -> None:
    """403 unless the caller's records role is `admin`. Since P20h this is also every door the
    retired Director marker guarded (moving a deadline's date; 30l, 42d), and giving or taking a
    grant."""
    if records_role(request) != roles.ADMIN:
        raise HttpError(403, "Administrators only.")


def _hidden(request: HttpRequest, tracking_number: str | None = None) -> Exception:
    """The refusal for something the caller may not see. Staff get P9's privileged 403 (it names a
    privileged request so they can ask for it); everyone else the plain 403 that confirms
    nothing."""
    if records_role(request) in roles.FULL_STAFF:
        return PrivilegedRefusal(tracking_number)
    return HttpError(403, _NOT_ALLOWED_TO_VIEW)


def require_may_release(request: HttpRequest) -> None:
    """For a door that files a request or sends something out of the portal by hand. An intern's
    letters and messages go through approval instead (P20h; 42b): they are refused here, before
    anything is written, and the services refuse them again for every other caller."""
    if records_role(request) in roles.NEEDS_APPROVAL:
        raise HttpError(
            403, "An intern's letters and messages go out only after approval: submit it instead."
        )


def require_human_actor(request: HttpRequest) -> Actor:
    return Actor.human(request.user)


def assignee_filter(request: HttpRequest, value: str | None) -> UUID | None:
    """The `assignee` query parameter: `me`, a person's uuid, or absent."""
    if value is None or value == "":
        return None
    if value == "me":
        return request.user.id
    try:
        return UUID(value)
    except ValueError:
        raise HttpError(422, "assignee must be 'me' or a person's id.") from None


# ---- requests -------------------------------------------------------------------------------


def require_viewable_request(request: HttpRequest, request_id: UUID) -> Request:
    """The single lookup+check for a request, whoever is asking.

    Staff: a missing id is `NotFound` (404), as before; a privileged request they are not on the
    list of is `PrivilegedRefusal`. For anyone else, a random uuid and someone else's real request
    must be indistinguishable: both come back as this SAME 403, with this same body, rather than
    letting a 404-vs-403 split confirm that a given id names a real request. One query does both
    the fetch and the ownership check, so there is no separate "does it exist" step whose result
    could leak through timing or a different error path.
    """
    role = records_role(request)
    if role in roles.FULL_STAFF:
        req = Request.objects.filter(pk=request_id).first()
        if req is None:
            # P17: the policies hide a privileged request from staff not on its list; the
            # refusal still names it, as P9 decided, through the one lookup that may.
            sealed = access.sealed_tracking_number(request_id)
            if sealed is not None:
                raise PrivilegedRefusal(sealed)
            raise NotFound(f"No request {request_id}.")
        if not access.can_view(viewer(request), req):
            raise PrivilegedRefusal(req.tracking_number)
        return req
    if role in roles.GRANT_SCOPED:
        # A limited-scope attorney or an intern: a request they may not see and an id that names
        # nothing are the same refusal (the policies already hide the row; this asks the rule too).
        req = Request.objects.filter(pk=request_id).first()
        if req is not None and access.can_view(viewer(request), req):
            return req
        raise HttpError(403, _NOT_ALLOWED_TO_VIEW)
    if role == roles.REQUESTER:
        req = Request.objects.filter(pk=request_id, requester_id=request.user.id).first()
        if req is not None:
            return req
    raise HttpError(403, _NOT_ALLOWED_TO_VIEW)


def require_request_scope(request: HttpRequest, request_id: UUID) -> Request:
    """For every door that names a request: someone on the team, and a request they may see."""
    require_team(request)
    return require_viewable_request(request, request_id)


def require_named_request_scope(request: HttpRequest, request_id: UUID) -> None:
    """For a request id carried in a BODY. An id that names nothing is left for the service or the
    database to refuse as it always has (HANDOFF convention 9); one that names a request the caller
    may not see is refused here. For a limited-scope attorney or an intern an id they cannot read
    is refused whether or not it names a request: a foreign key is checked past the row-level
    policies, so leaving it to the database would let them write onto a request hidden from them.
    """
    role = require_team(request)
    req = Request.objects.filter(pk=request_id).first()
    if role in roles.GRANT_SCOPED:
        if req is None or not access.can_view(viewer(request), req):
            raise HttpError(403, _NOT_ALLOWED_TO_VIEW)
        return
    if req is not None and not access.can_view(viewer(request), req):
        raise PrivilegedRefusal(req.tracking_number)
    if req is None:
        sealed = access.sealed_tracking_number(request_id)
        if sealed is not None:
            raise PrivilegedRefusal(sealed)


def scope_visible_requests(request: HttpRequest, rows: Sequence[Request]) -> Sequence[Request]:
    """The rows the caller may see. Totals are counted after this, so a hidden request moves no
    count the caller is shown."""
    if records_role(request) is None:
        raise HttpError(403, "No records access.")
    return access.scope(viewer(request), rows)


def visible_request_ids(request: HttpRequest, request_ids: Iterable[UUID]) -> set[UUID]:
    """Which of these requests the caller may see. For something that is not a request but names
    some (a draft and the requests it was started from or filed as), so that it can borrow their
    visibility without deciding it."""
    return access.visible_ids(viewer(request), request_ids)


def require_share_scope(request: HttpRequest, request_id: UUID, user_id: UUID) -> Request:
    """POST /requests/{id}/access and every other change to who has access: administrators only
    (P20h; 37a), who see every request. The service keeps the same rule for other callers."""
    require_records_admin(request)
    req = Request.objects.filter(pk=request_id).first()
    if req is None:
        raise NotFound(f"No request {request_id}.")
    return req


# ---- a child id -> its request ---------------------------------------------------------------


def _refuse_hidden(model: Any, pk: UUID, what: str) -> None:
    """P17: a row the policies hide is refused as P9 refuses it (403, naming nothing) when it
    exists, and is a 404 only when it does not."""
    table = model._meta.db_table.split(".")[-1].strip('"')
    if access.hidden_row_exists(table, pk):
        raise PrivilegedRefusal()
    raise NotFound(f"No {what} {pk}.")


def _require_child_scope(request: HttpRequest, model: Any, pk: UUID, what: str) -> Any:
    require_team(request)
    row = model.objects.filter(pk=pk).first()
    if row is None:
        _refuse_hidden(model, pk, what)
    require_viewable_request(request, row.request_id)
    return row


def require_event_scope(request: HttpRequest, event_id: UUID) -> Event:
    return _require_child_scope(request, Event, event_id, "event")


def require_deadline_scope(request: HttpRequest, deadline_id: UUID) -> Deadline:
    return _require_child_scope(request, Deadline, deadline_id, "deadline")


def require_task_scope(request: HttpRequest, task_id: UUID) -> Task:
    return _require_child_scope(request, Task, task_id, "task")


def require_fee_scope(request: HttpRequest, fee_id: UUID) -> Fee:
    return _require_child_scope(request, Fee, fee_id, "fee")


def require_access_scope(request: HttpRequest, access_id: UUID) -> RequestAccess:
    return _require_child_scope(request, RequestAccess, access_id, "access row")


def require_entity_scope(
    request: HttpRequest, request_id: UUID, entity_type: str, entity_id: UUID
) -> None:
    """POST /requests/{id}/edits names its target in the body. The request in the path must be
    visible AND the row being edited must belong to it, or the path would be a decoration and any
    visible request a key to every other request's rows."""
    require_request_scope(request, request_id)
    owner = edits_service.request_id_of(entity_type, entity_id)
    if owner is not None and owner != request_id:
        raise HttpError(422, "That record does not belong to this request.")


# ---- messages -------------------------------------------------------------------------------


def require_source_scope(request: HttpRequest, source_id: UUID) -> Source:
    require_team(request)
    source = Source.objects.filter(pk=source_id).first()
    if source is None:
        _refuse_hidden(Source, source_id, "source")
    if not access.can_view_source(viewer(request), source_id):
        raise _hidden(request)
    return source


def require_named_source_scope(request: HttpRequest, source_id: UUID | None) -> None:
    """For a source id carried in a body: attaching a message you cannot see to a request you can
    would be a way to read it. A missing one is left for the service to refuse (the services read
    a source through the policies, so one hidden from the caller is missing to them too)."""
    require_team(request)
    if source_id is None or not Source.objects.filter(pk=source_id).exists():
        return
    if not access.can_view_source(viewer(request), source_id):
        raise _hidden(request)


def require_attachment_scope(request: HttpRequest, attachment_id: UUID) -> Attachment:
    require_team(request)
    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None:
        _refuse_hidden(Attachment, attachment_id, "attachment")
    require_source_scope(request, attachment.source_id)
    return attachment


def require_source_note_scope(request: HttpRequest, note_id: UUID) -> SourceNote:
    require_team(request)
    note = SourceNote.objects.filter(pk=note_id).first()
    if note is None:
        _refuse_hidden(SourceNote, note_id, "note")
    require_source_scope(request, note.source_id)
    return note


def scope_visible_sources(request: HttpRequest, rows: QuerySet[Source]) -> QuerySet[Source]:
    require_team(request)
    return rows.filter(access.source_visible_q(viewer(request)))
