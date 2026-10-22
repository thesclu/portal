"""`compute_deadlines`: the one pure function that turns rules + a clock start into due dates."""

from collections.abc import Mapping, Sequence
from datetime import date

from .calendar import add_business_days, add_calendar_days, roll_forward
from .errors import NoRuleError, RulesDataError, UnknownCalendarError
from .loader import load_calendars, load_rules
from .model import (
    Calendar,
    DeadlineComputation,
    DeadlineSpec,
    DeadlineType,
    RequestItem,
    Rule,
    UnresolvedProvision,
)
from .select import select_rule

_DEADLINE_TYPES: tuple[DeadlineType, ...] = ("statutory_determination", "production")


def _due_date(rule: Rule, clock_start: date, cal: Calendar) -> date:
    if rule.unit == "calendar_days":
        raw = add_calendar_days(clock_start, rule.days)
    else:
        raw = add_business_days(clock_start, rule.days, cal)
    if rule.roll_forward_on_non_business_day:
        return roll_forward(raw, cal)
    return raw


def _resolve_calendar(
    *,
    records_law: str,
    jurisdiction: str | None,
    calendars: Mapping[str, Calendar],
    law_calendar: Mapping[str, str],
) -> Calendar:
    cal_key = jurisdiction if jurisdiction is not None else law_calendar.get(records_law)
    if cal_key is None or cal_key not in calendars:
        raise UnknownCalendarError(
            f"no calendar for records_law={records_law!r} jurisdiction={jurisdiction!r}"
        )
    return calendars[cal_key]


def _resolve_rule(
    rules: Sequence[Rule],
    *,
    records_law: str,
    provision: str,
    deadline_type: DeadlineType,
    clock_start: date,
    override_id: str | None,
) -> Rule | None:
    """An override id is matched by `id` ALONE -- it deliberately bypasses both `select_rule`'s
    effective-dating (a rule whose range has lapsed, or has not started yet relative to
    `clock_start`, is still accepted) and its provision matching (the override may name a rule
    for a different provision than `provision` asks for; see G8 in the fix-batch review -- the
    caller then gets a spec whose `provision` field reflects the rule actually used, not the
    provision that was asked for). This is deliberate, not an oversight: overriding to an
    explicitly-named rule is exactly what a human does when a request must be governed by a
    superseded version of the law (e.g. one filed while an earlier rule was in force), and the
    whole point of naming a rule id explicitly is to skip the effective-dating lookup that would
    otherwise reject it. `test_compute.py`'s
    `test_override_id_accepts_a_rule_whose_effective_range_has_lapsed` pins this as a decision.
    """
    if override_id is not None:
        for rule in rules:
            if rule.id == override_id:
                if rule.law != records_law or rule.deadline_type != deadline_type:
                    raise RulesDataError(
                        f"override rule {override_id!r} does not match "
                        f"law={records_law!r} deadline_type={deadline_type!r}"
                    )
                return rule
        raise RulesDataError(f"override rule {override_id!r} not found")
    return select_rule(
        rules, law=records_law, provision=provision, deadline_type=deadline_type, on=clock_start
    )


def compute_deadlines(
    *,
    records_law: str,
    provision: str | None,
    clock_start: date,
    items: Sequence[RequestItem] = (),
    rules: Sequence[Rule] | None = None,
    calendars: Mapping[str, Calendar] | None = None,
    law_calendar: Mapping[str, str] | None = None,
    jurisdiction: str | None = None,
    overrides: Mapping[DeadlineType, str] | None = None,
) -> DeadlineComputation:
    """Compute every deadline for a request: the request-level clocks plus any item carve-outs.

    Loads the shipped rules/calendars when `rules`/`calendars` are not given. The calendar comes
    from `calendars[jurisdiction]` if given, else `calendars[law_calendar[records_law]]` -- if
    neither resolves, this raises `UnknownCalendarError`. For each deadline type, the
    request-level rule is the override id in `overrides` if given (it must exist and match both
    `records_law` and the deadline type, else `RulesDataError`), else the rule selected for
    `provision or ""`. A missing general `statutory_determination` rule raises `NoRuleError`; a
    missing `production` rule simply produces no request-level row.

    Each item's provisions are looked up the same way per deadline type. A provision with no
    matching rule is NOT an error -- provisions are optional carve-outs, only the general
    determination clock is mandatory -- but it is not silently dropped either: it is recorded as
    an `UnresolvedProvision` in the returned `DeadlineComputation.unresolved`, naming the
    provision, the deadline type, and the request item, so a caller can raise its own
    missing-rule task instead of a real carve-out quietly producing no deadline. (This changed in
    the G5 fix-batch review: `compute_deadlines` used to return `list[DeadlineSpec]` and simply
    `continue` past an unresolved item provision, discarding the information at exactly the point
    it existed. See notes's P1 section and notes for the full rationale
    and what P2's `file_request` must do with `.unresolved`.)

    Returns a `DeadlineComputation` with `specs` (every resolved `DeadlineSpec`, sorted by
    `(deadline_type, due_at)`) and `unresolved` (every `UnresolvedProvision`, in the order
    encountered: by deadline type, then by item, then by provision).
    """
    if rules is None:
        rules = load_rules()
    if calendars is None:
        default_calendars, default_law_calendar = load_calendars()
        calendars = default_calendars
        if law_calendar is None:
            law_calendar = default_law_calendar
    if law_calendar is None:
        law_calendar = {}

    cal = _resolve_calendar(
        records_law=records_law,
        jurisdiction=jurisdiction,
        calendars=calendars,
        law_calendar=law_calendar,
    )
    general_provision = provision or ""
    specs: list[DeadlineSpec] = []
    unresolved: list[UnresolvedProvision] = []

    for deadline_type in _DEADLINE_TYPES:
        override_id = overrides.get(deadline_type) if overrides else None
        rule = _resolve_rule(
            rules,
            records_law=records_law,
            provision=general_provision,
            deadline_type=deadline_type,
            clock_start=clock_start,
            override_id=override_id,
        )
        if rule is None:
            if deadline_type == "statutory_determination":
                raise NoRuleError(records_law, deadline_type, general_provision)
            # production: no general rule in force yields no row, not an error.
        else:
            specs.append(
                DeadlineSpec(
                    deadline_type=deadline_type,
                    due_at=_due_date(rule, clock_start, cal),
                    legal_basis=records_law,
                    # G8: the rule ACTUALLY USED decides `provision`, not the `provision`
                    # argument the caller asked for -- these normally agree (`rule` was selected
                    # BY that exact provision), but an override can name a rule for a different
                    # one, and the emitted spec must not claim the general clock's provision
                    # while `rule_ref`/`citation` point at a named carve-out. `rule.provision`
                    # is `""` for the general rule; `DeadlineSpec.provision` uses `None` for that
                    # case, matching every other call site in this function.
                    provision=rule.provision or None,
                    request_item_id=None,
                    rule_ref=rule.id,
                    citation=rule.citation,
                )
            )

        for item in items:
            # G9: de-duplicate an item's provisions (order-preserving) so a request item that
            # lists the same carve-out twice -- a data-entry slip, not a meaningful signal --
            # produces one deadline row for that obligation, not two identical ledger rows.
            for item_provision in dict.fromkeys(item.provisions):
                item_rule = select_rule(
                    rules,
                    law=records_law,
                    provision=item_provision,
                    deadline_type=deadline_type,
                    on=clock_start,
                )
                if item_rule is None:
                    # G5: recorded, not discarded -- see DeadlineComputation/UnresolvedProvision
                    # in model.py and this function's docstring.
                    unresolved.append(
                        UnresolvedProvision(
                            deadline_type=deadline_type,
                            provision=item_provision,
                            request_item_id=item.id,
                        )
                    )
                    continue
                specs.append(
                    DeadlineSpec(
                        deadline_type=deadline_type,
                        due_at=_due_date(item_rule, clock_start, cal),
                        legal_basis=records_law,
                        provision=item_provision,
                        request_item_id=item.id,
                        rule_ref=item_rule.id,
                        citation=item_rule.citation,
                    )
                )

    specs.sort(key=lambda spec: (spec.deadline_type, spec.due_at))
    return DeadlineComputation(specs=tuple(specs), unresolved=tuple(unresolved))
