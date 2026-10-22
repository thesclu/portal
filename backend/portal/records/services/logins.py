"""Stored agency portal logins (P19g, design 2c): set, reveal, clear.

The password is encrypted with `PORTAL_SECRETS_KEY` (Fernet) before it reaches the row and
decrypted only by `reveal`, which leaves an activity line saying who looked and when. The value
itself is never written anywhere else: not the audit log (which copies old and new values), not
the activity log, not a task.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from portal.core.services import activity as core_activity
from portal.records.actors import Actor
from portal.records.models import Agency, AgencyLogin, writable
from portal.records.services.common import NotFound, ServiceError, require_human


class NoSecretsKey(ServiceError):
    """`PORTAL_SECRETS_KEY` is not set: nothing can be stored or read."""


@dataclass(frozen=True)
class LoginView:
    agency_id: UUID
    login: str
    has_password: bool
    notes: str | None
    changed_at: datetime | None
    changed_by_name: str | None


def _fernet() -> Fernet:
    key = str(settings.PORTAL.get("SECRETS_KEY") or "").strip()
    if not key:
        raise NoSecretsKey(
            "PORTAL_SECRETS_KEY is not set, so a portal login cannot be stored or shown."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise NoSecretsKey(
            "PORTAL_SECRETS_KEY is not a valid key; make one with `just secrets-key`."
        ) from exc


def view(agency_id: UUID) -> LoginView | None:
    row = AgencyLogin.objects.filter(pk=agency_id).select_related("changed_by").first()
    if row is None or (not row.login and not row.password_ciphertext):
        return None
    return LoginView(
        agency_id=row.agency_id,
        login=row.login,
        has_password=bool(row.password_ciphertext),
        notes=row.notes,
        changed_at=row.changed_at,
        changed_by_name=row.changed_by.name if row.changed_by_id else None,
    )


@transaction.atomic
def set_login(
    actor: Actor,
    agency_id: UUID,
    *,
    login: str,
    password: str | None,
    notes: str | None = None,
) -> LoginView:
    """Store the login and, when given, a new password. A password left out keeps the old one."""
    user_id = require_human(actor, "Storing a portal login")
    if not Agency.objects.filter(pk=agency_id).exists():
        raise NotFound(f"No agency {agency_id}.")
    login = (login or "").strip()
    if not login:
        raise ServiceError("The login is required.")
    fields = {
        "login": login,
        "notes": (notes or "").strip() or None,
        "changed_at": timezone.now(),
        "changed_by_id": user_id,
    }
    if password is not None:
        fields["password_ciphertext"] = (
            _fernet().encrypt(password.encode()).decode() if password else ""
        )
    with writable():
        existing = AgencyLogin.objects.filter(pk=agency_id).first()
        if existing is None:
            AgencyLogin.objects.create(agency_id=agency_id, **fields)
        else:
            AgencyLogin.objects.filter(pk=agency_id).update(**fields)
    core_activity.record(
        actor,
        "agency_login_changed",
        "Stored a portal login" + (" and password" if password else ""),
        subject_type="agency",
        subject_id=agency_id,
    )
    found = view(agency_id)
    assert found is not None
    return found


def reveal(actor: Actor, agency_id: UUID) -> str:
    """The password in clear, once, to the person asking; the asking is recorded."""
    require_human(actor, "Revealing a portal password")
    row = AgencyLogin.objects.filter(pk=agency_id).first()
    if row is None or not row.password_ciphertext:
        raise NotFound("This agency has no stored password.")
    try:
        password = _fernet().decrypt(row.password_ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ServiceError(
            "The stored password cannot be read with the current PORTAL_SECRETS_KEY."
        ) from exc
    core_activity.record(
        actor,
        "agency_password_revealed",
        "Looked at a stored portal password",
        subject_type="agency",
        subject_id=agency_id,
    )
    return password


@transaction.atomic
def clear_login(actor: Actor, agency_id: UUID) -> None:
    user_id = require_human(actor, "Clearing a portal login")
    row = AgencyLogin.objects.filter(pk=agency_id).first()
    if row is None:
        raise NotFound("This agency has no stored login.")
    with writable():
        AgencyLogin.objects.filter(pk=agency_id).update(
            login="",
            password_ciphertext="",
            notes=None,
            changed_at=timezone.now(),
            changed_by_id=user_id,
        )
    core_activity.record(
        actor,
        "agency_login_cleared",
        "Cleared a stored portal login",
        subject_type="agency",
        subject_id=agency_id,
    )
