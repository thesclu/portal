"""One person, one uuid.

`core.User` is the login account. `records.users` (defined by schema.sql, so
never edited) is the records app's view of the same person and carries the
records-app role. They share the uuid, which is what migration records_db.0003
enforces with a foreign key. Nothing else in the codebase creates either row.
"""

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.models import AppRole, KeycloakChange, User
from portal.core.services import signins
from portal.core.services.signins import end_sign_ins
from portal.records.actors import Actor
from portal.records.models import RecordsRole, RecordsUser, Task, writable
from portal.records.services import access as access_service
from portal.records.services import tasks as tasks_service
from portal.records.services.common import require_assignable

RECORDS_APP = "records"


class IdentityError(Exception):
    """An identity change was refused. The messages are ours: written for people, safe to show."""


class IdentityNotAllowed(IdentityError):
    """This person may not do this, whatever they ask for."""


class IdentityInvalid(IdentityError):
    """The request itself is wrong: a role that does not exist."""


def _owe_keycloak(user: User, action: str = KeycloakChange.SYNC, by: User | None = None) -> None:
    """P25a: Keycloak's copy of the person follows this change (queued, retried until applied).
    Nothing is owed for a person Keycloak has never known who still has no sign-in."""
    from portal.core.keycloak import sync
    from portal.records import roles

    if user.keycloak_sub or records_role_of(user) in roles.TEAM:
        sync.enqueue(user, action, requested_by=by)


@transaction.atomic
def create_user(
    *,
    email: str,
    name: str,
    records_role: str = RecordsRole.STAFF,
    can_login: bool = True,
    **extra: Any,
) -> User:
    """Create the account and its records profile together. P25a: no password, ever: a person
    signs in through Keycloak, whose account for them the invitation creates
    (`core.services.invites`).

    A requester who will never sign in (a member on whose behalf staff file) is an inactive
    account; the ledger still gets a proper user row to point at.
    """
    user = User.objects.create_user(email=email, name=name, is_active=can_login, **extra)
    grant_records_access(user, records_role)
    return user


@transaction.atomic
def grant_records_access(user: User, role: str, *, granted_by: User | None = None) -> RecordsUser:
    """Give a person a records role, one of the six (`RecordsRole`)."""
    if role not in RecordsRole.values:
        raise ValueError(f"Unknown records role {role!r}; expected one of {RecordsRole.values}.")
    with writable():
        profile = RecordsUser.objects.filter(account=user).first()
        if profile is None:
            profile = RecordsUser.objects.create(
                account=user, role=role, name=user.name, email=user.email
            )
        else:
            # `.update()` of exactly the granted columns, never `.save()` or `update_or_create()`
            # on an existing row: Django's full-column UPDATE is refused by portal_app's
            # column-level grants (records_db.0002). The first version of this function did that,
            # which was harmless while only a bootstrap command ran it as the owner and broke the
            # moment `set_role` ran it as the application role.
            RecordsUser.objects.filter(pk=profile.pk).update(
                role=role, name=user.name, email=user.email
            )
            profile = RecordsUser.objects.get(pk=profile.pk)
    defaults: dict[str, Any] = {"role": role, "granted_by": granted_by}
    AppRole.objects.update_or_create(user=user, app=RECORDS_APP, defaults=defaults)
    # P21c: a person's role decides which requests they see (an intern, interns' requests; a
    # limited-scope attorney, what they were given), so any request's audience may have moved.
    access_service.access_changed(None)
    return profile


@transaction.atomic
def update_profile(
    user: User,
    *,
    name: str | None = None,
    timezone_name: str | None = None,
    title: str | None = None,
) -> User:
    """A person's own name, time zone and title (Settings → Profile; the setup's second step).

    The name is held twice: on the login account (`core.users.name`) and on the records app's
    profile (`records.users.name`, which requests, tasks, the audit and the drafting tool's joins
    read). Both change here, in this one transaction, so a rename is a rename everywhere (P23a;
    before it, `PATCH /core/auth/me` changed only the first). A blank name changes nothing; the
    callers check the time zone and the title's length."""
    fields: list[str] = []
    if timezone_name is not None:
        user.timezone = timezone_name
        fields.append("timezone")
    if name is not None and name.strip():
        user.name = name.strip()
        fields.append("name")
    if title is not None:
        user.title = title
        fields.append("title")
    if fields:
        user.save(update_fields=fields)
    if "name" in fields:
        # `.update()` of the one column, as `grant_records_access` does: portal_app's column grants
        # refuse Django's full-row UPDATE (records_db.0002).
        with writable():
            RecordsUser.objects.filter(account=user).update(name=user.name)
        # P25a: the name on their Keycloak account (the tokens' `name`) follows.
        _owe_keycloak(user)
    return user


def records_app_role(user: User) -> AppRole | None:
    """The person's role row for the records app. Iterates `.all()`, not `.filter()`, so a caller
    that prefetched `app_roles` (the user list does) is not charged a query per person."""
    return next((r for r in user.app_roles.all() if r.app == RECORDS_APP), None)


def records_role_of(user: User) -> str | None:
    row = records_app_role(user)
    return row.role if row is not None else None


def _require_portal_admin(actor_user: User, what: str) -> None:
    if not (actor_user.is_active and actor_user.is_staff):
        raise IdentityNotAllowed(f"{what} is for portal administrators only.")


@transaction.atomic
def set_role(actor_user: User, user: User, role: str) -> RecordsUser:
    """Change a person's records role, to one of the six. Portal administrators only.

    The Director and "files without review" marks are retired (P20h; 42d): the role decides filing
    and approval, and what the Director mark guarded is an administrator's.

    **The role is what the person can do, so `User.is_staff` follows it: a person is a portal
    administrator exactly when their records role is `admin`.** `is_staff` is what gates the Users
    screen (`require_admin`), and the records role `admin` grants nothing else, so without this a
    demoted administrator kept every administrator power (and could promote themselves back) while
    a promoted member of staff was still refused. Both flags change in this one transaction.
    Because this is now the way to take administrator power away, it keeps the invariant
    `deactivate_user` keeps: the last active administrator cannot be demoted either.

    A role that sees less (staff made an intern, say) keeps its grants -- for a limited-scope
    attorney or an intern a grant is how a request is seen at all -- and its work on requests it can
    no longer open is unassigned, with an audit line (`access.on_role_narrowed`).
    """
    _require_portal_admin(actor_user, "Changing a role")
    if role not in RecordsRole.values:
        raise IdentityInvalid(f"Unknown role {role!r}; expected one of {list(RecordsRole.values)}.")
    administrator = role == RecordsRole.ADMIN
    if (
        user.is_staff
        and user.is_active
        and not administrator
        and not User.objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk).exists()
    ):
        raise IdentityInvalid("The last active administrator cannot be demoted.")
    if role == RecordsRole.REQUESTER:
        # A requester is on no access list. Take them off before the role moves, so no privileged
        # request is left with only someone who cannot open it (records.services.access).
        access_service.on_person_disabled(Actor.human(actor_user), user.pk)
    profile = grant_records_access(user, role, granted_by=actor_user)
    if role != RecordsRole.REQUESTER:
        access_service.on_role_narrowed(Actor.human(actor_user), user.pk)
    from portal.core.services import activity as core_activity

    core_activity.record(
        Actor.human(actor_user),
        "role_changed",
        f"{user.name} is now {RecordsRole(role).label.lower()}",
        subject_type="user",
        subject_id=user.pk,
        data={"role": role},
    )
    if user.is_staff != administrator:
        user.is_staff = administrator
        user.save(update_fields=["is_staff"])
    # P25a: the role on their Keycloak account (the tokens' `sclu_role`, read by the other tools)
    # follows; a requester's account is switched off and its sessions end (no public sign-in).
    # The portal's own decisions read the role from its database, never from a token. P25b: the
    # drafting tool reads the new role from Keycloak at its next re-check of the person's session
    # (a few minutes, while they use it) and at the next service call naming them; a requester's
    # sessions there end by Keycloak's back-channel logout.
    _owe_keycloak(user, by=actor_user)
    return profile


@transaction.atomic
def deactivate_user(actor_user: User, user: User, *, reassign_tasks_to: UUID | None = None) -> User:
    """Turn a person off. Their open tasks are handed on FIRST, so none is left with someone who
    can no longer see it.

    `reassign_tasks_to` names who gets them; None leaves them unassigned, still visible to
    everyone. Each handover is an audited `assign_task`, which also refuses a recipient who is a
    requester or already deactivated. The last active administrator cannot be deactivated: nobody
    would be left to reactivate anyone.

    Privileged requests come first. The person is taken off every access list (and where they were
    the last usable person on one, the administrator doing this is put on it, with a row), and
    their work on those requests is handed on or unassigned there. Then the rest of their tasks
    are handed on, except that a task on a privileged request never goes to someone who cannot
    open it: it is left unassigned, where the people on that request's list see it. The
    administrator doing this may not be able to see that request; they are told nothing about it.
    """
    _require_portal_admin(actor_user, "Deactivating a person")
    if not user.is_active:
        # P25a: an invited person who has not joined yet: the invitation is withdrawn and their
        # Keycloak account switched off, so the emailed link leads nowhere.
        from portal.core.models import Invite

        if Invite.objects.filter(
            user=user, accepted_at__isnull=True, expires_at__gt=timezone.now()
        ).update(expires_at=timezone.now()):
            _owe_keycloak(user, by=actor_user)
        return user
    if user.is_staff and not (
        User.objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk).exists()
    ):
        raise IdentityInvalid("The last active administrator cannot be deactivated.")
    if reassign_tasks_to == user.pk:
        raise IdentityInvalid("Open tasks cannot be handed back to the person being deactivated.")
    actor = Actor.human(actor_user)
    if reassign_tasks_to is not None:
        require_assignable(reassign_tasks_to)
    access_service.on_person_disabled(actor, user.pk, hand_work_to=reassign_tasks_to)
    open_tasks = Task.objects.filter(assigned_to_id=user.pk, completed_at__isnull=True)
    for task in open_tasks.select_related("request"):
        can_open = access_service.assignee_can_view(task.request, reassign_tasks_to)
        tasks_service.assign_task(actor, task.pk, reassign_tasks_to if can_open else None)
    user.is_active = False
    user.deactivated_at = timezone.now()
    user.save(update_fields=["is_active", "deactivated_at"])
    # P21c: a deactivated person sees nothing; the drafting tool's lists drop them.
    access_service.access_changed(None)
    # P25a: their portal sessions end now (the next request is signed out); Keycloak switches
    # their account off and ends every session it holds for them, which signs them out of every
    # other application by back-channel logout.
    end_sign_ins(user.pk, signins.DEACTIVATED)
    _owe_keycloak(user, by=actor_user)
    _owe_keycloak(user, KeycloakChange.END_SESSIONS, by=actor_user)
    from portal.core.services import activity as core_activity

    core_activity.record(
        actor,
        "user_deactivated",
        f"Deactivated {user.name}",
        subject_type="user",
        subject_id=user.pk,
    )
    return user


@transaction.atomic
def reactivate_user(actor_user: User, user: User) -> User:
    _require_portal_admin(actor_user, "Reactivating a person")
    if not user.is_active:
        user.is_active = True
        user.deactivated_at = None
        user.save(update_fields=["is_active", "deactivated_at"])
        access_service.access_changed(None)
        # P25a: their Keycloak account is switched back on (a requester's stays off).
        _owe_keycloak(user, by=actor_user)
        from portal.core.services import activity as core_activity

        core_activity.record(
            Actor.human(actor_user),
            "user_reactivated",
            f"Reactivated {user.name}",
            subject_type="user",
            subject_id=user.pk,
        )
    return user
