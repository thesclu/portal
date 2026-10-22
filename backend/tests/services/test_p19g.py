"""P19g: stored agency portal logins, encrypted at rest, never in any log."""

import json

import pytest
from cryptography.fernet import Fernet
from django.test import Client
from portal.core.models import Activity
from portal.records.actors import Actor
from portal.records.models import AgencyLogin
from portal.records.services import logins
from portal.records.services.common import NotFound, ServiceError


@pytest.fixture
def secrets_key(settings):
    settings.PORTAL = {**settings.PORTAL, "SECRETS_KEY": Fernet.generate_key().decode()}


def test_a_login_is_stored_encrypted_revealed_on_record_and_cleared(
    secrets_key, staff_user, agency
) -> None:
    actor = Actor.human(staff_user)
    assert logins.view(agency.pk) is None
    view = logins.set_login(actor, agency.pk, login="records@example.org", password="pw-test-1")
    assert view.login == "records@example.org" and view.has_password
    assert view.changed_by_name == "Staff Person"
    row = AgencyLogin.objects.get(pk=agency.pk)
    assert "pw-test-1" not in row.password_ciphertext and row.password_ciphertext
    assert logins.reveal(actor, agency.pk) == "pw-test-1"
    # The password is in no log; the looking is.
    lines = list(Activity.objects.order_by("at"))
    assert [a.action for a in lines] == ["agency_login_changed", "agency_password_revealed"]
    assert all("pw-test-1" not in json.dumps(a.data) + a.summary for a in lines)
    # A new login without a password keeps the old password; an empty one removes it.
    logins.set_login(actor, agency.pk, login="other@example.org", password=None)
    assert logins.reveal(actor, agency.pk) == "pw-test-1"
    logins.set_login(actor, agency.pk, login="other@example.org", password="")
    with pytest.raises(NotFound):
        logins.reveal(actor, agency.pk)
    logins.clear_login(actor, agency.pk)
    assert logins.view(agency.pk) is None
    with pytest.raises(ServiceError, match="login is required"):
        logins.set_login(actor, agency.pk, login=" ", password="x")


def test_without_a_key_nothing_is_stored_or_shown(settings, staff_user, agency) -> None:
    settings.PORTAL = {**settings.PORTAL, "SECRETS_KEY": ""}
    with pytest.raises(logins.NoSecretsKey):
        logins.set_login(Actor.human(staff_user), agency.pk, login="x", password="y")


def test_the_routes(secrets_key, staff_user, agency) -> None:
    client = Client()
    client.force_login(staff_user)
    base = f"/api/records/agencies/{agency.pk}/login"
    assert client.get(base).status_code == 204
    put = client.put(
        base,
        data=json.dumps({"login": "records@example.org", "password": "pw-test-2"}),
        content_type="application/json",
    )
    assert put.status_code == 200 and put.json()["has_password"] is True
    assert "pw-test-2" not in put.content.decode()
    assert client.get(base).json()["login"] == "records@example.org"
    assert client.post(f"{base}/reveal").json() == {"password": "pw-test-2"}
    assert client.delete(base).status_code == 204
    assert client.get(base).status_code == 204
