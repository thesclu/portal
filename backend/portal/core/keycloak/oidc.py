"""The portal as an OpenID Connect client of Keycloak (P25a), through Authlib.

Backend for frontend: this server runs the authorization code flow and keeps the tokens in its
own session; the browser holds only the portal's session cookie, never a token.

- `start(...)`: the redirect to Keycloak's authorization endpoint. Authlib makes the `state`, the
  `nonce` and the PKCE verifier (S256), keeps them in the session, and builds the address from the
  realm's discovery document (read on the back channel).
- `finish(request)`: at the callback. Authlib checks the state against the session, exchanges the
  code with the verifier, and checks the ID token: the signature against the realm's keys (fetched
  again when an unknown key id appears), `iss`, `exp`, `iat` and the `nonce`. Here: the `iss`
  parameter of the response (RFC 9207, mix-up defence), `aud` and `azp` (Authlib leaves the
  audience to the caller), and that the second step was taken (`acr`, Keycloak's level of
  authentication).
- `refresh(tokens)`: the refresh grant, which keeps Keycloak's session alive while the portal's is
  used and fails once Keycloak has ended it.
- `verify_logout_token(token)`: a back-channel logout token, checked as the OpenID Connect
  Back-Channel Logout 1.0 spec says (section 2.6), with Authlib's JOSE library (joserfc).

Nothing here trusts a claim for the portal's own decisions: who a person is in the portal, their
role and what they may see are the portal's database, as before.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.core.cache import cache
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry

from portal.core.keycloak.config import configured, discovery_url

NAME = "keycloak"
SECOND_STEP = "second-step"  # the realm's ACR for level 2 (`acr.loa.map`)
SCOPE = "openid email profile"
BACKCHANNEL_EVENT = "http://schemas.openid.net/event/backchannel-logout"
# How old a logout token may be (its `iat`), and how long its `jti` is remembered against replay.
LOGOUT_TOKEN_MAX_AGE = 5 * 60
LEEWAY_SECONDS = 60


class SignInFailed(Exception):
    """The callback could not sign anyone in. `code` is what the screen says (`/signed-out?state=
    <code>`): one of the few the SPA has words for."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class BadToken(Exception):
    """A token from Keycloak that did not pass its checks."""


class BadLogoutToken(BadToken):
    """A back-channel logout token that did not pass the spec's checks."""


@lru_cache(maxsize=1)
def _registry(issuer: str, discovery: str, client_id: str, secret: str, timeout: float) -> Any:
    oauth = OAuth()
    oauth.register(
        name=NAME,
        client_id=client_id,
        client_secret=secret,
        server_metadata_url=discovery,
        client_kwargs={
            "scope": SCOPE,
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_basic",
            "timeout": timeout,
        },
    )
    return oauth.create_client(NAME)


def client() -> Any:
    conf = settings.KEYCLOAK
    if not configured(conf):
        raise SignInFailed("unavailable", "Keycloak is not configured for the portal.")
    return _registry(
        conf["ISSUER"],
        discovery_url(conf),
        conf["CLIENT_ID"],
        conf["CLIENT_SECRET"],
        conf["TIMEOUT_SECONDS"],
    )


def reset_client() -> None:
    """Forget the registered client and its cached discovery and keys (tests; a rotated key is
    handled without this, by the refetch on an unknown key id)."""
    _registry.cache_clear()


def callback_url() -> str:
    return f"{settings.PORTAL['PUBLIC_URL']}/api/core/auth/oidc/callback"


def signed_out_url() -> str:
    return f"{settings.PORTAL['PUBLIC_URL']}/signed-out"


def start(
    request: Any,
    *,
    state: str,
    kc_action: str = "",
    login_hint: str = "",
    expired_hours: int = 0,
    app_second_step: bool = False,
):
    """The redirect to Keycloak's sign-in, asking for the second step (`acr_values`). An
    application-initiated action (`kc_action`: UPDATE_PASSWORD, webauthn-register, …) is
    Keycloak's own way to let a signed-in person change a credential on its pages.

    `app_second_step` (P25c): the person chose an authenticator app as their first second step
    (on the sclu theme's passkey page). The sign-in asks for the realm's `sclu-app-second-step`
    scope, which sends someone with no second step yet down the flow's "first app" branch (the
    app's setup, then recovery codes) instead of the passkey's; for anyone who has one already it
    changes nothing. Its token carries no claim for it."""
    from portal.core.keycloak.policy import APP_SCOPE

    extra: dict[str, str] = {"acr_values": SECOND_STEP, "state": state}
    if kc_action:
        extra["kc_action"] = kc_action
    elif app_second_step:
        extra["scope"] = f"{SCOPE} {APP_SCOPE}"
        # Read by the sign-in page only ("Sign in again to set up your authenticator app").
        extra["sclu_second_step"] = "app"
    if login_hint:
        extra["login_hint"] = login_hint
    if expired_hours:
        # Read by the sign-in page only (005: "You were signed out after N hours"); Keycloak
        # ignores parameters it does not know.
        extra["sclu_expired"] = str(expired_hours)
    try:
        return client().authorize_redirect(request, callback_url(), **extra)
    except OAuthError as exc:
        raise SignInFailed("unavailable", str(exc)) from exc
    except Exception as exc:  # discovery not reachable: requests' errors, a bad document
        raise SignInFailed("unavailable", f"Keycloak could not be reached: {exc}") from exc


@dataclass(frozen=True)
class Tokens:
    id_token: str
    access_token: str
    refresh_token: str
    access_expires_at: float
    claims: dict[str, Any]

    def session_data(self) -> dict[str, Any]:
        return {
            "id_token": self.id_token,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
            "sid": self.claims.get("sid", ""),
            "sub": self.claims.get("sub", ""),
            "loa": loa_times(self.claims),
            "acr": self.claims.get("acr", ""),
        }


def loa_times(claims: dict[str, Any]) -> dict[str, int]:
    """When each level of authentication was last done, from Keycloak's own record of it (the
    user-session note `loa-map`, put in the ID token as `sclu_loa` by the realm's mapper):
    {"1": <epoch seconds of the password>, "2": <of the second step>}."""
    raw = claims.get("sclu_loa")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for level, at in raw.items():
        try:
            out[str(level)] = int(at)
        except (TypeError, ValueError):
            continue
    return out


def _tokens(token: dict[str, Any], claims: dict[str, Any]) -> Tokens:
    expires_in = int(token.get("expires_in") or 60)
    return Tokens(
        id_token=str(token.get("id_token", "")),
        access_token=str(token.get("access_token", "")),
        refresh_token=str(token.get("refresh_token", "")),
        access_expires_at=time.time() + expires_in,
        claims=dict(claims),
    )


def finish(request: Any) -> Tokens:
    """The callback: everything Authlib checks, then the response's `iss` and the second step."""
    conf = settings.KEYCLOAK
    error = request.GET.get("error")
    if error:
        raise SignInFailed("cancelled" if error == "access_denied" else "refused", error)
    issuer = request.GET.get("iss")
    if issuer is not None and issuer != conf["ISSUER"]:
        raise SignInFailed("refused", f"The response came from another issuer: {issuer!r}.")
    options = {
        "iss": {"essential": True, "values": [conf["ISSUER"]]},
        "aud": {"essential": True, "values": [conf["CLIENT_ID"]]},
    }
    try:
        token = client().authorize_access_token(
            request, leeway=LEEWAY_SECONDS, claims_options=options
        )
    except OAuthError as exc:  # the state did not match, the code was refused
        raise SignInFailed("refused", str(exc)) from exc
    except JoseError as exc:  # a bad signature, an expired token, a wrong audience or nonce
        raise SignInFailed("refused", f"The ID token was not valid: {exc}") from exc
    claims = token.get("userinfo")
    if not claims:
        raise SignInFailed("refused", "No ID token came back.")
    if claims.get("iss") != conf["ISSUER"]:
        raise SignInFailed("refused", "The ID token's issuer is not the portal's Keycloak.")
    aud = claims.get("aud")
    if conf["CLIENT_ID"] not in (aud if isinstance(aud, list) else [aud]):
        raise SignInFailed("refused", "The ID token is for another client.")
    if claims.get("azp", conf["CLIENT_ID"]) != conf["CLIENT_ID"]:
        raise SignInFailed("refused", "The ID token was issued to another client.")
    if claims.get("acr") != SECOND_STEP or "2" not in loa_times(claims):
        raise SignInFailed("refused", "The sign-in did not take the second step.")
    return _tokens(token, claims)


def refresh(refresh_token: str) -> Tokens | None:
    """The refresh grant. None when Keycloak refuses (its session is over: signed out elsewhere,
    expired, the person switched off); raises `SignInFailed("unavailable")` when Keycloak could not
    be reached, so the caller keeps the session and tries again later."""
    kc = client()
    try:
        token = kc.fetch_access_token(grant_type="refresh_token", refresh_token=refresh_token)
    except OAuthError as exc:
        if exc.error in ("invalid_grant", "unauthorized_client", "invalid_client"):
            return None
        raise SignInFailed("unavailable", str(exc)) from exc
    except Exception as exc:
        raise SignInFailed("unavailable", f"Keycloak could not be reached: {exc}") from exc
    claims: dict[str, Any] = {}
    if token.get("id_token"):
        try:
            claims = dict(_decode(str(token["id_token"])).claims)
        except BadToken:
            return None
        aud = claims.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if claims.get("iss") != settings.KEYCLOAK["ISSUER"]:
            return None
        if settings.KEYCLOAK["CLIENT_ID"] not in audiences:
            return None
    return _tokens(token, claims)


def end_session(id_token: str) -> dict[str, Any]:
    """RP-initiated logout, as a form the browser posts to Keycloak's end-session endpoint (the
    ID token stays out of addresses and access logs): the endpoint and the fields."""
    metadata = client().load_server_metadata()
    return {
        "url": metadata["end_session_endpoint"],
        "fields": {
            "id_token_hint": id_token,
            "client_id": settings.KEYCLOAK["CLIENT_ID"],
            "post_logout_redirect_uri": signed_out_url(),
        },
    }


def end_session_url(id_token: str, state: str) -> str:
    """For a sign-in the portal refused after Keycloak let the person in (not invited,
    deactivated, a requester): the end-session address, so Keycloak's session ends too and the
    browser lands on /signed-out with the reason (`state`, which the end-session endpoint hands
    back). A redirect, so the ID token is in the address: it is the refused person's own, for
    this client, and already spent."""
    from urllib.parse import urlencode

    metadata = client().load_server_metadata()
    query = {
        "client_id": settings.KEYCLOAK["CLIENT_ID"],
        "post_logout_redirect_uri": signed_out_url(),
        "state": state,
    }
    if id_token:
        query["id_token_hint"] = id_token
    return f"{metadata['end_session_endpoint']}?{urlencode(query)}"


# ---- back-channel logout --------------------------------------------------------------------


def _decode(token: str) -> Any:
    kc = client()
    metadata = kc.load_server_metadata()
    algorithms = metadata.get("id_token_signing_alg_values_supported") or ["RS256"]
    registry = JWSRegistry(algorithms=[a for a in algorithms if a != "none"])
    try:
        return jwt.decode(token, KeySet.import_key_set(kc.fetch_jwk_set()), registry=registry)
    except JoseError:
        try:
            return jwt.decode(
                token, KeySet.import_key_set(kc.fetch_jwk_set(force=True)), registry=registry
            )
        except JoseError as exc:
            raise BadLogoutToken(f"bad signature: {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise BadLogoutToken(f"not a JWT: {exc}") from exc


@dataclass(frozen=True)
class LogoutToken:
    sub: str
    sid: str
    jti: str


def verify_logout_token(token: str) -> LogoutToken:
    """OpenID Connect Back-Channel Logout 1.0, 2.6: a signed JWT (never `alg: none`) from the
    realm's keys; `iss` the realm; `aud` the portal's client; `iat` recent; a `jti` not seen
    before; `events` naming the back-channel logout event; a `sub` or a `sid`; no `nonce`."""
    conf = settings.KEYCLOAK
    decoded = _decode(token)
    claims = dict(decoded.claims)
    header = dict(decoded.header)
    if str(header.get("alg", "")).lower() == "none":
        raise BadLogoutToken("unsigned")
    typ = str(header.get("typ", "")).lower()
    if typ and typ not in ("logout+jwt", "jwt"):
        raise BadLogoutToken(f"unexpected typ {typ!r}")
    if claims.get("iss") != conf["ISSUER"]:
        raise BadLogoutToken("wrong issuer")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if conf["CLIENT_ID"] not in audiences:
        raise BadLogoutToken("not for this client")
    now = time.time()
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or iat > now + LEEWAY_SECONDS:
        raise BadLogoutToken("no usable iat")
    if now - iat > LOGOUT_TOKEN_MAX_AGE:
        raise BadLogoutToken("too old")
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp + LEEWAY_SECONDS < now:
        raise BadLogoutToken("expired")
    events = claims.get("events")
    if not isinstance(events, dict) or BACKCHANNEL_EVENT not in events:
        raise BadLogoutToken("not a back-channel logout event")
    if "nonce" in claims:
        raise BadLogoutToken("carries a nonce")
    sub, sid = str(claims.get("sub") or ""), str(claims.get("sid") or "")
    if not sub and not sid:
        raise BadLogoutToken("names neither a person nor a session")
    jti = str(claims.get("jti") or "")
    if not jti:
        raise BadLogoutToken("no jti")
    if not cache.add(f"kc-logout-jti:{jti}", 1, timeout=LOGOUT_TOKEN_MAX_AGE * 2):
        raise BadLogoutToken("replayed")
    return LogoutToken(sub=sub, sid=sid, jti=jti)
