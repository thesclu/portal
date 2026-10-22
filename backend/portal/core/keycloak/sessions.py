"""A portal session tied to its Keycloak session (P25a).

The portal keeps its own session cookie (Django's database sessions, as before) with the
Keycloak tokens inside it. Each portal session:

- starts at the callback (`begin`), with a `SignInSession` row naming its Keycloak session (`sid`),
  so a back-channel logout ends exactly the portal sessions it means;
- is idle-limited by Settings' session length (`auth.session_hours`), applied on every request
  (Django's sliding expiry), the same number Keycloak's SSO idle timeout gets;
- lives only as long as its Keycloak session: once the access token is due, the next request
  refreshes it (which also keeps Keycloak's session from idling out while the portal is used);
  when Keycloak refuses (its session ended: idle, too old, signed out elsewhere, the person
  switched off), the portal session ends on that request. A Keycloak that cannot be reached keeps
  the session and is asked again half a minute later: an outage does not sign everyone out;
- asks for the passkey again once the last second step is older than Settings' re-verification
  age (`auth.reverify_hours`): Keycloak's own record of when each level was done comes in the ID
  token (`sclu_loa`), and once level 2 is too old every API route but the setup's answers 401
  `reverify_required` until the person has been through Keycloak again (which asks only for the
  passkey: the password, level 1, is still fresh).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse

from portal.core.keycloak import oidc
from portal.core.keycloak import policy as kc_policy
from portal.core.models import SignInSession

logger = logging.getLogger(__name__)

SESSION_KEY = "keycloak"
# Refresh this long before the access token runs out; after a failed attempt, wait this long.
REFRESH_EARLY_SECONDS = 30
RETRY_SECONDS = 30
# The policy is read from the database at most this often per process.
POLICY_SECONDS = 30
_policy_cache: dict[str, Any] = {"at": 0.0, "value": None}


def current_policy() -> kc_policy.Policy:
    now = time.monotonic()
    if _policy_cache["value"] is None or now - _policy_cache["at"] > POLICY_SECONDS:
        _policy_cache["value"] = kc_policy.current()
        _policy_cache["at"] = now
    return _policy_cache["value"]


def forget_policy() -> None:
    _policy_cache["value"] = None


def begin(request: HttpRequest, user: Any, tokens: oidc.Tokens) -> None:
    """After `login()`: the tokens into the session, its idle limit, and the row naming its
    Keycloak session."""
    data = tokens.session_data()
    request.session[SESSION_KEY] = data
    request.session.set_expiry(current_policy().session_seconds)
    request.session.save()
    key = request.session.session_key
    if key:
        SignInSession.objects.update_or_create(
            session_key=key, defaults={"user": user, "keycloak_sid": data.get("sid") or ""}
        )


def data_of(request: HttpRequest) -> dict[str, Any] | None:
    value = request.session.get(SESSION_KEY) if hasattr(request, "session") else None
    return value if isinstance(value, dict) else None


def reverify_due(request: HttpRequest, now: float | None = None) -> bool:
    """The last second step is older than the portal's re-verification age."""
    data = data_of(request)
    if data is None:
        return False
    now = time.time() if now is None else now
    checked = int((data.get("loa") or {}).get("2") or 0)
    deferred = float(data.get("reverify_after") or 0)
    age = current_policy().reverify_seconds
    return now >= max(checked + age, deferred)


def end(request: HttpRequest) -> None:
    """End this portal session (the person signed out, or Keycloak's session is over)."""
    from portal.core.services.signins import forget_session

    forget_session(request.session.session_key)
    logout(request)


class KeycloakSessionMiddleware:
    """After Django's authentication middleware: refresh, end and idle-limit portal sessions that
    came from Keycloak. A session without Keycloak data (a test's `force_login`) is left alone."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        data = data_of(request)
        if data is not None and request.user.is_authenticated:
            self._keep(request, data)
        return self.get_response(request)

    def _keep(self, request: HttpRequest, data: dict[str, Any]) -> None:
        seconds = current_policy().session_seconds
        if request.session.get("_session_expiry") != seconds:
            request.session.set_expiry(seconds)
        now = time.time()
        due = float(data.get("access_expires_at") or 0) - REFRESH_EARLY_SECONDS
        if now < due or now < float(data.get("retry_after") or 0):
            return
        try:
            fresh = oidc.refresh(str(data.get("refresh_token") or ""))
        except oidc.SignInFailed as exc:
            logger.info("Keycloak not reached to refresh a session: %s", exc)
            data["retry_after"] = now + RETRY_SECONDS
            request.session[SESSION_KEY] = data
            return
        if fresh is None:
            # Keycloak's session is over; so is this one.
            request.keycloak_ended = True  # type: ignore[attr-defined]
            end(request)
            return
        data.update(
            {
                "access_token": fresh.access_token,
                "refresh_token": fresh.refresh_token or data.get("refresh_token"),
                "access_expires_at": fresh.access_expires_at,
                "retry_after": 0,
            }
        )
        if fresh.id_token:
            data["id_token"] = fresh.id_token
            loa = oidc.loa_times(fresh.claims)
            if loa:
                data["loa"] = loa
        request.session[SESSION_KEY] = data
