"""A fake Keycloak at the HTTP layer (P25a), for the unit tests.

Authlib and python-keycloak both speak HTTP through `requests`, so `responses` answers for
Keycloak: the realm's discovery document and keys, the token endpoint (authorization code with
PKCE, refresh, client credentials for the admin service account), and the admin API calls the
portal makes, over an in-memory realm. ID tokens and logout tokens are signed with a key the test
holds (or a stranger's, to prove one is refused).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import responses
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

ISSUER = "https://portal.test/auth/realms/sclu"
INTERNAL = "http://keycloak.test/auth"
REALM = f"{INTERNAL}/realms/sclu"
TOKEN = f"{REALM}/protocol/openid-connect/token"
CERTS = f"{REALM}/protocol/openid-connect/certs"
DISCOVERY = f"{REALM}/.well-known/openid-configuration"
ADMIN = f"{INTERNAL}/admin/realms/sclu"
PUBLIC_AUTH = f"{ISSUER}/protocol/openid-connect/auth"
PUBLIC_LOGOUT = f"{ISSUER}/protocol/openid-connect/logout"
CLIENT_ID = "portal"
CLIENT_SECRET = "test-portal-secret-not-real"
ADMIN_CLIENT_SECRET = "test-admin-secret-not-real"

KEYCLOAK_SETTINGS = {
    "ISSUER": ISSUER,
    "INTERNAL_URL": INTERNAL,
    "REALM": "sclu",
    "CLIENT_ID": CLIENT_ID,
    "CLIENT_SECRET": CLIENT_SECRET,
    "ADMIN_CLIENT_ID": "portal-admin",
    "ADMIN_CLIENT_SECRET": ADMIN_CLIENT_SECRET,
    "ADMIN_CONSOLE_URL": "http://keycloak.test/auth/admin/sclu/console/",
    "TIMEOUT_SECONDS": 5.0,
}


def new_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig", "alg": "RS256"})


def sign(claims: dict[str, Any], key: RSAKey, *, typ: str = "JWT") -> str:
    header = {"alg": "RS256", "kid": key.kid, "typ": typ}
    return jwt.encode(header, claims, key)


def s256(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


@dataclass
class Grant:
    """One authorization the fake browser completed: the code, and what the request asked."""

    code: str
    nonce: str
    challenge: str
    sub: str
    claims: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeKeycloak:
    key: RSAKey = field(default_factory=lambda: new_key("test-key-1"))
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    credentials: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    grants: dict[str, Grant] = field(default_factory=dict)
    refresh_ok: bool = True
    admin_down: bool = False  # every admin call answers 503 while True
    token_down: bool = False  # the token endpoint answers 503 for people's tokens while True
    admin_calls: list[tuple[str, str, Any]] = field(default_factory=list)
    emails: list[tuple[str, list[str], dict[str, list[str]]]] = field(default_factory=list)
    logouts: list[str] = field(default_factory=list)
    token_requests: list[dict[str, list[str]]] = field(default_factory=list)
    realm: dict[str, Any] = field(
        default_factory=lambda: {
            "realm": "sclu",
            "ssoSessionIdleTimeout": 28800,
            "ssoSessionMaxLifespan": 86400,
        }
    )
    configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    executions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    required_actions: list[dict[str, Any]] = field(default_factory=list)
    moves: list[tuple[str, str, str]] = field(default_factory=list)  # (sub, moved, after)

    def __post_init__(self) -> None:
        for flow, ages in (
            ("sclu portal browser", ("86400", "14400")),
            ("sclu tools browser", ("86400", "1800")),
        ):
            rows = []
            for level, age in zip(("1", "2"), ages, strict=True):
                config_id = f"{flow}-loa-{level}"
                self.configs[config_id] = {
                    "id": config_id,
                    "alias": config_id,
                    "config": {"loa-condition-level": level, "loa-max-age": age},
                }
                rows.append(
                    {
                        "id": f"{config_id}-exec",
                        "providerId": "conditional-level-of-authentication",
                        "authenticationConfig": config_id,
                        "requirement": "REQUIRED",
                    }
                )
            # As keycloak/realm/sclu.json leaves them (P25c: authenticator apps on): the branch
            # someone with no second step yet takes after choosing an app, and its own OTP form,
            # REQUIRED (it sets the app up), which is never the policy's to change.
            rows.append(
                {
                    "id": f"{flow}-first-app",
                    "authenticationFlow": True,
                    "displayName": flow.replace(" browser", " first app"),
                    "requirement": "CONDITIONAL",
                    "priority": 30,
                }
            )
            rows.append(
                {
                    "id": f"{flow}-first-app-otp",
                    "providerId": "auth-otp-form",
                    "requirement": "REQUIRED",
                    "priority": 30,
                }
            )
            self.executions[flow] = rows
            # The second step a person already has: the app among its alternatives.
            self.executions[flow.replace(" browser", " second step known")] = [
                {
                    "id": f"{flow}-otp",
                    "providerId": "auth-otp-form",
                    "requirement": "ALTERNATIVE",
                    "priority": 30,
                }
            ]
        self.executions["sclu reset second step"] = [
            {
                "id": "reset-otp",
                "providerId": "auth-otp-form",
                "requirement": "ALTERNATIVE",
                "priority": 20,
            }
        ]
        self.required_actions = [
            {"alias": "CONFIGURE_TOTP", "name": "Configure OTP", "enabled": True},
            {"alias": "webauthn-register", "name": "Webauthn Register", "enabled": True},
        ]

    # ---- documents -------------------------------------------------------------------------

    def discovery(self) -> dict[str, Any]:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": PUBLIC_AUTH,
            "token_endpoint": TOKEN,
            "jwks_uri": CERTS,
            "end_session_endpoint": PUBLIC_LOGOUT,
            "id_token_signing_alg_values_supported": ["RS256"],
            "backchannel_logout_supported": True,
            "backchannel_logout_session_supported": True,
            "acr_values_supported": ["password", "second-step"],
        }

    def jwks(self) -> dict[str, Any]:
        return KeySet([self.key]).as_dict(private=False)

    # ---- tokens ------------------------------------------------------------------------------

    def id_claims(self, grant: Grant, **override: Any) -> dict[str, Any]:
        now = int(time.time())
        user = self.users.get(grant.sub, {})
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "azp": CLIENT_ID,
            "sub": grant.sub,
            "iat": now,
            "exp": now + 300,
            "auth_time": now,
            "nonce": grant.nonce,
            "sid": f"sid-{grant.sub}",
            "acr": "second-step",
            "sclu_loa": json.dumps({"1": now, "2": now}),
            "email": user.get("email", ""),
            "email_verified": True,
            "name": user.get("firstName", ""),
        }
        claims.update(grant.claims)
        claims.update(override)
        return {k: v for k, v in claims.items() if v is not None}

    def authorize(self, location: str, sub: str, **claims: Any) -> tuple[str, str]:
        """What Keycloak would do with the browser sent to `location` (the portal's redirect):
        a code for `sub`, remembering the nonce and the PKCE challenge. Returns (code, state)."""
        query = parse_qs(urlparse(location).query)
        code = f"code-{uuid.uuid4().hex[:8]}"
        self.grants[code] = Grant(
            code=code,
            nonce=query["nonce"][0],
            challenge=query["code_challenge"][0],
            sub=sub,
            claims=claims,
        )
        return code, query["state"][0]

    def logout_token(self, *, sub: str = "", sid: str = "", key: RSAKey | None = None, **extra):
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 120,
            "jti": uuid.uuid4().hex,
            "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
        }
        if sub:
            claims["sub"] = sub
        if sid:
            claims["sid"] = sid
        claims.update(extra)
        claims = {k: v for k, v in claims.items() if v is not None}
        return sign(claims, key or self.key, typ="logout+jwt")

    def _token(self, request: Any) -> tuple[int, dict[str, str], str]:
        form = parse_qs(request.body if isinstance(request.body, str) else request.body.decode())
        self.token_requests.append(form)
        grant_type = form.get("grant_type", [""])[0]
        if grant_type == "client_credentials":
            if self.admin_down:
                return 503, {}, "unavailable"
            body = {"access_token": "admin-access", "expires_in": 300, "token_type": "Bearer"}
            return 200, {"Content-Type": "application/json"}, json.dumps(body)
        if self.token_down and grant_type != "client_credentials":
            return 503, {}, "unavailable"
        if grant_type == "authorization_code":
            grant = self.grants.pop(form.get("code", [""])[0], None)
            verifier = form.get("code_verifier", [""])[0]
            if grant is None or not verifier or s256(verifier) != grant.challenge:
                error = {"error": "invalid_grant", "error_description": "PKCE or code refused"}
                return 400, {"Content-Type": "application/json"}, json.dumps(error)
            key = grant.claims.pop("_key", None) or self.key
            id_token = sign(self.id_claims(grant), key)
            body = {
                "access_token": f"access-{grant.sub}",
                "refresh_token": f"refresh-{grant.sub}",
                "id_token": id_token,
                "expires_in": 300,
                "token_type": "Bearer",
            }
            return 200, {"Content-Type": "application/json"}, json.dumps(body)
        if grant_type == "refresh_token":
            if not self.refresh_ok:
                error = {"error": "invalid_grant", "error_description": "Session not active"}
                return 400, {"Content-Type": "application/json"}, json.dumps(error)
            sub = form.get("refresh_token", ["refresh-"])[0].removeprefix("refresh-")
            grant = Grant(code="", nonce="", challenge="", sub=sub)
            claims = self.id_claims(grant, nonce=None)
            body = {
                "access_token": f"access-{sub}-2",
                "refresh_token": f"refresh-{sub}",
                "id_token": sign(claims, self.key),
                "expires_in": 300,
                "token_type": "Bearer",
            }
            return 200, {"Content-Type": "application/json"}, json.dumps(body)
        return 400, {}, json.dumps({"error": "unsupported_grant_type"})

    # ---- the admin API ------------------------------------------------------------------------

    def _admin(self, request: Any) -> tuple[int, dict[str, str], str]:
        url = urlparse(request.url)
        path = url.path.removeprefix(urlparse(ADMIN).path)
        body = json.loads(request.body) if request.body else None
        self.admin_calls.append((request.method, path, body))
        if self.admin_down:
            return 503, {}, "unavailable"
        query = parse_qs(url.query)
        json_type = {"Content-Type": "application/json"}

        def ok(value: Any = None, status: int = 200) -> tuple[int, dict[str, str], str]:
            return status, json_type, json.dumps(value) if value is not None else ""

        m = re.fullmatch(r"/users/([^/]+)(/.*)?", path)
        if path == "/users" and request.method == "GET":
            email = query.get("email", [""])[0].lower()
            found = [u for u in self.users.values() if u["email"].lower() == email]
            first = int(query.get("first", ["0"])[0])
            return ok(found[first:])
        if path == "/users" and request.method == "POST":
            if any(u["email"].lower() == body["email"].lower() for u in self.users.values()):
                return ok({"errorMessage": "User exists with same email"}, 409)
            sub = str(uuid.uuid4())
            user = {k: v for k, v in body.items() if k != "credentials"}
            user.update({"id": sub, "requiredActions": body.get("requiredActions", [])})
            self.users[sub] = user
            self.credentials[sub] = [
                {"id": str(uuid.uuid4()), "type": "password", "createdDate": 1_700_000_000_000}
                for c in body.get("credentials", [])
            ]
            return 201, {"Location": f"{ADMIN}/users/{sub}"}, ""
        if m:
            sub, rest = m.group(1), m.group(2) or ""
            user = self.users.get(sub)
            if user is None:
                return ok({"error": "User not found"}, 404)
            if rest == "" and request.method == "GET":
                return ok(user)
            if rest == "" and request.method == "PUT":
                user.update(body)
                return ok(status=204)
            if rest == "/credentials" and request.method == "GET":
                return ok(self.credentials.get(sub, []))
            mm = re.fullmatch(r"/credentials/([^/]+)/(?:moveAfter/([^/]+)|moveToFirst)", rest)
            if mm and request.method == "POST":
                held = self.credentials.get(sub, [])
                moving = next((c for c in held if c["id"] == mm.group(1)), None)
                after = mm.group(2)
                if moving is None or (after and not any(c["id"] == after for c in held)):
                    return ok({"error": "Credential not found"}, 404)
                held.remove(moving)
                at = next(i for i, c in enumerate(held) if c["id"] == after) + 1 if after else 0
                held.insert(at, moving)
                self.moves.append((sub, mm.group(1), after or ""))
                return ok(status=204)
            cm = re.fullmatch(r"/credentials/([^/]+)", rest)
            if cm and request.method == "DELETE":
                self.credentials[sub] = [
                    c for c in self.credentials.get(sub, []) if c["id"] != cm.group(1)
                ]
                return ok(status=204)
            if rest == "/execute-actions-email" and request.method == "PUT":
                self.emails.append((sub, body, query))
                return ok(status=204)
            if rest == "/logout" and request.method == "POST":
                self.logouts.append(sub)
                return ok(status=204)
            if rest == "/reset-password" and request.method == "PUT":
                return ok(status=204)
        if path == "" and request.method == "GET":
            return ok(self.realm)
        if path == "" and request.method == "PUT":
            self.realm.update(body)
            return ok(status=204)
        fm = re.fullmatch(r"/authentication/flows/([^/]+)/executions", path)
        if fm:
            from urllib.parse import unquote

            flow = unquote(fm.group(1))
            if request.method == "GET":
                return ok(self.executions.get(flow, []))
            for row in self.executions.get(flow, []):
                if row["id"] == body["id"]:
                    # As Keycloak 26.8: a priority left out of the body becomes 0.
                    row["requirement"] = body["requirement"]
                    row["priority"] = body.get("priority", 0)
            return ok(status=204)
        am = re.fullmatch(r"/authentication/config/([^/]+)", path)
        if am:
            from urllib.parse import unquote

            config_id = unquote(am.group(1))
            if request.method == "GET":
                return ok(self.configs[config_id])
            self.configs[config_id] = body
            return ok(status=204)
        if path == "/authentication/required-actions" and request.method == "GET":
            return ok(self.required_actions)
        rm = re.fullmatch(r"/authentication/required-actions/([^/]+)", path)
        if rm and request.method == "PUT":
            for row in self.required_actions:
                if row["alias"] == rm.group(1):
                    row.update(body)
            return ok(status=204)
        return ok({"error": f"fake has no {request.method} {path}"}, 404)

    # ---- wiring --------------------------------------------------------------------------

    def install(self, mock: responses.RequestsMock) -> None:
        mock.add(responses.GET, DISCOVERY, json=self.discovery())
        mock.add_callback(
            responses.GET, CERTS, callback=lambda r: (200, {}, json.dumps(self.jwks()))
        )
        mock.add_callback(responses.POST, TOKEN, callback=self._token)
        for method in ("GET", "POST", "PUT", "DELETE"):
            mock.add_callback(
                method, re.compile(re.escape(ADMIN) + r"(/.*)?(\?.*)?$"), callback=self._admin
            )

    def add_user(self, email: str, *, enabled: bool = True, **extra: Any) -> str:
        sub = str(uuid.uuid4())
        self.users[sub] = {
            "id": sub,
            "username": email,
            "email": email,
            "enabled": enabled,
            "attributes": {},
            "requiredActions": [],
            **extra,
        }
        self.credentials[sub] = []
        return sub
