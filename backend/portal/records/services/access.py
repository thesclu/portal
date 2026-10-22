"""Who may see a request, the access list that widens it, and the one predicate that reads both.

THE RULE (P20h; DECISIONS 37a, 43; ARCHITECTURE §6), by role (`portal/records/roles.py`):
  admin             every request, privileged included ("grant-only except for admins").
  staff,
  staff attorney    every request that is not privileged; a privileged one when they hold an
                    active grant or are its requester of record.
  limited attorney  only the requests they hold an active grant on, or are requester of record of.
  intern            the requests an intern made (whose requester of record is an intern), plus
                    those they hold a grant on or are requester of record of; a privileged one only
                    by grant or as its requester of record.
  requester         their own requests.
  anything else     nothing.
A grant is an active row in `records.request_access`. Before P20h only a privileged request had a
list; now a limited-scope attorney or an intern is given an ordinary request the same way, and
only an administrator gives or takes a grant (`grant_access`, `revoke_access`). The same rule is
written in Postgres (records_db.0021, `request_visible_cols`) and the RLS tests hold the two to it.

The rule is written once, in `_visible`, and has two forms that must always agree: `can_view` and
`scope` for rows already in hand, and `visible_q` for a queryset. `records/api/permissions.py` is
the only place that decides what an HTTP caller may see, and it does so by calling these; the read
helpers that build lists (`inbox`, `overview`, `tasks`, `rules_view`) take a viewer and call
`visible_q`. Nothing else in the codebase may compare `sensitivity` to decide visibility.

A MESSAGE (a source) has no sensitivity of its own; it borrows it from where it landed
(`source_visible_q`, BUILD_PLAN P9 rule 8). A message with a live event is visible when at least
one request it is attached to is visible. **A message attached to nothing is triage, and every
admin, member of staff and staff attorney sees it** (a limited-scope attorney and an intern never
do: 37a, "unmatched mail is for staff and admins"). **That is the one place a privileged message
can be read before it is filed**, and it is deliberate: somebody has to be able to open unplaced
mail to place it. One refinement keeps that window from reopening by accident: a message with no
live event but with voided ones is visible only if EVERY request those were on is visible. So
voiding the event that tied a message to a privileged request does not hand the message to
everyone, whatever ordinary request it passed through on the way. Such a message goes back to
general triage only when someone on the list places it on a request everyone may see.

What keeps a privileged request from being orphaned (BUILD_PLAN P9, rule 4):
  - reserving one grants the person reserving it (`on_reserved`), and reserving ANY request grants
    a limited-scope attorney or an intern who would otherwise not see what they just made;
  - making a request privileged grants the person doing it, in the same transaction, and making it
    not privileged revokes every grant, so a list never comes back to life unseen if the request
    is made privileged again later (`on_sensitivity_changed`);
  - revoking the last grant held by someone who can still use it is refused;
  - a person who is deactivated, or whose role becomes requester, comes off every list, and where
    they were the last usable person the administrator doing it is put on the list in their place,
    with a row that says so (`on_person_disabled`).

Work follows access. A request, or a task on it, is assigned only to someone who can open it
(`require_assignee_can_view`, called by the assigning services, so it holds at every door), and
when someone loses access their work on that request is unassigned with an audit line
(`_release_work`): on a revoke, on sealing, and when a person is disabled.
Only an administrator gives or takes a grant (P20h); an administrator sees every request anyway, so
a grant to one is a record, not a key. System actors need no grant: the webhook and the jobs act on
every request and have no viewer.

Lock order: the request row first (`lock_request`), as everywhere in the ledger. Grant, revoke and
a sensitivity edit all take it, which is what makes "the last grant" a fact rather than a guess.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from portal.core.services import activity as core_activity
from portal.core.services import notifications as core_notifications
from portal.records import roles
from portal.records.actors import Actor
from portal.records.models import (
    AccessRequest,
    Event,
    RecordsUser,
    Request,
    RequestAccess,
    Sensitivity,
    Source,
    Task,
    writable,
)
from portal.records.services import audit as audit_service
from portal.records.services.common import (
    InvalidTransition,
    NotAllowed,
    NotFound,
    ServiceError,
    lock_request,
    require_human,
)

PRIVILEGED = Sensitivity.PRIVILEGED.value


# ---- telling others that who may see something changed (P21c) ----------------------------------
#
# The drafting tool keeps its own copy of who may open each letter (a wave's access list, worked
# out by the portal from THE rule below and sent to the fork). `records` imports nothing from
# `drafting` (HANDOFF convention 13), so the drafting app asks to be told, as it asks
# `filing.after_filed`: every change that can move who sees a request calls `access_changed`,
# with the requests it touched, or None when it can touch anyone's (a person's role, their
# deactivation). Listeners run inside the caller's transaction and must not write to `records`;
# the drafting app's only defers its work to after the commit.

AccessListener = Callable[[frozenset[UUID] | None], None]
_ACCESS_LISTENERS: list[AccessListener] = []


def after_access_changed(listener: AccessListener) -> None:
    if listener not in _ACCESS_LISTENERS:
        _ACCESS_LISTENERS.append(listener)


def access_changed(request_ids: Iterable[UUID] | None) -> None:
    """Who may see these requests may have changed (None: any request, for any person)."""
    ids = None if request_ids is None else frozenset(request_ids)
    if ids is not None and not ids:
        return
    for listener in list(_ACCESS_LISTENERS):
        listener(ids)


@dataclass(frozen=True)
class Viewer:
    """The person a list or a page is being built for: who they are and their records role."""

    user_id: UUID
    role: str | None


# ---- the rule -------------------------------------------------------------------------------


def _visible(
    viewer: Viewer, request: Request, granted: Iterable[UUID], interns: Iterable[UUID] = ()
) -> bool:
    """THE rule. `granted` is the ids of requests on which the viewer holds an active grant;
    `interns` the ids of people whose role is intern (only asked of an intern viewer)."""
    role = viewer.role
    if role == roles.REQUESTER:
        return request.requester_id == viewer.user_id
    if role == roles.ADMIN:
        return True
    if role not in roles.TEAM:
        return False
    sealed = request.sensitivity == PRIVILEGED
    if not sealed and role in roles.FULL_STAFF:
        return True
    if request.requester_id == viewer.user_id or request.pk in granted:
        return True
    return not sealed and role == roles.INTERN and request.requester_id in interns


def _intern_ids() -> QuerySet[RecordsUser]:
    return RecordsUser.objects.filter(role=roles.INTERN)


def visible_q(viewer: Viewer, prefix: str = "") -> Q:
    """`_visible` as a filter. `prefix` is the path to the request, e.g. `"request__"` on a Task.

    The grant test is `pk IN (select the viewer's active grants)`, a subquery and not a join: a
    join would have to be repeated correctly under every prefix, and could multiply rows. The
    intern test is the same shape over `records.users` (a join to it would collide with
    `core.users`, both named `users`).
    """
    role = viewer.role
    mine = Q(**{f"{prefix}requester_id": viewer.user_id})
    if role == roles.REQUESTER:
        return mine
    if role == roles.ADMIN:
        return Q(**{f"{prefix}pk__isnull": False})
    if role not in roles.TEAM:
        return Q(**{f"{prefix}pk__in": []})
    granted = Q(**{f"{prefix}pk__in": _active_grants(viewer.user_id).values("request_id")})
    not_sealed = ~Q(**{f"{prefix}sensitivity": PRIVILEGED})
    if role in roles.FULL_STAFF:
        return not_sealed | mine | granted
    if role == roles.INTERN:
        made_by_intern = Q(**{f"{prefix}requester_id__in": _intern_ids().values("pk")})
        return (not_sealed & made_by_intern) | mine | granted
    return mine | granted


def _active_grants(user_id: UUID) -> QuerySet[RequestAccess]:
    return RequestAccess.objects.filter(user_id=user_id, revoked_at__isnull=True)


def granted_request_ids(user_id: UUID) -> set[UUID]:
    return set(_active_grants(user_id).values_list("request_id", flat=True))


def holds_grant(user_id: UUID | None, request_id: UUID) -> bool:
    """Whether this person holds an active grant on this request (a limited-scope attorney's
    approval turns on it: `core/services/review.py::may_approve`)."""
    return user_id is not None and _active_grants(user_id).filter(request_id=request_id).exists()


def _needs_lookup(viewer: Viewer) -> bool:
    """Whether the rule for this viewer can turn on a grant or a requester's role at all."""
    return viewer.role in roles.TEAM


def can_view(viewer: Viewer, request: Request) -> bool:
    if not _needs_lookup(viewer):
        return _visible(viewer, request, ())
    if (
        viewer.role in roles.FULL_STAFF
        and request.sensitivity != PRIVILEGED
        or viewer.role == roles.ADMIN
    ):
        return True
    held = holds_grant(viewer.user_id, request.pk)
    interns: tuple[UUID, ...] = ()
    if viewer.role == roles.INTERN and not held:
        interns = tuple(_intern_ids().filter(pk=request.requester_id).values_list("pk", flat=True))
    return _visible(viewer, request, (request.pk,) if held else (), interns)


def scope(viewer: Viewer, rows: Sequence[Request]) -> list[Request]:
    """The rows of `rows` the viewer may see, in the order given. A fixed handful of queries."""
    granted: set[UUID] = set()
    interns: set[UUID] = set()
    if _needs_lookup(viewer) and viewer.role != roles.ADMIN:
        granted = granted_request_ids(viewer.user_id)
        if viewer.role == roles.INTERN:
            wanted = {r.requester_id for r in rows}
            interns = set(_intern_ids().filter(pk__in=wanted).values_list("pk", flat=True))
    return [r for r in rows if _visible(viewer, r, granted, interns)]


def visible_ids(viewer: Viewer, request_ids: Iterable[UUID]) -> set[UUID]:
    """Which of these request ids the viewer may see. Ids that name no request are simply absent."""
    ids = set(request_ids)
    if not ids:
        return set()
    return set(Request.objects.filter(visible_q(viewer), pk__in=ids).values_list("pk", flat=True))


def unlisted_privileged(viewer: Viewer) -> list[Request]:
    """Every privileged request this viewer may NOT see, oldest first.

    For administrators only, and the caller shows nothing of these rows but the tracking number
    (DECISIONS, "Design v2", Still open 11). Since P20h an administrator sees every privileged
    request (37a), so for them this is empty; the route stays so nothing that links to it breaks.
    """
    if viewer.role != roles.ADMIN:
        return []
    from portal.core import rls

    # P17: the policies hide these rows from the administrator too; this is the one named
    # exception, for this query only.
    with rls.scope("index"):
        return list(
            Request.objects.filter(sensitivity=PRIVILEGED)
            .exclude(visible_q(viewer))
            .order_by("created_at", "pk")
        )


def as_viewer(viewer: "Viewer | UUID") -> Viewer:
    """A `Viewer`, from one or from a person's id (their role is then read from `records.users`)."""
    return viewer if isinstance(viewer, Viewer) else _viewer_of(viewer)


# ---- messages borrow their visibility from where they landed -----------------------------------


def source_visible_q(viewer: Viewer) -> Q:
    """The rule for a message, as a filter on `Source`. See the module docstring."""
    if viewer.role not in roles.TEAM:
        return Q(pk__in=[])
    events = Event.objects.filter(source_id=OuterRef("pk"))
    live = events.filter(voided_at__isnull=True)
    seen = visible_q(viewer, "request__")
    placed = Q(Exists(live.filter(seen)))
    if viewer.role not in roles.FULL_STAFF:
        # Triage is not theirs: a limited-scope attorney or an intern sees a message only through
        # a request they may see (37a, "unmatched mail is for staff and admins").
        return placed
    # Placed somewhere: seen through any request it is on. Placed nowhere: seen unless something
    # in its past was on a request this viewer may not see (no past at all is plain triage).
    return placed | (~Q(Exists(live)) & ~Q(Exists(events.exclude(seen))))


def can_view_source(viewer: Viewer, source_id: UUID) -> bool:
    return Source.objects.filter(source_visible_q(viewer), pk=source_id).exists()


# ---- the list -------------------------------------------------------------------------------


def active_access(request_id: UUID) -> list[RequestAccess]:
    return list(
        RequestAccess.objects.filter(request_id=request_id, revoked_at__isnull=True)
        .select_related("user")
        .order_by("granted_at", "id")
    )


def _active_row(request_id: UUID, user_id: UUID) -> RequestAccess | None:
    return RequestAccess.objects.filter(
        request_id=request_id, user_id=user_id, revoked_at__isnull=True
    ).first()


def person(user_id: UUID) -> RecordsUser | None:
    return RecordsUser.objects.filter(pk=user_id).first()


def _viewer_of(user_id: UUID) -> Viewer:
    person = RecordsUser.objects.filter(pk=user_id).first()
    return Viewer(user_id=user_id, role=person.role if person is not None else None)


def _usable(person: RecordsUser) -> bool:
    """Someone whose grant actually lets a person in: anyone on the team, able to sign in.

    Not `select_related("account")`: records.users and core.users are both named `users` and the
    join is ambiguous (see `common.require_assignable`).
    """
    return person.role in roles.TEAM and person.account.is_active


def _require_shareable(user_id: UUID) -> RecordsUser:
    person = RecordsUser.objects.filter(pk=user_id).first()
    if person is None:
        raise NotFound(f"No person {user_id}.")
    if person.role not in roles.TEAM:
        raise ServiceError(f"{person.name} is a requester; a request is shared with the team only.")
    if not person.account.is_active:
        raise ServiceError(f"{person.name} is deactivated and cannot be given access.")
    return person


def _insert(request_id: UUID, user_id: UUID, granted_by: UUID | None, note: str | None) -> None:
    with writable():
        RequestAccess.objects.create(
            request_id=request_id,
            user_id=user_id,
            granted_by_id=granted_by,
            note=(note or "").strip() or None,
        )
    access_changed({request_id})


def require_admin_actor(actor: Actor, what: str) -> UUID:
    """Only an administrator gives or takes a grant (P20h; 37a: "only an administrator gives a
    limited-scope attorney a request"). The API refuses first; this holds for every other door."""
    actor_id = require_human(actor, what)
    if _viewer_of(actor_id).role != roles.ADMIN:
        raise NotAllowed("Only an administrator can change who has access to a request.")
    return actor_id


@transaction.atomic
def grant_access(
    actor: Actor, request_id: UUID, user_id: UUID, note: str | None = None
) -> RequestAccess:
    """Give `user_id` this request: a grant. Administrators only, on any request.

    On a privileged request a grant is how anyone but an administrator sees it at all; on an
    ordinary one it is how a limited-scope attorney or an intern does (staff see it anyway, and a
    grant to them is a record of who was given it). The person is told by the bell.
    """
    actor_id = require_admin_actor(actor, "Sharing a request")
    request = lock_request(request_id)
    person = _require_shareable(user_id)
    if _active_row(request_id, user_id) is not None:
        raise InvalidTransition(f"{person.name} already has access to this request.")
    _insert(request_id, user_id, actor_id, note)
    row = _active_row(request_id, user_id)
    assert row is not None
    core_activity.record(
        actor,
        "shared",
        f"Shared {request.tracking_number} with {person.name}",
        subject_type="request",
        subject_id=request_id,
        request_id=request_id,
    )
    if user_id != actor_id:
        core_notifications.notify(
            user_id,
            "assigned",
            f"You were given {request.tracking_number}",
            link=f"/requests/{request_id}",
            subject_type="request",
            subject_id=request_id,
            request_id=request_id,
            actor_user_id=actor_id,
        )
    return row


@transaction.atomic
def revoke_access(actor: Actor, access_id: UUID) -> RequestAccess:
    """End one grant. Administrators only. On a privileged request it is refused when it is the
    last one held by someone who can still use it (an administrator still sees the request, but
    the list is what tells everyone who was given it)."""
    actor_id = require_admin_actor(actor, "Removing access")
    found = RequestAccess.objects.filter(pk=access_id).first()
    if found is None:
        raise NotFound(f"No access row {access_id}.")
    request = lock_request(found.request_id)
    # Re-read under the lock: another revoke may have landed while this one waited for it.
    row = RequestAccess.objects.get(pk=access_id)
    if row.revoked_at is not None:
        raise InvalidTransition("That access has already been removed.")
    if request.sensitivity == PRIVILEGED:
        others = RequestAccess.objects.filter(
            request_id=row.request_id, revoked_at__isnull=True
        ).exclude(pk=row.pk)
        if not any(_usable(other.user) for other in others.select_related("user")):
            raise InvalidTransition(
                "This is the last person with access. Share the request with someone else first."
            )
    RequestAccess.objects.filter(pk=row.pk).update(
        revoked_at=timezone.now(), revoked_by_id=actor_id
    )
    _release_work(actor, request.pk, only=row.user_id)
    access_changed({request.pk})
    return RequestAccess.objects.get(pk=row.pk)


# ---- the hooks that keep a privileged request from being orphaned (rule 4) ---------------------


def on_reserved(actor: Actor, request: Request) -> None:
    """Called by `reserve_request`, in its transaction. A system actor is granted nothing.

    The person reserving a privileged request goes on its list (rule 4). And whoever reserves a
    request they could not otherwise see -- a limited-scope attorney, or an intern reserving for
    a requester of record who is not an intern -- is given it, so nobody makes a request that
    vanishes from them the moment it exists. Both write a row, like every way onto a list."""
    if actor.user_id is None:
        return
    if request.sensitivity == PRIVILEGED or not can_view(_viewer_of(actor.user_id), request):
        if _viewer_of(actor.user_id).role in roles.TEAM:
            _ensure(request.pk, actor.user_id, "Reserved the request.")


def require_may_change_sensitivity(actor: Actor, request: Request) -> None:
    """Called by `edit_with_audit` with the request row it has just locked, before it writes.

    The door checked that the caller could see the request, but before the lock. If someone else
    sealed it in between, this caller is no longer on its list: letting the edit through would
    report success on a request they cannot open, and write their name into its audit trail."""
    actor_id = require_human(actor, "Changing who may see a request")
    acting = _viewer_of(actor_id)
    if acting.role not in roles.FULL_STAFF:
        # Sealing or unsealing changes who else sees the request; a limited-scope attorney and an
        # intern do not decide that (37a: access is an administrator's to give).
        raise NotAllowed("Only staff can change whether a request is privileged.")
    if not can_view(acting, request):
        raise NotAllowed("You no longer have access to this request.")


def on_sensitivity_changed(actor: Actor, request_id: UUID, old: str, new: str) -> None:
    """Called by `edit_with_audit` after it writes `requests.sensitivity`, under `lock_request`."""
    actor_id = require_human(actor, "Changing who may see a request")
    if new == PRIVILEGED and old != PRIVILEGED:
        _ensure(request_id, actor_id, "Made the request privileged.")
        _release_work(actor, request_id)
    elif old == PRIVILEGED and new != PRIVILEGED:
        # The list goes, so it never comes back to life unseen if the request is sealed again,
        # except the grants of people who see an ordinary request only through one (a
        # limited-scope attorney, an intern): they keep exactly what they had.
        scoped = RecordsUser.objects.filter(role__in=roles.GRANT_SCOPED).values("pk")
        RequestAccess.objects.filter(request_id=request_id, revoked_at__isnull=True).exclude(
            user_id__in=scoped
        ).update(revoked_at=timezone.now(), revoked_by_id=actor_id)
    # Sealed or unsealed: who may see it moved whatever the list did.
    access_changed({request_id})


def _ensure(request_id: UUID, user_id: UUID, note: str) -> None:
    if _active_row(request_id, user_id) is None:
        _insert(request_id, user_id, user_id, note)


@transaction.atomic
def on_person_disabled(actor: Actor, user_id: UUID, hand_work_to: UUID | None = None) -> None:
    """Called by `identity.deactivate_user` and by `identity.set_role` when a role becomes
    requester, BEFORE the change, in its transaction.

    The person comes off every list they are on, so a list never shows someone who cannot open the
    request and nothing comes back into force unseen if they are reactivated or promoted again.
    Where they were the last usable person, the administrator doing this is put on the list first:
    otherwise the request would be visible to nobody, and no list anywhere would show that it
    exists. That writes a row, like every other way onto a list. Their work on those requests goes
    to `hand_work_to` if that person can open the request, and to nobody otherwise.

    Requests are locked one at a time in id order, so two of these cannot deadlock each other.
    """
    actor_id = require_human(actor, "Removing a person's access")
    leaving = person(user_id)
    held = RequestAccess.objects.filter(user_id=user_id, revoked_at__isnull=True)
    for request_id in sorted(set(held.values_list("request_id", flat=True))):
        locked = lock_request(request_id)
        row = _active_row(request_id, user_id)
        if row is None:
            continue
        others = RequestAccess.objects.filter(
            request_id=request_id, revoked_at__isnull=True
        ).exclude(pk=row.pk)
        orphaned = not any(_usable(other.user) for other in others.select_related("user"))
        if locked.sensitivity == PRIVILEGED and orphaned:
            taking_over = person(actor_id)
            if taking_over is None or not _usable(taking_over) or actor_id == user_id:
                raise ServiceError(
                    f"{leaving.name if leaving else 'This person'} is the only person with access "
                    "to a privileged request, and it cannot pass to you: your own account has no "
                    "team role in records. Have them share it first."
                )
            _insert(
                request_id,
                actor_id,
                actor_id,
                f"Took over when {leaving.name if leaving else 'the last person'} lost access.",
            )
        RequestAccess.objects.filter(pk=row.pk).update(
            revoked_at=timezone.now(), revoked_by_id=actor_id
        )
        _release_work(actor, request_id, only=user_id, hand_to=hand_work_to)
        access_changed({request_id})


@transaction.atomic
def on_role_narrowed(actor: Actor, user_id: UUID) -> None:
    """Called by `identity.set_role` AFTER a person's role changed, in its transaction. Whatever
    they were working that their new role no longer lets them open (a member of staff made an
    intern, say) is unassigned, with an audit line, as when someone comes off a list. Their grants
    stay: for a limited-scope attorney or an intern a grant is how they see a request at all.
    Requests are locked in id order, as everywhere that holds more than one."""
    require_human(actor, "Changing a person's role")
    held = set(Request.objects.filter(assigned_to_id=user_id).values_list("pk", flat=True)) | set(
        Task.objects.filter(assigned_to_id=user_id, completed_at__isnull=True).values_list(
            "request_id", flat=True
        )
    )
    for request_id in sorted(held):
        lock_request(request_id)
        _release_work(actor, request_id, only=user_id)


# ---- work follows access ------------------------------------------------------------------------

_SHARE_FIRST = (
    "{name} does not have access to this request. An administrator gives it to them first; then "
    "it can be assigned."
)


def is_privileged(request: Request) -> bool:
    """For wording, never for visibility: a note that must not name a request to people off its
    list asks this. Whether someone may SEE a request is `can_view`, always."""
    return request.sensitivity == PRIVILEGED


def assignee_can_view(request: Request, user_id: UUID | None) -> bool:
    """Whether work on `request` may go to `user_id`. Nobody (None) always may."""
    return user_id is None or can_view(_viewer_of(user_id), request)


def require_assignee_can_view(request: Request, user_id: UUID | None) -> None:
    """Assigning is not sharing. Work on a privileged request goes only to someone already on its
    list: otherwise they would be handed something they cannot open, or the assignment would have
    to grant access as a side effect nobody chose. Called by the assigning services, whoever the
    actor is, so it holds at every door and for every job."""
    if user_id is None:
        return
    assignee = person(user_id)
    if assignee is None or assignee.role not in roles.TEAM:
        return  # nobody work can go to at all; `require_assignable` says so in its own words
    if not can_view(Viewer(user_id=user_id, role=assignee.role), request):
        raise ServiceError(_SHARE_FIRST.format(name=assignee.name))


def require_assignee_can_view_source(source_id: UUID, user_id: UUID | None) -> None:
    if user_id is None:
        return
    assignee = person(user_id)
    if assignee is None or assignee.role not in roles.TEAM:
        return
    if not can_view_source(Viewer(user_id=user_id, role=assignee.role), source_id):
        raise ServiceError(
            f"{assignee.name} does not have access to the request this message is on. An "
            "administrator gives it to them first; then it can be assigned."
        )


def _release_work(
    actor: Actor, request_id: UUID, *, only: UUID | None = None, hand_to: UUID | None = None
) -> None:
    """Take this request, and the open tasks on it, away from anyone who can no longer open it.

    Called under `lock_request`, after the change that removed their access. `only` limits it to
    one person. The work goes to `hand_to` if they can open the request, otherwise to nobody, where
    everyone on the list sees it. Each change is an audit line. Completed tasks are history and are
    left alone. Messages assigned to the person are not touched: a message's visibility is not
    this request's alone.
    """
    request = Request.objects.get(pk=request_id)
    new_holder = hand_to if hand_to is not None and assignee_can_view(request, hand_to) else None
    note = "Unassigned: the person holding this no longer has access to the request."
    if new_holder is not None:
        note = "Handed on: the person holding this no longer has access to the request."

    def losing(holder_id: UUID | None) -> bool:
        if holder_id is None or (only is not None and holder_id != only):
            return False
        return not assignee_can_view(request, holder_id)

    if losing(request.assigned_to_id):
        Request.objects.filter(pk=request_id).update(
            assigned_to_id=new_holder, updated_at=timezone.now()
        )
        audit_service.record_audit(
            actor,
            entity_type="requests",
            entity_id=request_id,
            field="assigned_to_user_id",
            old_value=request.assigned_to_id,
            new_value=new_holder,
            note=note,
        )
    open_tasks = Task.objects.filter(
        request_id=request_id, completed_at__isnull=True, assigned_to_id__isnull=False
    )
    for task in open_tasks.order_by("pk"):
        if not losing(task.assigned_to_id):
            continue
        Task.objects.filter(pk=task.pk).update(assigned_to_id=new_holder)
        audit_service.record_audit(
            actor,
            entity_type="tasks",
            entity_id=task.pk,
            field="assigned_to_user_id",
            old_value=task.assigned_to_id,
            new_value=new_holder,
            note=note,
        )


# ---- asking for access (P13) ------------------------------------------------------------------


@transaction.atomic
def ask_access(actor: Actor, request_id: UUID, reason: str) -> AccessRequest:
    """A staff member who cannot see a privileged request asks for it. One open ask per person
    per request; someone who already sees it has nothing to ask for."""
    user_id = require_human(actor, "Asking for access")
    if not reason or not reason.strip():
        raise ServiceError("Say why you need it.")
    asking = _viewer_of(user_id)
    if asking.role not in roles.FULL_STAFF:
        # Only staff learn that a privileged request they cannot open exists (P9's refusal
        # names it); a limited-scope attorney or an intern is never told, so never asks.
        raise NotAllowed("Only staff can ask for access.")
    # P17: under the policies the asker cannot read the row they are asking about. The one
    # fact they may learn is that it exists and is privileged (`sealed_tracking_number`).
    request = Request.objects.filter(pk=request_id).first()
    if request is not None:
        if request.sensitivity != PRIVILEGED:
            raise InvalidTransition(
                f"{request.tracking_number} is not privileged; everyone sees it."
            )
        if can_view(asking, request):
            raise InvalidTransition("You can already see this request.")
    elif sealed_tracking_number(request_id) is None:
        raise NotFound(f"No request {request_id}.")
    if AccessRequest.objects.filter(
        request_id=request_id, user_id=user_id, decided_at__isnull=True
    ).exists():
        raise InvalidTransition("You have already asked; it is waiting for an answer.")
    with writable():
        row = AccessRequest.objects.create(
            request_id=request_id, user_id=user_id, reason=reason.strip()
        )
    # Every administrator is told, by the bell only: never an email per ask (42e). The daily
    # summary lists the open asks for them (`notifications.summary_data`). The text names the
    # asker, not the request: the bell is read by people who see everything, but it is a list.
    core_notifications.notify_many(
        core_notifications.admin_ids(),
        "review",
        f"{person(user_id).name if person(user_id) else 'Someone'} asked for access to a request",
        link=f"/tasks?view=approvals&ask={row.pk}",
        subject_type="access_request",
        subject_id=row.pk,
        request_id=request_id,
        actor_user_id=user_id,
        email=False,
    )
    return row


@transaction.atomic
def decide_access(
    actor: Actor, ask_id: UUID, *, allow: bool, note: str | None = None
) -> AccessRequest:
    """Allow (which writes the grant, by the same rule as sharing) or decline, with a note the
    asker can read. Only an administrator decides (P20h: allowing is giving access)."""
    actor_id = require_human(actor, "Deciding an access request")
    found = AccessRequest.objects.filter(pk=ask_id).first()
    if found is None:
        raise NotFound(f"No access request {ask_id}.")
    request = lock_request(found.request_id)
    row = AccessRequest.objects.get(pk=ask_id)
    if row.decided_at is not None:
        raise InvalidTransition("This has already been decided.")
    if _viewer_of(actor_id).role != roles.ADMIN:
        raise NotAllowed("Only an administrator decides an ask for access.")
    if not allow and not (note or "").strip():
        raise ServiceError("Say why it is declined; the person asking will read it.")
    if allow:
        grant_access(actor, request.pk, row.user_id, note=f"Asked: {row.reason}")
    AccessRequest.objects.filter(pk=ask_id).update(
        decided_at=timezone.now(),
        decided_by_id=actor_id,
        decision=AccessRequest.ALLOWED if allow else AccessRequest.DECLINED,
        decision_note=(note or "").strip() or None,
    )
    core_activity.record(
        actor,
        "access_decided",
        f"Access to {request.tracking_number} {'allowed' if allow else 'declined'}",
        subject_type="access_request",
        subject_id=ask_id,
        request_id=request.pk,
    )
    core_notifications.notify(
        row.user_id,
        "review",
        f"Access to {request.tracking_number} was {'allowed' if allow else 'declined'}",
        link=f"/requests/{request.pk}" if allow else "/my-requests",
        subject_type="access_request",
        subject_id=ask_id,
        actor_user_id=actor_id,
    )
    return AccessRequest.objects.get(pk=ask_id)


def asks_of(request_id: UUID) -> list[AccessRequest]:
    return list(AccessRequest.objects.filter(request_id=request_id).order_by("created_at"))


def pending_asks_for(viewer: Viewer) -> list[AccessRequest]:
    """Every open ask, for an administrator (the only one who decides them); nothing for anyone
    else."""
    if viewer.role != roles.ADMIN:
        return []
    return list(
        AccessRequest.objects.filter(decided_at__isnull=True)
        .filter(visible_q(viewer, "request__"))
        .select_related("request", "user")
        .order_by("created_at")
    )


def my_asks(user_id: UUID) -> list[AccessRequest]:
    """The person's own asks. The request is not joined: under the policies an ask on a request
    the asker cannot read would vanish with it (P20f)."""
    return list(AccessRequest.objects.filter(user_id=user_id).order_by("-created_at"))


class PrivilegedRefusalError(NotAllowed):
    """Raised by a service for a request the actor cannot see; the API renders it as the
    privileged 403 with the tracking number."""

    def __init__(self, tracking_number: str) -> None:
        super().__init__("You don't have access to this.")
        self.tracking_number = tracking_number


def sealed_tracking_number(request_id: UUID) -> str | None:
    """The tracking number of a privileged request the caller cannot read, or None when no such
    request exists (P17, records_db.0017): what a refusal may name, and what an ask needs."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT records.sealed_tracking_number(%s)", [str(request_id)])
        row = cursor.fetchone()
    return row[0] if row else None


def hidden_row_exists(table: str, pk: UUID) -> bool:
    """Whether a child row the caller cannot read exists (P17): one bit, staff only, so the API
    can keep P9's 403 for a row on a sealed request instead of a 404 that would say more."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT records.hidden_row_exists(%s, %s)", [table, str(pk)])
        row = cursor.fetchone()
    return bool(row and row[0])
