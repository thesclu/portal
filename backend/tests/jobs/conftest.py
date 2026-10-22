"""Fixtures for the periodic-job tests. Fictional rule data only, as in tests/services."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from portal.records.actors import Actor
from portal.records.services import common
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service

FIXTURES = Path(__file__).resolve().parents[1] / "services" / "fixtures"
# 2026-02-10 is a Tuesday in testland; 18:00 UTC is 10:00 the same civil date in Los Angeles.
FILED_AT = datetime(2026, 2, 10, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fictional_rule_data_only():
    with common.using_rule_data(
        rules_path=FIXTURES / "rules_test.toml", calendars_path=FIXTURES / "calendars_test.toml"
    ) as data:
        assert "TESTLAW" in data.law_calendar
        yield data


@pytest.fixture
def owner(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def make_filed(db, owner, agency, staff_user):
    def make(subject="Widgets", agency_=None, **extra):
        request = requests_service.reserve_request(
            owner,
            agency_id=(agency_ or agency).pk,
            requester_user_id=staff_user.pk,
            subject=subject,
            request_text="Body.",
            **extra,
        )
        filing_service.file_request(owner, request.pk, filed_at=FILED_AT, channel="email")
        request.refresh_from_db()
        return request

    return make
