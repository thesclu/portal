"""Invitations (P15a; P25a: the link and what it asks for are Keycloak's).

An invite makes the account at once, inactive, with the role the administrator chose, and owes
Keycloak two changes (`portal.core.keycloak.sync`): create the person's Keycloak account, then
send Keycloak's own invitation email ("execute actions": choose a password, add a passkey, save
recovery codes; the link works for `INVITE_HOURS`). The invite is accepted at the person's first
sign-in, which links their Keycloak account (by `sub`) and switches their portal account on
(`accept_on_first_sign_in`). Resending retires the old invite and sends a new email.

A requester (a member of the public on whose behalf the SCLU files) has no sign-in (owner,
2026-10-20): inviting one makes the account and sends nothing.

Password resets are Keycloak's too: "Forgot password" on its sign-in page, or an administrator's
"Send password reset" from Users (`KeycloakChange.PASSWORD_RESET`).
"""

from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from portal.core.keycloak import sync
from portal.core.models import Invite, KeycloakChange, User
from portal.core.services import identity
from portal.records import roles
from portal.records.services.common import InvalidTransition, NotFound


@transaction.atomic
def invite(
    inviter: User,
    *,
    email: str,
    name: str,
    records_role: str,
) -> User:
    """Make the account, inactive, and (for a team role) owe Keycloak the account and the email."""
    email = email.strip().lower()
    if User.objects.filter(email=email).exists():
        raise InvalidTransition("A person with that email already has an account.")
    user = identity.create_user(
        email=email,
        name=name.strip() or email,
        records_role=records_role,
        can_login=False,
    )
    if records_role in roles.TEAM:
        _issue(inviter, user)
    return user


@transaction.atomic
def resend(inviter: User, user_id: UUID) -> Invite:
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise NotFound(f"No person {user_id}.")
    if identity.records_role_of(user) not in roles.TEAM:
        raise InvalidTransition("Requesters do not sign in to the portal.")
    if user.keycloak_sub and user.is_active and user.last_login is not None:
        raise InvalidTransition("This person has already joined.")
    Invite.objects.filter(user=user, accepted_at__isnull=True).update(expires_at=timezone.now())
    return _issue(inviter, user)


def _issue(inviter: User | None, user: User) -> Invite:
    """A new invite and the two Keycloak changes it needs. `inviter` None: the portal itself (an
    operator moving existing accounts over, `keycloak_sync --invite-unlinked`)."""
    row = Invite.objects.create(
        user=user,
        invited_by=inviter,
        expires_at=timezone.now() + timedelta(hours=settings.INVITE_HOURS),
    )
    sync.enqueue(user, KeycloakChange.SYNC, requested_by=inviter)
    sync.enqueue(user, KeycloakChange.INVITE, requested_by=inviter)
    return row


def pending_invite(user: User) -> Invite | None:
    return (
        Invite.objects.filter(user=user, accepted_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )


def latest_invite(user: User) -> Invite | None:
    return (
        Invite.objects.select_related("invited_by")
        .filter(user=user)
        .order_by("-created_at")
        .first()
    )


@transaction.atomic
def accept_on_first_sign_in(user: User) -> bool:
    """The invited person signed in through Keycloak for the first time: the invite is accepted
    and the account switched on. False when there is no invite still open."""
    row = pending_invite(user)
    if row is None:
        return False
    Invite.objects.filter(pk=row.pk).update(accepted_at=timezone.now())
    if not user.is_active:
        user.is_active = True
        user.deactivated_at = None
        user.save(update_fields=["is_active", "deactivated_at"])
    return True
