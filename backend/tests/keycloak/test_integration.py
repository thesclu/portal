"""P25a: the sign-in against the real development Keycloak (keycloak/compose.yml).

Skipped when that Keycloak is not up (like the fork's stack tests): `just keycloak-test` starts it
and runs these. What they hold: the realm keycloak-config-cli applied says what keycloak/realm/
sclu.json says (registration off, email as username, brute-force detection, the password policy,
the flows with their levels of authentication, the portal client confidential with PKCE and
back-channel logout, the placeholders off, the mappers, the service account's few roles); the
portal's admin-API code really drives it (an invited person's account, its attributes and required
actions, Keycloak's invitation email in Mailpit, a role change, a requester switched off, Settings'
rules written and read back); and Keycloak itself never lets a password alone through and locks an
address after five wrong passwords. P25c: authenticator apps are on by default, so a first
sign-in offers the passkey or (asked for by the sign-in, as the theme's "Use an authenticator app
instead" does) an app, each followed by recovery codes; someone with an app signs in with its
code; with the switch off it is the passkey alone. Every person here is synthetic
(`kc-it-*@example.org`) and is deleted from Keycloak afterwards.
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from django.conf import settings

pytestmark = pytest.mark.keycloak_integration

REPO = Path(__file__).resolve().parents[3]
DEV_ENV = REPO / "var" / "keycloak" / "dev.env"
MAILPIT = "http://localhost:8026"


def _dev_env() -> dict[str, str]:
    try:
        lines = DEV_ENV.read_text().splitlines()
    except OSError:
        return {}
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))


def _keycloak_up() -> bool:
    conf = settings.KEYCLOAK
    if not (conf.get("INTERNAL_URL") and conf.get("ADMIN_CLIENT_SECRET") and _dev_env()):
        return False
    try:
        url = f"{conf['INTERNAL_URL']}/realms/{conf['REALM']}/.well-known/openid-configuration"
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


if not _keycloak_up():
    pytest.skip(
        "the development Keycloak is not up (`just keycloak-test` starts it)",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def master():
    """Keycloak's own admin client in the master realm (the bootstrap service account), to read
    what the portal's service account may not (clients, client scopes)."""
    from keycloak import KeycloakAdmin, KeycloakOpenIDConnection

    env = _dev_env()
    connection = KeycloakOpenIDConnection(
        server_url=settings.KEYCLOAK["INTERNAL_URL"] + "/",
        realm_name="sclu",
        user_realm_name="master",
        client_id=env["KC_BOOTSTRAP_ADMIN_CLIENT_ID"],
        client_secret_key=env["KC_BOOTSTRAP_ADMIN_CLIENT_SECRET"],
        grant_type="client_credentials",
    )
    return KeycloakAdmin(connection=connection)


@pytest.fixture
def made(master):
    """Keycloak accounts the test made, deleted afterwards whatever happened."""
    subs: list[str] = []
    yield subs
    for sub in subs:
        try:
            master.delete_user(sub)
        except Exception:  # noqa: BLE001 - already gone is fine
            pass


@pytest.fixture(autouse=True)
def _fresh_clients():
    from portal.core.keycloak import admin as kc_admin
    from portal.core.keycloak import oidc, sessions

    oidc.reset_client()
    kc_admin.reset_client()
    sessions.forget_policy()
    yield
    kc_admin.reset_client()


def _email() -> str:
    return f"kc-it-{uuid.uuid4().hex[:10]}@example.org"


# ---- the realm says what the file says --------------------------------------------------------


def test_discovery_names_the_public_issuer_and_internal_back_channel():
    conf = settings.KEYCLOAK
    doc = httpx.get(
        f"{conf['INTERNAL_URL']}/realms/sclu/.well-known/openid-configuration", timeout=5
    ).json()
    assert doc["issuer"] == conf["ISSUER"] == "http://localhost:5173/auth/realms/sclu"
    assert doc["authorization_endpoint"].startswith("http://localhost:5173/auth/")
    assert doc["token_endpoint"].startswith(conf["INTERNAL_URL"])
    assert doc["jwks_uri"].startswith(conf["INTERNAL_URL"])
    assert "S256" in doc["code_challenge_methods_supported"]
    assert {"password", "second-step"} <= set(doc["acr_values_supported"])
    assert doc["backchannel_logout_supported"] and doc["backchannel_logout_session_supported"]


def test_the_realm_policies(master):
    realm = master.get_realm("sclu")
    assert realm["registrationAllowed"] is False
    assert realm["registrationEmailAsUsername"] is True and realm["loginWithEmailAllowed"]
    assert realm["duplicateEmailsAllowed"] is False and realm["editUsernameAllowed"] is False
    assert realm["resetPasswordAllowed"] is True and realm["rememberMe"] is False
    assert realm["bruteForceProtected"] is True and realm["failureFactor"] == 5
    assert realm["waitIncrementSeconds"] == 900 and realm["permanentLockout"] is False
    policy = realm["passwordPolicy"]
    assert "length(12)" in policy and "passwordBlacklist(common-passwords.txt)" in policy
    assert "notEmail" in policy and "notUsername" in policy
    assert realm["loginTheme"] == "sclu" and realm["emailTheme"] == "sclu"
    assert realm["browserFlow"] == "sclu tools browser"
    assert realm["resetCredentialsFlow"] == "sclu reset credentials"
    assert realm["otpPolicyCodeReusable"] is False
    assert realm["webAuthnPolicyUserVerificationRequirement"] == "required"
    assert realm["webAuthnPolicyPasswordlessPasskeysEnabled"] is False
    assert '"second-step":2' in realm["attributes"]["acr.loa.map"].replace(" ", "")


def test_the_attributes_the_portal_sets_are_editable_by_administrators_only(master):
    profile = master.connection.raw_get("admin/realms/sclu/users/profile").json()
    by_name = {a["name"]: a for a in profile["attributes"]}
    for name in ("sclu_uid", "sclu_role", "sclu_org", "sclu_role_label", "sclu_invited_by"):
        assert by_name[name]["permissions"]["edit"] == ["admin"], name
    for name in ("email", "firstName", "lastName", "username"):
        assert by_name[name]["permissions"]["edit"] == ["admin"], name


def test_the_flows_ask_for_the_password_then_the_second_step_by_level(master):
    for flow, second in (("sclu portal browser", "14400"), ("sclu tools browser", "1800")):
        executions = master.get_authentication_flow_executions(flow)
        providers = [e.get("providerId") for e in executions]
        assert "auth-username-password-form" in providers
        assert providers.count("webauthn-authenticator") == 2  # known, and the first passkey
        # P25c: known, and the first app; recovery codes in known and after either first step.
        assert providers.count("auth-otp-form") == 2
        assert providers.count("auth-recovery-authn-code-form") == 3
        assert "conditional-sub-flow-executed" in providers
        assert "conditional-client-scope" in providers
        branches = [e["displayName"] for e in executions if e.get("authenticationFlow")]
        app = flow.replace(" browser", " first app")
        passkey = flow.replace(" browser", " first passkey")
        assert branches.index(app) < branches.index(passkey), "the app branch decides first"
        levels = {}
        for e in executions:
            if e.get("providerId") == "conditional-level-of-authentication":
                config = master.get_authenticator_config(e["authenticationConfig"])["config"]
                levels[config["loa-condition-level"]] = config["loa-max-age"]
        assert set(levels) == {"1", "2"}
        # Settings may have moved the ages; the defaults are the realm file's.
        assert levels["2"].isdigit() and levels["1"].isdigit(), (flow, second)
    reset = [
        e.get("providerId")
        for e in master.get_authentication_flow_executions("sclu reset credentials")
    ]
    email_at, passkey_at = (
        reset.index("reset-credential-email"),
        reset.index("webauthn-authenticator"),
    )
    assert email_at < passkey_at < reset.index("reset-password")


def test_the_portal_client_the_drafting_client_and_the_placeholders(master):
    clients = {c["clientId"]: c for c in master.get_clients()}
    portal = clients["portal"]
    assert portal["publicClient"] is False and portal["standardFlowEnabled"] is True
    assert portal["directAccessGrantsEnabled"] is False and portal["implicitFlowEnabled"] is False
    assert portal["serviceAccountsEnabled"] is False and portal["fullScopeAllowed"] is False
    attrs = portal["attributes"]
    assert attrs["pkce.code.challenge.method"] == "S256"
    assert attrs["backchannel.logout.url"].endswith("/api/core/auth/oidc/backchannel-logout")
    assert attrs["backchannel.logout.session.required"] == "true"
    assert attrs["minimum.acr.value"] == "second-step"
    assert attrs["post.logout.redirect.uris"] == "http://localhost:5173/signed-out"
    assert portal["redirectUris"] == ["http://localhost:5173/api/core/auth/oidc/callback"]
    flows = {f["alias"]: f["id"] for f in master.get_authentication_flows()}
    assert portal["authenticationFlowBindingOverrides"]["browser"] == flows["sclu portal browser"]
    # P25b: the drafting tool's sign-in service (GoTrue's Keycloak provider): on, confidential,
    # the code flow without PKCE (GoTrue's provider sends none), the tools' flow, the second step,
    # GoTrue's callback, back-channel logout to the tool's backend.
    drafting = clients["drafting"]
    assert drafting["enabled"] is True and drafting["publicClient"] is False
    assert drafting["standardFlowEnabled"] is True and drafting["implicitFlowEnabled"] is False
    assert drafting["directAccessGrantsEnabled"] is False
    assert drafting["serviceAccountsEnabled"] is False and drafting["fullScopeAllowed"] is False
    assert drafting["frontchannelLogout"] is False
    d = drafting["attributes"]
    assert not d.get("pkce.code.challenge.method")
    assert d["minimum.acr.value"] == "second-step" and d["default.acr.values"] == "second-step"
    assert d["backchannel.logout.url"] == (
        "http://host.docker.internal:3001/sclu/keycloak/backchannel-logout"
    )
    assert d["backchannel.logout.session.required"] == "true"
    assert d["post.logout.redirect.uris"] == "http://localhost:3000/login"
    assert drafting["redirectUris"] == ["http://localhost:54321/auth/v1/callback"]
    assert drafting["authenticationFlowBindingOverrides"]["browser"] == flows["sclu tools browser"]
    assert set(drafting["defaultClientScopes"]) == {"acr", "basic", "profile", "email", "sclu"}
    for placeholder in ("chat", "mac-app"):
        assert clients[placeholder]["enabled"] is False, placeholder
        assert clients[placeholder]["attributes"]["pkce.code.challenge.method"] == "S256"
        assert clients[placeholder]["attributes"]["minimum.acr.value"] == "second-step"
    assert clients["mac-app"]["publicClient"] is True
    assert clients["account-console"]["enabled"] is False
    scopes = {scope["name"]: scope for scope in master.get_client_scopes()}
    mappers = {m["name"] for m in scopes["sclu"]["protocolMappers"]}
    assert mappers == {"sclu_role", "sclu_org", "sclu_uid", "sclu_loa"}


def test_the_service_account_holds_only_what_it_needs(master):
    clients = {c["clientId"]: c for c in master.get_clients()}
    account = master.get_client_service_account_user(clients["portal-admin"]["id"])
    management = clients["realm-management"]["id"]
    roles = {r["name"] for r in master.get_client_roles_of_user(account["id"], management)}
    assert roles == {"manage-users", "view-users", "query-users", "manage-realm", "view-realm"}
    assert clients["portal-admin"]["standardFlowEnabled"] is False
    assert clients["portal-admin"]["fullScopeAllowed"] is False


# ---- the portal's admin-API code against the real thing ----------------------------------------


def _mailpit_message_to(address: str) -> dict | None:
    found = httpx.get(f"{MAILPIT}/api/v1/search", params={"query": f"to:{address}"}, timeout=5)
    messages = found.json().get("messages") or []
    if not messages:
        return None
    return httpx.get(f"{MAILPIT}/api/v1/message/{messages[0]['ID']}", timeout=5).json()


@pytest.mark.django_db
def test_an_invited_person_gets_an_account_and_keycloaks_email(admin_user, made, master):
    import time

    from portal.core.keycloak import sync
    from portal.core.services import invites

    email = _email()
    person = invites.invite(admin_user, email=email, name="Kc Integration", records_role="intern")
    assert sync.apply_pending(person.pk) == 2
    person.refresh_from_db()
    made.append(person.keycloak_sub)
    account = master.get_user(person.keycloak_sub)
    assert account["email"] == email and account["emailVerified"] and account["enabled"]
    assert account["attributes"]["sclu_role"] == ["intern"]
    assert account["attributes"]["sclu_uid"] == [str(person.pk)]
    assert account["attributes"]["sclu_invited_by"] == [admin_user.name]
    # P25c: apps allowed (the default), so the account owes nothing of its own: the invitation
    # asks for the password, then a passkey or an app, and the flow for recovery codes after it.
    assert not {"webauthn-register", "CONFIGURE_RECOVERY_AUTHN_CODES"} & set(
        account["requiredActions"]
    )
    message = None
    for _ in range(20):
        message = _mailpit_message_to(email)
        if message:
            break
        time.sleep(0.5)
    assert message is not None, "Keycloak's invitation never reached Mailpit"
    link = re.search(
        r"https?://[^\s\"'<>]+action-token[^\s\"'<>]+", message.get("HTML") or message.get("Text")
    )
    assert link, "the email carries Keycloak's action link"
    target = html.unescape(link.group(0)).replace(
        "http://localhost:5173/auth", settings.KEYCLOAK["INTERNAL_URL"]
    )
    page = httpx.get(target, timeout=10)
    assert page.status_code == 200 and "SCLU" in page.text


@pytest.mark.django_db
def test_a_role_change_and_a_requester_reach_the_real_account(admin_user, made, master):
    from portal.core.keycloak import admin as kc_admin
    from portal.core.services import identity

    person = identity.create_user(email=_email(), name="Kc Role", records_role="staff")
    sub = kc_admin.sync_person(person)
    made.append(sub)
    identity.set_role(admin_user, person, "limited_attorney")
    kc_admin.sync_person(person)
    assert master.get_user(sub)["attributes"]["sclu_role"] == ["limited_attorney"]
    identity.set_role(admin_user, person, "requester")
    kc_admin.sync_person(person)
    assert master.get_user(sub)["enabled"] is False


@pytest.mark.django_db
def test_settings_rules_are_written_and_read_back(master):
    from portal.core.keycloak import admin as kc_admin
    from portal.core.keycloak import policy

    before = policy.current()
    changed = replace(before, reverify_hours=3, reverify_tool_minutes=20, session_hours=7)
    try:
        policy.apply(kc_admin.client(), changed)
        realm = master.get_realm("sclu")
        assert realm["ssoSessionIdleTimeout"] == 7 * 3600
        ages = {}
        for flow in (policy.PORTAL_FLOW, policy.TOOLS_FLOW):
            for e in master.get_authentication_flow_executions(flow):
                if e.get("providerId") == policy.LOA_PROVIDER:
                    config = master.get_authenticator_config(e["authenticationConfig"])["config"]
                    ages[(flow, config["loa-condition-level"])] = config["loa-max-age"]
        assert ages[(policy.PORTAL_FLOW, "2")] == str(3 * 3600)
        assert ages[(policy.TOOLS_FLOW, "2")] == str(20 * 60)
        assert policy.apply(kc_admin.client(), changed) == [], "applying again changes nothing"
    finally:
        policy.apply(kc_admin.client(), before)


# ---- Keycloak itself ---------------------------------------------------------------------------


class _Browser:
    """A cookie jar that sends Keycloak's Secure cookies over the development http address."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}
        self.http = httpx.Client(follow_redirects=False, timeout=10)

    def _take(self, response: httpx.Response) -> httpx.Response:
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            value = rest.split(";")[0]
            if "Max-Age=0" in header or not value:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        return response

    def get(self, url: str, **kw) -> httpx.Response:
        cookie = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        return self._take(self.http.get(url, headers={"Cookie": cookie}, **kw))

    def post(self, url: str, **kw) -> httpx.Response:
        cookie = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        return self._take(self.http.post(url, headers={"Cookie": cookie}, **kw))


def _internal(url: str) -> str:
    return html.unescape(url).replace(
        "http://localhost:5173/auth", settings.KEYCLOAK["INTERNAL_URL"]
    )


def _login_form(browser: _Browser, scope: str = "openid") -> str:
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    page = browser.get(
        f"{settings.KEYCLOAK['INTERNAL_URL']}/realms/sclu/protocol/openid-connect/auth",
        params={
            "client_id": "portal",
            "redirect_uri": "http://localhost:5173/api/core/auth/oidc/callback",
            "response_type": "code",
            "scope": scope,
            "state": "s",
            "nonce": "n",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "acr_values": "second-step",
        },
    )
    assert page.status_code == 200
    return _internal(_action_of(page.text))


def _action_of(page: str) -> str:
    """Where the page's form posts: a plain form's action, or, on a page the sclu theme renders
    in the browser (Keycloakify), the `url.loginAction` of the page context it embeds."""
    found = re.search(r'<form[^>]*action="([^"]+)"', page)
    if found:
        return found.group(1)
    found = re.search(r'"loginAction"\s*:\s*"([^"]+)"', page)
    assert found, "Keycloak's sign-in form"
    return found.group(1).replace("\\/", "/").replace("\\u0026", "&")


def _person_with_password(master, made, *, app_secret: str = "") -> tuple[str, str, str]:
    """A synthetic person with a password and, given `app_secret`, an authenticator app (an OTP
    credential as Keycloak stores one: the raw secret, SHA-1, 6 digits, 30 s)."""
    import json

    email, password = _email(), f"kc-it-{uuid.uuid4().hex}"
    credentials = [{"type": "password", "value": password, "temporary": False}]
    if app_secret:
        credentials.append(
            {
                "type": "otp",
                "userLabel": "Integration app",
                "secretData": json.dumps({"value": app_secret}),
                "credentialData": json.dumps(
                    {
                        "subType": "totp",
                        "digits": 6,
                        "counter": 0,
                        "period": 30,
                        "algorithm": "HmacSHA1",
                    }
                ),
            }
        )
    sub = master.create_user(
        {
            "username": email,
            "email": email,
            "emailVerified": True,
            "enabled": True,
            "firstName": "Kc Password",
            "attributes": {"sclu_role": ["staff"]},
            "credentials": credentials,
        }
    )
    made.append(sub)
    return sub, email, password


def _totp(secret: str, at: float | None = None) -> str:
    """The code an authenticator app shows (RFC 6238 with the realm's policy: HMAC-SHA1 over the
    30-second counter, six digits), keyed as Keycloak keys it: the raw secret's bytes."""
    import hmac
    import struct
    import time

    counter = struct.pack(">Q", int((time.time() if at is None else at) // 30))
    mac = hmac.new(secret.encode(), counter, "sha1").digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def _context_value(page: str, name: str) -> str:
    found = re.search(rf'"{name}"\s*:\s*"([^"]*)"', page)
    assert found, f"{name} in the page's context"
    return found.group(1)


@pytest.fixture
def apps_off(db):
    """Settings' authenticator-app switch off for one test, as an administrator would set it, and
    back to what Settings says afterwards."""
    from portal.core.keycloak import admin as kc_admin
    from portal.core.keycloak import policy

    before = policy.current()
    policy.apply(kc_admin.client(), replace(before, allow_authenticator_app=False))
    yield
    policy.apply(kc_admin.client(), before)


def _first_step_after_the_password(master, made, scope: str) -> tuple[_Browser, httpx.Response]:
    _sub, email, password = _person_with_password(master, made)
    browser = _Browser()
    answer = browser.post(
        _login_form(browser, scope), data={"username": email, "password": password}
    )
    assert answer.status_code in (302, 303) and "code=" not in answer.headers["location"]
    return browser, answer


@pytest.mark.django_db
def test_by_default_a_first_sign_in_offers_a_passkey_or_an_app_then_recovery_codes(master, made):
    from portal.core.keycloak import policy

    assert policy.current().allow_authenticator_app is True
    # The usual sign-in: the passkey (the theme's page offers the app under it).
    _browser, answer = _first_step_after_the_password(master, made, "openid")
    assert "execution=webauthn-register" in answer.headers["location"]
    # The sign-in again that asks for the app: its setup (004), then recovery codes.
    browser, answer = _first_step_after_the_password(master, made, f"openid {policy.APP_SCOPE}")
    assert "execution=CONFIGURE_TOTP" in answer.headers["location"]
    page = browser.get(_internal(answer.headers["location"])).text
    secret = _context_value(page, "totpSecret")
    done = browser.post(
        _internal(_action_of(page)), data={"totp": _totp(secret), "totpSecret": secret}
    )
    # Keycloak draws the next required action straight away: the recovery codes to save.
    assert done.status_code == 200 and '"login-recovery-authn-code-config.ftl"' in done.text
    assert "location" not in done.headers, "the codes before the portal hears of it"


def test_someone_with_an_app_signs_in_with_its_code(master, made):
    secret = uuid.uuid4().hex[:20]
    _sub, email, password = _person_with_password(master, made, app_secret=secret)
    browser = _Browser()
    page = browser.post(_login_form(browser), data={"username": email, "password": password})
    assert page.status_code == 200 and '"login-otp.ftl"' in page.text
    wrong = browser.post(_internal(_action_of(page.text)), data={"otp": "000000"})
    assert wrong.status_code == 200 and "code=" not in wrong.headers.get("location", "")
    right = browser.post(_internal(_action_of(wrong.text)), data={"otp": _totp(secret)})
    assert right.status_code in (302, 303)
    assert right.headers["location"].startswith("http://localhost:5173/api/core/auth/oidc/callback")
    assert "code=" in right.headers["location"]


@pytest.mark.django_db
def test_with_apps_off_a_first_sign_in_is_a_passkey_and_an_app_is_unused(master, made, apps_off):
    from portal.core.keycloak import policy

    for flow in (*policy.CHOICE_FLOWS, policy.PORTAL_FLOW, policy.TOOLS_FLOW):
        for e in master.get_authentication_flow_executions(flow):
            if e.get("displayName") in policy.FIRST_APP_FLOWS.values():
                assert e["requirement"] == "DISABLED", (flow, e["displayName"])
            if flow in policy.CHOICE_FLOWS and e.get("providerId") == policy.OTP_PROVIDER:
                assert e["requirement"] == "DISABLED", flow
    assert master.get_required_action_by_alias(policy.OTP_ACTION)["enabled"] is False
    # Asking for the app changes nothing: the passkey.
    _browser, answer = _first_step_after_the_password(master, made, f"openid {policy.APP_SCOPE}")
    assert "execution=webauthn-register" in answer.headers["location"]
    # Someone who has only an app: it is not asked for; they set up a passkey.
    secret = uuid.uuid4().hex[:20]
    _sub, email, password = _person_with_password(master, made, app_secret=secret)
    browser = _Browser()
    answer = browser.post(_login_form(browser), data={"username": email, "password": password})
    assert answer.status_code in (302, 303), "no code page"
    assert "execution=webauthn-register" in answer.headers["location"]


def test_a_password_alone_never_signs_anyone_in(master, made):
    _sub, email, password = _person_with_password(master, made)
    browser = _Browser()
    answer = browser.post(_login_form(browser), data={"username": email, "password": password})
    # No code for the portal: the next step is enrolling a passkey.
    assert answer.status_code in (302, 303)
    assert "code=" not in answer.headers["location"]
    assert "webauthn-register" in answer.headers["location"]


def test_five_wrong_passwords_lock_the_address(master, made):
    import time

    sub, email, password = _person_with_password(master, made)
    for _ in range(5):
        # More than a second apart: closer together, Keycloak's quick-login check locks the
        # account for a minute after the second failure, before the five-failure rule is reached.
        time.sleep(1.1)
        browser = _Browser()
        browser.post(_login_form(browser), data={"username": email, "password": "wrong-password-0"})
    status = master.connection.raw_get(
        f"admin/realms/sclu/attack-detection/brute-force/users/{sub}"
    ).json()
    assert status["disabled"] is True and status["numFailures"] >= 5
    # Locked: even the right password does not get past the form now.
    browser = _Browser()
    answer = browser.post(_login_form(browser), data={"username": email, "password": password})
    assert answer.status_code == 200 and "webauthn-register" not in answer.headers.get(
        "location", ""
    )
    master.connection.raw_delete(f"admin/realms/sclu/attack-detection/brute-force/users/{sub}")
