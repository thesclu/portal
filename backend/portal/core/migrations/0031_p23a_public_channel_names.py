"""P23a (QUESTIONS 30, taken as engineering): a channel's name is unique among public channels only.

Before, one unique index covered every channel, private ones included, so trying to make a channel
with the name of a private one was refused and told the person that private channel existed. Now
the index covers public channels alone: private channels may share names (with each other; the
service still refuses a private channel the name of a public one, which everyone can see anyway).

The new index is built before the old one is dropped, so public names are never without one. The
reverse puts the wider index back, and fails (Postgres names the duplicate) while two private
channels, or a private and a public one, share a name.
"""

import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0030_p23a_twostep_and_client_attempts"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="chatchannel",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("name"),
                condition=models.Q(("is_private", False), ("kind", "channel")),
                name="chat_public_channel_name_unique",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="chatchannel",
            name="chat_channel_name_unique",
        ),
    ]
