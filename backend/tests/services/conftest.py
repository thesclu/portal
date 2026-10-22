"""Fixtures shared by the service tests. Fictional data only -- never the shipped rules."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from portal.records.actors import Actor
from portal.records.services import common, directory
from portal.records.services import requests as requests_service

FIXTURES = Path(__file__).parent / "fixtures"

# A fixed instant, so every computed due date in these tests is reproducible. 2026-02-10 is a
# Tuesday in testland; 18:00 UTC is 10:00 the same civil date in America/Los_Angeles.
FILED_AT = datetime(2026, 2, 10, 18, 0, tzinfo=UTC)


@pytest.fixture
def rules():
    with common.using_rule_data(
        rules_path=FIXTURES / "rules_test.toml",
        calendars_path=FIXTURES / "calendars_test.toml",
    ) as data:
        yield data


@pytest.fixture(autouse=True)
def _fictional_rule_data_only():
    """Every test in this package gets the fictional TESTLAW data, whether or not it remembers to
    request `rules` explicitly. A test that forgets would otherwise silently resolve to the
    shipped data/rules.toml, where TESTLAW is unmapped -- making a "no rule" assertion pass for
    the wrong reason. Fail loudly, not silently, if the substitution somehow didn't take."""
    with common.using_rule_data(
        rules_path=FIXTURES / "rules_test.toml",
        calendars_path=FIXTURES / "calendars_test.toml",
    ) as data:
        assert "TESTLAW" in data.law_calendar, (
            "the autouse fictional rule-data fixture did not install the TESTLAW mapping; a "
            "test in this package would silently fall back to the shipped rules.toml."
        )
        yield data


@pytest.fixture
def staff_actor(staff_user):
    return Actor.human(staff_user)


@pytest.fixture
def system_actor():
    return Actor.for_system("test_sweep")


@pytest.fixture
def reserved(db, staff_actor, staff_user, agency):
    return requests_service.reserve_request(
        staff_actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Widgets, January 2026",
        request_text="All widget inspection records for January 2026.",
        items=[
            requests_service.NewItem("1", "Widget inspection reports"),
            requests_service.NewItem("2", "Widget camera footage"),
        ],
    )


@pytest.fixture
def norules_agency(db, staff_actor):
    return directory.create_agency(
        staff_actor,
        name="Test Agency Without Rules",
        jurisdiction="Testland",
        records_law="TESTLAW_NORULES",
    )
