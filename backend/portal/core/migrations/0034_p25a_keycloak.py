"""P25a: sign-in moves to Keycloak (DECISIONS 51, 52). Each person's Keycloak account
(`users.keycloak_sub`), the portal sessions with the Keycloak session each came from
(`sign_in_sessions`, for back-channel logout), and the queue of changes owed to Keycloak
(`keycloak_changes`). Retired with the portal's own sign-in: two-step secrets and backup codes
(`two_step`), the sign-in and client-secret attempt counts, the invite token hash (the link is
Keycloak's), and when a password was set (Keycloak's credentials say). The post-migrate hook grants
the new tables to portal_app with the rest of `core`."""

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_p21c_drafting_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='KeycloakChange',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(choices=[('sync', 'Update sign-in account'), ('invite', 'Send invitation'), ('password_reset', 'Send password reset'), ('end_sessions', 'Sign out everywhere'), ('reset_second_step', 'Reset two-step sign-in'), ('policy', 'Apply sign-in rules')], max_length=32)),
                ('state', models.CharField(choices=[('pending', 'Pending'), ('done', 'Done'), ('failed', 'Failed')], db_index=True, default='pending', max_length=16)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('done_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'keycloak_changes',
            },
        ),
        migrations.CreateModel(
            name='SignInSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(max_length=40, unique=True)),
                ('keycloak_sid', models.CharField(db_index=True, max_length=64)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'db_table': 'sign_in_sessions',
            },
        ),
        migrations.DeleteModel(
            name='ClientAuthAttempt',
        ),
        migrations.DeleteModel(
            name='SignInAttempt',
        ),
        migrations.RemoveField(
            model_name='invite',
            name='token_hash',
        ),
        migrations.RemoveField(
            model_name='user',
            name='password_changed_at',
        ),
        migrations.AddField(
            model_name='user',
            name='keycloak_sub',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='keycloakchange',
            name='requested_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='keycloakchange',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='keycloak_changes', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='signinsession',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sign_in_sessions', to=settings.AUTH_USER_MODEL),
        ),
        migrations.DeleteModel(
            name='TwoStep',
        ),
        migrations.AddIndex(
            model_name='keycloakchange',
            index=models.Index(fields=['user', 'state', 'created_at'], name='keycloak_ch_user_id_19ec55_idx'),
        ),
    ]
