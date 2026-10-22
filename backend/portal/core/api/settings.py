import uuid
from typing import Any

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from portal.core.api.users import require_admin
from portal.core.keycloak import policy as kc_policy
from portal.core.keycloak import sync as kc_sync
from portal.core.models import KeycloakChange, Setting, User
from portal.core.services import activity
from portal.core.services.settings import defaults
from portal.records.actors import Actor

router = Router(tags=["settings"])


class SettingOut(Schema):
    key: str
    value: Any
    description: str


class SettingIn(Schema):
    value: Any


@router.get("", response=list[SettingOut])
def list_settings(request: HttpRequest) -> list[SettingOut]:
    stored = {s.key: s for s in Setting.objects.all()}
    out: list[SettingOut] = []
    for key, spec in defaults().items():
        row = stored.get(key)
        out.append(
            SettingOut(
                key=key,
                value=row.value if row else spec["value"],
                description=row.description if row else spec.get("description", ""),
            )
        )
    return out


def _check_type(key: str, default: Any, value: Any) -> Any:
    """A setting keeps the type its default has: a switch stays a switch, a count a whole number
    (never negative), text text. The one answer is 422 with the reason."""
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise HttpError(422, f"{key} is on or off.")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise HttpError(422, f"{key} is a whole number.")
        if value < 0:
            raise HttpError(422, f"{key} cannot be negative.")
        return value
    if isinstance(default, str):
        if not isinstance(value, str):
            raise HttpError(422, f"{key} is text.")
        return value
    return value


# P20h (42d): the organisation's legal director, one person, named by an administrator. Empty is
# "nobody named". Nothing reads it until team chat (37h).
LEGAL_DIRECTOR = "org.legal_director"


def _check_person(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        person = uuid.UUID(text)
    except ValueError:
        raise HttpError(422, f"{LEGAL_DIRECTOR} is a person's id.") from None
    if not User.objects.filter(pk=person, is_active=True).exists():
        raise HttpError(422, "No active person has that id.")
    return str(person)


def _check_drafting(key: str, value: Any) -> Any:
    """P21c: the drafting tool's addresses are http(s) addresses or empty (the server's default);
    its usual letterhead is an id or empty."""
    from portal.drafting.services import fork
    from portal.records.services.common import ServiceError

    if key in (fork.API_URL_KEY, fork.WEB_URL_KEY):
        what = "The API address" if key == fork.API_URL_KEY else "The pages' address"
        try:
            return fork.check_address(value, what)
        except ServiceError as exc:
            raise HttpError(422, str(exc)) from None
    if key == fork.LETTERHEAD_KEY:
        text = value.strip()
        if not text:
            return ""
        try:
            return str(uuid.UUID(text))
        except ValueError:
            raise HttpError(422, "The letterhead is the drafting tool's id for it.") from None
    return value


@router.put("/{key}", response=SettingOut)
def put_setting(request: HttpRequest, key: str, payload: SettingIn) -> SettingOut:
    require_admin(request)
    spec = defaults().get(key)
    if spec is None:
        raise HttpError(404, f"Unknown setting {key!r}.")
    value = _check_type(key, spec["value"], payload.value)
    if key == LEGAL_DIRECTOR:
        value = _check_person(value)
    if key.startswith("drafting."):
        value = _check_drafting(key, value)
    if key in kc_policy.KEYS:
        why = kc_policy.check(key, value)
        if why:
            raise HttpError(422, why)
    before = Setting.objects.filter(pk=key).values_list("value", flat=True).first()
    row, _ = Setting.objects.update_or_create(
        key=key, defaults={"value": value, "description": spec.get("description", "")}
    )
    if before != value:
        activity.record(
            Actor.human(request.user),
            "setting_changed",
            f"Changed the setting {key}",
            subject_type="setting",
            data={"key": key, "old": before, "new": value},
        )
        if key.startswith("drafting.") and key != "drafting.fork_letterhead_id":
            # P21c: the drafting tool switched on or moved: its waves' access lists are sent again,
            # since nothing was sent while it was off (a no-op while it is not in use).
            from portal.drafting.services import fork

            fork.on_access_changed(None)
        if key in kc_policy.KEYS:
            # P25a: the sign-in rules are Keycloak's to enforce: written to the realm (queued,
            # retried until Keycloak has them), and read afresh by this process's sessions.
            from portal.core.keycloak import sessions as kc_sessions

            kc_sync.enqueue(None, KeycloakChange.POLICY, requested_by=request.user)
            kc_sessions.forget_policy()
        if key == kc_policy.ALLOW_APP:
            # P25c: what each person owes Keycloak follows from the switch (switched off, someone
            # with only an app adds a passkey at their next sign-in; on again, they need not; with
            # both, the passkey is offered first): everyone's account is brought in line.
            for person in User.objects.filter(keycloak_sub__isnull=False):
                kc_sync.enqueue(person, KeycloakChange.SYNC, requested_by=request.user)
        if key == "org.name":
            # P25a: the organisation's name is on every Keycloak account (`sclu_org`).
            for person in User.objects.filter(keycloak_sub__isnull=False):
                kc_sync.enqueue(person, KeycloakChange.SYNC, requested_by=request.user)
    return SettingOut(key=row.key, value=row.value, description=row.description)
