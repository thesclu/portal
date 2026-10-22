"""P25a: what the portal's own sign-in left behind goes.

- Every password hash: the portal holds no passwords any more (Keycloak does). Each account gets
  Django's unusable-password marker.
- The OpenID Connect provider's tables (django-oauth-toolkit, P21b; `oauth2_provider_*`), its
  migration records, content types and permissions: the app is no longer installed, and Keycloak
  is the suite's identity provider.
- The sign-in rules an administrator sets (`auth.*`, Settings → System → Sign-in), seeded as
  core.0002 seeds.

Not reversible: neither the hashes nor the provider's codes and tokens can be had back, and
nothing would read them.
"""

from importlib import import_module

from django.contrib.auth.hashers import make_password
from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed

PROVIDER_TABLES = (
    "oauth2_provider_accesstoken",
    "oauth2_provider_refreshtoken",
    "oauth2_provider_idtoken",
    "oauth2_provider_grant",
    "oauth2_provider_devicegrant",
    "oauth2_provider_application",
)


def forget_passwords(apps, schema_editor):
    User = apps.get_model("core", "User")
    for pk in User.objects.values_list("pk", flat=True):
        User.objects.filter(pk=pk).update(password=make_password(None))


def drop_provider(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    kinds = ContentType.objects.filter(app_label="oauth2_provider")
    Permission.objects.filter(content_type__in=kinds).delete()
    with schema_editor.connection.cursor() as cursor:
        for table in PROVIDER_TABLES:
            cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        cursor.execute("DELETE FROM django_migrations WHERE app = 'oauth2_provider'")
    kinds.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0034_p25a_keycloak"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [
        migrations.RunPython(forget_passwords, migrations.RunPython.noop),
        migrations.RunPython(drop_provider, migrations.RunPython.noop),
        migrations.RunPython(seed_settings, migrations.RunPython.noop),
    ]
