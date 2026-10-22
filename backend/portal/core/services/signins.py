"""Ending a person's portal sessions (P25a).

Before P25a this was `core.oidc.revoke`, which also deleted what the portal's own OpenID Connect
provider had issued. The provider is gone (Keycloak signs everyone in), so what is left is the
portal's own side: every portal session the person holds ends (the session rows, through
`SignInSession`). P25b retired the hook that told the drafting tool: Keycloak's own back-channel
logout to the tool ends its sessions now, whenever Keycloak's session ends.

Keycloak's side is not here: ending a person's Keycloak sessions (which back-channel-logs-out
every application they are signed in to) is a queued admin-API change
(`portal.core.keycloak.sync`, `KeycloakChange.END_SESSIONS`).
"""

import logging
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models.signals import post_save

from portal.core.models import SignInSession

logger = logging.getLogger(__name__)

# Why sessions ended (for the log and the callers' own reading).
SIGNED_OUT = "signed_out"
DEACTIVATED = "deactivated"
# Keycloak's back-channel logout: the person signed out of another application, an administrator
# ended their sessions, or Keycloak ended them after a password change or reset.
KEYCLOAK_LOGOUT = "keycloak_logout"
SIGNED_OUT_EVERYWHERE = "signed_out_everywhere"


def _end(rows: Iterable[SignInSession]) -> int:
    keys = [row.session_key for row in rows]
    if not keys:
        return 0
    Session.objects.filter(session_key__in=keys).delete()
    return SignInSession.objects.filter(session_key__in=keys).delete()[0]


@transaction.atomic
def end_sign_ins(user_id: UUID, reason: str = SIGNED_OUT, *, keycloak_sid: str = "") -> int:
    """End the person's portal sessions: all of them, or only those of one Keycloak session
    (`keycloak_sid`, from a back-channel logout that names it). Returns how many ended."""
    rows = SignInSession.objects.filter(user_id=user_id)
    if keycloak_sid:
        rows = rows.filter(keycloak_sid=keycloak_sid)
    gone = _end(list(rows))
    if gone:
        logger.info("ended %d portal session(s) of %s (%s)", gone, user_id, reason)
    return gone


def forget_session(session_key: str | None) -> None:
    """The session ended in the portal itself (signing out, the idle limit): its row goes."""
    if session_key:
        SignInSession.objects.filter(session_key=session_key).delete()


def on_user_saved(sender: Any, instance: Any, **kwargs: Any) -> None:
    """An inactive account holds no portal session, however it was switched off (the Users
    screen ends them itself; Django's admin or a shell does not)."""
    if not instance.is_active and not kwargs.get("raw"):
        if SignInSession.objects.filter(user_id=instance.pk).exists():
            end_sign_ins(instance.pk, DEACTIVATED)


def connect() -> None:
    from django.conf import settings

    post_save.connect(on_user_saved, sender=settings.AUTH_USER_MODEL, dispatch_uid="signins.saved")
