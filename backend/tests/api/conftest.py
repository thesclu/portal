"""Fixtures shared by the API tests. Django's default test Client does not enforce CSRF checks,
so no extra settings are needed for the POST/PATCH bodies below."""

import json

import pytest
from django.test import Client
from portal.records.actors import Actor
from portal.records.services import requests as requests_service


def post_json(client: Client, path: str, payload: dict):
    body = json.dumps(payload, default=str)
    return client.post(path, data=body, content_type="application/json")


def patch_json(client: Client, path: str, payload: dict):
    body = json.dumps(payload, default=str)
    return client.patch(path, data=body, content_type="application/json")


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def requester_user(db):
    from portal.core.services.identity import create_user

    return create_user(
        email="requester@example.org",
        name="Requester One",
        records_role="requester",
    )


@pytest.fixture
def other_requester_user(db):
    from portal.core.services.identity import create_user

    return create_user(
        email="requester2@example.org",
        name="Requester Two",
        records_role="requester",
    )


@pytest.fixture
def staff_client(client, staff_user):
    client.force_login(staff_user)
    return client


@pytest.fixture
def director_user(db):
    """P20h: the Director marker is retired and the doors it guarded (moving an existing deadline's
    date) are an administrator's (30l, 42d). The fixture keeps its name for the tests written
    against those doors; it is an administrator now."""
    from portal.core.services.identity import create_user

    return create_user(
        email="director@example.org",
        name="Director Person",
        records_role="admin",
        is_staff=True,
    )


@pytest.fixture
def director_client(director_user) -> Client:
    own = Client()
    own.force_login(director_user)
    return own


@pytest.fixture
def admin_client(admin_user) -> Client:
    """An administrator WITHOUT the Director marker."""
    own = Client()
    own.force_login(admin_user)
    return own


@pytest.fixture
def requester_client(client, requester_user):
    client.force_login(requester_user)
    return client


@pytest.fixture
def reserved(db, staff_user, agency):
    """A request filed by staff on behalf of staff -- the identity of the requester doesn't
    matter for tests that aren't exercising the requester-scoping rule itself."""
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Body-worn camera footage, March 2026",
        request_text="All body-worn camera footage for incidents in March 2026.",
    )


@pytest.fixture
def own_request(db, staff_user, agency, requester_user):
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=requester_user.pk,
        subject="Budget records, FY2026",
        request_text="All budget records for fiscal year 2026.",
    )


@pytest.fixture
def other_request(db, staff_user, agency, other_requester_user):
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=other_requester_user.pk,
        subject="Contracts, 2025",
        request_text="All executed vendor contracts for 2025.",
    )
