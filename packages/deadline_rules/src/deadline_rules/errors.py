"""Exception hierarchy for `deadline_rules`.

One shared root so callers can catch every failure this package can raise with a single
`except DeadlineRulesError`, plus four leaf types for the distinct failure shapes: malformed or
invalid rules/calendars data, no matching rule, more than one matching rule, and an unresolvable
calendar.
"""


class DeadlineRulesError(Exception):
    """Base class for every error `deadline_rules` raises."""


class RulesDataError(DeadlineRulesError):
    """The rules or calendars data is malformed, incomplete, or violates a data invariant."""


class NoRuleError(DeadlineRulesError):
    """No rule is in force for the given law, deadline type, and provision on the given date."""

    def __init__(self, law: str, deadline_type: str, provision: str) -> None:
        self.law = law
        self.deadline_type = deadline_type
        self.provision = provision
        super().__init__(
            f"no rule in force for law={law!r} deadline_type={deadline_type!r} "
            f"provision={provision!r}"
        )


class AmbiguousRuleError(DeadlineRulesError):
    """More than one rule matches the same selection -- the data has an overlap."""


class UnknownCalendarError(DeadlineRulesError):
    """No calendar could be resolved for the given jurisdiction key or law."""
