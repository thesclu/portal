"""Create the first administrator and seed default settings. Safe to re-run.

P25a: no password. The administrator's Keycloak account is made at once and Keycloak emails them
its invitation (choose a password, add a passkey, save recovery codes); their first sign-in
switches the portal account on. Keycloak must be up and its mail working; `--no-email` makes the
account only, for an operator who finishes it in Keycloak's admin console instead.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from portal.core.keycloak import admin as kc_admin
from portal.core.keycloak import sync
from portal.core.models import Invite, KeycloakChange, User
from portal.core.services import identity
from portal.core.services.settings import seed_defaults


class Command(BaseCommand):
    help = "Create the first admin (with its records profile and Keycloak account) and settings."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Make the Keycloak account but send no invitation.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        created = seed_defaults()
        self.stdout.write(f"settings: {created} default(s) inserted")
        email = options["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            self.stdout.write(f"{email} already exists; nothing changed")
            return
        with transaction.atomic():
            user = identity.create_user(
                email=email,
                name=options["name"],
                records_role="admin",
                can_login=False,
                is_staff=True,
                is_superuser=True,
            )
            Invite.objects.create(
                user=user, expires_at=timezone.now() + timedelta(hours=settings.INVITE_HOURS)
            )
        try:
            kc_admin.sync_person(user)
            if not options["no_email"]:
                kc_admin.send_invite(user)
        except kc_admin.KeycloakProblem as exc:
            sync.enqueue(user, KeycloakChange.SYNC)
            if not options["no_email"]:
                sync.enqueue(user, KeycloakChange.INVITE)
            raise CommandError(
                f"admin {email} created, but Keycloak did not take it yet ({exc}); the worker "
                "will keep trying."
            ) from exc
        what = "invitation sent" if not options["no_email"] else "no invitation sent"
        self.stdout.write(self.style.SUCCESS(f"admin {email} created in Keycloak; {what}"))
