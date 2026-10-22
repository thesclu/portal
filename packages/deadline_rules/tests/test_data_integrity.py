"""Data-shape invariants for both the shipped data and the test fixtures.

The shipped rules are now entered, so the invariants below are stated as REQUIREMENTS of that
data rather than as snapshots of it: each is written to fail in the direction that actually
breaks a filing. The overlap and citation helpers also run against the TEST fixtures, which
carry their own rules under the fictional law TESTLAW.

The zero-rules tripwire that guarded this file before the rules were entered has been retired;
it existed to force exactly this review and has served its purpose.
"""

import re
from datetime import date
from itertools import combinations
from pathlib import Path

from deadline_rules.loader import load_calendars, load_rules
from deadline_rules.model import Rule

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _ranges_overlap(a: Rule, b: Rule) -> bool:
    """True if a and b share (law, provision, deadline_type) and their effective ranges overlap.

    effective_from is inclusive, effective_to is exclusive (None = unbounded). Two half-open
    ranges [a_from, a_to) and [b_from, b_to) overlap iff a_from < b_to and b_from < a_to.
    """
    if (a.law, a.provision, a.deadline_type) != (b.law, b.provision, b.deadline_type):
        return False
    a_to = a.effective_to
    b_to = b.effective_to
    a_before_b_ends = b_to is None or a.effective_from < b_to
    b_before_a_ends = a_to is None or b.effective_from < a_to
    return a_before_b_ends and b_before_a_ends


def _overlapping_pairs(rules: tuple[Rule, ...]) -> list[tuple[Rule, Rule]]:
    return [(a, b) for a, b in combinations(rules, 2) if _ranges_overlap(a, b)]


# ---- shipped data: requirements now that the real rules are entered ------------------------

# For these laws the determination must state an estimated date of availability and the statute
# sets no numeric production count of its own. Their general production deadline is the agency's
# own stated date, opened per request as basis="agency_commitment" with a null rule_ref.
LAWS_WITHOUT_A_STATUTORY_PRODUCTION_COUNT = frozenset(
    {"CPRA", "UC_FOUNDATION", "CSU_AUXILIARY", "CCD_AUXILIARY"}
)

ID_CONVENTION = re.compile(
    r"^[a-z0-9_]+\.(determination|production)\.[a-z0-9_]+\.\d{4}-\d{2}-\d{2}$"
)


def test_shipped_rules_are_not_empty() -> None:
    """Without this every check below passes vacuously against an emptied file, and
    compute_deadlines raises NoRuleError on every filing."""
    assert load_rules(), "data/rules.toml carries no rules; every filing would raise NoRuleError"


def test_shipped_rules_have_no_overlapping_effective_ranges() -> None:
    """An overlap is the one defect select_rule refuses to resolve silently: it raises
    AmbiguousRuleError at lookup time, mid-filing, rather than at load."""
    overlaps = _overlapping_pairs(load_rules())
    assert not overlaps, [(a.id, b.id) for a, b in overlaps]


def test_every_shipped_rule_has_a_non_empty_citation() -> None:
    """The loader enforces this per entry; asserting it over the shipped set makes it a property
    of the data rather than of the parser."""
    for rule in load_rules():
        assert rule.citation.strip(), f"rule {rule.id!r} has an empty citation"


def test_every_shipped_rule_id_follows_the_documented_convention() -> None:
    """`id` becomes deadlines.rule_ref and is written into ledger rows, so a drifting id is not
    cosmetic -- it makes a stored rule_ref unresolvable after the fact."""
    for rule in load_rules():
        assert ID_CONVENTION.match(rule.id), f"rule id {rule.id!r} breaks the documented shape"
        kind = "determination" if rule.deadline_type == "statutory_determination" else "production"
        expected = (
            f"{rule.law.lower()}.{kind}."
            f"{rule.provision or 'general'}.{rule.effective_from.isoformat()}"
        )
        assert rule.id == expected, f"rule id {rule.id!r} should be {expected!r}"


def test_no_general_production_rule_where_the_statute_sets_no_count() -> None:
    """A count in that slot could only be invented, and would pre-empt the agency-commitment row
    with a fake statutory one."""
    offenders = [
        r.id
        for r in load_rules()
        if r.deadline_type == "production"
        and r.provision == ""
        and r.law in LAWS_WITHOUT_A_STATUTORY_PRODUCTION_COUNT
    ]
    assert not offenders, offenders


def test_every_law_with_shipped_rules_has_a_calendar() -> None:
    """Stated in the direction that breaks: a rule under an unmapped law loads fine and then
    raises UnknownCalendarError inside compute_deadlines at filing time. The reverse is not an
    error -- mapping a law before entering its clocks is the normal order of work."""
    calendars, law_calendar = load_calendars()
    unmapped = sorted({r.law for r in load_rules()} - set(law_calendar))
    assert not unmapped, f"laws carrying rules but with no calendar mapping: {unmapped}"
    dangling = sorted({k for k in law_calendar.values() if k not in calendars})
    assert not dangling, f"law -> calendar mapping points at missing calendars: {dangling}"


# The range notes Q5 commits to. Extend BOTH this and calendars.toml together.
_HOLIDAY_RANGE = range(2012, 2031)

# How much runway the holiday list must keep ahead of today before the expiry tripwire below
# fires. The longest clock in rules.toml is 45 calendar days, so the point of failure is only
# ~2 months from the edge; a year is deliberately generous, so the list gets extended as a
# considered piece of research rather than in a hurry after someone notices a wrong date.
_REQUIRED_RUNWAY_DAYS = 365


def test_shipped_california_calendar_has_holidays() -> None:
    """Replaces the old no-holidays pin, which fired when the list was entered (2026-09-08). The
    inverse is now the invariant: an empty list means roll_forward catches only weekends, so every
    due date landing on a weekday holiday is reported EARLY."""
    calendars, _ = load_calendars()
    assert calendars["california"].holidays, "california calendar lists no holidays"


def test_shipped_holidays_cover_the_committed_year_range_with_no_gap() -> None:
    """A gap year is the failure mode a non-empty check cannot see: dates would simply stop
    rolling for that year, silently and only for requests whose clock lands in it."""
    calendars, _ = load_calendars()
    years = {d.year for d in calendars["california"].holidays}
    missing = sorted(set(_HOLIDAY_RANGE) - years)
    assert not missing, f"holiday list has no entries for: {missing}"
    stray = sorted(y for y in years if y not in _HOLIDAY_RANGE)
    assert not stray, f"holiday list has entries outside the committed range: {stray}"


def test_no_shipped_holiday_falls_on_a_weekend() -> None:
    """Weekends are already excluded by `weekend`, so a weekend entry buys nothing and is a
    reliable sign of a bad paste or a mis-set observance rule."""
    calendars, _ = load_calendars()
    for key, cal in calendars.items():
        weekend_dates = sorted(d for d in cal.holidays if d.weekday() in cal.weekend)
        assert not weekend_dates, f"calendar {key!r} lists weekend dates: {weekend_dates}"


def test_holiday_list_reaches_back_to_the_earliest_rule() -> None:
    """The expiry problem at the other end. A request filed under a rule older than the holiday
    list computes against a calendar with no holidays for its year, so it under-rolls exactly as
    it would past the far edge -- and every other check here still passes, because the list is
    non-empty, gap-free and weekend-clean within its own range."""
    earliest_rule_year = min(rule.effective_from.year for rule in load_rules())
    assert min(_HOLIDAY_RANGE) <= earliest_rule_year, (
        f"holiday list starts {min(_HOLIDAY_RANGE)} but rules.toml has a rule effective from "
        f"{earliest_rule_year}; a clock starting in that gap would not roll off any holiday"
    )


def test_holiday_list_has_not_started_to_expire() -> None:
    """The one test in this suite that will start failing on its own, by design.

    The holiday list is literal dates, not a generating rule, so it expires. Once today passes its
    last committed year every computed due date silently stops rolling off holidays -- and nothing
    else here would notice, because the list is still non-empty, still gap-free, still
    weekend-clean. Only the calendar moving makes it wrong.

    WHEN THIS FAILS: extend `holidays` in calendars.toml and bump `_HOLIDAY_RANGE` above, together.
    Read notes Q2 first -- it records which holiday set was chosen and which days are
    deliberately NOT encoded (Lunar New Year, Diwali, Good Friday, proclaimed days), so the list is
    not "completed" by someone filling in what looks missing.
    """
    covered_through = date(max(_HOLIDAY_RANGE), 12, 31)
    runway = (covered_through - date.today()).days
    assert runway >= _REQUIRED_RUNWAY_DAYS, (
        f"the holiday list runs out on {covered_through.isoformat()} -- {runway} days of runway "
        f"left, and this test requires {_REQUIRED_RUNWAY_DAYS}. Extend calendars.toml and "
        f"_HOLIDAY_RANGE together; see notes Q2 (which set) and Q5 (range)."
    )


def test_shipped_holidays_spot_check_known_dates() -> None:
    """Four dates chosen because each proves a different decision in notes Q2/Q5 survived
    the paste -- a bare count would pass even if the wrong list were pasted in."""
    holidays = load_calendars()[0]["california"].holidays
    # New Year's Day 2026, a Thursday: the before/after probe in the task brief turns on it.
    assert date(2026, 1, 1) in holidays
    # Day after Thanksgiving: in Code Civ. Proc. s 135 but NOT Gov. Code s 6700 -- proves the
    # union of both chains was encoded, not just the Gov. Code list.
    assert date(2026, 11, 27) in holidays
    # Admission Day: in Gov. Code s 6700 but expressly EXCLUDED from s 135 -- proves the other
    # direction of the union.
    assert date(2025, 9, 9) in holidays
    # Juneteenth is honoured from its 2023 operative date, not backfilled: present 2023, absent
    # 2019 (a Wednesday, so its absence cannot be explained by the weekend filter).
    assert date(2023, 6, 19) in holidays
    assert date(2019, 6, 19) not in holidays


# ---- test fixtures: the checks that need data to have teeth --------------------------------


def test_fixture_rules_toml_actually_has_rules() -> None:
    """Guards against the fixture itself silently going empty and every other check here
    passing vacuously."""
    rules = load_rules(FIXTURES_DIR / "rules_test.toml")
    assert len(rules) == 3


def test_fixture_rules_have_no_overlapping_effective_ranges() -> None:
    rules = load_rules(FIXTURES_DIR / "rules_test.toml")
    overlaps = _overlapping_pairs(rules)
    assert overlaps == [], f"overlapping rule pairs: {[(a.id, b.id) for a, b in overlaps]}"


def test_overlap_checker_actually_detects_an_overlap() -> None:
    """Positive control for the helper above: without this, 'no overlaps found' on the real
    fixture is unfalsifiable -- it could mean the checker works, or that it never fires at all.
    Two synthetic rules sharing (law, provision, deadline_type) with overlapping ranges must be
    flagged.
    """
    rules = load_rules(FIXTURES_DIR / "rules_test.toml")
    general_rule = next(
        r for r in rules if r.provision == "" and r.deadline_type == "statutory_determination"
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
    overlaps = _overlapping_pairs((*rules, colliding))
    assert (general_rule, colliding) in overlaps or (colliding, general_rule) in overlaps


def test_fixture_rules_all_have_a_non_empty_citation() -> None:
    rules = load_rules(FIXTURES_DIR / "rules_test.toml")
    assert len(rules) > 0
    for rule in rules:
        assert rule.citation.strip(), f"rule {rule.id!r} has an empty citation"
