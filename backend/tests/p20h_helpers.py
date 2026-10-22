"""P20h: only an administrator gives or takes access (DECISIONS 37a, 43). Tests that need a
request shared as part of their setup give it through here, as an administrator would, so the
setup says what it means ("give it to them") without each test growing an administrator."""

from typing import Any
from uuid import UUID

from portal.core.models import User
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.services import access as access_service

GIVER_EMAIL = "giver-admin@example.org"


def giver() -> User:
    """An administrator who exists only to give access in a test's setup."""
    found = User.objects.filter(email=GIVER_EMAIL).first()
    if found is not None:
        return found
    return create_user(
        email=GIVER_EMAIL,
        name="Giving Admin",
        records_role="admin",
        is_staff=True,
    )


def give(request_id: UUID, user_id: UUID, note: str | None = None) -> Any:
    """Give `user_id` the request, as an administrator."""
    return access_service.grant_access(Actor.human(giver()), request_id, user_id, note)


def take(access_id: UUID) -> Any:
    """Take a grant away, as an administrator."""
    return access_service.revoke_access(Actor.human(giver()), access_id)
