"""The changes the portal owes Keycloak, each applied until Keycloak has it (P25a).

A change in the portal that Keycloak must follow (an invitation, a role, a name, a deactivation,
"sign out everywhere", a reset of the second step, a sign-in rule in Settings) writes a
`KeycloakChange` row in the same transaction, then defers one job per person after the commit
(`core.keycloak_changes`, with a lock per person so two workers never apply one person's changes at
once). The job applies that person's pending rows in the order they were made and stops at the
first that fails, so a later change never overtakes an earlier one. A row that fails is tried
again by the job's retry (waits of 3, 9, 27 … seconds) and, once those are spent, is marked
failed; the hourly sweep (`sweep`) takes up everything still pending or failed, and an
administrator can ask for another try from Users. Nothing is ever dropped: the row stays until
Keycloak has it.

Users shows each person's oldest unfinished change (`state_of`): "Updating sign-in" while
pending, "Sign-in update failed" with Keycloak's reason once failed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.core.keycloak import admin as kc_admin
from portal.core.keycloak import policy as kc_policy
from portal.core.models import KeycloakChange, User

logger = logging.getLogger(__name__)

# One job's retries before a row is marked failed (the job's RetryStrategy uses the same number).
MAX_ATTEMPTS = 8
# A pending row with no job in sight for this long is deferred again by the sweep.
STALE_MINUTES = 10
REALM = "realm"


class ChangeFailed(Exception):
    """A change could not be applied now; the job raises it so procrastinate retries."""


def _lock_key(user_id: UUID | None) -> str:
    return f"keycloak:{user_id or REALM}"


def _defer(user_id: UUID | None) -> None:
    """After the commit, one waiting job for this person (or the realm)."""

    def run() -> None:
        from procrastinate.exceptions import AlreadyEnqueued

        from portal.core.jobs import apply_keycloak_changes

        key = _lock_key(user_id)
        try:
            with transaction.atomic():
                apply_keycloak_changes.configure(lock=key, queueing_lock=key).defer(
                    user_id=str(user_id) if user_id else ""
                )
        except AlreadyEnqueued:
            pass

    transaction.on_commit(run)


@transaction.atomic
def enqueue(
    user: User | None, action: str, *, requested_by: User | None = None
) -> KeycloakChange | None:
    """Owe Keycloak `action` for `user` (None: the realm, for POLICY). A SYNC is not added
    behind another pending SYNC with nothing after it: one sync reads the person afresh."""
    user_id = user.pk if user is not None else None
    if action in (KeycloakChange.SYNC, KeycloakChange.POLICY):
        last = (
            KeycloakChange.objects.filter(user_id=user_id, state=KeycloakChange.PENDING)
            .order_by("-created_at")
            .first()
        )
        if last is not None and last.action == action:
            return last
    row = KeycloakChange.objects.create(user=user, action=action, requested_by=requested_by)
    _defer(user_id)
    return row


def _apply(row: KeycloakChange) -> None:
    if row.action == KeycloakChange.POLICY:
        changed = kc_policy.apply(kc_admin.client())
        if changed:
            logger.info("sign-in rules written to Keycloak: %s", ", ".join(changed))
        return
    user = User.objects.get(pk=row.user_id)
    if row.action == KeycloakChange.SYNC:
        kc_admin.sync_person(user)
    elif row.action == KeycloakChange.INVITE:
        kc_admin.send_invite(user)
    elif row.action == KeycloakChange.PASSWORD_RESET:
        kc_admin.send_password_reset(user)
    elif row.action == KeycloakChange.END_SESSIONS:
        kc_admin.end_sessions(user)
    elif row.action == KeycloakChange.RESET_SECOND_STEP:
        kc_admin.reset_second_step(user)
    else:  # pragma: no cover - the choices are closed
        raise ValueError(f"Unknown change {row.action!r}.")


def apply_pending(user_id: UUID | None) -> int:
    """Apply this person's (or the realm's) pending changes in order. Returns how many were
    applied. Raises `ChangeFailed` at the first that fails, after recording why."""
    applied = 0
    while True:
        row = (
            KeycloakChange.objects.filter(user_id=user_id, state=KeycloakChange.PENDING)
            .order_by("created_at")
            .first()
        )
        if row is None:
            return applied
        try:
            _apply(row)
        except Exception as exc:  # Keycloak's refusals and outages, and anything unexpected
            if not isinstance(exc, kc_admin.KeycloakProblem):
                logger.exception("Keycloak change %s (%s) raised", row.pk, row.action)
            row.attempts += 1
            row.last_error = str(exc)[:2000] or type(exc).__name__
            row.updated_at = timezone.now()
            if row.attempts >= MAX_ATTEMPTS:
                row.state = KeycloakChange.FAILED
            row.save(update_fields=["attempts", "last_error", "updated_at", "state"])
            logger.warning("Keycloak change %s (%s) failed: %s", row.pk, row.action, exc)
            if row.state == KeycloakChange.FAILED:
                return applied
            raise ChangeFailed(str(exc)) from exc
        now = timezone.now()
        KeycloakChange.objects.filter(pk=row.pk).update(
            state=KeycloakChange.DONE, done_at=now, updated_at=now, attempts=row.attempts + 1
        )
        applied += 1


@transaction.atomic
def retry(user: User | None) -> int:
    """An administrator's "Try again": the person's failed changes are pending again, in their
    first order, and a job is deferred. Returns how many were taken up."""
    taken = KeycloakChange.objects.filter(
        user_id=user.pk if user else None, state=KeycloakChange.FAILED
    ).update(state=KeycloakChange.PENDING, attempts=0, updated_at=timezone.now())
    if taken:
        _defer(user.pk if user else None)
    return taken


def sweep() -> int:
    """The hourly sweep: failed changes are tried again, and anyone with a pending change that
    has waited longer than a job would have gets a job. Returns how many people were taken up."""
    stale = timezone.now() - timedelta(minutes=STALE_MINUTES)
    owners: set[UUID | None] = set()
    with transaction.atomic():
        failed = KeycloakChange.objects.filter(state=KeycloakChange.FAILED)
        owners.update(failed.values_list("user_id", flat=True))
        failed.update(state=KeycloakChange.PENDING, attempts=0, updated_at=timezone.now())
        owners.update(
            KeycloakChange.objects.filter(
                state=KeycloakChange.PENDING, updated_at__lt=stale
            ).values_list("user_id", flat=True)
        )
        for owner in owners:
            _defer(owner)
    return len(owners)


# ---- what Users shows -----------------------------------------------------------------------


State = Literal["ok", "pending", "failed", "none", "unlinked"]


@dataclass(frozen=True)
class SyncState:
    state: State  # "none": no sign-in (a requester)
    action: str = ""
    detail: str = ""
    since: datetime | None = None


def unfinished_by_person(user_ids: list[UUID]) -> dict[UUID, KeycloakChange]:
    """Each person's oldest unfinished change, failed ones first, in one query."""
    rows = KeycloakChange.objects.filter(
        user_id__in=user_ids, state__in=(KeycloakChange.PENDING, KeycloakChange.FAILED)
    ).order_by("created_at")
    out: dict[UUID, KeycloakChange] = {}
    for row in rows:
        assert row.user_id is not None
        held = out.get(row.user_id)
        if held is None or (row.state == KeycloakChange.FAILED != held.state):
            out[row.user_id] = row
    return out


def state_of(user: Any, unfinished: KeycloakChange | None) -> SyncState:
    if unfinished is not None:
        return SyncState(
            state=cast(State, unfinished.state),
            action=unfinished.action,
            detail=unfinished.last_error if unfinished.state == KeycloakChange.FAILED else "",
            since=unfinished.created_at,
        )
    if not kc_admin.signs_in(user):
        return SyncState(state="none")
    if not user.keycloak_sub:
        # A team member with no Keycloak account yet (an account from before P25a): an invite
        # makes one (`keycloak_sync --invite-unlinked`, or Users → Resend invite).
        return SyncState(state="unlinked")
    return SyncState(state="ok")
