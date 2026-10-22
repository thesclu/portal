"""Unit tests for deadline_rules.select: select_rule and rules_in_force.

Uses hand-built Rule objects (not the TOML fixtures) so the ambiguous-match and boundary cases
are precise and don't depend on the shape of rules_test.toml.
"""

from datetime import date

import pytest
from deadline_rules.errors import AmbiguousRuleError
from deadline_rules.model import Rule
from deadline_rules.select import rules_in_force, select_rule

EARLY = Rule(
    id="testlaw.determination.general.2020-01-01",
    law="TESTLAW",
    provision="",
    deadline_type="statutory_determination",
    days=7,
    unit="calendar_days",
    effective_from=date(2020, 1, 1),
    effective_to=date(2026, 6, 1),
    citation="TESTLAW § 1(a) (fictional, for tests only)",
)
LATE = Rule(
    id="testlaw.determination.general.2026-06-01",
    law="TESTLAW",
    provision="",
    deadline_type="statutory_determination",
    days=9,
    unit="calendar_days",
    effective_from=date(2026, 6, 1),
    effective_to=None,
    citation="TESTLAW § 1(b) (fictional, for tests only)",
)
WIDGETS = Rule(
    id="testlaw.production.widgets.2020-01-01",
    law="TESTLAW",
    provision="widgets",
    deadline_type="production",
    days=12,
    unit="business_days",
    effective_from=date(2020, 1, 1),
    effective_to=None,
    citation="TESTLAW § 2 (fictional, for tests only)",
)
RULES = (EARLY, LATE, WIDGETS)


def test_select_rule_returns_none_when_nothing_matches() -> None:
    assert (
        select_rule(
            RULES,
            law="OTHERLAW",
            provision="",
            deadline_type="statutory_determination",
            on=date(2026, 9, 8),
        )
        is None
    )


def test_select_rule_returns_the_single_match() -> None:
    rule = select_rule(
        RULES, law="TESTLAW", provision="widgets", deadline_type="production", on=date(2026, 9, 8)
    )
    assert rule is WIDGETS


def test_select_rule_effective_from_is_inclusive() -> None:
    rule = select_rule(
        (EARLY,),
        law="TESTLAW",
        provision="",
        deadline_type="statutory_determination",
        on=date(2020, 1, 1),
    )
    assert rule is EARLY


def test_select_rule_effective_to_is_exclusive() -> None:
    """On effective_to itself, the rule no longer applies -- this is what makes EARLY/LATE
    non-ambiguous at their shared boundary date."""
    rule = select_rule(
        (EARLY,),
        law="TESTLAW",
        provision="",
        deadline_type="statutory_determination",
        on=date(2026, 6, 1),
    )
    assert rule is None


def test_select_rule_raises_on_more_than_one_match() -> None:
    """Two rules sharing (law, provision, deadline_type) with overlapping effective ranges is a
    data defect -- select_rule must refuse to silently pick one."""
    overlapping = Rule(
        id="testlaw.determination.general.overlap",
        law="TESTLAW",
        provision="",
        deadline_type="statutory_determination",
        days=5,
        unit="calendar_days",
        effective_from=date(2019, 1, 1),
        effective_to=date(2021, 1, 1),
        citation="TESTLAW § 1(c) (fictional, for tests only, deliberately overlaps EARLY)",
    )
    with pytest.raises(AmbiguousRuleError):
        select_rule(
            (EARLY, overlapping),
            law="TESTLAW",
            provision="",
            deadline_type="statutory_determination",
            on=date(2020, 6, 1),
        )


def test_rules_in_force_returns_every_rule_of_any_provision_or_type_for_the_law() -> None:
    in_force = rules_in_force(RULES, law="TESTLAW", on=date(2026, 9, 8))
    assert set(in_force) == {LATE, WIDGETS}


def test_rules_in_force_excludes_a_rule_whose_range_has_lapsed() -> None:
    in_force = rules_in_force(RULES, law="TESTLAW", on=date(2026, 9, 8))
    assert EARLY not in in_force


def test_rules_in_force_returns_nothing_for_an_unknown_law() -> None:
    assert rules_in_force(RULES, law="NOSUCHLAW", on=date(2026, 9, 8)) == ()
