import uuid
from datetime import datetime
from typing import Literal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from portal.core.keycloak import sync
from portal.core.models import KeycloakChange, User
from portal.core.services import identity, invites, signins
from portal.records import roles
from portal.records.api.permissions import require_team
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError

router = Router(tags=["users"])

# The six roles (P20h; `portal/records/roles.py`). A Literal so the OpenAPI schema lists them.
Role = Literal["admin", "staff", "staff_attorney", "limited_attorney", "intern", "requester"]


class SignInStateOut(Schema):
    state: Literal["ok", "pending", "failed", "none", "unlinked"]
    # The change waiting (one of KeycloakChange's actions) and, when failed, Keycloak's reason.
    action: str = ""
    detail: str = ""
    since: datetime | None = None


class UserOut(Schema):
    id: uuid.UUID
    email: str
    name: str
    is_active: bool
    is_staff: bool
    timezone: str
    records_role: str | None = None
    # P15a: invited, not yet joined; when the current link runs out.
    invite_pending_until: datetime | None = None
    last_login: datetime | None = None
    # P20f: the Users screen's "Last active": the newer of the person's last heartbeat (an open
    # tab, `core.presence`) and their last sign-in. Null when neither has happened.
    last_active_at: datetime | None = None
    # P23b: when an administrator deactivated them (205 "Deactivated Aug 30"); null while active,
    # and for anyone deactivated before the date was kept.
    deactivated_at: datetime | None = None
    # P25a: their sign-in account in Keycloak. "ok" (in line with the portal), "pending" (a change
    # is on its way), "failed" (Keycloak refused or could not be reached after every retry; the
    # hourly sweep keeps trying), "none" (no sign-in: a requester), "unlinked" (a team member
    # with no Keycloak account yet: an account from before P25a, until an invite makes one).
    sign_in: SignInStateOut


class UserPatch(Schema):
    """Every field optional; what is left out is left alone."""

    role: Role | None = None
    is_active: bool | None = None
    # Who gets the person's open tasks when `is_active` is false (42f). Left out: the
    # administrator doing it. Null: nobody (they stay open, unassigned).
    reassign_tasks_to: uuid.UUID | None = None


def require_admin(request: HttpRequest) -> None:
    if not request.user.is_staff:
        raise HttpError(403, "Portal administrators only.")


def last_active_at(user: User) -> datetime | None:
    """The newer of the last heartbeat and the last sign-in. Reads `user.presence`, so a list
    should `select_related("presence")` to keep this to the one query."""
    presence = getattr(user, "presence", None)
    seen = [t for t in (presence.seen_at if presence else None, user.last_login) if t]
    return max(seen, default=None)


def user_out(user: User, unfinished: KeycloakChange | None = None) -> UserOut:
    state = sync.state_of(user, unfinished)
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        is_active=user.is_active,
        is_staff=user.is_staff,
        timezone=user.timezone,
        records_role=identity.records_role_of(user),
        invite_pending_until=(lambda i: i.expires_at if i else None)(invites.pending_invite(user)),
        last_login=user.last_login,
        last_active_at=last_active_at(user),
        deactivated_at=None if user.is_active else user.deactivated_at,
        sign_in=SignInStateOut(
            state=state.state, action=state.action, detail=state.detail, since=state.since
        ),
    )


def _one(user_id: uuid.UUID) -> UserOut:
    user = User.objects.prefetch_related("app_roles").get(pk=user_id)
    return user_out(user, sync.unfinished_by_person([user.pk]).get(user.pk))


@router.get("", response=list[UserOut])
def list_users(request: HttpRequest) -> list[UserOut]:
    """Every person's name, email and role. Anyone on the team (P20h: interns and limited-scope
    attorneys hand work to a colleague too); not administrators only. A signed-in requester is
    refused with the records API's own 403."""
    # `require_team` rather than a copy of it: who counts as the team is decided in
    # records/api/permissions.py, and reusing it makes the refusal byte-for-byte the one every
    # other team-only door gives.
    require_team(request)
    people = list(
        User.objects.select_related("presence").prefetch_related("app_roles").order_by("name")
    )
    unfinished = sync.unfinished_by_person([u.pk for u in people])
    return [user_out(u, unfinished.get(u.pk)) for u in people]


# Our own messages, written for people: the identity family and the records ServiceError family
# (a task handover refused inside `deactivate_user` raises the latter).
_REFUSALS: tuple[tuple[type[Exception], int], ...] = (
    (identity.IdentityNotAllowed, 403),
    (identity.IdentityInvalid, 422),
    (NotFound, 404),
    (NotAllowed, 403),
    (InvalidTransition, 409),
    (ServiceError, 422),
)


class InviteIn(Schema):
    email: str
    name: str
    records_role: Role = "staff"


@router.post("/invite", response={201: UserOut})
def invite_user(request: HttpRequest, payload: InviteIn):
    """Make the account and have Keycloak email the person a link to join: choose a password, add
    a passkey, save recovery codes (P15a; P25a). A requester gets the account only: the public
    has no sign-in (owner, 2026-10-20)."""
    require_admin(request)
    email = payload.email.strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise HttpError(422, "That is not a valid email address.") from None
    try:
        user = invites.invite(
            request.user,
            email=email,
            name=payload.name,
            records_role=payload.records_role,
        )
    except ServiceError as exc:
        raise HttpError(409, str(exc)) from None
    return 201, _one(user.pk)


def _admin_target(request: HttpRequest, user_id: uuid.UUID) -> User:
    require_admin(request)
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise HttpError(404, f"No user {user_id}.")
    return user


@router.post("/{user_id}/resend-invite", response=UserOut)
def resend_invite(request: HttpRequest, user_id: uuid.UUID) -> UserOut:
    require_admin(request)
    try:
        row = invites.resend(request.user, user_id)
    except NotFound as exc:
        raise HttpError(404, str(exc)) from None
    except ServiceError as exc:
        raise HttpError(409, str(exc)) from None
    return _one(row.user_id)


def _owes_sign_in(user: User) -> None:
    if identity.records_role_of(user) not in roles.TEAM:
        raise HttpError(409, "Requesters do not sign in to the portal.")
    if not user.is_active:
        raise HttpError(409, f"{user.name} is deactivated.")


@router.post("/{user_id}/password-reset", response=UserOut)
def send_password_reset(request: HttpRequest, user_id: uuid.UUID) -> UserOut:
    """P25a (205 "Send password reset"): Keycloak emails the person a link to choose a new
    password. Signing in afterwards still takes their passkey."""
    user = _admin_target(request, user_id)
    _owes_sign_in(user)
    sync.enqueue(user, KeycloakChange.PASSWORD_RESET, requested_by=request.user)
    return _one(user.pk)


@router.post("/{user_id}/sign-out-everywhere", response=UserOut)
def sign_out_everywhere(request: HttpRequest, user_id: uuid.UUID) -> UserOut:
    """P25a: end every session the person has, in the portal now and in Keycloak (so in every
    application signed in through it) as soon as Keycloak takes the change."""
    user = _admin_target(request, user_id)
    signins.end_sign_ins(user.pk, signins.SIGNED_OUT_EVERYWHERE)
    if user.keycloak_sub:
        sync.enqueue(user, KeycloakChange.END_SESSIONS, requested_by=request.user)
    from portal.core.services import activity
    from portal.records.actors import Actor

    activity.record(
        Actor.human(request.user),
        "user_signed_out_everywhere",
        f"Signed {user.name} out everywhere",
        subject_type="user",
        subject_id=user.pk,
    )
    return _one(user.pk)


@router.post("/{user_id}/reset-second-step", response=UserOut)
def reset_second_step(request: HttpRequest, user_id: uuid.UUID) -> UserOut:
    """P25a: for a person who lost every passkey and recovery code, once an administrator has made
    sure it is them: their passkeys, authenticator-app codes and recovery codes are removed and
    their sessions end; at their next sign-in (password first) they add a new passkey."""
    user = _admin_target(request, user_id)
    _owes_sign_in(user)
    signins.end_sign_ins(user.pk, signins.SIGNED_OUT_EVERYWHERE)
    sync.enqueue(user, KeycloakChange.RESET_SECOND_STEP, requested_by=request.user)
    from portal.core.services import activity
    from portal.records.actors import Actor

    activity.record(
        Actor.human(request.user),
        "user_second_step_reset",
        f"Reset {user.name}'s two-step sign-in",
        subject_type="user",
        subject_id=user.pk,
    )
    return _one(user.pk)


@router.post("/{user_id}/sign-in/retry", response=UserOut)
def retry_sign_in_changes(request: HttpRequest, user_id: uuid.UUID) -> UserOut:
    """P25a: "Try again" on a sign-in change Keycloak refused or could not take: the person's
    failed changes are queued again, in their order."""
    user = _admin_target(request, user_id)
    sync.retry(user)
    return _one(user.pk)


@router.patch("/{user_id}", response=UserOut)
def update_user(request: HttpRequest, user_id: uuid.UUID, payload: UserPatch) -> UserOut:
    """Change a person's role or active state. Portal administrators only.

    The whole change is ONE transaction opened here, not left to ATOMIC_REQUESTS: ninja turns a
    refusal into a 4xx response without letting the exception reach Django, so the request
    transaction would commit whatever ran before it. Without this, a role change could stick while
    the deactivation sent with it was refused.
    """
    require_admin(request)
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise HttpError(404, f"No user {user_id}.")
    sent = payload.model_dump(exclude_unset=True)
    if "reassign_tasks_to" in sent and payload.is_active is not False:
        raise HttpError(422, "reassign_tasks_to only applies when deactivating a person.")
    # 42f: "Reassign their tasks to", defaulting to the administrator doing it (when they can hold
    # work at all: an administrator whose records role is requester cannot).
    default = request.user.id if identity.records_role_of(request.user) in roles.TEAM else None
    hand_to = payload.reassign_tasks_to if "reassign_tasks_to" in sent else default
    try:
        with transaction.atomic():
            if payload.role is not None:
                identity.set_role(request.user, user, payload.role)
            if payload.is_active is False:
                identity.deactivate_user(request.user, user, reassign_tasks_to=hand_to)
            elif payload.is_active is True:
                identity.reactivate_user(request.user, user)
    except Exception as exc:
        for exc_type, status in _REFUSALS:
            if isinstance(exc, exc_type):
                raise HttpError(status, str(exc)) from None
        raise
    return _one(user_id)
