"""Two deliveries of one message at the same moment. Postmark retries, and a retry can overlap the
first attempt. Real threads on real connections; run as the owner, like the other threaded tests."""

import json

import pytest
from django.db import connection
from portal.core.services.identity import create_user
from portal.records.actors import Actor
from portal.records.inbound import ingest
from portal.records.models import Attachment, Event, Source
from portal.records.services import directory
from portal.records.services import requests as requests_service

from tests.inbound.conftest import attachment, deliver, payload_for
from tests.services.test_v2_concurrency import _run_on_threads

_THREADED = {
    "transaction": True,
    "available_apps": [
        "portal.core",
        "portal.records",
        "portal.records_db",
        "procrastinate.contrib.django",
    ],
}


def _request():
    user = create_user(email="inbound-race@example.org", name="Racer")
    actor = Actor.human(user)
    agency = directory.create_agency(actor, name="Race Agency", jurisdiction="Testland")
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=user.pk,
        subject="Race",
        request_text="Body.",
    )


@pytest.mark.django_db(**_THREADED)
def test_two_deliveries_of_one_message_at_once_make_one_source_and_one_job(media) -> None:
    payload = payload_for(_request().inbound_alias_email)

    results, errors = _run_on_threads([lambda: deliver(payload)] * 2)

    assert errors == [None, None], errors
    assert sorted(json.loads(r.content)["status"] for r in results) == ["duplicate", "stored"]
    assert Source.objects.count() == 1
    with connection.cursor() as cursor:
        cursor.execute("select task_name from procrastinate_jobs order by id")
        names = [row[0] for row in cursor]
    # One ingest, and (P19d) one assistant check queued once the winner's ingest committed.
    assert names == ["records.ingest_postmark_source", "ai.check_inbound"], names
    raw = [p for p in (media / "sources").rglob("*") if p.is_file()]
    assert len(raw) == 1, "the loser stored a second copy of the original"


@pytest.mark.django_db(**_THREADED)
def test_two_runs_of_the_job_at_once_place_the_message_once(media) -> None:
    payload = payload_for(
        _request().inbound_alias_email, Attachments=[attachment("a.txt", b"one file")]
    )
    source_id = json.loads(deliver(payload).content)["source_id"]

    results, errors = _run_on_threads([lambda: ingest.ingest_source(source_id)] * 2)

    assert errors == [None, None], errors
    assert sorted(r.ingested for r in results) == [False, True]
    assert Event.objects.count() == 1 and Attachment.objects.count() == 1
