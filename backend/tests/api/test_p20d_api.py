"""P20d, from the caller's side: the Inbox's sort menu (`GET /records/inbox?sort=`), and that an
order never says where a message landed when the caller may not see that request."""

from datetime import UTC, datetime, timedelta

import pytest
from portal.records.actors import Actor
from portal.records.services import directory
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service

from tests.p8b_helpers import attach, make_source

pytestmark = pytest.mark.usefixtures("as_each_role")

BASE = "/api/records"
T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def actor(staff_user):
    return Actor.human(staff_user)


def _reserve(actor, agency, staff_user, subject, sensitivity="internal"):
    return requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject=subject,
        request_text="Synthetic.",
        sensitivity=sensitivity,
    )


@pytest.fixture
def mail(actor, staff_user, agency):
    """Three messages in All: one on Test Agency, one on an agency that sorts first, one placed
    nowhere; the oldest is reviewed."""
    first = directory.create_agency(
        actor, name="A Test Agency", jurisdiction="Testland", records_law="TESTLAW"
    )
    on_test = _reserve(actor, agency, staff_user, "On Test Agency")
    on_first = _reserve(actor, first, staff_user, "On A Test Agency")
    oldest = make_source(actor, captured_at=T0)
    attach(actor, on_test, oldest)
    sources_service.mark_source_reviewed(actor, oldest.pk)
    middle = make_source(actor, captured_at=T0 + timedelta(hours=1))
    attach(actor, on_first, middle)
    newest = make_source(actor, captured_at=T0 + timedelta(hours=2))
    return {"oldest": oldest, "middle": middle, "newest": newest, "on_test": on_test}


def _order(client, sort: str) -> list[str]:
    response = client.get(f"{BASE}/inbox", {"bucket": "all", "sort": sort})
    assert response.status_code == 200, response.content
    return [row["source"]["id"] for row in response.json()["items"]]


def test_each_sort_orders_the_bucket(staff_client, mail) -> None:
    ids = {name: str(source.pk) for name, source in mail.items() if name != "on_test"}
    assert _order(staff_client, "newest") == [ids["newest"], ids["middle"], ids["oldest"]]
    assert _order(staff_client, "oldest") == [ids["oldest"], ids["middle"], ids["newest"]]
    # By agency: "A Test Agency" before "Test Agency"; mail placed nowhere last.
    assert _order(staff_client, "agency") == [ids["middle"], ids["oldest"], ids["newest"]]
    # By request: tracking numbers in order (on_test was reserved first); unplaced last.
    assert _order(staff_client, "request") == [ids["oldest"], ids["middle"], ids["newest"]]
    # New first: what nobody reviewed, newest first, then the reviewed.
    assert _order(staff_client, "unreviewed") == [ids["newest"], ids["middle"], ids["oldest"]]
    # The default is newest first.
    default = staff_client.get(f"{BASE}/inbox", {"bucket": "all"}).json()["items"]
    assert [row["source"]["id"] for row in default] == _order(staff_client, "newest")


def test_an_unknown_sort_is_refused(staff_client, mail) -> None:
    response = staff_client.get(f"{BASE}/inbox", {"bucket": "all", "sort": "loudest"})
    assert response.status_code in (400, 422)


def test_the_order_never_reads_a_request_the_caller_may_not_see(
    actor, staff_user, colleague_user, agency, client
) -> None:
    """A message on a privileged request the colleague is not on sorts by the request they may
    see, never by the hidden request's agency."""
    hidden_agency = directory.create_agency(
        actor, name="AAA Hidden Agency", jurisdiction="Testland", records_law="TESTLAW"
    )
    middle_agency = directory.create_agency(
        actor, name="B Test Agency", jurisdiction="Testland", records_law="TESTLAW"
    )
    sealed = _reserve(actor, hidden_agency, staff_user, "SEALED", sensitivity="privileged")
    visible = _reserve(actor, agency, staff_user, "Open")
    on_middle = _reserve(actor, middle_agency, staff_user, "Middle")
    # One message on the sealed request AND on an open one ("Test Agency") the colleague may see.
    on_sealed = make_source(actor, captured_at=T0 + timedelta(hours=1))
    attach(actor, sealed, on_sealed)
    attach(actor, visible, on_sealed)
    on_b = make_source(actor, captured_at=T0 + timedelta(hours=2))
    attach(actor, on_middle, on_b)
    unplaced = make_source(actor, captured_at=T0)
    client.force_login(colleague_user)
    # Read through the hidden request it would sort first ("AAA"); read as they may, after "B".
    assert _order(client, "agency") == [str(on_b.pk), str(on_sealed.pk), str(unplaced.pk)]
    body = client.get(f"{BASE}/inbox", {"bucket": "all", "sort": "agency"}).content.decode()
    assert "AAA Hidden Agency" not in body and "SEALED" not in body
