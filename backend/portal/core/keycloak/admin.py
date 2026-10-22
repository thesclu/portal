"""Keycloak's admin API, as the portal uses it (P25a): python-keycloak, signed in as the
`portal-admin` service account (client credentials). Its roles in the realm are the least that
does the job: `manage-users`, `view-users`, `query-users` for people and their sessions, and
`manage-realm`/`view-realm` for the sign-in rules Settings writes (`policy.py`).

The portal is the authority for people and roles. Keycloak's copy of a person is derived from the
portal's (`wanted`): their address (also the username), their name, whether they may sign in, and
the attributes the tokens carry (`sclu_role`, `sclu_org`, `sclu_uid`) plus two the invitation
email reads (`sclu_role_label`, `sclu_invited_by`). Keycloak holds the rest: passwords, passkeys,
authenticator-app codes, recovery codes, lockouts, sessions.

Who has a Keycloak account at all: the five team roles. A requester has none (owner, 2026-10-20:
the public is not built here), so a person who becomes a requester has their account switched
off and their sessions ended; one who never had an account is not given one.

Every call raises `KeycloakUnavailable` when Keycloak could not be reached or answered with a
server error (a retry may work) and `KeycloakRefused` when it refused (4xx: a retry will not fix
it without a change somewhere). `sync.py` decides what to do with either.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import requests
from django.conf import settings
from keycloak.exceptions import (
    KeycloakConnectionError,
    KeycloakError,
    KeycloakPostError,
    raise_error_from_response,
)

from keycloak import KeycloakAdmin, KeycloakOpenIDConnection
from portal.core.keycloak.config import admin_configured

logger = logging.getLogger(__name__)

# Credential types, as Keycloak names them.
PASSWORD = "password"
PASSKEY = "webauthn"
PASSWORDLESS = "webauthn-passwordless"
APP_CODE = "otp"
RECOVERY_CODES = "recovery-authn-codes"
SECOND_STEP_TYPES = (PASSKEY, PASSWORDLESS, APP_CODE, RECOVERY_CODES)

# Required actions, as Keycloak names them.
SET_PASSWORD = "UPDATE_PASSWORD"
ADD_PASSKEY = "webauthn-register"
SAVE_RECOVERY_CODES = "CONFIGURE_RECOVERY_AUTHN_CODES"
INVITE_ACTIONS = (SET_PASSWORD, ADD_PASSKEY, SAVE_RECOVERY_CODES)


class KeycloakProblem(Exception):
    """A call to Keycloak's admin API did not do what was asked."""


class KeycloakUnavailable(KeycloakProblem):
    """Not reached, timed out, or a server error: worth trying again."""


class KeycloakRefused(KeycloakProblem):
    """Keycloak said no (4xx). The message is Keycloak's, for the log and the Users screen."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class KeycloakConflict(KeycloakRefused):
    """The address belongs to a Keycloak account the portal did not make for this person."""


@lru_cache(maxsize=1)
def _client(base: str, realm: str, client_id: str, secret: str, timeout: float) -> KeycloakAdmin:
    connection = KeycloakOpenIDConnection(
        server_url=base.rstrip("/") + "/",
        realm_name=realm,
        user_realm_name=realm,
        client_id=client_id,
        client_secret_key=secret,
        grant_type="client_credentials",
        timeout=int(timeout) or 10,
        verify=True,
    )
    return KeycloakAdmin(connection=connection)


def client() -> KeycloakAdmin:
    conf = settings.KEYCLOAK
    if not admin_configured(conf):
        raise KeycloakUnavailable(
            "Keycloak's admin client is not configured (KEYCLOAK_INTERNAL_URL, "
            "KEYCLOAK_ADMIN_CLIENT_SECRET)."
        )
    return _client(
        conf["INTERNAL_URL"],
        conf["REALM"],
        conf["ADMIN_CLIENT_ID"],
        conf["ADMIN_CLIENT_SECRET"],
        conf["TIMEOUT_SECONDS"],
    )


def reset_client() -> None:
    """Forget the cached client (a test changing settings, a rotated secret)."""
    _client.cache_clear()


def _call(what: str, run: Any) -> Any:
    try:
        return run()
    except KeycloakConnectionError as exc:
        raise KeycloakUnavailable(f"{what}: Keycloak could not be reached ({exc}).") from exc
    except requests.RequestException as exc:
        raise KeycloakUnavailable(f"{what}: Keycloak could not be reached ({exc}).") from exc
    except KeycloakError as exc:
        code = getattr(exc, "response_code", None) or 0
        body = getattr(exc, "error_message", "") or str(exc)
        if isinstance(body, bytes):
            body = body.decode(errors="replace")
        if code >= 500 or code == 0:
            raise KeycloakUnavailable(f"{what}: Keycloak answered {code or 'nothing'}.") from exc
        raise KeycloakRefused(
            f"{what}: Keycloak refused ({code}): {str(body)[:300]}", status=code
        ) from exc


# ---- what Keycloak should hold for a person -------------------------------------------------


def signs_in(user: Any) -> bool:
    """A person with a Keycloak account at all: one of the five team roles."""
    from portal.core.services.identity import records_role_of
    from portal.records import roles

    return records_role_of(user) in roles.TEAM


def may_sign_in(user: Any) -> bool:
    """Their Keycloak account is on: a team role, and active or invited and not yet joined."""
    from portal.core.services import invites

    return signs_in(user) and (user.is_active or invites.pending_invite(user) is not None)


def attributes_for(user: Any) -> dict[str, list[str]]:
    from portal.core.services import invites
    from portal.core.services.identity import records_role_of
    from portal.core.services.settings import organisation_name
    from portal.records.models import RecordsRole

    role = records_role_of(user) or ""
    label = RecordsRole(role).label if role in RecordsRole.values else ""
    invite = invites.latest_invite(user)
    inviter = invite.invited_by.name if invite and invite.invited_by else ""
    return {
        "sclu_uid": [str(user.pk)],
        "sclu_role": [role] if role else [],
        "sclu_role_label": [label] if label else [],
        "sclu_org": [organisation_name()],
        "sclu_invited_by": [inviter] if inviter else [],
    }


def wanted(user: Any) -> dict[str, Any]:
    """The account's representation, as the portal says it should be."""
    return {
        "username": user.email,
        "email": user.email,
        "emailVerified": True,
        "firstName": user.name,
        "lastName": "",
        "enabled": may_sign_in(user),
        "attributes": attributes_for(user),
    }


# ---- finding and changing a person's account ------------------------------------------------


def find_account(user: Any) -> dict[str, Any] | None:
    """The person's Keycloak account: by the sub the portal holds, else by address. An account
    found by address is theirs only if it carries no other person's portal id."""
    kc = client()
    if user.keycloak_sub:
        try:
            return _call("Reading the account", lambda: kc.get_user(user.keycloak_sub))
        except KeycloakRefused as exc:
            if exc.status != 404:
                raise
    found = _call(
        "Looking up the address",
        lambda: kc.get_users({"email": user.email, "exact": "true"}),
    )
    for account in found or []:
        if str(account.get("email", "")).lower() != user.email.lower():
            continue
        owner = (account.get("attributes") or {}).get("sclu_uid") or []
        if owner and owner[0] != str(user.pk):
            raise KeycloakConflict(
                f"{user.email} already has a Keycloak account belonging to someone else."
            )
        return account
    return None


def _link(user: Any, sub: str) -> None:
    if user.keycloak_sub != sub:
        type(user).objects.filter(pk=user.pk).update(keycloak_sub=sub)
        user.keycloak_sub = sub
        # P25b: the drafting tool names people by their Keycloak sub (its SCLU.md 16): a person
        # linked for the first time can be put on a wave's list now, and one whose Keycloak
        # account was re-created is named by the new sub. The lists are sent again (after the
        # commit, only while the tool is in use).
        from portal.records.services import access as access_service

        access_service.access_changed(None)


def credentials(user: Any) -> list[dict[str, Any]]:
    """The person's credentials as Keycloak lists them (type, label, created; never a secret)."""
    if not user.keycloak_sub:
        return []
    kc = client()
    return list(_call("Reading credentials", lambda: kc.get_credentials(user.keycloak_sub)) or [])


def _baseline_actions(account: dict[str, Any], held: set[str], allow_app: bool) -> list[str]:
    """The two required actions the portal owns on a person's account, set or cleared from what
    they hold, the rest of their required actions kept as they are.

    Nobody signs in on a password alone, and Keycloak's own flow sees to most of it: someone with
    no second step yet sets one up after their password (a passkey, or an authenticator app if
    they choose one while Settings allows them), then saves recovery codes (P25c; DECISIONS 54).
    What is left to the account's required actions:

    - Add a passkey (`webauthn-register`) when the person has nothing left that Keycloak will
      accept but recovery codes: with authenticator apps allowed, recovery codes alone; with them
      not allowed, no passkey (an app is then unused). They sign in with a recovery code and add
      a passkey straight after. Someone with nothing at all is not asked here: the flow asks, and
      offers the choice.
    - Save recovery codes when the person has a second step they may use but no codes (they used
      them up or removed them). Not before a first second step: those come after it in the flow,
      and codes alone would let the next sign-in skip setting one up.
    """
    passkey, app, codes = PASSKEY in held, APP_CODE in held, RECOVERY_CODES in held
    usable_app = app and allow_app
    owe_passkey = (not passkey and not usable_app and codes) or (not passkey and not allow_app)
    owe_codes = not codes and (passkey or usable_app)
    actions = [
        a
        for a in (account.get("requiredActions") or [])
        if a not in (ADD_PASSKEY, SAVE_RECOVERY_CODES)
    ]
    if owe_passkey:
        actions.append(ADD_PASSKEY)
    if owe_codes:
        actions.append(SAVE_RECOVERY_CODES)
    return actions


# The order Keycloak offers a person's second steps in (P25c): the passkey first, then the app,
# then recovery codes; the password ahead of them all, anything else after.
OFFER_ORDER = {PASSWORD: 0, PASSKEY: 1, APP_CODE: 2, RECOVERY_CODES: 3}


def _second_steps_in_order(user: Any, creds: list[dict[str, Any]]) -> int:
    """Someone with a passkey and something else is offered the passkey first (P25c). Keycloak
    offers a person's second steps in the order of their credentials, which is the order they
    were added in, so a passkey added after an authenticator app or after recovery codes would
    come behind them. The credentials are put in OFFER_ORDER (stable within a kind) with
    Keycloak's own `moveAfter`/`moveToFirst`, one move per credential out of place. Returns how
    many moved."""
    wanted = sorted(creds, key=lambda c: OFFER_ORDER.get(str(c.get("type") or ""), 9))
    if [c.get("id") for c in wanted] == [c.get("id") for c in creds]:
        return 0
    kc = client()
    base = f"admin/realms/{settings.KEYCLOAK['REALM']}/users/{user.keycloak_sub}/credentials"
    now = list(creds)
    moved = 0
    for i, cred in enumerate(wanted):
        if now[i].get("id") == cred.get("id"):
            continue
        path = (
            f"{base}/{cred['id']}/moveToFirst"
            if i == 0
            else f"{base}/{cred['id']}/moveAfter/{wanted[i - 1]['id']}"
        )
        _call(
            "Putting the passkey first",
            lambda path=path: raise_error_from_response(
                kc.connection.raw_post(path, data=None), KeycloakPostError, expected_codes=[204]
            ),
        )
        now.remove(cred)
        now.insert(i, cred)
        moved += 1
    return moved


def sync_person(user: Any, *, seed_password: str | None = None) -> str | None:
    """Make the person's Keycloak account what the portal says: create it (a team role and allowed
    to sign in), update it, or switch it off. Returns the account's id (`sub`), or None when the
    person has none. `seed_password` is for the development seed only (DEBUG)."""
    kc = client()
    account = find_account(user)
    if not signs_in(user):
        if account is not None:
            _link(user, account["id"])
            if account.get("enabled"):
                _call(
                    "Switching the account off",
                    lambda: kc.update_user(account["id"], {"enabled": False}),
                )
                _call("Ending sessions", lambda: kc.user_logout(account["id"]))
        return account["id"] if account else None
    body = wanted(user)
    if account is None:
        if not body["enabled"]:
            return None
        create = dict(body)
        if seed_password is not None:
            if not settings.DEBUG:
                raise KeycloakRefused("A seed password is for a development machine only.")
            create["credentials"] = [{"type": PASSWORD, "value": seed_password, "temporary": False}]
        sub = _call("Creating the account", lambda: kc.create_user(create, exist_ok=False))
        _link(user, sub)
        account = _call("Reading the new account", lambda: kc.get_user(sub))
    else:
        _link(user, account["id"])
    from portal.core.keycloak import policy

    creds = credentials(user)
    held = {str(c.get("type") or "") for c in creds}
    allow_app = policy.current().allow_authenticator_app
    body["requiredActions"] = _baseline_actions(account, held, allow_app)
    _call("Updating the account", lambda: kc.update_user(account["id"], body))
    _second_steps_in_order(user, creds)
    if not body["enabled"]:
        _call("Ending sessions", lambda: kc.user_logout(account["id"]))
    return account["id"]


def send_invite(user: Any) -> None:
    """Keycloak's own "execute actions" email: choose a password, add a passkey, save recovery
    codes. The link works for the invite's lifetime (`INVITE_HOURS`). While authenticator apps are
    allowed, the passkey page offers the app instead (the sclu theme; P25c), which goes on as a
    sign-in that sets one up (`oidc.start(app_second_step=True)`)."""
    sub = sync_person(user)
    if sub is None:
        raise KeycloakRefused(f"{user.email} has no sign-in to invite them to.")
    kc = client()
    lifespan = int(settings.INVITE_HOURS) * 3600
    _call(
        "Sending the invitation",
        lambda: kc.send_update_account(
            sub, list(INVITE_ACTIONS), client_id=settings.KEYCLOAK["CLIENT_ID"], lifespan=lifespan
        ),
    )


# How long an administrator's password-reset email works: the forgot-password link's half hour.
PASSWORD_RESET_SECONDS = 30 * 60


def send_password_reset(user: Any) -> None:
    """Keycloak's email asking the person to choose a new password (an administrator's "Send
    password reset"). Signing in afterwards still takes the second step."""
    sub = sync_person(user)
    if sub is None:
        raise KeycloakRefused(f"{user.email} has no sign-in.")
    kc = client()
    _call(
        "Sending the password reset",
        lambda: kc.send_update_account(
            sub,
            [SET_PASSWORD],
            client_id=settings.KEYCLOAK["CLIENT_ID"],
            lifespan=PASSWORD_RESET_SECONDS,
        ),
    )


def end_sessions(user: Any) -> None:
    """End every Keycloak session the person has. Keycloak then tells each application with a
    back-channel logout address (the portal included) that those sessions are over."""
    if not user.keycloak_sub:
        return
    kc = client()
    try:
        _call("Ending sessions", lambda: kc.user_logout(user.keycloak_sub))
    except KeycloakRefused as exc:
        if exc.status != 404:
            raise


def reset_second_step(user: Any) -> int:
    """Remove the person's passkeys, authenticator-app codes and recovery codes and end their
    sessions; at their next sign-in (password first) they set up a second step again (a passkey,
    or an authenticator app while those are allowed) and new recovery codes.
    For a person who lost every device and code, after an administrator has made sure it is
    them. Returns how many credentials went."""
    if not user.keycloak_sub:
        return 0
    kc = client()
    gone = 0
    for cred in credentials(user):
        if cred.get("type") in SECOND_STEP_TYPES:
            _call(
                "Removing a credential",
                lambda cred=cred: kc.delete_credential(user.keycloak_sub, cred["id"]),
            )
            gone += 1
    end_sessions(user)
    sync_person(user)
    return gone
