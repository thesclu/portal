"""P7: a record of sign-in attempts, so a limit on them can be counted. Ordinary `core` table:
the app role gets DML on it through `portal.db.hooks.grant_core_to_app_role`."""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0007_system_window")]
    operations = [
        migrations.CreateModel(
            name="SignInAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("email", models.CharField(db_index=True, max_length=254)),
                ("client", models.CharField(db_index=True, max_length=64)),
                (
                    "attempted_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("succeeded", models.BooleanField()),
            ],
            options={"db_table": "sign_in_attempts"},
        ),
    ]
