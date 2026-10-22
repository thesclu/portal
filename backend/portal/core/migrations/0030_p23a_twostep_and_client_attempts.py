"""P23a, additive: two-step codes work once (`two_step.last_used_step`, the time step of the last
code accepted), and a limit on client-secret attempts at the provider's token and revocation
endpoints (`client_auth_attempts`, the same shape as `sign_in_attempts`). The post-migrate hook
grants the new table to portal_app with the rest of `core`."""


import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_p22_chat_kept_forever'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClientAuthAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('client_id', models.CharField(db_index=True, max_length=100)),
                ('client', models.CharField(db_index=True, max_length=64)),
                ('attempted_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('succeeded', models.BooleanField()),
            ],
            options={
                'db_table': 'client_auth_attempts',
            },
        ),
        migrations.AddField(
            model_name='twostep',
            name='last_used_step',
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
