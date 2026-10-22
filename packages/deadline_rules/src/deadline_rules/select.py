"""Pick which rule is in force for a given law/provision/deadline-type on a given date."""

from collections.abc import Sequence
from datetime import date

from .errors import AmbiguousRuleError
from .model import DeadlineType, Rule


def _in_force(rule: Rule, on: date) -> bool:
    if rule.effective_from > on:
        return False
    return rule.effective_to is None or on < rule.effective_to


def select_rule(
    rules: Sequence[Rule],
    *,
    law: str,
    provision: str,
    deadline_type: DeadlineType,
    on: date,
) -> Rule | None:
    """The single rule in force for this exact (law, provision, deadline_type) on `on`.

    `effective_from` is inclusive, `effective_to` is exclusive. Returns `None` if nothing
    matches. Raises `AmbiguousRuleError` if more than one rule matches -- that is a data defect
    (an overlapping effective range), never a case to resolve silently by picking one.
    """
    matches = [
        rule
        for rule in rules
        if rule.law == law
        and rule.provision == provision
        and rule.deadline_type == deadline_type
        and _in_force(rule, on)
    ]
    if len(matches) > 1:
        ids = [rule.id for rule in matches]
        raise AmbiguousRuleError(
            f"ambiguous rule selection for law={law!r} provision={provision!r} "
            f"deadline_type={deadline_type!r} on={on.isoformat()}: matching rule ids {ids}"
        )
    return matches[0] if matches else None


def rules_in_force(rules: Sequence[Rule], *, law: str, on: date) -> tuple[Rule, ...]:
    """Every rule of any provision/deadline_type for `law` that is in force on `on`."""
    return tuple(rule for rule in rules if rule.law == law and _in_force(rule, on))
