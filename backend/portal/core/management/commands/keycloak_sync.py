"""Bring Keycloak in line with the portal now (P25a), without waiting for the worker.

manage.py keycloak_sync --policy        write Settings' sign-in rules to the realm (run by
                                        `just keycloak-up` after keycloak-config-cli applies
                                        the realm file, whose values are only defaults)
manage.py keycloak_sync --pending       apply every pending or failed change now, in order
manage.py keycloak_sync --people        bring every person who signs in in line (their
                                        account, its attributes, on or off)
manage.py keycloak_sync --invite-unlinked
                                        queue an invitation for every active team member
                                        with no Keycloak account yet (moving existing
                                        accounts over: each chooses a password and adds a
                                        passkey from Keycloak's email)
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from portal.core.keycloak import admin as kc_admin
from portal.core.keycloak import policy, sync
from portal.core.models import KeycloakChange, User
from portal.core.services import invites


class Command(BaseCommand):
    help = "Bring Keycloak in line with the portal: sign-in rules, pending changes, people."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--policy", action="store_true")
        parser.add_argument("--pending", action="store_true")
        parser.add_argument("--people", action="store_true")
        parser.add_argument("--invite-unlinked", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not any(options[k] for k in ("policy", "pending", "people", "invite_unlinked")):
            raise CommandError(
                "Say what to sync: --policy, --pending, --people, --invite-unlinked."
            )
        try:
            if options["policy"]:
                changed = policy.apply(kc_admin.client())
                self.stdout.write(f"sign-in rules: {', '.join(changed) or 'already in line'}")
            if options["people"]:
                done = 0
                for person in User.objects.filter(is_active=True).order_by("email"):
                    if person.keycloak_sub or kc_admin.signs_in(person):
                        kc_admin.sync_person(person)
                        done += 1
                self.stdout.write(f"people in line: {done}")
            if options["invite_unlinked"]:
                queued = 0
                for person in User.objects.filter(is_active=True, keycloak_sub__isnull=True):
                    if kc_admin.signs_in(person) and invites.pending_invite(person) is None:
                        invites._issue(None, person)
                        queued += 1
                self.stdout.write(f"invitations queued: {queued}")
            if options["pending"]:
                sync.sweep()
                owners = set(
                    KeycloakChange.objects.filter(state=KeycloakChange.PENDING).values_list(
                        "user_id", flat=True
                    )
                )
                applied = 0
                for owner in owners:
                    try:
                        applied += sync.apply_pending(owner)
                    except sync.ChangeFailed as exc:
                        self.stderr.write(f"{owner or 'the realm'}: {exc}")
                self.stdout.write(f"changes applied: {applied}")
        except kc_admin.KeycloakProblem as exc:
            raise CommandError(str(exc)) from exc
