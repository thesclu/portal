"""Coverage for loader.py's validation layer -- the safety net for P1b, the step where a human
hand-edits rules.toml and calendars.toml. Before this module, none of loader.py's 25+ distinct
`RulesDataError` raise sites had a single assertion anywhere in the suite: deleting the
unknown-key check, or any other single validation branch, left every other test green.

Each case below writes a small TOML file to `tmp_path` and asserts BOTH that the right exception
type is raised AND that the message names the offending entry -- pinning the promise BUILD_PLAN
makes ("unknown keys raise `RulesDataError` naming the entry id"), not just "something raised".

Everything here is fictional under TESTLAW / a fictional "testland" calendar, per this package's
own rule: no real statute, citation, or holiday date anywhere under packages/deadline_rules.
"""

from pathlib import Path

import pytest
from deadline_rules.errors import RulesDataError
from deadline_rules.loader import load_calendars, load_rules

# A minimal, fully valid [[rule]] entry -- every RULES case starts from this and breaks exactly
# one thing, so a failure can only be attributed to the one field/table under test.
_VALID_RULE = """\
[[rule]]
id = "testlaw.determination.general.2020-01-01"
law = "TESTLAW"
provision = ""
deadline_type = "statutory_determination"
days = 5
unit = "calendar_days"
effective_from = 2020-01-01
citation = "TESTLAW § 1 (fictional, for tests only)"
"""

# A minimal, fully valid calendars.toml -- every CALENDARS case starts from this.
_VALID_CALENDAR = """\
[calendar.testland]
timezone = "America/New_York"
holidays = [2026-01-13]
"""

RULE_CASES = [
    (
        "unknown_field",
        _VALID_RULE + 'typo_key = "oops"\n',
        "unknown field(s)",
    ),
    (
        "missing_required_field_non_id",
        _VALID_RULE.replace('unit = "calendar_days"\n', ""),
        "missing required field(s) ['unit']",
    ),
    (
        "missing_id_field",
        _VALID_RULE.replace('id = "testlaw.determination.general.2020-01-01"\n', ""),
        "<rule at index 0, missing id>': missing required field(s) ['id']",
    ),
    (
        "id_empty_string",
        _VALID_RULE.replace('id = "testlaw.determination.general.2020-01-01"', 'id = ""'),
        "<rule at index 0, missing id>': 'id' must be a non-empty string",
    ),
    (
        "law_empty_string",
        _VALID_RULE.replace('law = "TESTLAW"', 'law = ""'),
        "'law' must be a non-empty string",
    ),
    (
        "provision_wrong_type",
        _VALID_RULE.replace('provision = ""', "provision = 5"),
        "'provision' must be a string",
    ),
    (
        "deadline_type_invalid",
        _VALID_RULE.replace('deadline_type = "statutory_determination"', 'deadline_type = "bogus"'),
        "'deadline_type' must be one of",
    ),
    (
        "days_not_positive",
        _VALID_RULE.replace("days = 5", "days = 0"),
        "'days' must be a positive integer",
    ),
    (
        "unit_invalid",
        _VALID_RULE.replace('unit = "calendar_days"', 'unit = "fortnights"'),
        "'unit' must be one of",
    ),
    (
        "count_from_invalid",
        _VALID_RULE + 'count_from = "filing_date"\n',
        "'count_from' must be 'clock_start'",
    ),
    (
        "roll_forward_wrong_type",
        _VALID_RULE + 'roll_forward_on_non_business_day = "yes"\n',
        "'roll_forward_on_non_business_day' must be a boolean",
    ),
    (
        # G1: a TOML datetime is a `datetime.datetime`, which SUBCLASSES `date` -- isinstance()
        # let this through; the fix checks `type(x) is date` exactly.
        "effective_from_is_datetime",
        _VALID_RULE.replace("effective_from = 2020-01-01", "effective_from = 2020-01-01T00:00:00"),
        "'effective_from' must be a date",
    ),
    (
        # G1, same defect on effective_to.
        "effective_to_is_datetime",
        _VALID_RULE + "effective_to = 2021-01-01T00:00:00\n",
        "'effective_to' must be a date or absent",
    ),
    (
        "effective_to_before_effective_from",
        _VALID_RULE + "effective_to = 2019-01-01\n",
        "must be after 'effective_from'",
    ),
    (
        "citation_empty",
        _VALID_RULE.replace(
            'citation = "TESTLAW § 1 (fictional, for tests only)"', 'citation = "   "'
        ),
        "'citation' must be a non-empty string",
    ),
    (
        "duplicate_rule_id",
        _VALID_RULE + _VALID_RULE,
        "duplicate rule id",
    ),
    (
        # G3: a mistyped top-level table name (plural [[rules]] instead of [[rule]]) used to be
        # silently ignored -- data.get("rule", []) returns () and load_rules "succeeds" with
        # zero rules, exactly the case where a real carve-out clock would vanish unnoticed.
        "unknown_top_level_table",
        _VALID_RULE.replace("[[rule]]", "[[rules]]"),
        "unknown top-level table(s) ['rules']",
    ),
    (
        # G3: `[rule]` (a table) instead of `[[rule]]` (an array of tables) used to raise a bare
        # AttributeError from inside _rule_from_entry (iterating a dict yields string keys).
        "rule_as_singular_table",
        '[rule]\nid = "testlaw.x"\n',
        "'rule' must be an array of tables",
    ),
]

CALENDAR_CASES = [
    (
        "unknown_field",
        _VALID_CALENDAR + 'typo_key = "oops"\n',
        "unknown field(s)",
    ),
    (
        "missing_timezone",
        "[calendar.testland]\nholidays = []\n",
        "missing required field 'timezone'",
    ),
    (
        "missing_holidays",
        '[calendar.testland]\ntimezone = "UTC"\n',
        "missing required field 'holidays'",
    ),
    (
        "timezone_empty_string",
        _VALID_CALENDAR.replace('timezone = "America/New_York"', 'timezone = ""'),
        "'timezone' must be a non-empty string",
    ),
    (
        "timezone_unknown",
        _VALID_CALENDAR.replace('timezone = "America/New_York"', 'timezone = "Not/AZone"'),
        "unknown timezone 'Not/AZone'",
    ),
    (
        # G10: a path-like timezone string raises a bare ValueError from zoneinfo, not
        # ZoneInfoNotFoundError -- the old code only caught the latter.
        "timezone_path_like",
        _VALID_CALENDAR.replace('timezone = "America/New_York"', 'timezone = "/etc/passwd"'),
        "invalid timezone '/etc/passwd'",
    ),
    (
        "holidays_wrong_type",
        _VALID_CALENDAR.replace("holidays = [2026-01-13]", 'holidays = "notalist"'),
        "'holidays' must be a list of dates",
    ),
    (
        # G1: same datetime-vs-date defect as the rule fields, on the calendar side -- the
        # actual defect the director reproduced end to end (a holiday that silently stops being
        # a holiday).
        "holidays_contains_datetime",
        _VALID_CALENDAR.replace("holidays = [2026-01-13]", "holidays = [2026-01-13T00:00:00]"),
        "'holidays' must be a list of dates",
    ),
    (
        "weekend_invalid_values",
        _VALID_CALENDAR + "weekend = [7]\n",
        "'weekend' must be a list of ints in 0..6",
    ),
    (
        # G4: a weekend covering all seven weekdays leaves no reachable business day; rejected
        # at load time instead of letting add_business_days/roll_forward spin toward the
        # year-10000 wall.
        "weekend_covers_all_seven_days",
        _VALID_CALENDAR + "weekend = [0, 1, 2, 3, 4, 5, 6]\n",
        "'weekend' must leave at least one weekday free",
    ),
    (
        "law_unknown_field",
        _VALID_CALENDAR + '[law.TESTLAW]\ncalendar = "testland"\ntypo_key = 1\n',
        "unknown field(s)",
    ),
    (
        "law_missing_calendar_field",
        _VALID_CALENDAR + "[law.TESTLAW]\n",
        "missing required field 'calendar'",
    ),
    (
        "law_unknown_calendar_reference",
        _VALID_CALENDAR + '[law.TESTLAW]\ncalendar = "nosuchcalendar"\n',
        "unknown calendar 'nosuchcalendar'",
    ),
    (
        # G3, calendars.toml side: a mistyped top-level table name.
        "unknown_top_level_table",
        _VALID_CALENDAR.replace("[calendar.testland]", "[calendars.testland]"),
        "unknown top-level table(s) ['calendars']",
    ),
    (
        # G3, calendars.toml side: `[[calendar]]` (an array) instead of `[calendar.<key>]`
        # (sub-tables) parses "calendar" to a list, not a dict.
        "calendar_table_not_a_table",
        '[[calendar]]\ntimezone = "UTC"\nholidays = []\n',
        "'calendar' must be a table of sub-tables",
    ),
]


@pytest.mark.parametrize(
    "toml_text,message_fragment", [c[1:] for c in RULE_CASES], ids=[c[0] for c in RULE_CASES]
)
def test_load_rules_error_case(tmp_path: Path, toml_text: str, message_fragment: str) -> None:
    path = tmp_path / "rules.toml"
    path.write_text(toml_text)
    with pytest.raises(RulesDataError) as exc_info:
        load_rules(path)
    assert message_fragment in str(exc_info.value), str(exc_info.value)


@pytest.mark.parametrize(
    "toml_text,message_fragment",
    [c[1:] for c in CALENDAR_CASES],
    ids=[c[0] for c in CALENDAR_CASES],
)
def test_load_calendars_error_case(tmp_path: Path, toml_text: str, message_fragment: str) -> None:
    path = tmp_path / "calendars.toml"
    path.write_text(toml_text)
    with pytest.raises(RulesDataError) as exc_info:
        load_calendars(path)
    assert message_fragment in str(exc_info.value), str(exc_info.value)


def test_every_rule_case_name_is_unique() -> None:
    names = [c[0] for c in RULE_CASES]
    assert len(names) == len(set(names))


def test_every_calendar_case_name_is_unique() -> None:
    names = [c[0] for c in CALENDAR_CASES]
    assert len(names) == len(set(names))


def test_rule_and_calendar_cases_cover_every_raise_site_in_loader() -> None:
    """A coarse but real tripwire: count the RulesDataError raise statements in loader.py's
    source and assert this module has at least that many distinct cases. If a future edit adds a
    new validation branch without a matching case here, this fails instead of staying quiet."""
    import re
    from importlib import import_module

    loader_module = import_module("deadline_rules.loader")
    source = Path(loader_module.__file__).read_text()
    raise_sites = len(re.findall(r"raise RulesDataError", source))
    total_cases = len(RULE_CASES) + len(CALENDAR_CASES)
    assert total_cases >= raise_sites, (
        f"loader.py has {raise_sites} RulesDataError raise sites but only {total_cases} "
        "cases here cover them -- add a case for the new branch"
    )
