"""P25a: sign-in through Keycloak, with Keycloak faked at the HTTP layer (`keycloak_fake.py`).

The portal is an OpenID Connect client (Authlib) and drives Keycloak's admin API
(python-keycloak); both speak HTTP through `requests`, which `responses` answers. Covered: the
redirect (state, nonce, PKCE S256, the second step asked for), the callback's every check (state,
PKCE, nonce, signature, expiry, issuer, the second step), finding the person (by sub; an invited
person once by verified email; nobody else; never a deactivated person or a requester), the
portal session's life (refresh, Keycloak ending it, re-verification, the idle limit), RP-initiated
and back-channel logout (the logout token checked per spec), and Users driving Keycloak through
queued, retried changes whose state Users shows.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from django.contrib.sessions.models import Session
from django.test import Client
from django.utils import timezone
from portal.core.keycloak import admin as kc_admin
from portal.core.keycloak import oidc, sessions
from portal.core.keycloak import sync as kc_sync
from portal.core.models import Invite, KeycloakChange, Setting, SignInSession, User
from portal.core.services import identity, invites

from tests.core.keycloak_fake import (
    ADMIN,
    ISSUER,
    KEYCLOAK_SETTINGS,
    PUBLIC_AUTH,
    PUBLIC_LOGOUT,
    FakeKeycloak,
    new_key,
    s256,
)

LOGIN = "/api/core/auth/oidc/login"
CALLBACK = "/api/core/auth/oidc/callback"
BACKCHANNEL = "/api/core/auth/oidc/backchannel-logout"


@pytest.fixture
def kc(settings):
    settings.KEYCLOAK = dict(KEYCLOAK_SETTINGS)
    oidc.reset_client()
    kc_admin.reset_client()
    sessions.forget_policy()
    fake = FakeKeycloak()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        fake.install(mock)
        yield fake
    oidc.reset_client()
    kc_admin.reset_client()
    sessions.forget_policy()


def _linked(fake: FakeKeycloak, user: User) -> str:
    sub = fake.add_user(user.email, firstName=user.name)
    User.objects.filter(pk=user.pk).update(keycloak_sub=sub)
    user.keycloak_sub = sub
    return sub


def _start(client, next_path: str = "/docket", **params) -> str:
    response = client.get(LOGIN, {"next": next_path, **params})
    assert response.status_code == 302, response.content
    return response["Location"]


def _sign_in(client, fake: FakeKeycloak, sub: str, next_path: str = "/docket", **claims):
    location = _start(client, next_path)
    code, state = fake.authorize(location, sub, **claims)
    return client.get(CALLBACK, {"code": code, "state": state, "iss": ISSUER})


# ---- the redirect ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_redirects_to_keycloak_with_state_nonce_pkce_and_the_second_step(client, kc):
    location = _start(client, "/docket", login_hint="a@example.org", expired=1)
    url = urlparse(location)
    assert f"{url.scheme}://{url.netloc}{url.path}" == PUBLIC_AUTH
    query = parse_qs(url.query)
    assert query["client_id"] == ["portal"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["http://localhost:5173/api/core/auth/oidc/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    assert query["nonce"][0] and query["state"][0]
    assert query["acr_values"] == ["second-step"]
    assert query["login_hint"] == ["a@example.org"]
    assert query["sclu_expired"] == ["8"], "the hours are Settings' session length"
    assert "openid" in query["scope"][0].split()
    # No token, code or secret ever reaches the browser: only the portal's own session cookie.
    assert "client_secret" not in location


@pytest.mark.django_db
def test_an_action_is_only_for_a_signed_in_person_and_only_the_offered_ones(client, kc, staff_user):
    anonymous = parse_qs(urlparse(_start(client, kc_action="UPDATE_PASSWORD")).query)
    assert "kc_action" not in anonymous
    client.force_login(staff_user)
    signed_in = parse_qs(
        urlparse(_start(client, "/settings/profile", kc_action="webauthn-register")).query
    )
    assert signed_in["kc_action"] == ["webauthn-register"]
    deleting = (
        "delete_credential:0f3b8a3e-1c0b-4c7e-9c49-0b1e8a2f6d11"  # gitleaks:allow (an example id)
    )
    assert parse_qs(urlparse(_start(client, kc_action=deleting)).query)["kc_action"] == [deleting]
    assert client.get(LOGIN, {"kc_action": "delete_account"}).status_code == 422


@pytest.mark.django_db
def test_choosing_an_authenticator_app_asks_keycloak_for_its_setup(client, kc):
    # P25c: the passkey page's "Use an authenticator app instead" is a sign-in again asking for
    # the realm's scope that sends someone with no second step yet to the app's setup.
    query = parse_qs(urlparse(_start(client, "/", second_step="app")).query)
    assert query["scope"][0].split() == ["openid", "email", "profile", "sclu-app-second-step"]
    assert query["sclu_second_step"] == ["app"], "the sign-in page says why it asks again"
    assert query["acr_values"] == ["second-step"] and query["nonce"][0]
    plain = parse_qs(urlparse(_start(client, "/")).query)
    assert "sclu-app-second-step" not in plain["scope"][0] and "sclu_second_step" not in plain
    # Off in Settings: the usual sign-in, whose first second step is a passkey.
    Setting.objects.filter(pk="auth.allow_authenticator_app").update(value=False)
    sessions.forget_policy()
    off = parse_qs(urlparse(_start(client, "/", second_step="app")).query)
    assert "sclu-app-second-step" not in off["scope"][0] and "sclu_second_step" not in off


@pytest.mark.django_db
def test_every_sign_in_brings_the_account_in_line_afterwards(client, kc, staff_user):
    # P25c: a sign-in may have set up a second step (a first one, a passkey added from Settings),
    # and the sync that follows puts the passkey first.
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    assert KeycloakChange.objects.filter(user=staff_user, action="sync", state="pending").exists()
    _holding(kc, sub, "password", "otp", "recovery-authn-codes", "webauthn")
    _drain(staff_user)
    assert [c["type"] for c in kc.credentials[sub]][:2] == ["password", "webauthn"]


@pytest.mark.django_db
def test_login_when_keycloak_cannot_be_reached_says_so(client, settings):
    settings.KEYCLOAK = {**KEYCLOAK_SETTINGS, "INTERNAL_URL": "http://keycloak.test/auth"}
    oidc.reset_client()
    with responses.RequestsMock(assert_all_requests_are_fired=False):
        response = client.get(LOGIN)
    oidc.reset_client()
    assert response.status_code == 302
    assert response["Location"] == "/signed-out?state=unavailable"


# ---- the callback ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_linked_person_signs_in_and_gets_a_portal_session_tied_to_keycloaks(
    client, kc, staff_user
):
    sub = _linked(kc, staff_user)
    response = _sign_in(client, kc, sub, "/docket")
    assert response.status_code == 302
    assert response["Location"] == "/docket"
    me = client.get("/api/core/auth/me")
    assert me.status_code == 200 and me.json()["email"] == staff_user.email
    row = SignInSession.objects.get(user=staff_user)
    assert row.keycloak_sid == f"sid-{sub}"
    data = client.session[sessions.SESSION_KEY]
    assert data["sub"] == sub and data["id_token"] and data["refresh_token"]
    assert set(data["loa"]) == {"1", "2"}
    assert client.session.get_expiry_age() == 8 * 3600
    # The PKCE verifier the portal sent hashes to the challenge it sent Keycloak.
    sent = kc.token_requests[-1]
    assert sent["grant_type"] == ["authorization_code"] and sent["code_verifier"][0]


@pytest.mark.django_db
def test_an_invited_person_is_found_once_by_the_verified_address_and_switched_on(
    client, kc, admin_user
):
    person = invites.invite(
        admin_user, email="new@example.org", name="New Person", records_role="staff"
    )
    assert not person.is_active and person.keycloak_sub is None
    sub = kc.add_user("new@example.org")
    response = _sign_in(client, kc, sub, "/")
    assert response.status_code == 302 and response["Location"] == "/"
    person.refresh_from_db()
    assert person.is_active and person.keycloak_sub == sub
    assert Invite.objects.get(user=person).accepted_at is not None


@pytest.mark.django_db
def test_an_unknown_sub_with_an_address_nobody_invited_is_refused(client, kc, staff_user):
    # The address is a portal person's, but they were never invited: no linking by address.
    sub = kc.add_user(staff_user.email)
    response = _sign_in(client, kc, sub)
    assert response.status_code == 302
    location = urlparse(response["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == PUBLIC_LOGOUT
    query = parse_qs(location.query)
    assert query["state"] == ["not_invited"] and query["id_token_hint"][0]
    assert query["post_logout_redirect_uri"] == ["http://localhost:5173/signed-out"]
    assert client.get("/api/core/auth/me").status_code == 401
    staff_user.refresh_from_db()
    assert staff_user.keycloak_sub is None


@pytest.mark.django_db
def test_an_unverified_address_never_links(client, kc, admin_user):
    invites.invite(admin_user, email="new@example.org", name="New", records_role="staff")
    sub = kc.add_user("new@example.org")
    response = _sign_in(client, kc, sub, email_verified=False)
    assert parse_qs(urlparse(response["Location"]).query)["state"] == ["not_invited"]


@pytest.mark.django_db
def test_a_deactivated_person_is_refused(client, kc, staff_user, admin_user):
    sub = _linked(kc, staff_user)
    identity.deactivate_user(admin_user, staff_user, reassign_tasks_to=None)
    response = _sign_in(client, kc, sub)
    assert parse_qs(urlparse(response["Location"]).query)["state"] == ["deactivated"]
    assert client.get("/api/core/auth/me").status_code == 401


@pytest.mark.django_db
def test_a_requester_is_refused(client, kc):
    person = identity.create_user(
        email="member@example.org", name="Member", records_role="requester"
    )
    sub = _linked(kc, person)
    response = _sign_in(client, kc, sub)
    assert parse_qs(urlparse(response["Location"]).query)["state"] == ["no_sign_in"]


@pytest.mark.django_db
def test_a_state_that_does_not_match_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    location = _start(client)
    code, _state = kc.authorize(location, sub)
    response = client.get(CALLBACK, {"code": code, "state": "not-the-state", "iss": ISSUER})
    assert response["Location"] == "/signed-out?state=refused"
    assert client.get("/api/core/auth/me").status_code == 401


@pytest.mark.django_db
def test_a_pkce_verifier_that_does_not_match_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    location = _start(client)
    code, state = kc.authorize(location, sub)
    kc.grants[code].challenge = s256("some other verifier")  # Keycloak checks S256(verifier)
    response = client.get(CALLBACK, {"code": code, "state": state, "iss": ISSUER})
    assert response["Location"] == "/signed-out?state=refused"


@pytest.mark.django_db
def test_a_nonce_that_does_not_match_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    response = _sign_in(client, kc, sub, nonce="a-replayed-nonce")
    assert response["Location"] == "/signed-out?state=refused"


@pytest.mark.django_db
def test_an_expired_id_token_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    old = int(time.time()) - 3600
    response = _sign_in(client, kc, sub, iat=old, exp=old + 300, auth_time=old)
    assert response["Location"] == "/signed-out?state=refused"


@pytest.mark.django_db
def test_an_id_token_signed_by_another_key_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    response = _sign_in(client, kc, sub, _key=new_key("test-key-1"))  # same kid, other key
    assert response["Location"] == "/signed-out?state=refused"


@pytest.mark.django_db
def test_an_id_token_for_another_client_or_issuer_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    assert _sign_in(client, kc, sub, aud="drafting")["Location"] == "/signed-out?state=refused"
    assert (
        _sign_in(client, kc, sub, iss="https://evil.test/auth/realms/sclu")["Location"]
        == "/signed-out?state=refused"
    )


@pytest.mark.django_db
def test_a_response_from_another_issuer_is_refused_before_the_code_is_used(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    location = _start(client)
    code, state = kc.authorize(location, sub)
    response = client.get(CALLBACK, {"code": code, "state": state, "iss": "https://evil.test"})
    assert response["Location"] == "/signed-out?state=refused"
    assert code in kc.grants, "the code was never sent to the token endpoint"


@pytest.mark.django_db
def test_a_sign_in_without_the_second_step_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    now = int(time.time())
    assert (
        _sign_in(client, kc, sub, acr="password", sclu_loa=json.dumps({"1": now}))["Location"]
        == "/signed-out?state=refused"
    )


@pytest.mark.django_db
def test_keycloak_cancelling_says_so(client, kc):
    location = _start(client)
    state = parse_qs(urlparse(location).query)["state"][0]
    response = client.get(CALLBACK, {"error": "access_denied", "state": state})
    assert response["Location"] == "/signed-out?state=cancelled"


@pytest.mark.django_db
def test_an_application_initiated_action_comes_back_where_it_started(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    location = _start(client, "/settings/profile", kc_action="UPDATE_PASSWORD")
    code, state = kc.authorize(location, sub)
    response = client.get(
        CALLBACK, {"code": code, "state": state, "iss": ISSUER, "kc_action_status": "success"}
    )
    assert response["Location"] == "/settings/profile?done=success"


@pytest.mark.django_db
def test_next_never_leaves_the_site(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    assert _sign_in(client, kc, sub, "https://evil.example/")["Location"] == "/"
    assert _sign_in(client, kc, sub, "//evil.example/")["Location"] == "/"


# ---- the portal session's life ----------------------------------------------------------------


def _expire_access(client) -> None:
    session = client.session
    data = session[sessions.SESSION_KEY]
    data["access_expires_at"] = time.time() - 60
    session[sessions.SESSION_KEY] = data
    session.save()


@pytest.mark.django_db
def test_a_due_access_token_is_refreshed_on_the_next_request(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    _expire_access(client)
    assert client.get("/api/core/auth/me").status_code == 200
    assert kc.token_requests[-1]["grant_type"] == ["refresh_token"]
    assert client.session[sessions.SESSION_KEY]["access_token"] == f"access-{sub}-2"


@pytest.mark.django_db
def test_when_keycloak_has_ended_the_session_the_portal_session_ends_on_that_request(
    client, kc, staff_user
):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    _expire_access(client)
    kc.refresh_ok = False
    assert client.get("/api/core/auth/me").status_code == 401
    assert not SignInSession.objects.filter(user=staff_user).exists()


@pytest.mark.django_db
def test_an_outage_keeps_the_session_and_asks_again_later(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    _expire_access(client)
    kc.token_down = True
    assert client.get("/api/core/auth/me").status_code == 200
    assert client.session[sessions.SESSION_KEY]["retry_after"] > time.time()
    assert SignInSession.objects.filter(user=staff_user).exists()


@pytest.mark.django_db
def test_a_passkey_check_that_is_due_holds_every_route_until_reverified(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    session = client.session
    data = session[sessions.SESSION_KEY]
    data["loa"]["2"] = int(time.time()) - 4 * 3600 - 5
    session[sessions.SESSION_KEY] = data
    session.save()
    answer = client.get("/api/records/requests")
    assert answer.status_code == 401
    assert answer.json()["code"] == "reverify_required"
    assert client.get("/api/core/auth/me").json()["code"] == "reverify_required"
    # Signing out still works, and the passkey check (Keycloak's) clears it.
    _sign_in(client, kc, sub)
    assert client.get("/api/core/auth/me").status_code == 200


@pytest.mark.django_db
def test_the_reverification_age_is_settings(client, kc, staff_user):
    Setting.objects.update_or_create(key="auth.reverify_hours", defaults={"value": 1})
    sessions.forget_policy()
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    session = client.session
    data = session[sessions.SESSION_KEY]
    data["loa"]["2"] = int(time.time()) - 3600 - 5
    session[sessions.SESSION_KEY] = data
    session.save()
    assert client.get("/api/core/auth/me").status_code == 401


@pytest.mark.django_db
def test_the_idle_limit_is_settings_session_length(client, kc, staff_user):
    Setting.objects.update_or_create(key="auth.session_hours", defaults={"value": 3})
    sessions.forget_policy()
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    assert client.session.get_expiry_age() == 3 * 3600


# ---- signing out ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_sign_out_ends_the_portal_session_and_hands_over_keycloaks_end_session(
    client, kc, staff_user
):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    id_token = client.session[sessions.SESSION_KEY]["id_token"]
    csrf = client.get("/api/core/auth/csrf").json()["csrf_token"]
    answer = client.post("/api/core/auth/logout", HTTP_X_CSRFTOKEN=csrf)
    assert answer.status_code == 200
    end = answer.json()["end_session"]
    assert end["url"] == PUBLIC_LOGOUT
    assert end["fields"] == {
        "id_token_hint": id_token,
        "client_id": "portal",
        "post_logout_redirect_uri": "http://localhost:5173/signed-out",
    }
    assert client.get("/api/core/auth/me").status_code == 401
    assert not SignInSession.objects.filter(user=staff_user).exists()


# ---- back-channel logout ----------------------------------------------------------------------


@pytest.mark.django_db
def test_a_back_channel_logout_naming_a_session_ends_that_session_only(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    other_key = Session.objects.create(
        session_key="other-session-key-000000000000000",
        session_data="",
        expire_date=timezone.now() + timedelta(hours=1),
    ).session_key
    SignInSession.objects.create(session_key=other_key, user=staff_user, keycloak_sid="sid-other")
    token = kc.logout_token(sub=sub, sid=f"sid-{sub}")
    answer = Client().post(BACKCHANNEL, {"logout_token": token})
    assert answer.status_code == 200
    assert answer["Cache-Control"] == "no-store"
    assert client.get("/api/core/auth/me").status_code == 401
    assert SignInSession.objects.filter(keycloak_sid="sid-other").exists()


@pytest.mark.django_db
def test_a_back_channel_logout_naming_only_the_person_ends_all_their_sessions(
    client, kc, staff_user
):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    answer = Client().post(BACKCHANNEL, {"logout_token": kc.logout_token(sub=sub)})
    assert answer.status_code == 200
    assert client.get("/api/core/auth/me").status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize(
    "why, make",
    [
        (
            "signed by another key",
            lambda kc, sub: kc.logout_token(sub=sub, key=new_key("test-key-1")),
        ),
        ("for another client", lambda kc, sub: kc.logout_token(sub=sub, aud="drafting")),
        ("from another issuer", lambda kc, sub: kc.logout_token(sub=sub, iss="https://evil.test")),
        ("not the logout event", lambda kc, sub: kc.logout_token(sub=sub, events={"x": {}})),
        ("carrying a nonce", lambda kc, sub: kc.logout_token(sub=sub, nonce="n")),
        (
            "too old",
            lambda kc, sub: kc.logout_token(sub=sub, iat=int(time.time()) - 3600, exp=None),
        ),
        ("naming nobody", lambda kc, sub: kc.logout_token()),
        ("without a jti", lambda kc, sub: kc.logout_token(sub=sub, jti=None)),
        ("not a token", lambda kc, sub: "not.a.jwt"),
    ],
)
def test_a_back_channel_logout_token_that_fails_a_check_is_refused(
    client, kc, staff_user, why, make
):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    answer = Client().post(BACKCHANNEL, {"logout_token": make(kc, sub)})
    assert answer.status_code == 400, why
    assert client.get("/api/core/auth/me").status_code == 200, why


@pytest.mark.django_db
def test_a_replayed_back_channel_logout_token_is_refused(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    token = kc.logout_token(sub=sub)
    assert Client().post(BACKCHANNEL, {"logout_token": token}).status_code == 200
    assert Client().post(BACKCHANNEL, {"logout_token": token}).status_code == 400


# ---- Users drives Keycloak -------------------------------------------------------------------


def _drain(user: User | None) -> int:
    return kc_sync.apply_pending(user.pk if user else None)


@pytest.mark.django_db
def test_an_invite_creates_the_keycloak_account_and_sends_keycloaks_email(kc, admin_user):
    person = invites.invite(
        admin_user, email="new@example.org", name="New Person", records_role="intern"
    )
    assert list(KeycloakChange.objects.filter(user=person).values_list("action", flat=True)) == [
        "sync",
        "invite",
    ]
    assert _drain(person) == 2
    person.refresh_from_db()
    account = kc.users[person.keycloak_sub]
    assert account["email"] == "new@example.org" and account["emailVerified"] is True
    assert account["enabled"] is True, "invited and not joined yet: on, so the link works"
    assert account["firstName"] == "New Person"
    assert account["attributes"]["sclu_role"] == ["intern"]
    assert account["attributes"]["sclu_uid"] == [str(person.pk)]
    assert account["attributes"]["sclu_invited_by"] == [admin_user.name]
    assert account["attributes"]["sclu_role_label"] == ["Intern"]
    # P25c: with authenticator apps allowed (the default) the account owes nothing of its own:
    # the invitation's steps ask for the password, then a passkey or (the person's choice) an app,
    # and Keycloak's flow asks for recovery codes after either.
    assert "webauthn-register" not in account["requiredActions"]
    assert "CONFIGURE_RECOVERY_AUTHN_CODES" not in account["requiredActions"]
    sub, actions, query = kc.emails[-1]
    assert sub == person.keycloak_sub
    assert actions == ["UPDATE_PASSWORD", "webauthn-register", "CONFIGURE_RECOVERY_AUTHN_CODES"]
    assert query["client_id"] == ["portal"] and query["lifespan"] == [str(7 * 24 * 3600)]


@pytest.mark.django_db
def test_with_apps_off_an_invite_owes_a_passkey_as_before(kc, admin_user):
    Setting.objects.filter(pk="auth.allow_authenticator_app").update(value=False)
    person = invites.invite(admin_user, email="off@example.org", name="Off", records_role="staff")
    _drain(person)
    person.refresh_from_db()
    assert "webauthn-register" in kc.users[person.keycloak_sub]["requiredActions"]
    assert kc.emails[-1][1] == [
        "UPDATE_PASSWORD",
        "webauthn-register",
        "CONFIGURE_RECOVERY_AUTHN_CODES",
    ]


def _holding(kc: FakeKeycloak, sub: str, *kinds: str) -> None:
    kc.credentials[sub] = [{"id": f"{kind}-{i}", "type": kind} for i, kind in enumerate(kinds)]


@pytest.mark.django_db
def test_what_an_account_owes_follows_the_authenticator_app_switch(client, kc, admin_user):
    """P25c (DECISIONS 54). On: someone with nothing chooses at their next sign-in (Keycloak's
    flow asks), someone with an app or a passkey owes nothing, someone left with recovery codes
    alone adds a passkey. Switched off: everyone without a passkey adds one at their next sign-in
    (an app-only person after a recovery code; their app stays, unused). On again: undone."""
    people = {}
    for name, kinds in (
        ("nothing", ()),
        ("app", ("password", "otp", "recovery-authn-codes")),
        ("passkey", ("password", "webauthn", "recovery-authn-codes")),
        ("codes", ("password", "recovery-authn-codes")),
        ("used-up", ("password", "otp")),
    ):
        person = identity.create_user(email=f"{name}@example.org", name=name, records_role="staff")
        sub = _linked(kc, person)
        _holding(kc, sub, *kinds)
        people[name] = (person, sub)

    def owed() -> dict[str, set[str]]:
        for person, _sub in people.values():
            kc_sync.enqueue(person, KeycloakChange.SYNC)
            _drain(person)
        return {name: set(kc.users[sub]["requiredActions"]) for name, (_p, sub) in people.items()}

    assert owed() == {
        "nothing": set(),
        "app": set(),
        "passkey": set(),
        "codes": {"webauthn-register"},
        "used-up": {"CONFIGURE_RECOVERY_AUTHN_CODES"},
    }
    client.force_login(admin_user)
    _put_setting(client, "auth.allow_authenticator_app", False)
    synced = set(
        KeycloakChange.objects.filter(action="sync", state="pending").values_list(
            "user_id", flat=True
        )
    )
    assert {person.pk for person, _sub in people.values()} <= synced, "everyone is re-synced"
    assert owed() == {
        "nothing": {"webauthn-register"},
        "app": {"webauthn-register"},
        "passkey": set(),
        "codes": {"webauthn-register"},
        "used-up": {"webauthn-register"},
    }
    # The app stays in Keycloak, unused while apps are off.
    assert "otp" in {c["type"] for c in kc.credentials[people["app"][1]]}
    _put_setting(client, "auth.allow_authenticator_app", True)
    assert owed()["app"] == set() and owed()["nothing"] == set()


@pytest.mark.django_db
def test_someone_with_a_passkey_and_an_app_is_offered_the_passkey_first(kc, staff_user):
    sub = _linked(kc, staff_user)
    # The app and its recovery codes came first, then passkeys: Keycloak offers second steps in
    # the order of the credentials, so it would offer the app (or a recovery code) first.
    _holding(kc, sub, "password", "otp", "recovery-authn-codes", "webauthn", "webauthn")
    kc_admin.sync_person(staff_user)
    assert [c["id"] for c in kc.credentials[sub]] == [
        "password-0",
        "webauthn-3",
        "webauthn-4",
        "otp-1",
        "recovery-authn-codes-2",
    ]
    assert kc.moves == [(sub, "webauthn-3", "password-0"), (sub, "webauthn-4", "webauthn-3")]
    # Already in order: nothing moves.
    kc_admin.sync_person(staff_user)
    assert len(kc.moves) == 2


@pytest.mark.django_db
def test_inviting_a_requester_makes_the_account_and_nothing_in_keycloak(kc, admin_user):
    person = invites.invite(
        admin_user, email="member@example.org", name="M", records_role="requester"
    )
    assert not KeycloakChange.objects.filter(user=person).exists()
    assert not Invite.objects.filter(user=person).exists()


@pytest.mark.django_db
def test_a_role_change_reaches_the_keycloak_account_and_a_requester_is_switched_off(
    kc, admin_user, staff_user
):
    sub = _linked(kc, staff_user)
    identity.set_role(admin_user, staff_user, "staff_attorney")
    _drain(staff_user)
    assert kc.users[sub]["attributes"]["sclu_role"] == ["staff_attorney"]
    assert kc.users[sub]["enabled"] is True
    identity.set_role(admin_user, staff_user, "requester")
    _drain(staff_user)
    assert kc.users[sub]["enabled"] is False
    assert kc.logouts[-1] == sub


@pytest.mark.django_db
def test_deactivating_ends_the_portal_sessions_now_and_keycloaks_after(
    client, kc, admin_user, staff_user
):
    sub = _linked(kc, staff_user)
    _sign_in(client, kc, sub)
    assert client.get("/api/core/auth/me").status_code == 200
    identity.deactivate_user(admin_user, staff_user, reassign_tasks_to=None)
    # The very next request is signed out, before Keycloak hears of it.
    assert client.get("/api/core/auth/me").status_code == 401
    assert [
        r.action for r in KeycloakChange.objects.filter(user=staff_user).order_by("created_at")
    ] == [
        "sync",
        "end_sessions",
    ]
    _drain(staff_user)
    assert kc.users[sub]["enabled"] is False
    assert sub in kc.logouts


@pytest.mark.django_db
def test_reactivating_switches_the_account_back_on(kc, admin_user, staff_user):
    sub = _linked(kc, staff_user)
    identity.deactivate_user(admin_user, staff_user, reassign_tasks_to=None)
    _drain(staff_user)
    identity.reactivate_user(admin_user, staff_user)
    _drain(staff_user)
    assert kc.users[sub]["enabled"] is True


@pytest.mark.django_db
def test_a_name_change_reaches_keycloak(kc, staff_user):
    sub = _linked(kc, staff_user)
    identity.update_profile(staff_user, name="Renamed Person")
    _drain(staff_user)
    assert kc.users[sub]["firstName"] == "Renamed Person"


@pytest.mark.django_db
def test_an_admin_api_failure_is_retried_and_shown_in_users_until_keycloak_takes_it(
    client, kc, admin_user, staff_user
):
    sub = _linked(kc, staff_user)
    kc.admin_down = True
    identity.set_role(admin_user, staff_user, "staff_attorney")
    with pytest.raises(kc_sync.ChangeFailed):
        _drain(staff_user)
    change = KeycloakChange.objects.get(user=staff_user, action="sync")
    assert change.state == "pending" and change.attempts == 1
    assert "could not be reached" in change.last_error or "503" in change.last_error
    client.force_login(admin_user)
    row = next(u for u in client.get("/api/core/users").json() if u["id"] == str(staff_user.pk))
    assert row["sign_in"]["state"] == "pending" and row["sign_in"]["action"] == "sync"
    # Every retry spent: marked failed, still kept, and shown with Keycloak's reason.
    for _ in range(kc_sync.MAX_ATTEMPTS - 1):
        try:
            _drain(staff_user)
        except kc_sync.ChangeFailed:
            pass
    change.refresh_from_db()
    assert change.state == "failed" and change.attempts == kc_sync.MAX_ATTEMPTS
    row = next(u for u in client.get("/api/core/users").json() if u["id"] == str(staff_user.pk))
    assert row["sign_in"]["state"] == "failed" and row["sign_in"]["detail"]
    assert kc.users[sub]["attributes"].get("sclu_role") != ["staff_attorney"]
    # Keycloak is back; "Try again" queues it and it goes through, in order.
    kc.admin_down = False
    csrf = client.get("/api/core/auth/csrf").json()["csrf_token"]
    answer = client.post(f"/api/core/users/{staff_user.pk}/sign-in/retry", HTTP_X_CSRFTOKEN=csrf)
    assert answer.status_code == 200 and answer.json()["sign_in"]["state"] == "pending"
    assert _drain(staff_user) == 1
    assert kc.users[sub]["attributes"]["sclu_role"] == ["staff_attorney"]
    row = next(u for u in client.get("/api/core/users").json() if u["id"] == str(staff_user.pk))
    assert row["sign_in"]["state"] == "ok"


@pytest.mark.django_db
def test_a_later_change_never_overtakes_an_earlier_one_that_failed(kc, admin_user, staff_user):
    sub = _linked(kc, staff_user)
    kc.admin_down = True
    identity.set_role(admin_user, staff_user, "intern")
    kc_sync.enqueue(staff_user, KeycloakChange.END_SESSIONS)
    with pytest.raises(kc_sync.ChangeFailed):
        _drain(staff_user)
    assert kc.logouts == []
    kc.admin_down = False
    assert _drain(staff_user) == 2
    assert kc.users[sub]["attributes"]["sclu_role"] == ["intern"] and kc.logouts == [sub]


@pytest.mark.django_db
def test_the_sweep_takes_up_failed_changes(
    kc, admin_user, staff_user, django_capture_on_commit_callbacks
):
    _linked(kc, staff_user)
    KeycloakChange.objects.create(user=staff_user, action="sync", state="failed", attempts=8)
    assert kc_sync.sweep() == 1
    assert KeycloakChange.objects.get(user=staff_user).state == "pending"


@pytest.mark.django_db
def test_users_actions_queue_keycloak_changes(client, kc, admin_user, staff_user):
    _linked(kc, staff_user)
    client.force_login(admin_user)
    csrf = client.get("/api/core/auth/csrf").json()["csrf_token"]
    for path, action in (
        ("password-reset", "password_reset"),
        ("sign-out-everywhere", "end_sessions"),
        ("reset-second-step", "reset_second_step"),
    ):
        answer = client.post(f"/api/core/users/{staff_user.pk}/{path}", HTTP_X_CSRFTOKEN=csrf)
        assert answer.status_code == 200, (path, answer.content)
        assert KeycloakChange.objects.filter(user=staff_user, action=action).exists()
    kc.credentials[staff_user.keycloak_sub] = [
        {"id": "pk-1", "type": "webauthn"},
        {"id": "rc-1", "type": "recovery-authn-codes"},
        {"id": "pw-1", "type": "password"},
    ]
    _drain(staff_user)
    sent = [actions for _sub, actions, _q in kc.emails]
    assert ["UPDATE_PASSWORD"] in sent
    assert [c["id"] for c in kc.credentials[staff_user.keycloak_sub]] == ["pw-1"]
    assert staff_user.keycloak_sub in kc.logouts


@pytest.mark.django_db
def test_users_actions_are_for_administrators_and_not_for_requesters(
    client, kc, staff_user, admin_user
):
    client.force_login(staff_user)
    csrf = client.get("/api/core/auth/csrf").json()["csrf_token"]
    assert (
        client.post(
            f"/api/core/users/{admin_user.pk}/password-reset", HTTP_X_CSRFTOKEN=csrf
        ).status_code
        == 403
    )
    member = identity.create_user(email="m@example.org", name="M", records_role="requester")
    client.force_login(admin_user)
    csrf = client.get("/api/core/auth/csrf").json()["csrf_token"]
    assert (
        client.post(
            f"/api/core/users/{member.pk}/password-reset", HTTP_X_CSRFTOKEN=csrf
        ).status_code
        == 409
    )


@pytest.mark.django_db
def test_an_address_that_is_someone_elses_keycloak_account_is_a_conflict(kc, admin_user):
    kc.add_user("taken@example.org", attributes={"sclu_uid": ["someone-else"]})
    person = invites.invite(admin_user, email="taken@example.org", name="T", records_role="staff")
    with pytest.raises(kc_sync.ChangeFailed):
        _drain(person)
    # The invite queues a sync and then the email; the sync is the one refused. (`.first()` over
    # both went by the random uuid key, so it found the untouched email row about half the time.)
    sync = KeycloakChange.objects.get(user=person, action=KeycloakChange.SYNC)
    assert "someone else" in sync.last_error


# ---- sign-in rules from Settings -------------------------------------------------------------


def _put_setting(client, key, value):
    csrf = client.get("/api/core/auth/csrf").json()["csrf_token"]
    answer = client.put(
        f"/api/core/settings/{key}",
        data=json.dumps({"value": value}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert answer.status_code == 200, answer.content
    return answer


def _app_steps(kc: FakeKeycloak) -> dict[str, tuple[str, int]]:
    """Every execution the authenticator-app switch is about, by id: (requirement, priority)."""
    return {
        row["id"]: (row["requirement"], row.get("priority", 0))
        for flow in kc.executions.values()
        for row in flow
        if row.get("providerId") == "auth-otp-form" or row.get("authenticationFlow")
    }


@pytest.mark.django_db
def test_a_sign_in_rule_changed_in_settings_is_written_to_keycloak(client, kc, admin_user):
    client.force_login(admin_user)
    for key, value in (
        ("auth.session_hours", 6),
        ("auth.reverify_hours", 2),
        ("auth.reverify_tool_minutes", 15),
    ):
        _put_setting(client, key, value)
    assert KeycloakChange.objects.filter(user=None, action="policy", state="pending").count() == 1
    _drain(None)
    assert kc.realm["ssoSessionIdleTimeout"] == 6 * 3600
    assert kc.configs["sclu portal browser-loa-2"]["config"]["loa-max-age"] == str(2 * 3600)
    assert kc.configs["sclu tools browser-loa-2"]["config"]["loa-max-age"] == str(15 * 60)
    assert kc.configs["sclu portal browser-loa-1"]["config"]["loa-max-age"] == str(24 * 3600)


@pytest.mark.django_db
def test_authenticator_apps_are_allowed_by_default(client, kc, staff_user):
    # P25c (owner, 2026-10-20: "i want to allow both pass key and auth app").
    import tomllib
    from pathlib import Path

    from portal.core.keycloak import policy

    defaults = Path(policy.__file__).resolve().parents[1] / "defaults.toml"
    with defaults.open("rb") as fh:
        assert tomllib.load(fh)["settings"]["auth.allow_authenticator_app"]["value"] is True
    assert Setting.objects.get(pk="auth.allow_authenticator_app").value is True
    assert policy.current().allow_authenticator_app is True
    # Applying the default to the realm as the file leaves it changes nothing.
    assert policy.apply(kc_admin.client()) == []
    # What the sign-in pages ask before offering an app (no one signed in).
    assert client.get("/api/core/auth/second-steps").json() == {
        "passkey": True,
        "authenticator_app": True,
    }


@pytest.mark.django_db
def test_the_authenticator_app_switch_writes_every_app_step_in_both_states(client, kc, admin_user):
    _linked(kc, admin_user)
    client.force_login(admin_user)
    as_the_file = _app_steps(kc)
    _put_setting(client, "auth.allow_authenticator_app", False)
    _drain(None)
    off = _app_steps(kc)
    # The app as a second step one already has (both browser flows' choices, the forgot-password
    # flow's), and the branch a first-timer reaches by choosing one: off, each in its place.
    for row in ("sclu portal browser-otp", "sclu tools browser-otp", "reset-otp"):
        assert off[row] == ("DISABLED", as_the_file[row][1]), row
    for row in ("sclu portal browser-first-app", "sclu tools browser-first-app"):
        assert off[row] == ("DISABLED", 30), row
    # The first-app branch's own OTP form (REQUIRED: it sets the app up) is not the switch's.
    for row in ("sclu portal browser-first-app-otp", "sclu tools browser-first-app-otp"):
        assert off[row] == ("REQUIRED", 30), row
    totp = next(a for a in kc.required_actions if a["alias"] == "CONFIGURE_TOTP")
    assert totp["enabled"] is False
    assert client.get("/api/core/auth/second-steps").json()["authenticator_app"] is False

    _put_setting(client, "auth.allow_authenticator_app", True)
    _drain(None)
    assert _app_steps(kc) == as_the_file
    assert totp["enabled"] is True
    assert client.get("/api/core/auth/second-steps").json()["authenticator_app"] is True
    # Twice, once each way: and each change re-syncs everyone with a Keycloak account.
    assert KeycloakChange.objects.filter(action="policy").count() == 2
    assert KeycloakChange.objects.filter(user=admin_user, action="sync").count() >= 1


@pytest.mark.django_db
def test_the_migration_turns_apps_on_unless_an_administrator_chose(admin_user):
    from importlib import import_module

    from django.apps import apps
    from portal.core.services import activity
    from portal.records.actors import Actor

    migration = import_module("portal.core.migrations.0036_p25c_authenticator_app_on")
    key = "auth.allow_authenticator_app"
    # The row as core.0035 seeded it, nobody having touched it: turned on.
    Setting.objects.filter(pk=key).update(value=False, description="old")
    migration.allow_by_default(apps, None)
    row = Setting.objects.get(pk=key)
    assert row.value is True and row.description.startswith("On (the default")
    # An administrator switched it off in Settings (the activity log says so): it stays off.
    Setting.objects.filter(pk=key).update(value=False)
    activity.record(
        Actor.human(admin_user),
        "setting_changed",
        f"Changed the setting {key}",
        subject_type="setting",
        data={"key": key, "old": True, "new": False},
    )
    migration.allow_by_default(apps, None)
    assert Setting.objects.get(pk=key).value is False


@pytest.mark.django_db
def test_sign_in_rules_out_of_bounds_are_refused(client, kc, admin_user):
    client.force_login(admin_user)
    csrf = client.get("/api/core/auth/csrf").json()["csrf_token"]

    def put(key, value):
        return client.put(
            f"/api/core/settings/{key}",
            data=json.dumps({"value": value}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )

    assert put("auth.session_hours", 0).status_code == 422
    assert put("auth.session_hours", 48).status_code == 422, "longer than the longest sign-in"
    assert put("auth.reverify_tool_minutes", 5000).status_code == 422
    assert not KeycloakChange.objects.filter(action="policy").exists()


# ---- what Settings → Profile → Security shows -------------------------------------------------


@pytest.mark.django_db
def test_security_lists_what_keycloak_holds_without_a_secret(client, kc, staff_user):
    sub = _linked(kc, staff_user)
    kc.credentials[sub] = [
        {"id": "pw", "type": "password", "createdDate": 1_760_000_000_000},
        {"id": "pk", "type": "webauthn", "userLabel": "MacBook", "createdDate": 1_760_000_000_000},
        {
            "id": "rc",
            "type": "recovery-authn-codes",
            "createdDate": 1_760_000_000_000,
            "credentialData": json.dumps({"remainingCodes": 11, "totalCodes": 12}),
        },
    ]
    _sign_in(client, kc, sub)
    answer = client.get("/api/core/auth/security").json()
    assert answer["passkeys"] == [
        {"id": "pk", "label": "MacBook", "created_at": "2025-10-09T08:53:20Z", "remaining": None}
    ]
    assert answer["recovery_codes"]["remaining"] == 11
    assert answer["password_changed_at"] == "2025-10-09T08:53:20Z"
    assert answer["authenticator_app_allowed"] is True and answer["reverify_hours"] == 4
    assert answer["verified_at"]


@pytest.mark.django_db
def test_security_says_when_keycloak_is_down(client, kc, staff_user):
    _linked(kc, staff_user)
    client.force_login(staff_user)
    kc.admin_down = True
    assert client.get("/api/core/auth/security").status_code == 503


@pytest.mark.django_db
def test_the_portal_holds_no_passwords(kc):
    person = identity.create_user(email="p@example.org", name="P", records_role="staff")
    assert not person.has_usable_password()
    with pytest.raises(ValueError):
        User.objects.create_user(email="q@example.org", name="Q", password="anything-at-all")


@pytest.mark.django_db
def test_the_admin_client_is_the_service_account_by_client_credentials(kc, staff_user):
    _linked(kc, staff_user)
    kc_admin.credentials(staff_user)
    grant = [r for r in kc.token_requests if r.get("grant_type") == ["client_credentials"]]
    assert grant and grant[-1]["client_id"] == ["portal-admin"]
    assert any(path.startswith("/users/") for _m, path, _b in kc.admin_calls)
    assert ADMIN.startswith("http://keycloak.test/auth/admin/realms/sclu")
