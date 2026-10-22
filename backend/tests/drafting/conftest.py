"""Fixtures for the drafting service tests. Fictional data only -- "Test Agency", TESTLAW.

The rule-data fixture is the same autouse guard `backend/tests/services/conftest.py` installs,
and points at the same fictional files: filing a recipient reaches
`records.services.filing.file_request`, which computes deadlines, so a test here that fell back
to the shipped `data/rules.toml` would be asserting against owner's real statutory numbers.
"""

from pathlib import Path

import pytest
from portal.drafting.models import LetterRecipient
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import library as library_service
from portal.records.actors import Actor
from portal.records.services import common, directory

FIXTURES = Path(__file__).resolve().parents[1] / "services" / "fixtures"


@pytest.fixture(autouse=True)
def _fictional_rule_data_only():
    with common.using_rule_data(
        rules_path=FIXTURES / "rules_test.toml",
        calendars_path=FIXTURES / "calendars_test.toml",
    ) as data:
        assert "TESTLAW" in data.law_calendar, (
            "the autouse fictional rule-data fixture did not install the TESTLAW mapping"
        )
        yield data


@pytest.fixture
def staff_actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def shell(db):
    return library_service.create_shell(
        name="SCLU standard -- TESTLAW",
        letterhead="SOUTHERN CALIFORNIA TEST UNION",
        opening_body="To {{RECIPIENT_OFFICE}} at {{AGENCY_FULL}}: under TESTLAW, we request",
        closing_body="Please direct replies to the address in this letter.",
        is_default=True,
    )


@pytest.fixture
def block(db):
    return library_service.create_block(
        name="Record definition", body="'Record' means any writing, however stored."
    )


@pytest.fixture
def template(db, shell, block, staff_user):
    """A template with prose, two asks, a sub-ask and a citation -- enough shape that copying it
    into a draft has something to get wrong."""
    row = library_service.create_template(
        name="Widget inspection records",
        jurisdiction="Testland",
        legal_basis="TESTLAW",
        shell_id=shell.pk,
        created_by_user_id=staff_user.pk,
    )
    library_service.set_template_blocks(row.pk, [block.pk])
    second = library_service.add_template_item(
        row.pk, item_number="2", body="Widget camera footage", sort_order=1
    )
    library_service.add_template_item(
        row.pk,
        item_number="2a",
        body="Body-worn footage only",
        parent_item_id=second.pk,
        sort_order=2,
    )
    library_service.add_template_item(
        row.pk, item_number="10", body="Widget disposal manifests", sort_order=9
    )
    library_service.add_template_citation(
        row.pk, marker="1", citation_text="Test Code section 1000 et seq."
    )
    return row


def sendable_agency(actor, name: str, **extra):
    """An agency a letter can actually go to: a recipient office, a postal address, and a request
    email somebody has verified. Since P10 a recipient for anything less arrives held and a letter
    with a blank in it is not reserved, so the plain agency of the other test packages (a name and
    a law) can no longer be filed to."""
    made = directory.create_agency(
        actor,
        name=name,
        jurisdiction="Testland",
        records_law="TESTLAW",
        short_name="".join(word[0] for word in name.split()),
        recipient_office="Records Unit",
        mailing_street="1 Test Street",
        mailing_city_state_zip="Testville, TL 00000",
        default_request_email=f"records@{name.lower().replace(' ', '-')}.example.org",
        filing_method="email",
        **extra,
    )
    return directory.verify_request_email(actor, made.pk, "Confirmed by a test.")


@pytest.fixture
def agency(db, staff_actor):
    """Overrides the package-wide `agency`: the drafting tests file letters, so theirs is
    complete. `bare_agency` is the plain one, for the tests about what is missing."""
    return sendable_agency(staff_actor, "Test Agency")


@pytest.fixture
def second_agency(db, staff_actor):
    return sendable_agency(staff_actor, "Second Test Agency")


@pytest.fixture
def bare_agency(db, staff_actor):
    """A name and a law and nothing else: no office, no address, no filing method."""
    return directory.create_agency(
        staff_actor, name="Bare Agency", jurisdiction="Testland", records_law="TESTLAW"
    )


@pytest.fixture
def draft(db, template, staff_user):
    return drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )


@pytest.fixture
def two_recipients(db, draft, agency, second_agency):
    return [
        drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email"),
        drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, channel="email"),
    ]


@pytest.fixture
def recipient(db, draft, agency):
    return drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")


@pytest.fixture
def pending_status():
    return LetterRecipient.PENDING


@pytest.fixture
def reserved_with_items(db, staff_actor, staff_user, agency):
    from portal.records.services import requests as requests_service

    return requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="A request filed last year",
        request_text="The whole letter, shell and asks already fused.",
        items=[
            requests_service.NewItem("1", "First ask", sort_order=0),
            requests_service.NewItem("2", "Second ask", sort_order=1),
        ],
    )


# ---- P21c: the drafting tool, faked at the HTTP layer -----------------------------------------


@pytest.fixture
def fake_fork(monkeypatch):
    """The fork's API as an httpx MockTransport (tests/drafting/fork_fake.py). Every call the
    portal makes lands here; no fork runs."""
    from portal.drafting.services import fork

    from tests.drafting.fork_fake import FakeFork

    fake = FakeFork()
    monkeypatch.setattr(fork, "_transport", fake.transport)
    monkeypatch.setattr(fork, "_sleep", lambda seconds: None)
    return fake


@pytest.fixture
def fork_configured(settings):
    """The server's side: both addresses and the two secrets (synthetic values)."""
    from tests.drafting import fork_fake

    settings.DRAFTING = {
        "API_URL": fork_fake.API_URL,
        "WEB_URL": fork_fake.WEB_URL,
        "SERVICE_SECRET": fork_fake.SERVICE_SECRET,
        "WEBHOOK_SECRET": fork_fake.WEBHOOK_SECRET,
        "TIMEOUT_SECONDS": 5.0,
    }
    return settings.DRAFTING


@pytest.fixture(autouse=True)
def keycloak_accounts():
    """P25b: the drafting tool names people as Keycloak does (their `keycloak_sub`), which every
    person the portal has synced to Keycloak holds. Here every account made gets a synthetic one
    as it is saved, unless a test says otherwise (`keycloak_sub=None` after)."""
    from django.conf import settings as django_settings
    from django.db.models.signals import post_save

    def link(sender, instance, created, raw=False, **kwargs):
        if created and not raw and not instance.keycloak_sub:
            sub = f"kc-{instance.pk}"
            type(instance).objects.filter(pk=instance.pk).update(keycloak_sub=sub)
            instance.keycloak_sub = sub

    post_save.connect(link, sender=django_settings.AUTH_USER_MODEL, dispatch_uid="tests.kc-link")
    yield
    post_save.disconnect(sender=django_settings.AUTH_USER_MODEL, dispatch_uid="tests.kc-link")


@pytest.fixture
def fork_on(db, fork_configured, fake_fork):
    """The administrator's switch on as well: letters are written in the drafting tool."""
    from portal.core.models import Setting

    Setting.objects.update_or_create(key="drafting.fork_enabled", defaults={"value": True})
    return fake_fork
