"""Table-driven tests for compute_deadlines, reading tests/cases.toml.

Each case supplies clock_start (and optionally items/overrides) against the fixtures in
tests/fixtures/ (law TESTLAW, calendar testland) and either a list of expected DeadlineSpec
field sets or an expected error name. See cases.toml's header for the exact schema and why each
scenario is there.
"""

import tomllib
from datetime import date
from pathlib import Path

import pytest
from deadline_rules import errors as errors_module
from deadline_rules.compute import compute_deadlines
from deadline_rules.errors import AmbiguousRuleError, UnknownCalendarError
from deadline_rules.loader import load_calendars, load_rules
from deadline_rules.model import RequestItem, Rule

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CASES_PATH = Path(__file__).parent / "cases.toml"
RECORDS_LAW = "TESTLAW"  # every case.toml case runs under the fixture's one law

RULES = load_rules(FIXTURES_DIR / "rules_test.toml")
CALENDARS, LAW_CALENDAR = load_calendars(FIXTURES_DIR / "calendars_test.toml")

with CASES_PATH.open("rb") as _f:
    _CASES_DATA = tomllib.load(_f)
CASES = _CASES_DATA["case"]


def _build_items(case: dict) -> tuple[RequestItem, ...]:
    return tuple(
        RequestItem(id=item["id"], provisions=tuple(item.get("provisions", ())))
        for item in case.get("items", [])
    )


def _spec_as_dict(spec) -> dict:
    return {
        "deadline_type": spec.deadline_type,
        "due_at": spec.due_at,
        "provision": spec.provision,
        "request_item_id": spec.request_item_id,
        "rule_ref": spec.rule_ref,
        "citation": spec.citation,
        "legal_basis": spec.legal_basis,
    }


def _expected_as_dict(expected: dict) -> dict:
    # provision / request_item_id are OMITTED in cases.toml to mean None -- TOML has no null.
    return {
        "deadline_type": expected["deadline_type"],
        "due_at": expected["due_at"],
        "provision": expected.get("provision"),
        "request_item_id": expected.get("request_item_id"),
        "rule_ref": expected["rule_ref"],
        "citation": expected["citation"],
        "legal_basis": RECORDS_LAW,
    }


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case: dict) -> None:
    assert len(CASES) >= 10, "cases.toml must carry at least the 10 required scenarios"

    kwargs = dict(
        records_law=RECORDS_LAW,
        provision=case.get("provision"),
        clock_start=case["clock_start"],
        items=_build_items(case),
        rules=RULES,
        calendars=CALENDARS,
        law_calendar=LAW_CALENDAR,
        overrides=case.get("overrides"),
    )

    if "error" in case:
        error_cls = getattr(errors_module, case["error"])
        with pytest.raises(error_cls):
            compute_deadlines(**kwargs)
        return

    result = compute_deadlines(**kwargs)
    actual = [_spec_as_dict(spec) for spec in result.specs]
    expected = [_expected_as_dict(e) for e in case["expected"]]
    assert actual == expected


def test_every_case_is_either_a_success_or_an_error_case() -> None:
    for case in CASES:
        has_expected = "expected" in case
        has_error = "error" in case
        assert has_expected != has_error, (
            f"case {case['name']!r} must have exactly one of 'expected' or 'error', "
            f"got expected={has_expected} error={has_error}"
        )


def test_cases_cover_every_required_scenario_shape() -> None:
    """BUILD_PLAN names seven required scenario shapes; assert each is exercised by name rather
    than trusting the count alone."""
    names = " ".join(c["name"] for c in CASES)
    for fragment in (
        "weekend_roll",
        "holiday_roll",
        "effective_from_is_inclusive",
        "effective_to_is_exclusive",
        "item_scoped",
        "override_id",
        "missing_production_rule",
        "missing_determination_rule",
    ):
        assert fragment in names, f"no case name mentions {fragment!r}"


# ---- G2: UnknownCalendarError and AmbiguousRuleError reached THROUGH compute_deadlines, not
# just through select_rule/_resolve_calendar in isolation. Neither case fits cases.toml's schema
# (every case shares the same RULES/CALENDARS/LAW_CALENDAR), so these are standalone.


def test_compute_deadlines_raises_unknown_calendar_error_for_unresolvable_jurisdiction() -> None:
    with pytest.raises(UnknownCalendarError):
        compute_deadlines(
            records_law=RECORDS_LAW,
            provision=None,
            clock_start=date(2026, 9, 8),
            rules=RULES,
            calendars=CALENDARS,
            law_calendar=LAW_CALENDAR,
            jurisdiction="nosuchjurisdiction",
        )


def test_compute_deadlines_raises_ambiguous_rule_error_for_overlapping_rules() -> None:
    """Two rules sharing (law, provision, deadline_type) with overlapping effective ranges is a
    data defect -- test_select.py pins this at select_rule's level directly; this pins the same
    defect surfacing through the public compute_deadlines entry point, which is what callers
    (P2's file_request) actually call."""
    general_rule = next(
        r for r in RULES if r.provision == "" and r.deadline_type == "statutory_determination"
    )
    colliding = Rule(
        id="testlaw.determination.general.deliberately-overlapping",
        law=general_rule.law,
        provision=general_rule.provision,
        deadline_type=general_rule.deadline_type,
        days=1,
        unit="calendar_days",
        effective_from=date(2021, 1, 1),  # inside general_rule's 2020-01-01..2026-06-01 range
        effective_to=date(2022, 1, 1),
        citation="TESTLAW § 1(z) (fictional, for tests only, deliberately overlapping)",
    )
    overlapping_rules = (*RULES, colliding)
    with pytest.raises(AmbiguousRuleError):
        compute_deadlines(
            records_law=RECORDS_LAW,
            provision=None,
            clock_start=date(2021, 6, 1),  # inside both EARLY and colliding's ranges
            rules=overlapping_rules,
            calendars=CALENDARS,
            law_calendar=LAW_CALENDAR,
        )


# ---- G8: `overrides` bypasses select_rule's effective-dating AND its provision matching --
# deliberately, for the first; the second must not leave an inconsistent spec behind.


def test_override_id_accepts_a_rule_whose_effective_range_has_lapsed() -> None:
    """This is a decision, not an oversight (see _resolve_rule's docstring): overriding to an
    explicitly-named rule id is exactly what a human does to govern a request under a superseded
    version of the law. `clock_start` here is 2026-09-08, well past the EARLY rule's
    effective_to (2026-06-01) -- an un-overridden lookup would select the LATE rule instead (9
    days); the override forces EARLY (7 days) anyway."""
    early_rule_id = "testlaw.determination.general.2020-01-01"
    result = compute_deadlines(
        records_law=RECORDS_LAW,
        provision=None,
        clock_start=date(2026, 9, 8),
        rules=RULES,
        calendars=CALENDARS,
        law_calendar=LAW_CALENDAR,
        overrides={"statutory_determination": early_rule_id},
    )
    determination = next(s for s in result.specs if s.deadline_type == "statutory_determination")
    assert determination.rule_ref == early_rule_id
    assert determination.due_at == date(2026, 9, 15)  # 7 calendar days, no roll needed


def test_override_id_to_a_different_provision_matches_the_rule_used() -> None:
    """Before the G8 fix, this spec's `provision` field echoed the caller's request-level
    `provision` argument (None, asking for the general clock) even though the override forced a
    NAMED-carve-out rule (`provision="widgets"`) -- an internally inconsistent
    `records.deadlines` row (`rule_ref`/`citation` naming widgets, `provision` claiming the
    general clock). The fixture has no general `production` rule, so without the override this
    deadline_type would simply produce no row (see `missing_production_rule_yields_no_row`)."""
    widgets_rule_id = "testlaw.production.widgets.2020-01-01"
    result = compute_deadlines(
        records_law=RECORDS_LAW,
        provision=None,
        clock_start=date(2026, 9, 8),
        rules=RULES,
        calendars=CALENDARS,
        law_calendar=LAW_CALENDAR,
        overrides={"production": widgets_rule_id},
    )
    production = next(s for s in result.specs if s.deadline_type == "production")
    assert production.rule_ref == widgets_rule_id
    assert production.provision == "widgets"


# ---- G9: duplicate provisions on one item must not emit duplicate specs.


def test_item_scoped_duplicate_provisions_emit_only_one_spec() -> None:
    item = RequestItem(id="item-dup", provisions=("widgets", "widgets"))
    result = compute_deadlines(
        records_law=RECORDS_LAW,
        provision=None,
        clock_start=date(2026, 2, 2),
        items=(item,),
        rules=RULES,
        calendars=CALENDARS,
        law_calendar=LAW_CALENDAR,
    )
    widgets_specs = [s for s in result.specs if s.provision == "widgets"]
    assert len(widgets_specs) == 1, widgets_specs


# ---- G5: an unresolved item provision is recorded, not silently dropped.


def test_unresolved_item_provision_is_recorded_and_produces_no_spec() -> None:
    item = RequestItem(id="item-missing", provisions=("nonexistent_provision",))
    result = compute_deadlines(
        records_law=RECORDS_LAW,
        provision=None,
        clock_start=date(2026, 9, 8),
        items=(item,),
        rules=RULES,
        calendars=CALENDARS,
        law_calendar=LAW_CALENDAR,
    )
    assert all(spec.provision != "nonexistent_provision" for spec in result.specs)

    for_item = [u for u in result.unresolved if u.request_item_id == "item-missing"]
    # Looked up independently per deadline type -- neither TESTLAW deadline type has a rule for
    # this provision, so both are recorded.
    assert {u.deadline_type for u in for_item} == {"statutory_determination", "production"}
    for unresolved_provision in for_item:
        assert unresolved_provision.provision == "nonexistent_provision"
        assert unresolved_provision.request_item_id == "item-missing"
