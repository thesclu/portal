"""P18b: the shipped rules and calendars become rows an administrator edits. Copied once from the
package's own `rules.toml` and `calendars.toml` through its loader (so the same validation
applies), never overwriting a row that exists. The files stay in the package as the seed for a
fresh database; after this, the tables are what the portal reads."""

from django.db import migrations


def seed(apps, schema_editor):
    from deadline_rules import load_calendars, load_rules

    DeadlineRule = apps.get_model("core", "DeadlineRule")
    CalendarDefinition = apps.get_model("core", "CalendarDefinition")
    CalendarHoliday = apps.get_model("core", "CalendarHoliday")
    LawCalendar = apps.get_model("core", "LawCalendar")

    calendars, law_calendar = load_calendars()
    for key, calendar in calendars.items():
        row, _ = CalendarDefinition.objects.get_or_create(
            key=key,
            defaults={"timezone": calendar.timezone, "weekend": sorted(calendar.weekend)},
        )
        for day in sorted(calendar.holidays):
            CalendarHoliday.objects.get_or_create(calendar=row, date=day)
    for law, key in law_calendar.items():
        LawCalendar.objects.get_or_create(law=law, defaults={"calendar_id": key})
    for position, rule in enumerate(load_rules()):
        DeadlineRule.objects.get_or_create(
            rule_id=rule.id,
            defaults={
                "position": position,
                "law": rule.law,
                "provision": rule.provision,
                "deadline_type": rule.deadline_type,
                "count": rule.days,
                "unit": rule.unit,
                "roll_forward_on_non_business_day": rule.roll_forward_on_non_business_day,
                "effective_from": rule.effective_from,
                "effective_to": rule.effective_to,
                "citation": rule.citation,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0016_p18b_rules_tables")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
