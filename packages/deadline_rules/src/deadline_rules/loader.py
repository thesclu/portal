"""Load and validate `rules.toml` and `calendars.toml`.

Every field of every entry is validated; an unknown key or a missing/malformed required field
raises `RulesDataError` naming the offending entry (its `id` for a rule, its table key for a
calendar or law mapping).
"""

import tomllib
from datetime import date as _date
from importlib.resources import files
from pathlib import Path
from typing import Any, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import RulesDataError
from .model import Calendar, DeadlineType, Rule, Unit

_REQUIRED_RULE_KEYS = {
    "id",
    "law",
    "provision",
    "deadline_type",
    "days",
    "unit",
    "effective_from",
    "citation",
}
_OPTIONAL_RULE_KEYS = {"count_from", "roll_forward_on_non_business_day", "effective_to"}
_ALLOWED_RULE_KEYS = _REQUIRED_RULE_KEYS | _OPTIONAL_RULE_KEYS

_ALLOWED_CALENDAR_KEYS = {"timezone", "holidays", "weekend"}
_ALLOWED_LAW_KEYS = {"calendar"}

_DEADLINE_TYPES = frozenset(get_args(DeadlineType))
_UNITS = frozenset(get_args(Unit))


def _default_data_dir() -> Any:
    return files("deadline_rules") / "data"


def _rule_from_entry(entry: dict[str, Any], index: int) -> Rule:
    name = entry.get("id") or f"<rule at index {index}, missing id>"

    unknown = set(entry) - _ALLOWED_RULE_KEYS
    if unknown:
        raise RulesDataError(f"rule {name!r}: unknown field(s) {sorted(unknown)}")
    missing = _REQUIRED_RULE_KEYS - set(entry)
    if missing:
        raise RulesDataError(f"rule {name!r}: missing required field(s) {sorted(missing)}")

    if not isinstance(entry["id"], str) or not entry["id"]:
        raise RulesDataError(f"rule {name!r}: 'id' must be a non-empty string")
    if not isinstance(entry["law"], str) or not entry["law"]:
        raise RulesDataError(f"rule {name!r}: 'law' must be a non-empty string")
    if not isinstance(entry["provision"], str):
        raise RulesDataError(
            f"rule {name!r}: 'provision' must be a string ('' for the general rule)"
        )
    if entry["deadline_type"] not in _DEADLINE_TYPES:
        raise RulesDataError(
            f"rule {name!r}: 'deadline_type' must be one of {sorted(_DEADLINE_TYPES)}, "
            f"got {entry['deadline_type']!r}"
        )
    # Named without "days" glued to a digit anywhere on one line -- see test_purity.py: this
    # module's whole job is keeping statutory counts out of source, so its own count field
    # can't read like one either.
    count_value = entry["days"]
    if not isinstance(count_value, int) or isinstance(count_value, bool) or count_value <= 0:
        raise RulesDataError(f"rule {name!r}: 'days' must be a positive integer")
    if entry["unit"] not in _UNITS:
        raise RulesDataError(
            f"rule {name!r}: 'unit' must be one of {sorted(_UNITS)}, got {entry['unit']!r}"
        )

    count_from = entry.get("count_from", "clock_start")
    if count_from != "clock_start":
        raise RulesDataError(
            f"rule {name!r}: 'count_from' must be 'clock_start', got {count_from!r}"
        )

    roll_forward = entry.get("roll_forward_on_non_business_day", True)
    if not isinstance(roll_forward, bool):
        raise RulesDataError(f"rule {name!r}: 'roll_forward_on_non_business_day' must be a boolean")

    effective_from = entry["effective_from"]
    # `type(x) is _date`, not `isinstance` -- TOML's datetime type is a `datetime.datetime`,
    # which SUBCLASSES `date`, so `isinstance(a_datetime, date)` is True. A datetime value that
    # slips past this check never compares equal to a plain `date` downstream (select.py's
    # range comparisons, calendar.py's holiday-set membership), so it must be rejected here,
    # not merely coerced.
    if type(effective_from) is not _date:
        raise RulesDataError(f"rule {name!r}: 'effective_from' must be a date")

    effective_to = entry.get("effective_to")
    if effective_to is not None:
        if type(effective_to) is not _date:
            raise RulesDataError(f"rule {name!r}: 'effective_to' must be a date or absent")
        if effective_to <= effective_from:
            raise RulesDataError(
                f"rule {name!r}: 'effective_to' ({effective_to}) must be after 'effective_from' "
                f"({effective_from})"
            )

    if not isinstance(entry["citation"], str) or not entry["citation"].strip():
        raise RulesDataError(f"rule {name!r}: 'citation' must be a non-empty string")

    return Rule(
        id=entry["id"],
        law=entry["law"],
        provision=entry["provision"],
        deadline_type=entry["deadline_type"],
        days=count_value,
        unit=entry["unit"],
        count_from=count_from,
        roll_forward_on_non_business_day=roll_forward,
        effective_from=effective_from,
        effective_to=effective_to,
        citation=entry["citation"],
    )


def load_rules(path: Path | None = None) -> tuple[Rule, ...]:
    """Load and validate `rules.toml`. Defaults to the package's shipped `data/rules.toml`."""
    if path is None:
        with (_default_data_dir() / "rules.toml").open("rb") as f:
            data = tomllib.load(f)
    else:
        with path.open("rb") as f:
            data = tomllib.load(f)

    unknown_top = set(data) - {"rule"}
    if unknown_top:
        raise RulesDataError(f"rules.toml: unknown top-level table(s) {sorted(unknown_top)}")

    entries = data.get("rule", [])
    if not isinstance(entries, list):
        # `[rule]` (a single table) instead of `[[rule]]` (an array of tables) parses to a
        # plain dict, not a list -- iterating it would yield its string keys, and calling
        # `.get()` on a string inside _rule_from_entry would raise a raw AttributeError. Name
        # the mistake instead.
        raise RulesDataError(
            "rules.toml: 'rule' must be an array of tables (use [[rule]], not [rule])"
        )
    rules = tuple(_rule_from_entry(entry, i) for i, entry in enumerate(entries))

    seen_ids: dict[str, int] = {}
    for i, rule in enumerate(rules):
        if rule.id in seen_ids:
            raise RulesDataError(
                f"duplicate rule id {rule.id!r} (entries {seen_ids[rule.id]} and {i})"
            )
        seen_ids[rule.id] = i

    return rules


def _calendar_from_entry(key: str, entry: dict[str, Any]) -> Calendar:
    unknown = set(entry) - _ALLOWED_CALENDAR_KEYS
    if unknown:
        raise RulesDataError(f"calendar {key!r}: unknown field(s) {sorted(unknown)}")
    if "timezone" not in entry:
        raise RulesDataError(f"calendar {key!r}: missing required field 'timezone'")
    if "holidays" not in entry:
        raise RulesDataError(f"calendar {key!r}: missing required field 'holidays'")

    timezone = entry["timezone"]
    if not isinstance(timezone, str) or not timezone:
        raise RulesDataError(f"calendar {key!r}: 'timezone' must be a non-empty string")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise RulesDataError(f"calendar {key!r}: unknown timezone {timezone!r}") from exc
    except ValueError as exc:
        # A path-like string (e.g. "/etc/passwd", "../../etc/passwd") is rejected by ZoneInfo
        # with a bare ValueError, not ZoneInfoNotFoundError -- that would otherwise escape past
        # this module's `except DeadlineRulesError` boundary uncaught.
        raise RulesDataError(f"calendar {key!r}: invalid timezone {timezone!r}: {exc}") from exc

    holidays_raw = entry["holidays"]
    # `type(h) is _date`, not `isinstance` -- see the matching comment in _rule_from_entry: a
    # TOML datetime value passes `isinstance(h, date)` (datetime subclasses date) but never
    # compares equal to the `date` objects `is_business_day` checks set membership against, so
    # a holiday written with a time component would silently stop being a holiday.
    if not isinstance(holidays_raw, list) or not all(type(h) is _date for h in holidays_raw):
        raise RulesDataError(f"calendar {key!r}: 'holidays' must be a list of dates")
    holidays = frozenset(holidays_raw)

    weekend_raw = entry.get("weekend")
    if weekend_raw is None:
        weekend = frozenset({5, 6})
    else:
        if not isinstance(weekend_raw, list) or not all(
            isinstance(w, int) and not isinstance(w, bool) and 0 <= w <= 6 for w in weekend_raw
        ):
            raise RulesDataError(f"calendar {key!r}: 'weekend' must be a list of ints in 0..6")
        weekend = frozenset(weekend_raw)
    if len(weekend) >= 7:
        # Every weekday blocked: no business day can ever exist for this calendar, so
        # add_business_days/roll_forward would step forever (bounded only by calendar.py's
        # own step guard, which then raises the *year 10000* overflow, not a package error).
        # Reject at the data layer instead of letting the arithmetic discover it.
        raise RulesDataError(
            f"calendar {key!r}: 'weekend' must leave at least one weekday free, "
            f"got {sorted(weekend)}"
        )

    return Calendar(key=key, timezone=timezone, holidays=holidays, weekend=weekend)


def load_calendars(path: Path | None = None) -> tuple[dict[str, Calendar], dict[str, str]]:
    """Load and validate `calendars.toml`. Returns (calendars by key, law -> calendar key)."""
    if path is None:
        with (_default_data_dir() / "calendars.toml").open("rb") as f:
            data = tomllib.load(f)
    else:
        with path.open("rb") as f:
            data = tomllib.load(f)

    unknown_top = set(data) - {"calendar", "law"}
    if unknown_top:
        raise RulesDataError(f"calendars.toml: unknown top-level table(s) {sorted(unknown_top)}")

    calendar_table = data.get("calendar", {})
    if not isinstance(calendar_table, dict):
        raise RulesDataError(
            "calendars.toml: 'calendar' must be a table of sub-tables (use [calendar.<key>])"
        )
    calendars = {key: _calendar_from_entry(key, entry) for key, entry in calendar_table.items()}

    law_table = data.get("law", {})
    if not isinstance(law_table, dict):
        raise RulesDataError(
            "calendars.toml: 'law' must be a table of sub-tables (use [law.<key>])"
        )
    law_calendar: dict[str, str] = {}
    for law_key, entry in law_table.items():
        unknown = set(entry) - _ALLOWED_LAW_KEYS
        if unknown:
            raise RulesDataError(f"law {law_key!r}: unknown field(s) {sorted(unknown)}")
        if "calendar" not in entry:
            raise RulesDataError(f"law {law_key!r}: missing required field 'calendar'")
        cal_key = entry["calendar"]
        if cal_key not in calendars:
            raise RulesDataError(f"law {law_key!r}: unknown calendar {cal_key!r}")
        law_calendar[law_key] = cal_key

    return calendars, law_calendar
