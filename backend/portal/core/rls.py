"""The per-transaction settings Postgres's row-level security reads (records_db.0017, P17).

`app.context` is 'web' for an API request, set by `SessionAuthWithContext` inside the request's
transaction (ATOMIC_REQUESTS) once the person is known; 'system' for the worker and management
commands, set at connection level by settings when the process is not a web server; and set by
the two webhooks for their own transaction. Unset means no request row is visible at all, so a
path nobody thought about leaks nothing.

The same session auth holds the new-user setup on the server (P23a; see the class).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.db import connection
from django.http import HttpRequest
from ninja.security import SessionAuth

from portal.core.services.identity import records_role_of

WEB = "web"
SYSTEM = "system"


def _set(name: str, value: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [name, value])


def set_web_context(user: Any) -> None:
    """This transaction acts for `user`: their id and records role, for the policies."""
    _set_web_context(user, records_role_of(user))


def _set_web_context(user: Any, role: str | None) -> None:
    _set("app.context", WEB)
    _set("app.user_id", str(user.pk))
    _set("app.role", role or "")


def set_system_context() -> None:
    """This transaction is the portal's own work (a webhook, a job): every row is visible."""
    _set("app.context", SYSTEM)


@contextmanager
def scope(name: str) -> Iterator[None]:
    """A named exception the policies know ('index': an administrator's list of privileged
    requests they are not on). Cleared on the way out, whatever happened."""
    _set("app.scope", name)
    try:
        yield
    finally:
        _set("app.scope", "")


def current_context() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT coalesce(current_setting('app.context', true), '')")
        return cursor.fetchone()[0]


SETUP_REQUIRED_CODE = "setup_required"
SETUP_REQUIRED_DETAIL = "Finish setting up your account first."

# Which routes stay open to a person the new-user setup holds (P23a): none, every person's
# (the setup's own routes), or only their own (`/core/users/{user_id}/...` with their own id).
CLOSED, OPEN, OWN = "closed", "open", "own"


REVERIFY_CODE = "reverify_required"
REVERIFY_DETAIL = "Confirm it's you with your passkey."


class ReverifyRequired(Exception):
    """P25a: the person's last second step is older than Settings' re-verification age
    (`portal.core.keycloak.sessions.reverify_due`). Answered 401 with `code: reverify_required`
    (portal/api.py); the client sends them through Keycloak again, which asks only for the
    passkey."""


class SetupRequired(Exception):
    """A signed-in person the new-user setup holds (not finished, or an agreement in force not
    accepted: `core.services.setup.required`) asked for a route the setup does not need.
    Answered 403 with `code: setup_required` (portal/api.py); the client goes to /setup."""


class SessionAuthWithContext(SessionAuth):
    """ninja's session auth, plus the policies' settings for the signed-in person. Runs inside
    the view's transaction, so `set_config(..., true)` lasts exactly as long as the request.

    It also holds the new-user setup on the server (P23a): a person whose setup is unfinished, or
    who has an agreement version in force they have not accepted, is refused every route except
    the ones the setup screens call, which carry `setup_session_auth` (or
    `own_setup_session_auth`, for a person's own signature) instead of the API's default. The
    same predicate sends them to /setup from the screens (`MeOut.setup_required`) and refuses team
    chat's socket. P25a: it also holds a person whose passkey check is due (`ReverifyRequired`)."""

    def __init__(self, *, during_setup: str = CLOSED, reverify: bool = True) -> None:
        super().__init__()
        self.during_setup = during_setup
        self.reverify = reverify

    def authenticate(self, request: HttpRequest, key: Any) -> Any:
        user = super().authenticate(request, key)
        if user is None:
            return None
        if self.reverify:
            from portal.core.keycloak.sessions import reverify_due

            if reverify_due(request):
                raise ReverifyRequired()
        role = records_role_of(user)
        _set_web_context(user, role)
        if not self._open_during_setup(request, user):
            from portal.core.services import setup as setup_service

            if setup_service.required_for(user, role):
                raise SetupRequired()
        return user

    def _open_during_setup(self, request: HttpRequest, user: Any) -> bool:
        if self.during_setup == OPEN:
            return True
        if self.during_setup == OWN:
            match = getattr(request, "resolver_match", None)
            target = match.kwargs.get("user_id") if match is not None else None
            return target is not None and str(target) == str(user.pk)
        return False


session_auth = SessionAuthWithContext()
# The routes the new-user setup itself calls (P23a): who the session is, the name and title, the
# setup's steps and Finish, agreeing, and signing out. Nothing else is open to a person the setup
# holds; tests/core/test_p23a_fixes.py pins this list against the registry.
setup_session_auth = SessionAuthWithContext(during_setup=OPEN)
# A person's own signature (read, its image, draw, upload): the setup's third step. Open only
# with the person's own id; an unfinished administrator may not touch anyone else's.
own_setup_session_auth = SessionAuthWithContext(during_setup=OWN)
# P25a: signing out works whatever else holds the person, a passkey check that is due included.
signout_session_auth = SessionAuthWithContext(during_setup=OPEN, reverify=False)
