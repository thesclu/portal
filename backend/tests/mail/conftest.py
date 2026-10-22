"""A fake provider: every test here runs with `transport._post` replaced, so nothing reaches the
network, and the job runner is replaced so queueing does not need a worker."""

import pytest
from portal.mail.services import send as send_service
from portal.mail.services import transport
from portal.records.actors import Actor
from portal.records.services import requests as requests_service


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = 200
        self.body: dict = {"ErrorCode": 0, "MessageID": "pm-0001"}

    def __call__(self, token: str, payload: dict):
        self.calls.append((token, payload))
        return self.status, self.body


@pytest.fixture
def provider(monkeypatch) -> FakeProvider:
    fake = FakeProvider()
    monkeypatch.setattr(transport, "_post", fake)
    return fake


@pytest.fixture(autouse=True)
def _no_worker(monkeypatch):
    """`queue` defers a job; here the deferral is recorded and nothing runs."""
    deferred: list[str] = []
    monkeypatch.setattr(
        send_service, "_defer", lambda message_id, send_after: deferred.append(str(message_id))
    )
    return deferred


@pytest.fixture
def actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def a_request(db, actor, staff_user, agency):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Mail me",
        request_text="Dear agency, the letter.",
    )
