"""Effective-dated public-records deadline rules. Pure Python; the only place a statutory day
count may live -- ships with zero live rules (see data/rules.toml).
"""

from .calendar import today_in
from .compute import compute_deadlines
from .errors import (
    AmbiguousRuleError,
    DeadlineRulesError,
    NoRuleError,
    RulesDataError,
    UnknownCalendarError,
)
from .loader import load_calendars, load_rules
from .model import (
    Calendar,
    DeadlineComputation,
    DeadlineSpec,
    DeadlineType,
    RequestItem,
    Rule,
    Unit,
    UnresolvedProvision,
)

__all__ = [
    "AmbiguousRuleError",
    "Calendar",
    "DeadlineComputation",
    "DeadlineRulesError",
    "DeadlineSpec",
    "DeadlineType",
    "NoRuleError",
    "RequestItem",
    "Rule",
    "RulesDataError",
    "Unit",
    "UnknownCalendarError",
    "UnresolvedProvision",
    "compute_deadlines",
    "load_calendars",
    "load_rules",
    "today_in",
]
