"""Frozen data shapes for effective-dated public-records deadline rules.

Every dataclass here is `frozen=True, kw_only=True`. `kw_only` is required for `Rule` to compile
at all (`count_from` and `roll_forward_on_non_business_day` carry defaults while `effective_from`,
`effective_to` and `citation` -- which follow them in the field order BUILD_PLAN documents -- do
not; a positional dataclass cannot have a non-default field after a default one). The other three
dataclasses do not have that shape, but keyword-only construction is applied to all four anyway:
several fields share a plain `str` / `str | None` type, so positional construction would be an
easy transposition bug for any caller. Field order matches BUILD_PLAN exactly in every dataclass;
`kw_only` makes that order documentation rather than a construction contract.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

Unit = Literal["calendar_days", "business_days"]
DeadlineType = Literal["statutory_determination", "production"]


@dataclass(frozen=True, kw_only=True)
class Rule:
    """One effective-dated statutory clock. The only place a day count may live."""

    id: str  # stable, e.g. "testlaw.determination.general.2020-01-01"; becomes deadlines.rule_ref
    law: str  # matches agencies.records_law, e.g. "CPRA"
    provision: str  # "" = the general rule; else a named carve-out, e.g. "body_worn_camera"
    deadline_type: DeadlineType
    days: int
    unit: Unit
    count_from: Literal["clock_start"] = "clock_start"
    roll_forward_on_non_business_day: bool = True
    effective_from: date
    effective_to: date | None  # exclusive; None = still in force
    citation: str  # required, non-empty


@dataclass(frozen=True, kw_only=True)
class Calendar:
    """A named jurisdiction calendar: weekend definition plus a dated holiday list."""

    key: str
    timezone: str
    holidays: frozenset[date]
    # date.weekday(): Monday is zero, so the default {5, 6} is Saturday and Sunday.
    weekend: frozenset[int] = frozenset({5, 6})


@dataclass(frozen=True, kw_only=True)
class RequestItem:
    """One line item on a request; its provisions may carve out item-scoped deadlines."""

    id: UUID | str
    provisions: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class DeadlineSpec:
    """One computed deadline, ready to become a `records.deadlines` row."""

    deadline_type: DeadlineType
    due_at: date
    legal_basis: str
    provision: str | None
    request_item_id: UUID | str | None
    rule_ref: str
    citation: str


@dataclass(frozen=True, kw_only=True)
class UnresolvedProvision:
    """An item-level provision with no rule in force for it on the clock-start date.

    Item-level provisions are optional carve-outs -- a request item's `provisions` tuple is
    free text a human enters, and P1b (entering real rules) lands the general clocks first, so a
    carve-out with no matching rule yet is the expected, common case, not a data error. But the
    information that a real provision went unresolved must survive `compute_deadlines`, not be
    silently discarded -- this is the record of that: which item, which provision, for which
    deadline type. `deadline_type` is included because a provision is looked up independently per
    deadline type (a carve-out might have a production rule but no determination rule, or vice
    versa), so the same (item, provision) pair can appear here once per deadline type.
    """

    deadline_type: DeadlineType
    provision: str
    request_item_id: UUID | str


@dataclass(frozen=True, kw_only=True)
class DeadlineComputation:
    """The full result of `compute_deadlines`: every deadline it could resolve, plus every
    item-level provision it could not. `specs` alone was the old return shape (a plain list);
    silently dropping what couldn't be resolved threw away exactly the information a caller
    needs to raise the missing-rule task P1b's `data/rules.toml` entry will eventually satisfy.
    """

    specs: tuple[DeadlineSpec, ...]
    unresolved: tuple[UnresolvedProvision, ...]
