"""Signing in and out, and who is signed in (P25a: through Keycloak).

The portal never sees a password, a passkey or a code: Keycloak's pages take them. The portal is
an OpenID Connect client in the backend-for-frontend style (`portal.core.keycloak`):

  GET  /core/auth/oidc/login                 to Keycloak (a full page load from the SPA)
  GET  /core/auth/oidc/callback              back from Keycloak: checked, then signed in here
  POST /core/auth/oidc/backchannel-logout    Keycloak ends a session: the portal's ends with it
  POST /core/auth/logout                     sign out here, then (the browser) at Keycloak

Changing a password, adding or removing a passkey, new recovery codes: Keycloak's own pages too,
reached through `oidc/login?kc_action=…` (its application-initiated actions); `security` says what
the person has, for Settings → Profile.
"""

import logging
import re
import secrets
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token
from ninja import Router, Schema, Status
from ninja.errors import HttpError

from portal.core.keycloak import admin as kc_admin
from portal.core.keycloak import oidc, sessions
from portal.core.models import User
from portal.core.rls import setup_session_auth, signout_session_auth
from portal.core.services import identity, invites, setup, signins
from portal.core.services.identity import records_role_of
from portal.records import roles

logger = logging.getLogger(__name__)

router = Router(tags=["auth"])


class MeOut(Schema):
    id: uuid.UUID
    email: str
    name: str
    timezone: str
    is_staff: bool
    # One of the six roles (P20h). The client shows what a role may do from this; the server
    # still refuses on its own.
    records_role: str | None = None
    # P20i: the title the person signs with, and whether they are sent to the new-user setup
    # (not finished, or an agreement in force not accepted; core.services.setup).
    title: str = ""
    setup_required: bool = False


TITLE_MAX = 200  # core.User.title's length


class MeUpdate(Schema):
    name: str | None = None
    timezone: str | None = None
    # P20i: an empty string clears it.
    title: str | None = None


class ErrorOut(Schema):
    detail: str


class CsrfOut(Schema):
    csrf_token: str


class EndSessionOut(Schema):
    """Keycloak's end-session endpoint and the fields to post to it (RP-initiated logout; posted,
    so the ID token stays out of addresses)."""

    url: str
    fields: dict[str, str]


class SignedOutOut(Schema):
    ok: bool = True
    end_session: EndSessionOut | None = None


def me_out(user: User) -> MeOut:
    return MeOut(
        id=user.id,
        email=user.email,
        name=user.name,
        timezone=user.timezone,
        is_staff=user.is_staff,
        records_role=records_role_of(user),
        title=user.title,
        setup_required=setup.required(user),
    )


def _client_of(request: HttpRequest) -> str:
    """The nearest client address (P20i's agreement record): behind the proxy that terminates TLS
    the first X-Forwarded-For entry; without one, the socket's."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    first = forwarded.split(",")[0].strip() if forwarded else ""
    return (first or request.META.get("REMOTE_ADDR", "") or "unknown")[:64]


@router.get("/csrf", auth=None, response=CsrfOut)
def csrf(request: HttpRequest) -> CsrfOut:
    """Sets the CSRF cookie and returns the token the client must echo in X-CSRFToken."""
    return CsrfOut(csrf_token=get_token(request))


@router.post("/logout", auth=signout_session_auth, response=SignedOutOut)
def logout_view(request: HttpRequest) -> SignedOutOut:
    """Sign out. The portal session ends here; the answer carries the form the browser posts to
    Keycloak's end-session endpoint, which ends the Keycloak session too (and so every other
    application signed in with it, by back-channel logout: the drafting tool's sessions in that
    Keycloak session end that way since P25b)."""
    data = sessions.data_of(request) or {}
    form = None
    if data.get("id_token"):
        try:
            form = EndSessionOut(**oidc.end_session(str(data["id_token"])))
        except oidc.SignInFailed as exc:
            logger.info("signing out without Keycloak's end-session address: %s", exc)
    sessions.end(request)
    return SignedOutOut(end_session=form)


@router.get("/me", auth=setup_session_auth, response=MeOut)
def me(request: HttpRequest) -> MeOut:
    return me_out(request.user)


@router.patch("/me", auth=setup_session_auth, response=MeOut)
def update_me(request: HttpRequest, payload: MeUpdate) -> MeOut:
    """One's own name, time zone and title. The name changes on the records profile too, in the
    same transaction (`identity.update_profile`), and on the Keycloak account after it."""
    user: User = request.user
    if payload.timezone is not None:
        try:
            ZoneInfo(payload.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise HttpError(422, f"Unknown time zone {payload.timezone!r}.") from None
    title = None
    if payload.title is not None:
        title = " ".join(payload.title.split())
        if len(title) > TITLE_MAX:
            raise HttpError(422, "That title is too long.")
    identity.update_profile(user, name=payload.name, timezone_name=payload.timezone, title=title)
    return me_out(user)


# ---- what the person has, for Settings → Profile → Security ---------------------------------


class CredentialOut(Schema):
    id: str
    label: str
    created_at: datetime | None = None
    # Recovery codes only: how many are left.
    remaining: int | None = None


class SecurityOut(Schema):
    password_changed_at: datetime | None = None
    passkeys: list[CredentialOut]
    recovery_codes: CredentialOut | None = None
    authenticator_app: CredentialOut | None = None
    # Whether an administrator allows authenticator-app codes (Settings → System → Sign-in).
    authenticator_app_allowed: bool
    # When the passkey was last asked for, and how often the portal asks again.
    verified_at: datetime | None = None
    reverify_hours: int


def _when(millis: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(millis) / 1000, tz=UTC)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def _remaining(credential: dict) -> int | None:
    import json

    try:
        data = json.loads(credential.get("credentialData") or "{}")
    except ValueError:
        return None
    value = data.get("remainingCodes")
    return int(value) if isinstance(value, int) else None


@router.get("/security", response={200: SecurityOut, 503: ErrorOut})
def security(request: HttpRequest):
    """The person's password date, passkeys, recovery codes and authenticator app, as Keycloak
    holds them (labels and dates only; never a secret)."""
    try:
        held = kc_admin.credentials(request.user)
    except kc_admin.KeycloakProblem as exc:
        logger.warning("security: %s", exc)
        return Status(
            503, ErrorOut(detail="Keycloak could not be reached just now. Try again shortly.")
        )
    rules = sessions.current_policy()
    out = SecurityOut(
        passkeys=[],
        authenticator_app_allowed=rules.allow_authenticator_app,
        reverify_hours=rules.reverify_hours,
    )
    for cred in held:
        kind = cred.get("type")
        item = CredentialOut(
            id=str(cred.get("id", "")),
            label=str(cred.get("userLabel") or ""),
            created_at=_when(cred.get("createdDate")),
        )
        if kind == kc_admin.PASSWORD:
            out.password_changed_at = item.created_at
        elif kind in (kc_admin.PASSKEY, kc_admin.PASSWORDLESS):
            out.passkeys.append(item)
        elif kind == kc_admin.RECOVERY_CODES:
            item.remaining = _remaining(cred)
            out.recovery_codes = item
        elif kind == kc_admin.APP_CODE:
            out.authenticator_app = item
    loa = (sessions.data_of(request) or {}).get("loa") or {}
    if loa.get("2"):
        out.verified_at = datetime.fromtimestamp(int(loa["2"]), tz=UTC)
    return Status(200, out)


# ---- the OpenID Connect client: to Keycloak and back ------------------------------------------

NEXT_PREFIX = "keycloak_next:"
# Keycloak's application-initiated actions the portal offers (Settings → Profile → Security).
ACTIONS = {
    "UPDATE_PASSWORD",
    "webauthn-register",
    "CONFIGURE_RECOVERY_AUTHN_CODES",
    "CONFIGURE_TOTP",
}
DELETE_CREDENTIAL = re.compile(r"^delete_credential:[0-9a-f-]{36}$")
# What /signed-out can explain (the SPA has words for each).
REASONS = ("not_invited", "deactivated", "no_sign_in", "refused", "unavailable", "cancelled")


def safe_next(value: str | None) -> str:
    """Only ever a path on this site: one slash, no scheme, no backslash."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


def _signed_out(reason: str) -> HttpResponse:
    from urllib.parse import urlencode

    reason = reason if reason in REASONS else "refused"
    return _no_store(HttpResponseRedirect(f"/signed-out?{urlencode({'state': reason})}"))


class SecondStepsOut(Schema):
    """Which second steps a person may set up (P25c): a passkey always; an authenticator app while
    an administrator allows them (Settings → System → Sign-in)."""

    passkey: bool = True
    authenticator_app: bool


@router.get("/second-steps", auth=None, response=SecondStepsOut)
def second_steps(request: HttpRequest) -> SecondStepsOut:
    """Asked by the sign-in pages (the sclu theme, on the portal's own address), so that the page
    where someone sets up their second step offers an authenticator app only while Settings
    allows one. Settings' switch, nothing about any person."""
    return SecondStepsOut(authenticator_app=sessions.current_policy().allow_authenticator_app)


@router.get("/oidc/login", auth=None, include_in_schema=True)
def oidc_login(
    request: HttpRequest,
    next: str = "/",  # noqa: A002 - the query parameter's name
    kc_action: str = "",
    login_hint: str = "",
    expired: int = 0,
    second_step: str = "",
):
    """To Keycloak's sign-in (a full page load, not a fetch). `next` is where to come back to;
    `kc_action` asks Keycloak for one of its own credential pages instead (signed-in people
    only); `login_hint` and `expired` (a flag; the hours are Settings') fill in the "signed out
    after N hours" page (005); `second_step=app` (P25c) is someone setting up their first second
    step who chose an authenticator app on the passkey page, honoured while Settings allows apps
    (otherwise the sign-in is the usual one, and its first second step a passkey)."""
    action = ""
    if kc_action and request.user.is_authenticated:
        if kc_action in ACTIONS or DELETE_CREDENTIAL.match(kc_action):
            action = kc_action
        else:
            raise HttpError(422, "Not an action the portal offers.")
    policy = sessions.current_policy()
    state = secrets.token_urlsafe(24)
    request.session[NEXT_PREFIX + state] = safe_next(next)
    try:
        response = oidc.start(
            request,
            state=state,
            kc_action=action,
            login_hint=login_hint.strip()[:254],
            expired_hours=policy.session_hours if expired else 0,
            app_second_step=second_step == "app" and policy.allow_authenticator_app,
        )
    except oidc.SignInFailed as exc:
        logger.warning("sign-in could not start: %s", exc)
        request.session.pop(NEXT_PREFIX + state, None)
        return _signed_out("unavailable")
    return _no_store(response)


def _person_for(claims: dict) -> tuple[User | None, str]:
    """Who this is in the portal, or why nobody: by the Keycloak account the portal linked
    (`keycloak_sub`); the first time only, an invited person by the address Keycloak verified.
    The portal's database decides; nothing in the token does but the identity."""
    sub = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").strip().lower()
    user = User.objects.filter(keycloak_sub=sub).first() if sub else None
    if user is None:
        candidate = None
        if email and claims.get("email_verified") is True:
            candidate = User.objects.filter(email=email, keycloak_sub__isnull=True).first()
        if candidate is None or invites.pending_invite(candidate) is None:
            return None, "not_invited"
        # Through `_link`, so the drafting tool's wave lists are sent again now that the person
        # can be named there (P25b).
        from portal.core.keycloak.admin import _link

        _link(candidate, sub)
        user = candidate
    if records_role_of(user) not in roles.TEAM:
        return None, "no_sign_in"
    if not user.is_active:
        if not invites.accept_on_first_sign_in(user):
            return None, "deactivated"
    elif invites.pending_invite(user) is not None:
        invites.accept_on_first_sign_in(user)
    return user, ""


@router.get("/oidc/callback", auth=None, include_in_schema=True)
def oidc_callback(request: HttpRequest):
    """Back from Keycloak. Checked (state, PKCE, nonce, the ID token, the second step), the person
    found, then signed in here with a portal session of their own."""
    from urllib.parse import urlencode

    state = request.GET.get("state", "")
    back = safe_next(request.session.pop(NEXT_PREFIX + state, "/") if state else "/")
    action_status = request.GET.get("kc_action_status", "")
    if action_status and request.GET.get("error") and request.user.is_authenticated:
        # A credential page the person left with Cancel: back where they were, nothing changed.
        joiner = "&" if "?" in back else "?"
        return _no_store(HttpResponseRedirect(f"{back}{joiner}{urlencode({'done': 'cancelled'})}"))
    try:
        tokens = oidc.finish(request)
    except oidc.SignInFailed as exc:
        logger.info("sign-in refused at the callback: %s", exc)
        return _signed_out(exc.code)
    person, why = _person_for(tokens.claims)
    if person is None:
        logger.info("sign-in refused for sub %s: %s", tokens.claims.get("sub"), why)
        if request.user.is_authenticated:
            sessions.end(request)
        try:
            return _no_store(HttpResponseRedirect(oidc.end_session_url(tokens.id_token, why)))
        except oidc.SignInFailed:
            return _signed_out(why)
    if request.user.is_authenticated and request.user.pk != person.pk:
        sessions.end(request)
    login(request, person, backend="django.contrib.auth.backends.ModelBackend")
    sessions.begin(request, person, tokens)
    # P25c: a sign-in may have set up a second step (the first one, a passkey Keycloak asked for,
    # one added from Settings), so the account is brought in line after it, by the queue: the
    # passkey offered first next time, the passkey or recovery codes owed or no longer owed.
    from portal.core.keycloak import sync as kc_sync
    from portal.core.models import KeycloakChange

    kc_sync.enqueue(person, KeycloakChange.SYNC)
    if action_status:
        joiner = "&" if "?" in back else "?"
        back = f"{back}{joiner}{urlencode({'done': action_status})}"
    return _no_store(HttpResponseRedirect(back))


@router.post("/oidc/backchannel-logout", auth=None, include_in_schema=True)
def backchannel_logout(request: HttpRequest):
    """Keycloak says a session is over (OpenID Connect Back-Channel Logout 1.0): the person signed
    out in another application, an administrator ended their sessions, or Keycloak ended them.
    The portal sessions it names end now. 400 for a token that does not pass the spec's checks."""
    token = request.POST.get("logout_token", "")
    try:
        logout_token = oidc.verify_logout_token(token)
    except (oidc.BadLogoutToken, oidc.SignInFailed) as exc:
        logger.warning("back-channel logout refused: %s", exc)
        body = '{"error": "invalid_request"}'
        return _no_store(HttpResponse(body, status=400, content_type="application/json"))
    user = User.objects.filter(keycloak_sub=logout_token.sub).first() if logout_token.sub else None
    if user is not None:
        signins.end_sign_ins(user.pk, signins.KEYCLOAK_LOGOUT, keycloak_sid=logout_token.sid)
    elif logout_token.sid:
        from portal.core.models import SignInSession

        for owner in set(
            SignInSession.objects.filter(keycloak_sid=logout_token.sid).values_list(
                "user_id", flat=True
            )
        ):
            signins.end_sign_ins(owner, signins.KEYCLOAK_LOGOUT, keycloak_sid=logout_token.sid)
    return _no_store(HttpResponse(status=200))
