"""The rules and calendars as tables (P18b, DECISIONS 23): what `records.services.common.rule_data`
reads, and what an administrator edits from Settings → Rules & calendars.

Nothing here is a rule: every count, date and citation comes from a person. The checks are the
ones `deadline_rules.loader` makes on the file (the unit and the deadline type from a short list,
a positive count, an end date after the start, a citation on every rule), so a row here is a row
the package would have accepted. A rule is retired by an end date and never deleted: a deadline
may name it (`deadlines.rule_ref`). A calendar is never deleted either; a law can be pointed at
another one.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from deadline_rules import Calendar, Rule
from deadline_rules.model import DeadlineType, Unit
from django.db import transaction

from portal.core.models import CalendarDefinition, CalendarHoliday, DeadlineRule, LawCalendar, User
from portal.core.services import activity
from portal.records.actors import Actor
from portal.records.services.common import InvalidTransition, NotFound, ServiceError

UNITS = tuple(get_args(Unit))
DEADLINE_TYPES = tuple(get_args(DeadlineType))
RULE_FIELDS = (
    "law",
    "provision",
    "deadline_type",
    "count",
    "unit",
    "roll_forward_on_non_business_day",
    "effective_from",
    "effective_to",
    "citation",
)


@dataclass(frozen=True)
class Loaded:
    rules: tuple[Rule, ...]
    calendars: dict[str, Calendar]
    law_calendar: dict[str, str]
    empty: bool


def load() -> Loaded:
    """The tables, in the package's own shapes. `empty` says no row of any kind exists yet."""
    calendars: dict[str, Calendar] = {}
    for row in CalendarDefinition.objects.prefetch_related("holidays"):
        calendars[row.key] = Calendar(
            key=row.key,
            timezone=row.timezone,
            holidays=frozenset(h.date for h in row.holidays.all()),
            weekend=frozenset(int(d) for d in (row.weekend or [])),
        )
    law_calendar = {row.law: row.calendar_id for row in LawCalendar.objects.all()}
    rules = tuple(_rule_of(row) for row in DeadlineRule.objects.all())
    return Loaded(
        rules=rules,
        calendars=calendars,
        law_calendar=law_calendar,
        empty=not rules and not calendars,
    )


def _rule_of(row: DeadlineRule) -> Rule:
    return Rule(
        id=row.rule_id,
        law=row.law,
        provision=row.provision,
        deadline_type=row.deadline_type,  # type: ignore[arg-type]
        days=row.count,
        unit=row.unit,  # type: ignore[arg-type]
        roll_forward_on_non_business_day=row.roll_forward_on_non_business_day,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        citation=row.citation,
    )


def _text(value: Any, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ServiceError(f"{what} is required.")
    return text


def _check_rule(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    out["law"] = _text(out.get("law"), "The law")
    out["provision"] = str(out.get("provision") or "").strip()
    if out.get("deadline_type") not in DEADLINE_TYPES:
        raise ServiceError(f"The deadline type is one of {', '.join(DEADLINE_TYPES)}.")
    count = out.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ServiceError("The count is a whole number above zero.")
    if out.get("unit") not in UNITS:
        raise ServiceError(f"The unit is one of {', '.join(UNITS)}.")
    out["roll_forward_on_non_business_day"] = bool(
        out.get("roll_forward_on_non_business_day", True)
    )
    starts = out.get("effective_from")
    ends = out.get("effective_to")
    if not isinstance(starts, date):
        raise ServiceError("The date the rule takes effect is required.")
    if ends is not None and (not isinstance(ends, date) or ends <= starts):
        raise ServiceError("The end date must be after the date the rule takes effect.")
    out["citation"] = _text(out.get("citation"), "The citation")
    return out


@transaction.atomic
def add_rule(user: User, *, rule_id: str, **fields: Any) -> DeadlineRule:
    rule_id = _text(rule_id, "The rule id")
    if DeadlineRule.objects.filter(pk=rule_id).exists():
        raise InvalidTransition(f"A rule named {rule_id} already exists.")
    checked = _check_rule({k: fields.get(k) for k in RULE_FIELDS})
    if checked["law"] not in dict(LawCalendar.objects.values_list("law", "calendar_id")):
        raise ServiceError(
            f"{checked['law']} has no calendar yet; map the law to a calendar first."
        )
    last = DeadlineRule.objects.order_by("-position").values_list("position", flat=True).first()
    row = DeadlineRule.objects.create(
        rule_id=rule_id, updated_by=user, position=(last or 0) + 1, **checked
    )
    activity.record(
        Actor.human(user),
        "rule_added",
        f"Added the rule {rule_id} ({checked['citation'][:80]})",
        subject_type="rule",
        data={"rule_id": rule_id, **_jsonable(checked)},
    )
    return row


@transaction.atomic
def update_rule(user: User, rule_id: str, **fields: Any) -> DeadlineRule:
    row = DeadlineRule.objects.select_for_update().filter(pk=rule_id).first()
    if row is None:
        raise NotFound(f"No rule {rule_id}.")
    unknown = set(fields) - set(RULE_FIELDS)
    if unknown:
        raise ServiceError(f"Not editable: {sorted(unknown)}.")
    merged = {k: getattr(row, k) for k in RULE_FIELDS}
    merged.update({k: v for k, v in fields.items()})
    checked = _check_rule(merged)
    changed = {k: v for k, v in checked.items() if getattr(row, k) != v}
    if not changed:
        return row
    DeadlineRule.objects.filter(pk=rule_id).update(updated_by=user, **changed)
    activity.record(
        Actor.human(user),
        "rule_changed",
        f"Changed the rule {rule_id}: {', '.join(sorted(changed))}",
        subject_type="rule",
        data={
            "rule_id": rule_id,
            "old": _jsonable({k: getattr(row, k) for k in changed}),
            "new": _jsonable(changed),
        },
    )
    return DeadlineRule.objects.get(pk=rule_id)


def retire_rule(user: User, rule_id: str, *, effective_to: date) -> DeadlineRule:
    return update_rule(user, rule_id, effective_to=effective_to)


def _jsonable(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in fields.items()}


# ---- calendars ------------------------------------------------------------------------------


def _check_calendar(timezone: Any, weekend: Any) -> tuple[str, list[int]]:
    zone = _text(timezone, "The time zone")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ServiceError(f"{zone} is not a time zone name.") from exc
    chosen = weekend if weekend is not None else [5, 6]
    if (
        not isinstance(chosen, list)
        or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 or d > 6 for d in chosen)
        or len(set(chosen)) != len(chosen)
    ):
        raise ServiceError("The weekend is a list of weekday numbers, Monday being zero.")
    return zone, sorted(chosen)


@transaction.atomic
def add_calendar(
    user: User, *, key: str, timezone: str, weekend: list[int] | None = None
) -> CalendarDefinition:
    key = _text(key, "The calendar key").lower()
    if CalendarDefinition.objects.filter(pk=key).exists():
        raise InvalidTransition(f"A calendar named {key} already exists.")
    zone, days = _check_calendar(timezone, weekend)
    row = CalendarDefinition.objects.create(key=key, timezone=zone, weekend=days)
    activity.record(
        Actor.human(user), "calendar_added", f"Added the calendar {key}", subject_type="calendar"
    )
    return row


@transaction.atomic
def update_calendar(
    user: User, key: str, *, timezone: str | None = None, weekend: list[int] | None = None
) -> CalendarDefinition:
    row = CalendarDefinition.objects.select_for_update().filter(pk=key).first()
    if row is None:
        raise NotFound(f"No calendar {key}.")
    zone, days = _check_calendar(
        timezone if timezone is not None else row.timezone,
        weekend if weekend is not None else row.weekend,
    )
    if zone == row.timezone and days == list(row.weekend):
        return row
    CalendarDefinition.objects.filter(pk=key).update(timezone=zone, weekend=days)
    activity.record(
        Actor.human(user),
        "calendar_changed",
        f"Changed the calendar {key}",
        subject_type="calendar",
        data={
            "old": {"timezone": row.timezone, "weekend": row.weekend},
            "new": {"timezone": zone, "weekend": days},
        },
    )
    return CalendarDefinition.objects.get(pk=key)


@transaction.atomic
def add_holidays(user: User, key: str, dates: list[tuple[date, str]]) -> int:
    """Dates added to a calendar; one already there is left alone (its name too)."""
    row = CalendarDefinition.objects.filter(pk=key).first()
    if row is None:
        raise NotFound(f"No calendar {key}.")
    if not dates:
        raise ServiceError("Name at least one date.")
    added = 0
    for when, name in dates:
        if not isinstance(when, date):
            raise ServiceError("Each holiday is a date.")
        label = (name or "").strip()[:200]
        _, made = CalendarHoliday.objects.get_or_create(
            calendar=row, date=when, defaults={"name": label}
        )
        added += int(made)
    if added:
        activity.record(
            Actor.human(user),
            "holidays_added",
            f"Added {added} date(s) to the calendar {key}",
            subject_type="calendar",
            data={"dates": [d.isoformat() for d, _ in dates]},
        )
    return added


@transaction.atomic
def name_holiday(user: User, key: str, when: date, name: str | None) -> CalendarHoliday:
    """Give a holiday already on a calendar its name, change it, or clear it (empty). The name
    is what a person typed and only that: the portal never fills one in."""
    row = CalendarHoliday.objects.select_for_update().filter(calendar_id=key, date=when).first()
    if row is None:
        raise NotFound(f"{when} is not a holiday on the calendar {key}.")
    label = (name or "").strip()[:200]
    if label != row.name:
        old = row.name
        CalendarHoliday.objects.filter(pk=row.pk).update(name=label)
        activity.record(
            Actor.human(user),
            "holiday_named",
            f"Named {when} on the calendar {key}" if label else f"Cleared the name of {when}",
            subject_type="calendar",
            data={"date": when.isoformat(), "old": old, "new": label},
        )
    return CalendarHoliday.objects.get(pk=row.pk)


def holiday_names() -> dict[tuple[str, date], str]:
    """Every holiday name an administrator has typed, by (calendar, date). Names live only in the
    managed table; the rule files hold dates."""
    return {
        (key, when): name
        for key, when, name in CalendarHoliday.objects.exclude(name="").values_list(
            "calendar_id", "date", "name"
        )
    }


@transaction.atomic
def remove_holiday(user: User, key: str, when: date) -> None:
    deleted, _ = CalendarHoliday.objects.filter(calendar_id=key, date=when).delete()
    if not deleted:
        raise NotFound(f"{when} is not a holiday on the calendar {key}.")
    activity.record(
        Actor.human(user),
        "holiday_removed",
        f"Removed {when} from the calendar {key}",
        subject_type="calendar",
        data={"date": when.isoformat()},
    )


@transaction.atomic
def map_law(user: User, law: str, calendar_key: str) -> LawCalendar:
    law = _text(law, "The law")
    if not CalendarDefinition.objects.filter(pk=calendar_key).exists():
        raise NotFound(f"No calendar {calendar_key}.")
    row, made = LawCalendar.objects.update_or_create(
        law=law, defaults={"calendar_id": calendar_key}
    )
    activity.record(
        Actor.human(user),
        "law_mapped",
        f"{law} runs on the calendar {calendar_key}",
        subject_type="law",
        data={"law": law, "calendar": calendar_key, "new": made},
    )
    return row


@transaction.atomic
def unmap_law(user: User, law: str) -> None:
    if DeadlineRule.objects.filter(law=law).exists():
        raise InvalidTransition(f"{law} has rules; retire them before taking its calendar away.")
    deleted, _ = LawCalendar.objects.filter(law=law).delete()
    if not deleted:
        raise NotFound(f"{law} is not mapped to a calendar.")
    activity.record(
        Actor.human(user), "law_unmapped", f"{law} no longer has a calendar", subject_type="law"
    )
