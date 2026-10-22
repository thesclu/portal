"""P20h: the role decides filing, so "review before filing" goes; the legal director is named.

`drafting.review_required` (core.0011) is REMOVED, row and default both, rather than left inert:
the intern approval queue replaced it (DECISIONS 42d), nothing reads it, and a stored switch that
does nothing would still be listed by GET /core/settings and could be flipped by an
administrator believing it worked. The reverse puts the row back, off, as core.0011 seeded it.

`org.legal_director` is seeded from defaults.toml as core.0002 seeds: one person's id, empty until
an administrator names someone (Settings -> Organisation). Nothing reads it until team chat.

The Director and "files without review" marks on `core.app_roles` are left in the table and the
model, read by nothing (DECISIONS 43): dropping the columns would destroy who held them, and an
additive migration is the rule.
"""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed

RETIRED = "drafting.review_required"


def forward(apps, schema_editor):
    seed_settings(apps, schema_editor)
    apps.get_model("core", "Setting").objects.filter(key=RETIRED).delete()


def backward(apps, schema_editor):
    apps.get_model("core", "Setting").objects.get_or_create(
        key=RETIRED,
        defaults={
            "value": False,
            "description": (
                "When on, a letter is filed and a message is sent only after a director approves "
                "it, unless the person may file without review."
            ),
        },
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0024_signature")]
    operations = [migrations.RunPython(forward, backward)]
