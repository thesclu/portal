"""P18b: the rules and calendars, written by an administrator. Read through /records/rules."""

from datetime import date

from django.http import HttpRequest
from ninja import Router, Schema

from portal.core.api.users import require_admin
from portal.core.models import CalendarDefinition, DeadlineRule, LawCalendar
from portal.core.services import rules as rules_service

router = Router(tags=["rules"])


class RuleIn(Schema):
    rule_id: str
    law: str
    provision: str = ""
    deadline_type: str
    count: int
    unit: str
    roll_forward_on_non_business_day: bool = True
    effective_from: date
    effective_to: date | None = None
    citation: str


class RulePatchIn(Schema):
    law: str | None = None
    provision: str | None = None
    deadline_type: str | None = None
    count: int | None = None
    unit: str | None = None
    roll_forward_on_non_business_day: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    citation: str | None = None


class RuleRowOut(Schema):
    rule_id: str
    law: str
    provision: str
    deadline_type: str
    count: int
    unit: str
    roll_forward_on_non_business_day: bool
    effective_from: date
    effective_to: date | None
    citation: str


class RetireIn(Schema):
    effective_to: date


class CalendarIn(Schema):
    key: str
    timezone: str
    weekend: list[int] | None = None


class CalendarPatchIn(Schema):
    timezone: str | None = None
    weekend: list[int] | None = None


class CalendarRowOut(Schema):
    key: str
    timezone: str
    weekend: list[int]


class HolidayIn(Schema):
    date: date
    name: str = ""


class HolidaysIn(Schema):
    holidays: list[HolidayIn]


class AddedOut(Schema):
    added: int


class HolidayNameIn(Schema):
    """Empty or null clears the name."""

    name: str | None = None


class HolidayRowOut(Schema):
    date: date
    name: str | None


class LawIn(Schema):
    calendar: str


class LawOut(Schema):
    law: str
    calendar: str


def _rule_out(row: DeadlineRule) -> RuleRowOut:
    return RuleRowOut(**{k: getattr(row, k) for k in RuleRowOut.model_fields})


def _calendar_out(row: CalendarDefinition) -> CalendarRowOut:
    return CalendarRowOut(key=row.key, timezone=row.timezone, weekend=list(row.weekend or []))


@router.post("/rules", response={201: RuleRowOut})
def add_rule(request: HttpRequest, payload: RuleIn):
    require_admin(request)
    data = payload.model_dump()
    rule_id = data.pop("rule_id")
    return 201, _rule_out(rules_service.add_rule(request.user, rule_id=rule_id, **data))


@router.patch("/rules/{rule_id}", response=RuleRowOut)
def update_rule(request: HttpRequest, rule_id: str, payload: RulePatchIn) -> RuleRowOut:
    require_admin(request)
    fields = payload.model_dump(exclude_unset=True)
    return _rule_out(rules_service.update_rule(request.user, rule_id, **fields))


@router.post("/rules/{rule_id}/retire", response=RuleRowOut)
def retire_rule(request: HttpRequest, rule_id: str, payload: RetireIn) -> RuleRowOut:
    require_admin(request)
    return _rule_out(
        rules_service.retire_rule(request.user, rule_id, effective_to=payload.effective_to)
    )


@router.post("/calendars", response={201: CalendarRowOut})
def add_calendar(request: HttpRequest, payload: CalendarIn):
    require_admin(request)
    return 201, _calendar_out(
        rules_service.add_calendar(
            request.user, key=payload.key, timezone=payload.timezone, weekend=payload.weekend
        )
    )


@router.patch("/calendars/{key}", response=CalendarRowOut)
def update_calendar(request: HttpRequest, key: str, payload: CalendarPatchIn) -> CalendarRowOut:
    require_admin(request)
    return _calendar_out(
        rules_service.update_calendar(
            request.user, key, timezone=payload.timezone, weekend=payload.weekend
        )
    )


@router.post("/calendars/{key}/holidays", response=AddedOut)
def add_holidays(request: HttpRequest, key: str, payload: HolidaysIn) -> AddedOut:
    require_admin(request)
    added = rules_service.add_holidays(
        request.user, key, [(h.date, h.name) for h in payload.holidays]
    )
    return AddedOut(added=added)


@router.patch("/calendars/{key}/holidays/{on}", response=HolidayRowOut)
def name_holiday(request: HttpRequest, key: str, on: date, payload: HolidayNameIn) -> HolidayRowOut:
    """Name a holiday already on the calendar (or clear its name). Administrators only."""
    require_admin(request)
    row = rules_service.name_holiday(request.user, key, on, payload.name)
    return HolidayRowOut(date=row.date, name=row.name or None)


@router.delete("/calendars/{key}/holidays/{on}", response={204: None})
def remove_holiday(request: HttpRequest, key: str, on: date):
    require_admin(request)
    rules_service.remove_holiday(request.user, key, on)
    return 204, None


@router.put("/laws/{law}", response=LawOut)
def map_law(request: HttpRequest, law: str, payload: LawIn) -> LawOut:
    require_admin(request)
    row: LawCalendar = rules_service.map_law(request.user, law, payload.calendar)
    return LawOut(law=row.law, calendar=row.calendar_id)


@router.delete("/laws/{law}", response={204: None})
def unmap_law(request: HttpRequest, law: str):
    require_admin(request)
    rules_service.unmap_law(request.user, law)
    return 204, None
