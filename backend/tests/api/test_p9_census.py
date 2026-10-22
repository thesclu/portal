"""The census: every route the API has, against a privileged request, as every kind of caller.

BUILD_PLAN P9: "for every route in django-ninja's own registry, against a privileged request and
each kind of child id, as five callers -- an admin with no grant, staff with no grant, staff with a
grant, the requester of record, anonymous. The first two get 403 or an omitted row on every route;
the third and fourth get what they get on an internal request. A route that is not in the census
map fails the test."

How it holds a route to that:

  - `CENSUS` maps every (method, path) in the registry either to `NO_REQUEST` (the route neither
    takes nor resolves to a request, a message or a draft) or to the cases that reach a privileged
    thing through it. `test_every_route_in_the_registry_is_in_the_census` compares the map to the
    registry in both directions, so a route added by a later part fails until someone decides
    which it is.
  - Each case is run against a SEALED world (a privileged request with events, messages, files,
    deadlines, tasks, a fee, an access list and a privileged draft, every string in it marked).
    Anonymous is 401. The two outsiders get exactly the privileged refusal, or, on a list, a 200
    in which no marked string and no id of the sealed world appears. Nothing in the database may
    have moved after they have all tried.
  - Then the insider (on the list, and the creator of the draft) makes the same call, and it must
    work; on a list they must actually see the sealed row, so "nothing leaked" is never true only
    because the list was empty. Finally the same call is made against a TWIN world that differs
    only in being `internal`: the insider and the requester of record must get the same status on
    both, which is what "what they get on an internal request" means.

P20h (six roles; DECISIONS 37a, 43) widened it:
  - an ADMINISTRATOR sees every request, privileged included, so the administrator with no grant is
    no longer an outsider: they must not be refused, and on the doors only an administrator opens
    (giving and taking access, deciding asks: `by_admin`) they are the insider;
  - a STAFF ATTORNEY with no grant is an outsider like staff, with the same privileged refusal;
  - a LIMITED-SCOPE ATTORNEY and an INTERN with no grant are outsiders who get the plain refusal (a
    403 or 404 that names nothing: they are never told a request exists), and they are outsiders
    on the INTERNAL twin as well, which is an ordinary request they were not given and no intern
    made -- so every route is tried against a request each of them may not see;
  - the same two roles WITH a grant are insiders on both worlds, so every route is tried against a
    request each of them may see: they are not refused, except where their role itself is the
    reason (`scoped_refused`, the doors that are staff's or an administrator's, and an intern's
    filing and sending, which go through approval).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from portal.api import api
from portal.core.services.identity import create_user
from portal.drafting.models import (
    LetterDraft,
    LetterDraftCitation,
    LetterDraftItem,
    LetterRecipient,
)
from portal.drafting.services import drafts as drafts_service
from portal.mail.services import compose as mail_compose
from portal.records.actors import Actor
from portal.records.api.permissions import PRIVILEGED_DETAIL
from portal.records.models import (
    Attachment,
    AuditLog,
    Deadline,
    Event,
    Fee,
    Request,
    RequestAccess,
    RequestItem,
    Source,
    SourceNote,
    StatusChange,
    Task,
    TaskNote,
)
from portal.records.services import access as access_service
from portal.records.services import challenges as challenges_service
from portal.records.services import deadlines as deadlines_service
from portal.records.services import directory
from portal.records.services import fees as fees_service
from portal.records.services import links as links_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import tasks as tasks_service

from tests.p8b_helpers import RECEIVED_AT, attach, make_source

NO_REQUEST = "no request"
WHEN = "2026-03-02T17:30:00Z"
OVERDUE = date(2020, 1, 6)
LATER = date(2020, 2, 3)

# ---- the world --------------------------------------------------------------------------------


@dataclass
class World:
    """One request with one of everything hanging off it, and a draft. `mark` is in every string a
    person typed, so a leak of any of them is findable in a response body."""

    mark: str
    request: Request
    agency: Any
    other_agency: Any  # not yet a recipient of the draft
    items: list[RequestItem]
    source: Source  # attached by `event`, reviewed, one stored file
    attachment: Attachment
    event: Event
    note: SourceNote
    flagged_source: Source  # attached and flagged: the Inbox's `flagged` bucket
    new_source: Source  # attached, not flagged, not reviewed: the `new` bucket
    deadline: Deadline
    later_deadline: Deadline
    task: Task
    task_note: TaskNote  # P23b: a note on `task`
    rules_task: Task
    fee: Fee
    access_row: RequestAccess | None  # the insider's grant; None in a world that is not sealed
    draft: LetterDraft
    draft_item: LetterDraftItem
    draft_citation: LetterDraftCitation
    recipient: LetterRecipient
    # A draft that is NOT privileged itself but was started from the request, so it carries the
    # request's subject and items: it must be exactly as visible as the request.
    linked_draft: LetterDraft
    linked_item: LetterDraftItem
    linked_recipient: LetterRecipient
    # Things everyone may see, built beside it, for the calls that name TWO things.
    side_request: Request
    side_event: Event
    link: Any  # P12: the sealed request named as a follow-on to side_request
    message: Any  # P11: a draft message on the sealed request
    ask: Any  # P13: staff_out asking for the sealed request
    challenge: Any  # P14: an appeal opened on the sealed request, no letter yet
    loose_source: Source
    secrets: list[str] = field(default_factory=list)


@dataclass
class People:
    owner: Any
    insider: Any
    staff_out: Any
    attorney_out: Any
    limited_out: Any
    intern_out: Any
    limited_in: Any
    intern_in: Any
    admin: Any
    bystander: Any
    requester: Any


def _person(email: str, role: str = "staff", **extra: Any):
    return create_user(email=email, name=email.split("@")[0], records_role=role, **extra)


@pytest.fixture
def people(db) -> People:
    return People(
        owner=_person("census-owner@example.org"),
        insider=_person("census-insider@example.org"),
        staff_out=_person("census-staff-out@example.org"),
        attorney_out=_person("census-attorney-out@example.org", "staff_attorney"),
        limited_out=_person("census-limited-out@example.org", "limited_attorney"),
        intern_out=_person("census-intern-out@example.org", "intern"),
        limited_in=_person("census-limited-in@example.org", "limited_attorney"),
        intern_in=_person("census-intern-in@example.org", "intern"),
        admin=_person("census-admin@example.org", "admin", is_staff=True),
        bystander=_person("census-bystander@example.org"),
        requester=_person("census-requester@example.org", "requester"),
    )


def _sendable(owner: Actor, name: str):
    made = directory.create_agency(
        owner,
        name=name,
        jurisdiction="Testland",
        recipient_office="Records Unit",
        mailing_street="1 Test Street",
        mailing_city_state_zip="Testville, TL 00000",
        default_request_email="records@agency.example.org",
        filing_method="email",
    )
    return directory.verify_request_email(owner, made.pk, "Confirmed by a test.")


def build_world(people: People, mark: str, sensitivity: str) -> World:
    owner = Actor.human(people.owner)
    # Agencies a letter can really go to: since P10 a recipient for anything less arrives held and
    # a letter with a blank in it is not reserved, so the drafting cases would prove nothing.
    agency = _sendable(owner, f"Agency for {mark[:2]}")
    request = requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=people.requester.pk,
        subject=f"{mark}-SUBJECT",
        request_text=f"{mark}-TEXT",
        items=[
            requests_service.NewItem("1", f"{mark}-ITEM-ONE"),
            requests_service.NewItem("2", f"{mark}-ITEM-TWO"),
        ],
        sensitivity=sensitivity,
    )
    access_row = None
    giver = Actor.human(people.admin)  # P20h: only an administrator gives access
    if sensitivity == "privileged":
        access_row = access_service.grant_access(giver, request.pk, people.insider.pk, "Second.")
    for scoped in (people.limited_in, people.intern_in):
        access_service.grant_access(giver, request.pk, scoped.pk, "Given.")
    added = sources_service.add_document(
        owner,
        request.pk,
        source_type="scanned_mail",
        received_at=RECEIVED_AT,
        files=[sources_service.IncomingFile(f"{mark}-file.pdf", [f"{mark}-BYTES".encode()])],
        display_body=f"{mark}-BODY",
    )
    note = sources_service.add_source_note(owner, added.source.pk, f"{mark}-NOTE")
    # P12: the file is a produced record, so the Records listing has a sealed row to leave out.
    sources_service.mark_attachments_as_records(owner, added.source.pk, [added.attachments[0].pk])
    flagged_source = make_source(owner, flag=("other", f"{mark}-FLAGNOTE"))
    flagged_event = attach(owner, request, flagged_source, body=f"{mark}-FLAGGED-BODY")
    new_source = make_source(owner)
    new_event = attach(owner, request, new_source, body=f"{mark}-NEW-BODY")
    deadline = deadlines_service.open_deadline(
        owner, request.pk, deadline_type="production", due_at=OVERDUE, basis="agency_commitment"
    )
    later = deadlines_service.open_deadline(
        owner, request.pk, deadline_type="production", due_at=LATER, basis="agency_commitment"
    )
    task = tasks_service.create_task(
        owner, request.pk, "manual", f"{mark}-TASK", assigned_to_user_id=people.insider.pk
    )
    rules_task = tasks_service.create_task(
        owner, request.pk, "enter_deadline_rules", f"{mark}-RULETASK"
    )
    task_note = tasks_service.add_task_note(owner, task.pk, f"{mark}-TASKNOTE")
    fee = fees_service.record_fee(
        owner, request.pk, status="assessed", amount_cents=500, description=f"{mark}-FEE"
    )
    draft = drafts_service.create_draft(
        subject=f"{mark}-DRAFT", sensitivity=sensitivity, created_by_user_id=people.insider.pk
    )
    draft_item = drafts_service.add_draft_item(draft.pk, item_number="1", body=f"{mark}-DRAFT-ITEM")
    draft_citation = drafts_service.add_draft_citation(
        draft.pk, marker="1", citation_text=f"{mark}-CITATION"
    )
    recipient = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    linked_draft = drafts_service.create_draft(
        from_request_id=request.pk, sensitivity="internal", created_by_user_id=people.insider.pk
    )
    linked_item = LetterDraftItem.objects.filter(draft_id=linked_draft.pk).order_by("pk").first()
    linked_recipient = drafts_service.add_recipient(
        linked_draft.pk, agency_id=agency.pk, channel="email"
    )

    side_request = requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=people.requester.pk,
        subject="An ordinary request",
        request_text="Nothing sealed here.",
    )
    side_event = attach(owner, side_request, make_source(owner), body="An ordinary message.")
    loose_source = make_source(owner)

    request = Request.objects.get(pk=request.pk)
    rows = [
        request,
        *RequestItem.objects.filter(request_id=request.pk),
        added.source,
        added.attachments[0],
        added.event,
        note,
        flagged_source,
        flagged_event,
        new_source,
        new_event,
        deadline,
        later,
        task,
        task_note,
        rules_task,
        fee,
        draft,
        draft_item,
        draft_citation,
        recipient,
        linked_draft,
        linked_item,
        linked_recipient,
    ]
    secrets = [mark, request.tracking_number, request.inbound_alias_email or mark]
    secrets += [str(row.pk) for row in rows]
    return World(
        mark=mark,
        request=request,
        agency=agency,
        other_agency=_sendable(owner, f"Second agency for {mark[:2]}"),
        items=list(RequestItem.objects.filter(request_id=request.pk).order_by("item_number")),
        source=added.source,
        attachment=added.attachments[0],
        event=added.event,
        note=note,
        flagged_source=flagged_source,
        new_source=new_source,
        deadline=deadline,
        later_deadline=later,
        task=task,
        task_note=task_note,
        rules_task=rules_task,
        fee=fee,
        access_row=access_row,
        draft=draft,
        draft_item=draft_item,
        draft_citation=draft_citation,
        recipient=recipient,
        linked_draft=linked_draft,
        linked_item=linked_item,
        linked_recipient=linked_recipient,
        side_request=side_request,
        side_event=side_event,
        link=links_service.link_requests(
            owner, request.pk, side_request.pk, kind="follow_on", reason=f"{mark}-LINK"
        ),
        message=mail_compose.create_draft(
            owner, request.pk, subject=f"{mark}-MAIL", to=["records@agency.example.org"]
        ),
        ask=(
            access_service.ask_access(Actor.human(people.staff_out), request.pk, f"{mark}-ASK")
            if sensitivity == "privileged"
            else None
        ),
        challenge=challenges_service.open_challenge(
            owner, request.pk, kind="appeal", basis=f"{mark}-CHALLENGE"
        ),
        loose_source=loose_source,
        secrets=secrets,
    )


# ---- a case: how to reach something privileged through one route --------------------------------


@dataclass(frozen=True)
class Call:
    path: str
    json: Any = None
    form: dict | None = None  # multipart


@dataclass(frozen=True)
class Case:
    """`kind` says what the route resolves to, which decides what a refusal looks like:
    `request` (403 naming the tracking number), `message` or `draft` (403 naming nothing), or
    `list` (200 with the sealed rows left out; `shows` picks a string the insider must see).
    `insider` is the status the insider must get when there is no internal twin to compare with
    (the access doors mean nothing on a request that is not privileged)."""

    kind: str
    build: Callable[[World, People], Call]
    insider: int | None = None
    shows: Callable[[World], str] | None = None
    name: str = ""
    # P13: the one shape of route an OUTSIDER must reach: asking for access. When set, outsiders
    # must get this status instead of the privileged 403, and the body is not checked.
    outsider: tuple[int, ...] | None = None
    # P20h: a door only an administrator opens (giving and taking access, the asks). The insider
    # whose call must work is then the administrator, and staff with a grant are refused there.
    by_admin: bool = False
    # P20h: a door only an intern opens (submitting a letter for approval). The insider is then
    # the intern given the request, and an administrator, who files without approval, is refused.
    by_intern: bool = False


def R(build, **kw) -> Case:  # noqa: N802 -- reads as a table below
    return Case("request", build, **kw)


def M(build, **kw) -> Case:  # noqa: N802
    return Case("message", build, **kw)


def D(build, **kw) -> Case:  # noqa: N802
    return Case("draft", build, **kw)


def L(build, shows, **kw) -> Case:  # noqa: N802
    return Case("list", build, shows=shows, **kw)


def _path(template: str):
    """A case that only fills the path: `{request}` is the sealed request's id, and so on."""

    def build(w: World, p: People) -> Call:
        return Call(template.format(**_ids(w)))

    return build


def _ids(w: World) -> dict[str, Any]:
    return {
        "request": w.request.pk,
        "event": w.event.pk,
        "deadline": w.deadline.pk,
        "task": w.task.pk,
        "task_note": w.task_note.pk,
        "fee": w.fee.pk,
        "source": w.source.pk,
        "note": w.note.pk,
        "attachment": w.attachment.pk,
        "access": w.access_row.pk if w.access_row else "00000000-0000-0000-0000-000000000000",
        "agency": w.agency.pk,
        "draft": w.draft.pk,
        "draft_item": w.draft_item.pk,
        "draft_citation": w.draft_citation.pk,
        "recipient": w.recipient.pk,
        "linked_draft": w.linked_draft.pk,
        "linked_item": w.linked_item.pk,
        "linked_recipient": w.linked_recipient.pk,
        "side_request": w.side_request.pk,
        "link": w.link.pk,
        "message": w.message.pk,
        "ask": w.ask.pk if w.ask else "00000000-0000-0000-0000-000000000000",
        "challenge": w.challenge.pk,
        "loose_source": w.loose_source.pk,
        "new_source": w.new_source.pk,
    }


def _json(template: str, body):
    """A case with a JSON body; `body` is a dict or a function of the world (and the people)."""

    def build(w: World, p: People) -> Call:
        payload = body(w, p) if callable(body) else body
        return Call(template.format(**_ids(w)), json=payload)

    return build


EVENT = {
    "event_type": "correspondence",
    "channel": "email",
    "direction": "inbound",
    "occurred_at": WHEN,
    "display_body": "census",
}
NEW_DEADLINE = {"deadline_type": "production", "due_at": "2031-01-06", "basis": "agency_commitment"}
NOTE = {"note": "census"}


def _edit(entity_type: str, row: Callable[[World], Any], field_name: str, value: Any = "census"):
    return _json(
        "/records/requests/{request}/edits",
        lambda w, p: {
            "entity_type": entity_type,
            "entity_id": str(row(w).pk),
            "field": field_name,
            "new_value": value,
            "note": "census",
        },
    )


def tracking(w: World) -> str:
    return w.request.tracking_number


RQ = "/records/requests/{request}"
LINKED = "an ordinary draft that names the sealed request"
SRC = "/records/sources/{source}"

CENSUS: dict[tuple[str, str], Any] = {
    # ---- core: people, sign-in, settings. None takes or resolves to a request. ------------------
    ("GET", "/core/auth/csrf"): NO_REQUEST,
    ("POST", "/core/auth/logout"): NO_REQUEST,
    ("GET", "/core/auth/me"): NO_REQUEST,
    ("PATCH", "/core/auth/me"): NO_REQUEST,
    ("GET", "/core/users"): NO_REQUEST,
    # Takes no request, but deactivating a person or making them a requester reaches every
    # privileged request they are on (their access, their tasks). It cannot be a census case, since
    # the caller is by definition an administrator acting on a PERSON; what it may and may not do
    # to a sealed request is pinned in test_p9_api.py ("the Users screen reaches privileged
    # requests too").
    ("PATCH", "/core/users/{user_id}"): NO_REQUEST,
    ("GET", "/core/settings"): NO_REQUEST,
    ("PUT", "/core/settings/{key}"): NO_REQUEST,
    # P6. Counts of mail and the jobs' run times; administrators only; names no request.
    ("GET", "/core/system"): NO_REQUEST,
    # ---- the directory. An agency is not privileged; its page lists requests, so that one is. ---
    ("GET", "/records/agencies"): NO_REQUEST,
    ("POST", "/records/agencies"): NO_REQUEST,
    ("PATCH", "/records/agencies/{agency_id}"): NO_REQUEST,
    ("GET", "/records/agencies/{agency_id}/contacts"): NO_REQUEST,
    ("POST", "/records/agencies/{agency_id}/contacts"): NO_REQUEST,
    ("GET", "/records/agencies/{agency_id}"): [L(_path("/records/agencies/{agency}"), tracking)],
    ("GET", "/records/definitions"): NO_REQUEST,
    # ---- requests -----------------------------------------------------------------------------
    ("GET", "/records/requests"): [
        L(_path("/records/requests"), tracking),
        L(_path("/records/requests?sensitivity=privileged"), tracking, name="filtered"),
        L(lambda w, p: Call(f"/records/requests?q={w.mark}"), tracking, name="searched"),
        # P20b2: the stat boxes' filters (several statuses, a span of dates, open only).
        L(
            _path(
                "/records/requests?status=pending_filing,filed&open=true"
                "&due_after=2020-01-01&due_before=2020-02-01"
            ),
            tracking,
            name="stat box filters",
        ),
    ],
    # P20b2: the stat strip, its sparklines and the assign dialog's per-person counts are counts
    # over what the caller may see, like the reports; they name no request and return no row.
    # Their visibility is tested with a sealed request in tests/api/test_p20b2_api.py.
    ("GET", "/records/requests/summary"): NO_REQUEST,
    ("GET", "/records/requests/history"): NO_REQUEST,
    ("GET", "/records/requests/assignee-counts"): NO_REQUEST,
    # Reserving makes a request; it reaches no existing one.
    ("POST", "/records/requests"): NO_REQUEST,
    ("GET", "/records/requests/{request_id}"): [R(_path(RQ))],
    # P4b. The Files tab and the Notes tab: the request's files and the notes on its messages.
    ("GET", "/records/requests/{request_id}/attachments"): [R(_path(RQ + "/attachments"))],
    ("GET", "/records/requests/{request_id}/source-notes"): [R(_path(RQ + "/source-notes"))],
    ("POST", "/records/requests/{request_id}/file"): [
        R(_json(RQ + "/file", {"filed_at": WHEN, "channel": "email"})),
        M(
            _json(
                "/records/requests/{side_request}/file",
                lambda w, p: {"filed_at": WHEN, "channel": "email", "source_id": str(w.source.pk)},
            ),
            name="a sealed message named in the body",
        ),
    ],
    # P23b: the deadlines filing would open (083), a dry run; and the record as a PDF (069).
    ("POST", "/records/requests/{request_id}/file/preview"): [
        R(_json(RQ + "/file/preview", {"filed_at": WHEN}))
    ],
    ("GET", "/records/requests/{request_id}/record.pdf"): [R(_path(RQ + "/record.pdf"))],
    ("POST", "/records/requests/{request_id}/status"): [
        R(_json(RQ + "/status", {"status": "acknowledged", "note": "census"}))
    ],
    ("POST", "/records/requests/{request_id}/edits"): [
        R(_edit("requests", lambda w: w.request, "subject"), name="the request"),
        R(_edit("events", lambda w: w.event, "display_body"), name="an event"),
        R(_edit("deadlines", lambda w: w.deadline, "legal_basis"), name="a deadline"),
        R(_edit("request_items", lambda w: w.items[0], "description"), name="an item"),
    ],
    ("POST", "/records/requests/{request_id}/assign"): [
        R(_json(RQ + "/assign", {"user_id": None}))
    ],
    ("POST", "/records/requests/{request_id}/narrow"): [
        R(
            _json(
                RQ + "/narrow",
                lambda w, p: {"edits": {str(w.items[0].pk): "narrower"}, "note": "census"},
            )
        )
    ],
    ("POST", "/records/requests/{request_id}/items"): [
        R(_json(RQ + "/items", {"item_number": "9", "description": "census", "note": "census"}))
    ],
    ("POST", "/records/requests/{request_id}/documents"): [
        R(
            lambda w, p: Call(
                f"/records/requests/{w.request.pk}/documents",
                form={
                    "source_type": "scanned_mail",
                    "received_at": WHEN,
                    "display_body": "census",
                    "files": SimpleUploadedFile("census.pdf", b"census", "application/pdf"),
                },
            )
        )
    ],
    ("POST", "/records/requests/{request_id}/events"): [
        R(_json(RQ + "/events", EVENT)),
        M(
            _json(
                "/records/requests/{side_request}/events",
                lambda w, p: {**EVENT, "source_id": str(w.source.pk)},
            ),
            name="a sealed message named in the body",
        ),
    ],
    ("POST", "/records/requests/{request_id}/deadlines"): [
        R(_json(RQ + "/deadlines", NEW_DEADLINE))
    ],
    ("POST", "/records/requests/{request_id}/fees"): [
        R(_json(RQ + "/fees", {"status": "assessed", "amount_cents": 100}))
    ],
    # ---- the access list ------------------------------------------------------------------------
    ("GET", "/records/requests/{request_id}/access"): [R(_path(RQ + "/access"), insider=200)],
    ("POST", "/records/requests/{request_id}/access"): [
        # Naming a third person: an administrator adding THEMSELVES is the one exception to the
        # rule and has its own tests in test_p9_api.py.
        R(
            _json(RQ + "/access", lambda w, p: {"user_id": str(p.bystander.pk)}),
            insider=201,
            by_admin=True,
        )
    ],
    # The one deliberate exception to "an outsider learns nothing": an ADMINISTRATOR may list the
    # privileged requests they are not on, by tracking number only, so that adding themselves
    # (which leaves a row) is possible for a request they did not already know of. It cannot be a
    # census case, because the census requires an administrator to see nothing; what it shows,
    # and to whom, is pinned in test_p9_api.py.
    ("GET", "/records/access/unlisted"): NO_REQUEST,
    ("POST", "/records/access/{access_id}/revoke"): [
        R(_path("/records/access/{access}/revoke"), insider=200, by_admin=True)
    ],
    # ---- events, deadlines, fees, tasks: a child id resolves to its request ---------------------
    ("POST", "/records/events/{event_id}/void"): [
        R(_json("/records/events/{event}/void", {"reason": "census"}))
    ],
    ("GET", "/records/events/{event_id}/classify"): [
        R(_path("/records/events/{event}/classify?kind=acknowledgment"))
    ],
    ("POST", "/records/events/{event_id}/classify"): [
        R(_json("/records/events/{event}/classify", {"kind": "acknowledgment"}))
    ],
    ("POST", "/records/deadlines/{deadline_id}/satisfy"): [
        R(
            _json(
                "/records/deadlines/{deadline}/satisfy",
                lambda w, p: {"satisfied_by_event_id": str(w.event.pk)},
            )
        )
    ],
    ("POST", "/records/deadlines/{deadline_id}/waive"): [
        R(_json("/records/deadlines/{deadline}/waive", NOTE))
    ],
    # P20h: moving an existing deadline's date is an administrator's (the retired Director mark).
    ("POST", "/records/deadlines/{deadline_id}/supersede"): [
        R(
            _json("/records/deadlines/{deadline}/supersede", {**NEW_DEADLINE, "note": "census"}),
            insider=201,
            by_admin=True,
        )
    ],
    ("POST", "/records/deadlines/{deadline_id}/resolve"): [
        R(
            _json(
                "/records/deadlines/{deadline}/resolve",
                lambda w, p: {"drop_deadline_id": str(w.later_deadline.pk), "note": "census"},
            )
        )
    ],
    ("PATCH", "/records/fees/{fee_id}"): [
        R(_json("/records/fees/{fee}", {"status": "disputed", "note": "census"}))
    ],
    ("GET", "/records/tasks"): [
        L(_path("/records/tasks"), lambda w: f"{w.mark}-TASK"),
        L(_path("/records/tasks?state=all&request_id={request}"), lambda w: str(w.task.pk)),
        L(
            lambda w, p: Call(f"/records/tasks?assignee={p.insider.pk}"),
            lambda w: str(w.task.pk),
            name="by assignee",
        ),
    ],
    ("POST", "/records/tasks"): [
        R(
            _json(
                "/records/tasks",
                lambda w, p: {
                    "request_id": str(w.request.pk),
                    "task_type": "manual",
                    "description": "census",
                },
            )
        ),
        # P23b: a task names the message it is about; a sealed message named on an ordinary
        # request's task is refused before anything is written.
        M(
            _json(
                "/records/tasks",
                lambda w, p: {
                    "request_id": str(w.side_request.pk),
                    "task_type": "manual",
                    "description": "census",
                    "source_id": str(w.source.pk),
                },
            ),
            name="a sealed message named in the body",
        ),
    ],
    ("PATCH", "/records/tasks/{task_id}"): [
        R(_json("/records/tasks/{task}", {"note": "census", "description": "census"}))
    ],
    ("POST", "/records/tasks/{task_id}/complete"): [R(_path("/records/tasks/{task}/complete"))],
    ("POST", "/records/tasks/{task_id}/assign"): [
        R(_json("/records/tasks/{task}/assign", {"user_id": None}))
    ],
    ("POST", "/records/tasks/{task_id}/reopen"): [R(_json("/records/tasks/{task}/reopen", NOTE))],
    ("GET", "/records/tasks/{task_id}/history"): [R(_path("/records/tasks/{task}/history"))],
    # P23b: notes on a task (177), seen and written by whoever sees the task's request; a note is
    # voided under its task, so the task decides.
    ("GET", "/records/tasks/{task_id}/notes"): [R(_path("/records/tasks/{task}/notes"))],
    ("POST", "/records/tasks/{task_id}/notes"): [
        R(_json("/records/tasks/{task}/notes", {"body": "census"}))
    ],
    ("POST", "/records/tasks/{task_id}/notes/{note_id}/void"): [
        R(_json("/records/tasks/{task}/notes/{task_note}/void", {"reason": "census"}))
    ],
    # ---- messages: a source borrows its visibility from where it landed --------------------------
    ("GET", "/records/sources"): [L(_path("/records/sources"), lambda w: str(w.source.pk))],
    ("GET", "/records/sources/{source_id}"): [M(_path(SRC))],
    ("POST", "/records/sources/{source_id}/assign"): [
        M(
            _json(
                SRC + "/assign",
                lambda w, p: {
                    "request_id": str(w.side_request.pk),
                    "mode": "also_applies",
                    "from_event_id": str(w.event.pk),
                },
            ),
            name="a sealed message onto an ordinary request",
        ),
        R(
            _json(
                "/records/sources/{loose_source}/assign",
                lambda w, p: {
                    "request_id": str(w.request.pk),
                    "mode": "also_applies",
                    "from_event_id": str(w.side_event.pk),
                },
            ),
            name="an ordinary message onto the sealed request",
        ),
        R(
            _json(
                "/records/sources/{loose_source}/assign",
                lambda w, p: {
                    "request_id": str(w.side_request.pk),
                    "mode": "also_applies",
                    "from_event_id": str(w.event.pk),
                },
            ),
            name="copying from a sealed event",
        ),
    ],
    ("POST", "/records/sources/{source_id}/reviewed"): [M(_path(SRC + "/reviewed"))],
    ("POST", "/records/sources/{source_id}/flag"): [
        # The message that is not yet reviewed: a reviewed one cannot be flagged by anyone.
        M(_json("/records/sources/{new_source}/flag", {"note": "census", "reason": "other"}))
    ],
    ("POST", "/records/sources/{source_id}/assignee"): [
        M(_json(SRC + "/assignee", {"user_id": None}))
    ],
    ("POST", "/records/sources/{source_id}/unreview"): [M(_path(SRC + "/unreview"))],
    ("POST", "/records/sources/{source_id}/resolve"): [
        M(_json(SRC + "/resolve", {"resolution": "other", "note": "census"}))
    ],
    ("GET", "/records/sources/{source_id}/notes"): [M(_path(SRC + "/notes"))],
    # P5. The candidates of a message the caller MAY see are filtered per person; that is pinned
    # in tests/inbound. Here: the candidates of a message they may not see are refused whole.
    ("GET", "/records/sources/{source_id}/candidates"): [M(_path(SRC + "/candidates"))],
    # P4c. The text of a message, read from its stored original.
    ("GET", "/records/sources/{source_id}/message"): [M(_path(SRC + "/message"))],
    ("POST", "/records/sources/{source_id}/notes"): [M(_json(SRC + "/notes", {"body": "census"}))],
    ("POST", "/records/source-notes/{note_id}/void"): [
        M(_json("/records/source-notes/{note}/void", {"reason": "census"}))
    ],
    ("POST", "/records/sources/{source_id}/records"): [
        M(_json(SRC + "/records", {"record_attachment_ids": []}))
    ],
    ("GET", "/records/attachments/{attachment_id}/download"): [
        M(_path("/records/attachments/{attachment}/download"))
    ],
    ("GET", "/records/sources/{source_id}/raw"): [M(_path(SRC + "/raw"))],
    # P12: production review, the Records screen, request links.
    ("GET", "/records/requests/{request_id}/reviews"): [R(_path(RQ + "/reviews"), insider=200)],
    ("POST", "/records/requests/{request_id}/reviews"): [
        # The world's event is a correspondence, not a production: the insider is refused by the
        # service (409), after the scope check, which is what the census is about.
        R(
            _json(
                RQ + "/reviews",
                lambda w, p: {"event_id": str(w.event.pk), "rows": [], "files": []},
            ),
            insider=409,
        )
    ],
    ("GET", "/records/events/{event_id}/review-sheet"): [
        R(_path("/records/events/{event}/review-sheet"), insider=200)
    ],
    ("GET", "/records/produced"): [L(_path("/records/produced"), tracking)],
    # P15c: search across everything, and the registry export, each scoped like the docket.
    ("GET", "/records/search"): [
        L(lambda w, p: Call(f"/records/search?q={w.mark}"), tracking, name="searched"),
        L(
            lambda w, p: Call(f"/records/search?q={w.request.tracking_number}"),
            tracking,
            name="by tracking number",
        ),
    ],
    ("GET", "/records/requests/export"): [
        L(_path("/records/requests/export"), tracking),
        L(lambda w, p: Call(f"/records/requests/export?q={w.mark}"), tracking, name="searched"),
        # P20b2: the selected rows, by id. An outsider naming the sealed id gets it left out.
        L(
            lambda w, p: Call(f"/records/requests/export?ids={w.request.pk},{w.side_request.pk}"),
            tracking,
            name="selected ids",
        ),
    ],
    ("GET", "/records/reports"): NO_REQUEST,
    ("GET", "/records/reports/export"): NO_REQUEST,
    # P16: closure days and merging are about the directory, not any request.
    ("GET", "/records/agencies/closures"): NO_REQUEST,
    ("POST", "/records/agencies/closures"): NO_REQUEST,
    ("POST", "/records/agencies/closures/{closure_id}/void"): NO_REQUEST,
    ("POST", "/records/agencies/merge"): NO_REQUEST,
    ("POST", "/records/agencies/{agency_id}/unmerge"): NO_REQUEST,
    # P19g: an agency's stored portal login; the directory, not any request.
    # P20e: importing a spreadsheet of agencies (preview and import) and editing a contact. They
    # name no request; the importer's refusal of a requester is in test_p20e_api.py.
    ("POST", "/records/agencies/import/preview"): NO_REQUEST,
    ("POST", "/records/agencies/import"): NO_REQUEST,
    ("PATCH", "/records/agencies/contacts/{contact_id}"): NO_REQUEST,
    ("GET", "/records/agencies/{agency_id}/login"): NO_REQUEST,
    ("PUT", "/records/agencies/{agency_id}/login"): NO_REQUEST,
    ("POST", "/records/agencies/{agency_id}/login/reveal"): NO_REQUEST,
    ("DELETE", "/records/agencies/{agency_id}/login"): NO_REQUEST,
    # P19d: the assistant. A draft's rail and doors are scoped like the draft; the rest names
    # no request.
    ("GET", "/core/ai/drafts/{draft_id}/rail"): [
        D(_path("/core/ai/drafts/{draft}/rail")),
        D(_path("/core/ai/drafts/{linked_draft}/rail"), name=LINKED),
    ],
    ("POST", "/core/ai/drafts/{draft_id}/suggest"): [
        D(_json("/core/ai/drafts/{draft}/suggest", {})),
        D(_json("/core/ai/drafts/{linked_draft}/suggest", {}), name=LINKED),
    ],
    ("POST", "/core/ai/drafts/{draft_id}/ask"): [
        D(_json("/core/ai/drafts/{draft}/ask", {"question": "census"})),
        D(_json("/core/ai/drafts/{linked_draft}/ask", {"question": "census"}), name=LINKED),
    ],
    ("POST", "/core/ai/drafts/{draft_id}/notes"): [
        D(_json("/core/ai/drafts/{draft}/notes", {"body": "census"})),
        D(_json("/core/ai/drafts/{linked_draft}/notes", {"body": "census"}), name=LINKED),
    ],
    ("POST", "/core/ai/notes/{note_id}/void"): NO_REQUEST,
    ("POST", "/core/ai/suggestions/{suggestion_id}/apply"): NO_REQUEST,
    ("POST", "/core/ai/suggestions/{suggestion_id}/dismiss"): NO_REQUEST,
    ("POST", "/core/ai/pick"): NO_REQUEST,
    ("GET", "/core/ai/runs"): NO_REQUEST,
    # P19a: a heartbeat and who's online.
    ("POST", "/core/presence"): NO_REQUEST,
    ("GET", "/core/presence"): NO_REQUEST,
    # P18b: rules and calendars are an administrator's tables; no request is named.
    ("POST", "/core/rules"): NO_REQUEST,
    ("PATCH", "/core/rules/{rule_id}"): NO_REQUEST,
    ("POST", "/core/rules/{rule_id}/retire"): NO_REQUEST,
    ("POST", "/core/calendars"): NO_REQUEST,
    ("PATCH", "/core/calendars/{key}"): NO_REQUEST,
    ("POST", "/core/calendars/{key}/holidays"): NO_REQUEST,
    ("DELETE", "/core/calendars/{key}/holidays/{on}"): NO_REQUEST,
    # P20b2: a holiday's name.
    ("PATCH", "/core/calendars/{key}/holidays/{on}"): NO_REQUEST,
    ("PUT", "/core/laws/{law}"): NO_REQUEST,
    ("DELETE", "/core/laws/{law}"): NO_REQUEST,
    ("GET", "/records/produced/set"): [M(_path("/records/produced/set?ids={attachment}"))],
    ("GET", "/records/requests/{request_id}/links"): [R(_path(RQ + "/links"), insider=200)],
    ("POST", "/records/requests/{request_id}/links"): [
        R(
            _json(
                RQ + "/links",
                # The world already holds a follow-on between these two; this is the other kind.
                lambda w, p: {
                    "to_request_id": str(w.side_request.pk),
                    "kind": "duplicate",
                    "reason": "census",
                },
            ),
            insider=201,
        ),
        # The sealed request named in the body of a call on a request everyone sees.
        R(
            _json(
                "/records/requests/{side_request}/links",
                lambda w, p: {
                    "to_request_id": str(w.request.pk),
                    "kind": "follow_on",
                    "reason": "census",
                },
            ),
            insider=201,
        ),
    ],
    # P11: a message is seen by whoever sees its request. `/mail/messages` is a list; the rest
    # resolve to the message's request, or, for reply-draft, to the message replied to.
    ("GET", "/mail/sending"): NO_REQUEST,
    ("GET", "/mail/counts"): NO_REQUEST,
    ("GET", "/mail/messages"): [L(_path("/mail/messages?bucket=drafts"), tracking)],
    ("POST", "/mail/messages"): [
        R(_json("/mail/messages", lambda w, p: {"request_id": str(w.request.pk)}), insider=201)
    ],
    # P20b2: the follow-up the Docket's dialog opens with; read only, as visible as the request.
    ("POST", "/mail/requests/{request_id}/followup-draft"): [
        R(_path("/mail/requests/{request}/followup-draft"))
    ],
    ("POST", "/mail/sources/{source_id}/reply-draft"): [
        M(_json("/mail/sources/{source}/reply-draft", {"forward": False}))
    ],
    ("GET", "/mail/messages/{message_id}"): [R(_path("/mail/messages/{message}"), insider=200)],
    ("PATCH", "/mail/messages/{message_id}"): [
        R(_json("/mail/messages/{message}", {"subject": "census"}), insider=200)
    ],
    ("DELETE", "/mail/messages/{message_id}"): [R(_path("/mail/messages/{message}"), insider=204)],
    ("POST", "/mail/messages/{message_id}/send"): [
        R(_json("/mail/messages/{message}/send", {}), insider=200)
    ],
    ("POST", "/mail/messages/{message_id}/cancel"): [
        # The world's message is a draft, not queued: the insider is refused by the service.
        R(_json("/mail/messages/{message}/cancel", {}), insider=409)
    ],
    ("POST", "/mail/recipients/{recipient_id}/send-and-file"): [
        D(
            _json(
                "/mail/recipients/{recipient}/send-and-file",
                {"subject": "s", "body_text": "b", "to": [{"address": "a@agency.example.org"}]},
            )
        ),
        D(
            _json(
                "/mail/recipients/{linked_recipient}/send-and-file",
                {"subject": "s", "body_text": "b", "to": [{"address": "a@agency.example.org"}]},
            ),
            name=LINKED,
        ),
    ],
    # P20h: an intern's "Submit for approval" at stage 4 (232), scoped like send-and-file. Only the
    # ordinary draft that names the sealed request: the privileged draft is its creator's alone,
    # and its creator is not an intern.
    ("POST", "/mail/recipients/{recipient_id}/submit"): [
        D(
            _json(
                "/mail/recipients/{linked_recipient}/submit",
                {"subject": "s", "body_text": "b", "to": [{"address": "a@agency.example.org"}]},
            ),
            name=LINKED,
            insider=201,
            by_intern=True,
        )
    ],
    ("GET", "/mail/saved-replies"): NO_REQUEST,
    ("POST", "/mail/saved-replies"): NO_REQUEST,
    ("PATCH", "/mail/saved-replies/{reply_id}"): NO_REQUEST,
    ("DELETE", "/mail/saved-replies/{reply_id}"): NO_REQUEST,
    ("POST", "/mail/contacts/{contact_id}/bad-address"): NO_REQUEST,
    # P13: asking for access is the one door an outsider reaches on a sealed request; deciding
    # and listing asks are for those who see it. Reviews and suggestions live under /core and
    # check their subject's own scope.
    ("POST", "/records/requests/{request_id}/access-requests"): [
        R(
            _json(RQ + "/access-requests", {"reason": "census"}),
            # staff_out already asked in the world (409); admin_out has not (201).
            outsider=(201, 409),
            insider=409,
            name="insider-already-sees",
        )
    ],
    ("GET", "/records/requests/{request_id}/access-requests"): [
        R(_path(RQ + "/access-requests"), insider=200, by_admin=True)
    ],
    ("GET", "/records/access-requests/pending"): [
        L(_path("/records/access-requests/pending"), lambda w: f"{w.mark}-ASK", by_admin=True)
    ],
    ("GET", "/records/access-requests/mine"): NO_REQUEST,
    ("POST", "/records/access-requests/{ask_id}/decide"): [
        R(
            _json("/records/access-requests/{ask}/decide", {"allow": False, "note": "census"}),
            insider=200,
            by_admin=True,
        )
    ],
    ("GET", "/core/reviews/pending"): NO_REQUEST,
    ("GET", "/core/reviews/mine"): NO_REQUEST,
    ("GET", "/core/reviews/clearance"): NO_REQUEST,
    ("GET", "/core/reviews"): NO_REQUEST,
    # P27d: a question can be about a request (069 "Ask a teammate"), so the route names one; put
    # to the request's owner, who is on its list (asking is not sharing: someone off the list is
    # refused, test_p27d_ask.py).
    ("POST", "/core/reviews"): [
        R(
            _json(
                "/core/reviews",
                lambda w, p: {
                    "subject_type": "request",
                    "subject_id": str(w.request.pk),
                    "kind": "question",
                    "note": "census",
                    "assigned_to_user_id": str(p.owner.pk),
                },
            ),
        )
    ],
    ("GET", "/core/reviews/{review_id}"): NO_REQUEST,
    ("POST", "/core/reviews/{review_id}/decide"): NO_REQUEST,
    # P20h: the intern takes back what they submitted; scoped by its subject, like decide.
    ("POST", "/core/reviews/{review_id}/withdraw"): NO_REQUEST,
    ("GET", "/core/suggestions"): NO_REQUEST,
    ("POST", "/core/suggestions"): NO_REQUEST,
    ("POST", "/core/suggestions/{suggestion_id}/decline"): NO_REQUEST,
    ("POST", "/core/suggestions/{suggestion_id}/start"): NO_REQUEST,
    # P14: a challenge is seen and acted on by whoever sees its request.
    ("GET", "/records/requests/{request_id}/challenges"): [
        R(_path(RQ + "/challenges"), insider=200)
    ],
    ("POST", "/records/requests/{request_id}/challenges"): [
        R(
            _json(
                RQ + "/challenges",
                {"kind": "search_challenge", "basis": "census", "with_letter": False},
            ),
            insider=201,
        )
    ],
    ("POST", "/records/challenges/{challenge_id}/sent"): [
        R(_json("/records/challenges/{challenge}/sent", {"channel": "mail"}), insider=200)
    ],
    ("POST", "/records/challenges/{challenge_id}/follow-up"): [
        R(
            _json("/records/challenges/{challenge}/follow-up", {"follow_up_by": "2026-12-01"}),
            insider=200,
        )
    ],
    ("POST", "/records/challenges/{challenge_id}/outcome"): [
        R(
            _json("/records/challenges/{challenge}/outcome", {"outcome": "withdrawn"}),
            insider=200,
        )
    ],
    # P15a: invites name no request. P25a: sign-in is Keycloak's; the portal's side of it (to
    # Keycloak and back, its back-channel logout, what a person's credentials are) and the
    # administrator's sign-in doors on Users (a password reset email, signing a person out
    # everywhere, resetting their second step, trying a failed Keycloak change again) name none.
    ("POST", "/core/users/invite"): NO_REQUEST,
    ("GET", "/core/auth/security"): NO_REQUEST,
    ("GET", "/core/auth/oidc/login"): NO_REQUEST,
    # P25c: which second steps a person may set up (Settings' switch), for the sign-in pages.
    ("GET", "/core/auth/second-steps"): NO_REQUEST,
    ("GET", "/core/auth/oidc/callback"): NO_REQUEST,
    ("POST", "/core/auth/oidc/backchannel-logout"): NO_REQUEST,
    ("POST", "/core/users/{user_id}/password-reset"): NO_REQUEST,
    ("POST", "/core/users/{user_id}/sign-out-everywhere"): NO_REQUEST,
    ("POST", "/core/users/{user_id}/reset-second-step"): NO_REQUEST,
    ("POST", "/core/users/{user_id}/sign-in/retry"): NO_REQUEST,
    # P15b: a person's own bell and preferences; the activity log is an administrator's read.
    ("GET", "/core/notifications"): NO_REQUEST,
    ("POST", "/core/notifications/read"): NO_REQUEST,
    ("GET", "/core/notifications/preferences"): NO_REQUEST,
    ("PUT", "/core/notifications/preferences"): NO_REQUEST,
    ("GET", "/core/activity"): NO_REQUEST,
    ("GET", "/core/activity/export"): NO_REQUEST,
    ("POST", "/core/users/{user_id}/resend-invite"): NO_REQUEST,
    # P20f: a person's own signature, read and changed by them or an administrator.
    ("GET", "/core/users/{user_id}/signature"): NO_REQUEST,
    ("GET", "/core/users/{user_id}/signature/image"): NO_REQUEST,
    ("POST", "/core/users/{user_id}/signature/upload"): NO_REQUEST,
    ("POST", "/core/users/{user_id}/signature/draw"): NO_REQUEST,
    # P20i: the caller's own new-user setup, and the agreements it asks for (a person's own
    # acceptances and signed copies; an administrator's versions and anyone's copy).
    ("GET", "/core/setup"): NO_REQUEST,
    ("POST", "/core/setup/finish"): NO_REQUEST,
    ("GET", "/core/agreements"): NO_REQUEST,
    ("POST", "/core/agreements"): NO_REQUEST,
    ("POST", "/core/agreements/{agreement_id}/accept"): NO_REQUEST,
    ("GET", "/core/agreements/acceptances"): NO_REQUEST,
    ("GET", "/core/agreements/acceptances/{acceptance_id}/pdf"): NO_REQUEST,
    # P22: team chat names no request. A request is only ever named inside a message's text, and
    # each reader gets it resolved by the visibility rule (`core/chat/render.py`, tested in
    # tests/core/test_p22_chat.py); conversations have their own rule (`core/chat/access.py`).
    ("GET", "/core/chat"): NO_REQUEST,
    ("GET", "/core/chat/browse"): NO_REQUEST,
    ("POST", "/core/chat/channels"): NO_REQUEST,
    ("GET", "/core/chat/channels/{channel_id}"): NO_REQUEST,
    ("POST", "/core/chat/channels/{channel_id}/join"): NO_REQUEST,
    ("POST", "/core/chat/channels/{channel_id}/leave"): NO_REQUEST,
    ("POST", "/core/chat/channels/{channel_id}/members"): NO_REQUEST,
    ("POST", "/core/chat/channels/{channel_id}/archive"): NO_REQUEST,
    ("POST", "/core/chat/channels/{channel_id}/read"): NO_REQUEST,
    ("GET", "/core/chat/channels/{channel_id}/messages"): NO_REQUEST,
    ("POST", "/core/chat/channels/{channel_id}/messages"): NO_REQUEST,
    ("POST", "/core/chat/channels/{channel_id}/attachments"): NO_REQUEST,
    ("PATCH", "/core/chat/messages/{message_id}"): NO_REQUEST,
    ("DELETE", "/core/chat/messages/{message_id}"): NO_REQUEST,
    ("GET", "/core/chat/attachments/{attachment_id}"): NO_REQUEST,
    ("POST", "/core/chat/direct"): NO_REQUEST,
    ("GET", "/core/chat/people"): NO_REQUEST,
    ("GET", "/core/chat/online"): NO_REQUEST,
    ("GET", "/core/chat/updates"): NO_REQUEST,
    ("GET", "/core/chat/discovery"): NO_REQUEST,
    ("GET", "/core/chat/discovery/{channel_id}"): NO_REQUEST,
    ("GET", "/core/chat/discovery/attachments/{attachment_id}"): NO_REQUEST,
    # Voiding a link resolves to the request the link is FROM: the sealed one, here.
    ("POST", "/records/links/{link_id}/void"): [
        R(_json("/records/links/{link}/void", {"reason": "census"}), insider=200)
    ],
    # ---- the screens that are lists of everything ------------------------------------------------
    # P23b: one message as an Inbox row, read by its id (a link past the loaded page, 140; the
    # full-page message, 107). A message borrows its visibility from where it landed.
    ("GET", "/records/inbox/{source_id}"): [
        M(_path("/records/inbox/{source}")),
        M(_path("/records/inbox/{new_source}"), name="a new message"),
    ],
    ("GET", "/records/inbox"): [
        L(_path("/records/inbox?bucket=all"), lambda w: str(w.source.pk), name="all"),
        L(_path("/records/inbox?bucket=flagged"), lambda w: f"{w.mark}-FLAGNOTE", name="flagged"),
        L(_path("/records/inbox?bucket=new"), lambda w: f"{w.mark}-NEW-BODY", name="new"),
        L(
            _path("/records/inbox?bucket=all&request_id={request}"),
            lambda w: str(w.source.pk),
            name="filtered to the request",
        ),
        L(
            lambda w, p: Call(f"/records/inbox?bucket=all&q={w.request.tracking_number}"),
            lambda w: str(w.source.pk),
            name="searched by tracking number",
        ),
    ],
    ("GET", "/records/home"): [L(_path("/records/home"), tracking)],
    ("GET", "/records/calendar"): [
        L(_path("/records/calendar?start=2019-12-01&end=2020-03-01"), tracking)
    ],
    ("GET", "/records/rules"): [L(_path("/records/rules"), lambda w: f"{w.mark}-RULETASK")],
    # ---- drafting: the library is shared and names no request; a draft has its own rule ---------
    ("GET", "/drafting/shells"): NO_REQUEST,
    ("POST", "/drafting/shells"): NO_REQUEST,
    ("PATCH", "/drafting/shells/{shell_id}"): NO_REQUEST,
    ("DELETE", "/drafting/shells/{shell_id}"): NO_REQUEST,
    ("GET", "/drafting/blocks"): NO_REQUEST,
    ("POST", "/drafting/blocks"): NO_REQUEST,
    ("PATCH", "/drafting/blocks/{block_id}"): NO_REQUEST,
    ("DELETE", "/drafting/blocks/{block_id}"): NO_REQUEST,
    # P19h: a block's own footnotes.
    ("POST", "/drafting/blocks/{block_id}/citations"): NO_REQUEST,
    ("DELETE", "/drafting/block-citations/{citation_id}"): NO_REQUEST,
    ("GET", "/drafting/templates"): NO_REQUEST,
    ("POST", "/drafting/templates"): NO_REQUEST,
    ("GET", "/drafting/templates/{template_id}"): NO_REQUEST,
    ("PATCH", "/drafting/templates/{template_id}"): NO_REQUEST,
    ("DELETE", "/drafting/templates/{template_id}"): NO_REQUEST,
    ("POST", "/drafting/templates/{template_id}/archive"): NO_REQUEST,
    ("PUT", "/drafting/templates/{template_id}/blocks"): NO_REQUEST,
    # P19b: a template as the editor sees it; the letterhead's seal.
    ("GET", "/drafting/templates/{template_id}/letter"): NO_REQUEST,
    ("PUT", "/drafting/templates/{template_id}/letter"): NO_REQUEST,
    ("GET", "/drafting/templates/{template_id}/html"): NO_REQUEST,
    ("GET", "/drafting/templates/{template_id}/pdf"): NO_REQUEST,
    ("POST", "/drafting/shells/{shell_id}/seal"): NO_REQUEST,
    ("GET", "/drafting/shells/{shell_id}/seal"): NO_REQUEST,
    ("POST", "/drafting/templates/{template_id}/items"): NO_REQUEST,
    ("PATCH", "/drafting/template-items/{item_id}"): NO_REQUEST,
    ("DELETE", "/drafting/template-items/{item_id}"): NO_REQUEST,
    ("POST", "/drafting/templates/{template_id}/citations"): NO_REQUEST,
    ("DELETE", "/drafting/template-citations/{citation_id}"): NO_REQUEST,
    ("GET", "/drafting/templates/{template_id}/revisions"): NO_REQUEST,
    # P10. Saving a template freezes the template; it names no request and no draft.
    ("POST", "/drafting/templates/{template_id}/save"): NO_REQUEST,
    ("GET", "/drafting/drafts"): [L(_path("/drafting/drafts"), lambda w: f"{w.mark}-DRAFT")],
    ("POST", "/drafting/drafts"): [
        R(
            _json("/drafting/drafts", lambda w, p: {"from_request_id": str(w.request.pk)}),
            name="started from the sealed request",
        )
    ],
    # Each draft route twice: against the privileged draft, and against an ordinary draft that
    # names the sealed request (`LINKED`), which is the request's text under another name.
    ("GET", "/drafting/drafts/{draft_id}"): [
        D(_path("/drafting/drafts/{draft}")),
        D(_path("/drafting/drafts/{linked_draft}"), name=LINKED),
    ],
    ("PATCH", "/drafting/drafts/{draft_id}"): [
        D(_json("/drafting/drafts/{draft}", {"subject": "census"})),
        D(_json("/drafting/drafts/{linked_draft}", {"subject": "census"}), name=LINKED),
    ],
    ("DELETE", "/drafting/drafts/{draft_id}"): [
        D(_path("/drafting/drafts/{draft}")),
        D(_path("/drafting/drafts/{linked_draft}"), name=LINKED),
    ],
    ("POST", "/drafting/drafts/{draft_id}/items"): [
        D(_json("/drafting/drafts/{draft}/items", {"item_number": "9", "body": "census"})),
        D(
            _json("/drafting/drafts/{linked_draft}/items", {"item_number": "9", "body": "census"}),
            name=LINKED,
        ),
    ],
    ("PATCH", "/drafting/draft-items/{item_id}"): [
        D(_json("/drafting/draft-items/{draft_item}", {"body": "census"})),
        D(_json("/drafting/draft-items/{linked_item}", {"body": "census"}), name=LINKED),
    ],
    ("DELETE", "/drafting/draft-items/{item_id}"): [
        D(_path("/drafting/draft-items/{draft_item}")),
        D(_path("/drafting/draft-items/{linked_item}"), name=LINKED),
    ],
    ("POST", "/drafting/drafts/{draft_id}/citations"): [
        D(_json("/drafting/drafts/{draft}/citations", {"marker": "9", "citation_text": "census"}))
    ],
    ("DELETE", "/drafting/draft-citations/{citation_id}"): [
        D(_path("/drafting/draft-citations/{draft_citation}"))
    ],
    ("POST", "/drafting/drafts/{draft_id}/recipients"): [
        D(
            _json(
                "/drafting/drafts/{draft}/recipients",
                lambda w, p: {"agency_id": str(w.other_agency.pk), "channel": "email"},
            )
        )
    ],
    # P20c: the draft a reserved request was made from ("Review & file", 076). The sealed request
    # was not made from a draft, so the insider gets the same 404 on it and on its twin.
    ("GET", "/drafting/requests/{request_id}/draft"): [
        R(_path("/drafting/requests/{request}/draft"))
    ],
    ("PATCH", "/drafting/recipients/{recipient_id}"): [
        D(_json("/drafting/recipients/{recipient}", {"placeholder_overrides": {"X": "census"}})),
        D(
            _json("/drafting/recipients/{linked_recipient}", {"placeholder_overrides": {"X": "c"}}),
            name=LINKED,
        ),
    ],
    ("DELETE", "/drafting/recipients/{recipient_id}"): [
        D(_path("/drafting/recipients/{recipient}")),
        D(_path("/drafting/recipients/{linked_recipient}"), name=LINKED),
    ],
    ("GET", "/drafting/recipients/{recipient_id}/preview"): [
        D(_path("/drafting/recipients/{recipient}/preview")),
        D(_path("/drafting/recipients/{linked_recipient}/preview"), name=LINKED),
    ],
    ("POST", "/drafting/drafts/{draft_id}/file"): [
        D(_json("/drafting/drafts/{draft}/file", {"filed_at": WHEN})),
        D(_json("/drafting/drafts/{linked_draft}/file", {"filed_at": WHEN}), name=LINKED),
    ],
    # ---- P10: reserve, file and skip one letter; "Preview as" ---------------------------------
    # P19b: the letter as the editor sees it, and the page.
    ("GET", "/drafting/drafts/{draft_id}/letter"): [
        D(_path("/drafting/drafts/{draft}/letter")),
        D(_path("/drafting/drafts/{linked_draft}/letter"), name=LINKED),
    ],
    ("PUT", "/drafting/drafts/{draft_id}/letter"): [
        D(_json("/drafting/drafts/{draft}/letter", {})),
        D(_json("/drafting/drafts/{linked_draft}/letter", {}), name=LINKED),
    ],
    ("GET", "/drafting/drafts/{draft_id}/html"): [
        D(_path("/drafting/drafts/{draft}/html?agency_id={agency}")),
        D(_path("/drafting/drafts/{linked_draft}/html?agency_id={agency}"), name=LINKED),
    ],
    ("GET", "/drafting/drafts/{draft_id}/pdf"): [
        D(_path("/drafting/drafts/{draft}/pdf?agency_id={agency}")),
        D(_path("/drafting/drafts/{linked_draft}/pdf?agency_id={agency}"), name=LINKED),
    ],
    ("GET", "/drafting/drafts/{draft_id}/preview"): [
        D(_path("/drafting/drafts/{draft}/preview?agency_id={agency}")),
        D(_path("/drafting/drafts/{linked_draft}/preview?agency_id={agency}"), name=LINKED),
    ],
    ("POST", "/drafting/recipients/{recipient_id}/reserve"): [
        D(_json("/drafting/recipients/{recipient}/reserve", {})),
        D(_json("/drafting/recipients/{linked_recipient}/reserve", {}), name=LINKED),
    ],
    ("POST", "/drafting/recipients/{recipient_id}/file"): [
        D(_json("/drafting/recipients/{recipient}/file", {"filed_at": WHEN})),
        D(_json("/drafting/recipients/{linked_recipient}/file", {"filed_at": WHEN}), name=LINKED),
    ],
    ("POST", "/drafting/recipients/{recipient_id}/skip"): [
        D(_json("/drafting/recipients/{recipient}/skip", {"note": "census"})),
        D(_json("/drafting/recipients/{linked_recipient}/skip", {"note": "census"}), name=LINKED),
    ],
    # ---- P21c: the drafting tool ------------------------------------------------------------
    # Whether it is in use and where its pages are; the fork's webhooks (its signature is the
    # whole of its authentication, no session); the Templates frame's mirror of a template.
    # None names a request, a message or a draft.
    ("GET", "/drafting/fork"): NO_REQUEST,
    ("POST", "/drafting/fork/webhook"): NO_REQUEST,
    ("POST", "/drafting/fork/templates"): NO_REQUEST,
    # A draft's letters in the drafting tool: made again, one agency's merge values, a file. The
    # world's drafts are the portal's own (the tool is off in tests), so the insider gets the same
    # 409 on both worlds after the same scope check.
    ("POST", "/drafting/drafts/{draft_id}/fork/merge"): [
        D(_path("/drafting/drafts/{draft}/fork/merge")),
        D(_path("/drafting/drafts/{linked_draft}/fork/merge"), name=LINKED),
    ],
    ("GET", "/drafting/drafts/{draft_id}/fork/values"): [
        D(_path("/drafting/drafts/{draft}/fork/values?agency_id={agency}")),
        D(_path("/drafting/drafts/{linked_draft}/fork/values?agency_id={agency}"), name=LINKED),
    ],
    ("GET", "/drafting/drafts/{draft_id}/fork/file"): [
        D(_path("/drafting/drafts/{draft}/fork/file?recipient_id={recipient}")),
        D(_path("/drafting/drafts/{linked_draft}/fork/file"), name=LINKED),
    ],
    # The request's letter in the drafting tool for its Files tab (an empty list on the world's).
    ("GET", "/drafting/requests/{request_id}/fork/letter"): [
        R(_path("/drafting/requests/{request}/fork/letter"))
    ],
}


# ---- the registry -----------------------------------------------------------------------------


def registered_routes() -> set[tuple[str, str]]:
    routes = set()
    for prefix, router in api._routers:
        for path, view in router.path_operations.items():
            for operation in view.operations:
                for method in operation.methods:
                    routes.add((method, f"{prefix}{path}"))
    return routes


def test_every_route_in_the_registry_is_in_the_census() -> None:
    """Both directions. A route missing from the map is a route nobody has decided about; an
    entry for a route that no longer exists is a claim about nothing."""
    registered = registered_routes()
    assert len(registered) > 100, "the registry walk found too little to be the whole API"
    missing = sorted(registered - set(CENSUS))
    stale = sorted(set(CENSUS) - registered)
    assert not missing, (
        "routes with no census entry. Decide whether each takes or resolves to a request, a "
        f"message or a draft, pass it through records/api/permissions.py, and add it: {missing}"
    )
    assert not stale, f"census entries for routes that do not exist: {stale}"


def test_the_census_is_not_mostly_exemptions() -> None:
    """NO_REQUEST is a claim that has to be made route by route, not a way out. The count is pinned
    so that adding an exemption is a visible edit to this number as well as to the map."""
    exempt = [route for route, cases in CENSUS.items() if cases == NO_REQUEST]
    # 40 since P10 added Save on a template; 41 with the administrators' index of privileged
    # requests (see its entry).
    # 42 with the System screen's health read (P6); 49 with P11's seven mail routes that name
    # no request (sending, counts, saved replies, a contact's bad-address mark).
    # 61 with P13: seven review routes and four suggestion routes under /core, and a person's
    # own access asks.
    # 73 with P15a: two-step, invites and resets (twelve routes under /core).
    # 79 with P15b: the bell (four routes) and the activity log (two).
    # 81 with P15c: the reports, which are counts over what the caller may see (two routes).
    # 86 with P16: closure days (three routes) and merging (two), all under /records/agencies/.
    # 95 with P18b: the rules and calendars an administrator writes (nine routes under /core).
    # 97 with P19a: the presence heartbeat and list.
    # 103 with P19b: a template's letter, page and PDF, and a letterhead's seal (six routes).
    # 107 with P19g: an agency's stored portal login (four routes).
    # 112 with P19d: a note, a suggestion's two decisions, the picker's question, the run log.
    # 114 with P19h: a shared block's footnotes (two routes).
    # 118 with P20b2: the Docket's summary, history and per-person counts (counts over what the
    # caller may see, tested in test_p20b2_api.py) and a holiday's name.
    # 121 with P20e: the agency importer's preview and import, and editing a contact, all under
    # /records/agencies/.
    # 125 with P20f: a person's own signature (read, its image, upload, draw), under /core/users/.
    # 126 with P20h: withdrawing a submission (/core/reviews/{id}/withdraw).
    # 133 with P20i: the new-user setup (read, finish) and the agreements (versions, publish,
    # accept, acceptances, a signed copy), under /core/setup and /core/agreements.
    # 155 with P22: team chat's twenty-two routes under /core/chat (conversations, messages,
    # files, people, who's online, the polling fallback and the legal director's discovery).
    # 158 with P21c: whether the drafting tool is in use, its webhooks, its template mirror.
    # 153 with P25a: thirteen of the portal's own sign-in routes retired (password sign-in and its
    # two-step, the two-step set-up, backup codes and turning it off, the invite link, resets,
    # changing a password, adding a person with a password), eight added (to Keycloak and back,
    # its back-channel logout, a person's credentials, and Users' four sign-in doors).
    # 154 with P25c: which second steps a person may set up, for the sign-in pages.
    # 153 with P27d: POST /core/reviews names a request when a question is about one.
    assert len(exempt) == 153, len(exempt)
    held = [route for route, cases in CENSUS.items() if cases != NO_REQUEST]
    assert len(held) == len(CENSUS) - 153 and all(CENSUS[route] for route in held)
    # Every records route with a path parameter that is not about the directory is held.
    for method, path in exempt:
        if path.startswith("/records/") and "{" in path:
            assert path.startswith("/records/agencies/"), (method, path)


# ---- running a case ---------------------------------------------------------------------------

CASES = [
    pytest.param(
        route, case, id=f"{route[0]} {route[1]}" + (f" [{case.name}]" if case.name else "")
    )
    for route, cases in CENSUS.items()
    if cases != NO_REQUEST
    for case in cases
]


def _client(user=None) -> Client:
    client = Client()
    if user is not None:
        client.force_login(user)
    return client


def _send(client: Client, method: str, call: Call):
    path = f"/api{call.path}"
    if call.form is not None:
        # A file object is read once; give each caller its own copy.
        form = {
            key: SimpleUploadedFile(value.name, b"census", value.content_type)
            if isinstance(value, SimpleUploadedFile)
            else value
            for key, value in call.form.items()
        }
        return client.post(path, data=form)
    if method == "GET":
        return client.get(path)
    send = getattr(client, method.lower())
    if call.json is None:
        return send(path)
    import json

    return send(path, data=json.dumps(call.json), content_type="application/json")


def _content(response) -> str:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content).decode("utf-8", "replace")
    return response.content.decode("utf-8", "replace")


def _fingerprint() -> dict[str, list]:
    """Everything a refused call could have changed, row by row."""

    def rows(model, *fields):
        return list(model.objects.order_by("pk").values_list("pk", *fields))

    return {
        "requests": rows(Request, "sensitivity", "subject", "assigned_to_id", "current_status_id"),
        "items": rows(RequestItem, "description"),
        "events": rows(Event, "voided_at", "display_body", "event_type_id", "source_id"),
        "deadlines": rows(Deadline, "status", "due_at", "legal_basis"),
        "status_changes": rows(StatusChange, "voided_at"),
        "tasks": rows(Task, "completed_at", "assigned_to_id", "description"),
        "fees": rows(Fee, "status"),
        "audit": rows(AuditLog),
        "access": rows(RequestAccess, "revoked_at"),
        "sources": rows(
            Source, "flagged_for_review", "reviewed_at", "assigned_to_id", "resolution"
        ),
        "attachments": rows(Attachment, "role"),
        "notes": rows(SourceNote, "voided_at"),
        "task_notes": rows(TaskNote, "voided_at"),
        "drafts": rows(LetterDraft, "subject", "sensitivity"),
        "draft_items": rows(LetterDraftItem, "body"),
        "draft_citations": rows(LetterDraftCitation),
        "recipients": rows(LetterRecipient, "status", "request_id", "placeholder_overrides"),
    }


def _assert_plain_refusal(
    who, case: Case, response, sealed_secrets: list[str], call: Call | None = None
) -> None:
    """A limited-scope attorney or an intern reaching what they may not see: a list leaves it out
    (or the door is not theirs at all); anything else is a 403 or a 404 that names nothing it was
    not given (an id in the address may be echoed back; never the privileged shape, which would
    tell them a privileged request exists)."""
    text = _content(response)
    asked = call.path if call is not None else ""
    leaked = [secret for secret in sealed_secrets if secret in text and secret not in asked]
    assert not leaked, (who.email, leaked)
    if case.kind == "list":
        assert response.status_code in (200, 403), (who.email, response.status_code, text[:300])
    else:
        assert response.status_code in (403, 404), (who.email, response.status_code, text[:300])
        assert '"privileged"' not in text, (who.email, text[:300])


@pytest.mark.parametrize(("route", "case"), CASES)
def test_census(route, case: Case, people: People, media) -> None:
    method, _ = route
    sealed = build_world(people, "SEALED9X", "privileged")
    call = case.build(sealed, people)
    before = _fingerprint()

    assert _send(_client(), method, call).status_code == 401, "anonymous"
    as_requester = _send(_client(people.requester), method, call).status_code

    for who in (people.staff_out, people.attorney_out):
        response = _send(_client(who), method, call)
        text = _content(response)
        if case.by_admin and '"privileged"' not in text:
            # Refused for their role before the request is looked at: a plain 403 that names
            # nothing, or a list that leaves the sealed rows out. (A door that looks the request
            # up first refuses with the privileged shape instead, checked below.)
            _assert_plain_refusal(who, case, response, sealed.secrets, call)
        elif case.kind == "list":
            assert response.status_code == 200, (who.email, response.status_code, text[:300])
            leaked = [secret for secret in sealed.secrets if secret in text]
            assert not leaked, (who.email, leaked)
        elif case.outsider is not None:
            assert response.status_code in case.outsider, (who.email, response.status_code, text)
            # The tracking number and the request's id are what the refusing page and its address
            # already gave the asker; nothing else may show.
            known = {sealed.request.tracking_number, str(sealed.request.pk)}
            leaked = [secret for secret in sealed.secrets if secret in text and secret not in known]
            assert not leaked, (who.email, leaked)
        else:
            assert response.status_code == 403, (who.email, response.status_code, text[:300])
            assert response.json() == {
                "detail": PRIVILEGED_DETAIL,
                "code": "privileged",
                "tracking_number": (
                    sealed.request.tracking_number if case.kind == "request" else None
                ),
            }, who.email
    for who in (people.limited_out, people.intern_out):
        _assert_plain_refusal(who, case, _send(_client(who), method, call), sealed.secrets, call)
    assert _fingerprint() == before, "a refused caller changed something"

    insider = people.admin if case.by_admin else people.insider
    if case.by_intern:
        insider = people.intern_in
    response = _send(_client(insider), method, call)
    as_insider = response.status_code
    text = _content(response)
    assert as_insider not in (401, 403) and as_insider < 500, (as_insider, text[:300])
    if case.kind == "list":
        assert as_insider == 200
        assert case.shows is not None and case.shows(sealed) in text, (
            "the insider saw no sealed row"
        )
    if not (case.by_admin or case.by_intern):
        # P20h: an administrator sees every request, privileged included, with no grant.
        as_admin = _send(_client(people.admin), method, call)
        assert as_admin.status_code not in (401, 403) and as_admin.status_code < 500, (
            as_admin.status_code,
            _content(as_admin)[:300],
        )
    if case.insider is not None:
        assert as_insider == case.insider, (as_insider, text[:300])
        return

    # The same call where nothing is privileged: the insider and the requester of record must be
    # treated exactly as they were on the sealed request. A limited-scope attorney and an intern
    # who were not given it are still outsiders: it is an ordinary request nobody gave them and
    # no intern made.
    twin = build_world(people, "TWIN9X", "internal")
    twin_call = case.build(twin, people)
    twin_before = _fingerprint()
    for who in (people.limited_out, people.intern_out):
        response = _send(_client(who), method, twin_call)
        _assert_plain_refusal(who, case, response, twin.secrets, twin_call)
    assert _fingerprint() == twin_before, "a refused caller changed something on the twin"
    assert _send(_client(people.requester), method, twin_call).status_code == as_requester
    twin_response = _send(_client(people.insider), method, twin_call)
    assert twin_response.status_code == as_insider, (
        as_insider,
        twin_response.status_code,
        _content(twin_response)[:300],
    )


# ---- P20h: the two roles that see a request only when it is given to them ---------------------

# The doors a limited-scope attorney or an intern is refused by their ROLE, whatever they were
# given: an administrator's (access, the asks), staff's (asking for access is for staff, who
# alone learn a privileged request exists), and, for an intern only, filing and sending by hand
# (theirs go through approval, `mail/services/approval.py`).
SCOPED_REFUSED: dict[str, set[tuple[str, str]]] = {
    "limited_attorney": {
        ("POST", "/records/requests/{request_id}/access-requests"),
    },
    "intern": {
        ("POST", "/records/requests/{request_id}/access-requests"),
        ("POST", "/records/requests/{request_id}/file"),
        ("POST", "/records/requests/{request_id}/status"),  # not filed yet: through approval
        ("POST", "/mail/messages/{message_id}/send"),
        ("POST", "/records/challenges/{challenge_id}/sent"),
    },
}
# What the insider gets too, whoever asks: the world's request was not made from a draft.
GIVEN_NOT_FOUND = {("GET", "/drafting/requests/{request_id}/draft")}
# Cases that move a message placed on nothing (the world's `loose_source`): unplaced mail is staff's
# (37a), so a limited-scope attorney or an intern is refused it even on a request they were given.
GIVEN_UNPLACED = {"an ordinary message onto the sealed request", "copying from a sealed event"}

SCOPED_CASES = [
    pytest.param(
        route,
        case,
        role,
        id=f"{role} {route[0]} {route[1]}" + (f" [{case.name}]" if case.name else ""),
    )
    for route, cases in CENSUS.items()
    if cases != NO_REQUEST
    for case in cases
    if case.kind in ("request", "message", "list") and not case.by_admin
    for role in ("limited_attorney", "intern")
]


@pytest.mark.parametrize(("route", "case", "role"), SCOPED_CASES)
def test_census_given(route, case: Case, role: str, people: People, media) -> None:
    """Every route, against an ordinary request a limited-scope attorney or an intern WAS given
    (a grant from an administrator): they reach it as the team does, refused only where their
    role is the reason. A fresh world each time, so nothing another caller did is in the way."""
    method, _ = route
    world = build_world(people, "GIVEN9X", "internal")
    who = people.limited_in if role == "limited_attorney" else people.intern_in
    # The ordinary request some cases name beside the world's is given to them too.
    give_side = Actor.human(people.admin)
    access_service.grant_access(give_side, world.side_request.pk, who.pk, "Given too.")
    response = _send(_client(who), method, case.build(world, people))
    text = _content(response)
    if route in SCOPED_REFUSED[role] or case.name in GIVEN_UNPLACED:
        assert response.status_code == 403, (response.status_code, text[:300])
        return
    if route in GIVEN_NOT_FOUND:
        assert response.status_code == 404, (response.status_code, text[:300])
        return
    assert response.status_code not in (401, 403, 404) and response.status_code < 500, (
        response.status_code,
        text[:300],
    )
