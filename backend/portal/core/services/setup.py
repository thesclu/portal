"""The new-user setup (P20i; v6 006–012, ARCHITECTURE "New user setup (first sign-in)"; DECISIONS
44): shown once, at a person's first sign-in, full screen, one decision per screen.

The steps, in order: welcome (who the account says they are), name and title, signature (drawn
or uploaded, stored where Settings → Profile → Your signature edits it), the confidentiality
agreement (everyone), working as an intern (the non-attorney roles), then done. Nothing is asked
that the portal already holds and nobody needs to confirm; there is no organisation step (removed
in v4) and no notification step (everyone gets the daily summary).

A person who has finished is never shown it again, except for an agreement whose version in
force they have not accepted: publishing a new version, or a role that brings an agreement with
it, sends them to that agreement alone at their next sign-in. A person who has not finished is
sent to it from every page (the client asks `required`).

The run's steps stay put while the person is in it: an agreement already accepted in this run
stays in the list (shown as agreed) until they finish, so "Step 4 of 5" never turns into "Step 4
of 4" under them.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from portal.core.models import Agreement, AgreementAcceptance, User
from portal.core.services import activity
from portal.core.services import agreements as agreements_service
from portal.core.services import signatures as signatures_service
from portal.core.services.identity import records_role_of
from portal.records.actors import Actor
from portal.records.services.common import ServiceError

WELCOME, NAME, SIGNATURE = "welcome", "name", "signature"


@dataclass(frozen=True)
class AgreementStep:
    agreement: Agreement
    acceptance: AgreementAcceptance | None


@dataclass(frozen=True)
class SetupState:
    # Whether the person is sent to the setup from every page.
    required: bool
    completed_at: datetime | None
    # This run's steps in order, "done" not included: welcome, name, signature, and the kinds of
    # agreement, each by its kind ("confidentiality", "intern").
    steps: tuple[str, ...]
    agreements: tuple[AgreementStep, ...]


def required(user: User) -> bool:
    """Not finished yet, or an agreement in force not accepted. The one rule the screens
    (`MeOut.setup_required`), the API (`portal.core.rls`) and team chat's socket all apply
    (P23a)."""
    if user.setup_completed_at is None:
        return True
    return required_for(user, records_role_of(user))


def required_for(user: User, role: str | None) -> bool:
    """`required`, for a caller that already knows the person's records role (the API's session
    auth reads it once per request): one query for a person who has finished."""
    return user.setup_completed_at is None or agreements_service.has_pending(user, role)


def state(user: User) -> SetupState:
    in_force = agreements_service.applicable(user)
    accepted = agreements_service.acceptances_of(user, in_force)
    finished = user.setup_completed_at
    if finished is None:
        shown = in_force
        steps = [WELCOME, NAME, SIGNATURE]
    else:
        # Asked again: an agreement not accepted, or accepted since they last finished (this
        # run's own acceptance, kept until they finish so the step count holds still).
        shown = [
            a for a in in_force if a.pk not in accepted or accepted[a.pk].accepted_at > finished
        ]
        steps = []
        if shown and signatures_service.get(user.pk) is None:
            steps.append(SIGNATURE)
    steps.extend(a.kind for a in shown)
    return SetupState(
        required=finished is None or any(a.pk not in accepted for a in in_force),
        completed_at=finished,
        steps=tuple(steps),
        agreements=tuple(AgreementStep(a, accepted.get(a.pk)) for a in shown),
    )


@transaction.atomic
def finish(user: User) -> User:
    """The last step's button: every agreement in force accepted, a signature stored, a name.
    Marks the setup finished (again, after a new version was agreed to)."""
    locked = User.objects.select_for_update().get(pk=user.pk)
    if not locked.name.strip():
        raise ServiceError("Give your name first.")
    if signatures_service.get(locked.pk) is None:
        raise ServiceError("Draw or upload your signature first.")
    waiting = agreements_service.pending(locked)
    if waiting:
        names = " and the ".join(agreements_service.title_of(a.kind).lower() for a in waiting)
        raise ServiceError(f"Agree to the {names} first.")
    first_time = locked.setup_completed_at is None
    locked.setup_completed_at = timezone.now()
    locked.save(update_fields=["setup_completed_at"])
    if first_time:
        activity.record(
            Actor.human(locked),
            "setup_finished",
            f"{locked.name} finished the new-user setup",
            subject_type="user",
            subject_id=locked.pk,
        )
    return locked
