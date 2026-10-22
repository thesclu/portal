"""Fill a DEVELOPMENT database with obviously fake data, through the services only.

    just seed

Refuses unless DEBUG is on, and refuses a database that already has it. Everything is fictional:
agencies named "Test Agency ...", the law TESTLAW with the made-up clocks of the test rule files,
people at example.org. Nothing is read from any real record, and nothing here is a legal number:
the deadlines come from the fictional rule file the tests use.

What it leaves you with, so every screen has something on it: a person in each of the six roles;
agencies with each filing method and one with none; requests in several states with a
deadline of each kind and one conflicting pair; a fee; tasks made by a person and by the system;
mail in each flag reason and one message handled without attaching; a privileged request shared
with one person; a template with a shared block after its last item, saved twice; and a draft with
one letter reserved and not yet filed. P20h added (`seed_roles`): a request an intern made, a letter
of the intern's waiting for approval and one sent back with a note, and grants to the limited-scope
attorney and the intern. A database seeded before P20h gets those with `--roles-only`. P20i added
(`seed_setup`): every seed person has finished the new-user setup (a synthetic drawn signature,
the agreements in force accepted, so the other screens open on their page), and one newcomer, an
intern who has not, for the setup's own screens; `--setup-only` brings an older database up to it.
P22 added (`seed_chat`): three synthetic channels (two public, one private) with a few synthetic
messages, one naming a seeded request by its tracking number, and three direct conversations with
the seed administrator, so the team chat panel has something on it; `--chat-only` adds them to a
database seeded before.
"""

import struct
import zlib
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portal.core.chat import channels as chat_channels
from portal.core.chat import messages as chat_messages
from portal.core.models import ChatChannel, ChatMembership, Setting, User
from portal.core.services import agreements as agreements_service
from portal.core.services import setup as setup_service
from portal.core.services import signatures as signatures_service
from portal.core.services.identity import create_user, set_role
from portal.drafting.models import LetterTemplate
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing
from portal.drafting.services import library as library_service
from portal.mail.services import approval as approval_service
from portal.mail.services.compose import Address
from portal.records.actors import Actor
from portal.records.models import Agency, DeadlineDefinition, Request
from portal.records.services import access as access_service
from portal.records.services import common, directory, sweeps
from portal.records.services import deadlines as deadlines_service
from portal.records.services import events as events_service
from portal.records.services import fees as fees_service
from portal.records.services import filing as filing_service
from portal.records.services import requests as requests_service
from portal.records.services import sources as sources_service
from portal.records.services import tasks as tasks_service

FIXTURES = Path(settings.BASE_DIR) / "tests" / "services" / "fixtures"
PASSWORD = "seed-password-not-secret"
MARKER_EMAIL = "seed-admin@example.org"
INTERN_EMAIL = "seed-intern@example.org"
NEWCOMER_EMAIL = "seed-newcomer@example.org"
CHAT_LOBBY = "seed-lobby"


class Command(BaseCommand):
    help = "Populate a development database with synthetic data (never real records)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--roles-only",
            action="store_true",
            help="Add only P20h's people and approvals to a database seeded before P20h.",
        )
        parser.add_argument(
            "--setup-only",
            action="store_true",
            help="P20i: finish the new-user setup for every seed person, and add the newcomer.",
        )
        parser.add_argument(
            "--chat-only",
            action="store_true",
            help="P22: add the synthetic team chat channels and messages.",
        )
        parser.add_argument(
            "--keycloak-only",
            action="store_true",
            help=(
                "P25a: give every seed person who signs in an account in the development "
                "Keycloak (the seed password; a passkey is added at their first sign-in)."
            ),
        )

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_synthetic only runs with DEBUG on. It is for development.")
        rules, calendars = FIXTURES / "rules_test.toml", FIXTURES / "calendars_test.toml"
        if not (rules.is_file() and calendars.is_file()):
            raise CommandError(f"The fictional rule files are not at {FIXTURES}.")
        if options["keycloak_only"]:
            if not User.objects.filter(email=MARKER_EMAIL).exists():
                raise CommandError(
                    "This database has no synthetic seed; run it without --keycloak-only."
                )
            counts = seed_keycloak()
        elif options["chat_only"]:
            if not User.objects.filter(email=MARKER_EMAIL).exists():
                raise CommandError(
                    "This database has no synthetic seed; run it without --chat-only."
                )
            if ChatChannel.objects.filter(name=CHAT_LOBBY).exists():
                raise CommandError("This database already has P22's synthetic chat.")
            counts = seed_chat()
        elif options["setup_only"]:
            if not User.objects.filter(email=MARKER_EMAIL).exists():
                raise CommandError(
                    "This database has no synthetic seed; run it without --setup-only."
                )
            counts = seed_setup()
        elif options["roles_only"]:
            admin = User.objects.filter(email=MARKER_EMAIL).first()
            if admin is None:
                raise CommandError(
                    "This database has no synthetic seed; run it without --roles-only."
                )
            if User.objects.filter(email=INTERN_EMAIL).exists():
                raise CommandError("This database already has P20h's synthetic people.")
            with common.using_rule_data(rules_path=rules, calendars_path=calendars):
                counts = seed_roles(admin)
        else:
            if User.objects.filter(email=MARKER_EMAIL).exists():
                raise CommandError("This database already has the synthetic seed.")
            with common.using_rule_data(rules_path=rules, calendars_path=calendars):
                counts = seed()
            try:
                counts.update(seed_keycloak())
            except CommandError as exc:
                self.stderr.write(f"{exc} Run `seed_synthetic --keycloak-only` once it is up.")
        for name, number in counts.items():
            self.stdout.write(f"{name}: {number}")
        self.stdout.write(
            f"\nSign in at http://localhost:5173 as {MARKER_EMAIL} / {PASSWORD} (Keycloak; the "
            "first sign-in adds a passkey and recovery codes)"
        )
        self.stdout.write(
            "To have the dev server compute TESTLAW dates the way the seed did, start it with:\n"
            f"  DEADLINE_RULES_PATH={rules}\n  DEADLINE_CALENDARS_PATH={calendars}"
        )


def _person(email: str, name: str, role: str, **extra):
    """P25a: no password here; the seed person's Keycloak account holds it (`seed_keycloak`)."""
    return create_user(email=email, name=name, records_role=role, **extra)


def seed_keycloak() -> dict[str, int]:
    """P25a: every active seed person with a team role gets an account in the development Keycloak
    with the seed password (DEBUG only), linked by `sub`, through the same code that provisions an
    invited person (`portal.core.keycloak.admin.sync_person`). A person whose account exists keeps
    their passkey and has the seed password set again. Requesters get none: the public has no
    sign-in. Two-step is as everyone's: a passkey and recovery codes at the first sign-in."""
    from portal.core.keycloak import admin as kc_admin

    made = 0
    people = User.objects.filter(
        email__startswith="seed-", email__endswith="@example.org", is_active=True
    ).order_by("email")
    try:
        kc = kc_admin.client()
        for person in people:
            if not kc_admin.signs_in(person):
                continue
            had = bool(person.keycloak_sub)
            sub = kc_admin.sync_person(person, seed_password=PASSWORD)
            if sub and had:
                kc_admin._call(
                    "Setting the seed password",
                    lambda sub=sub: kc.set_user_password(sub, PASSWORD, temporary=False),
                )
            made += 1
    except kc_admin.KeycloakProblem as exc:
        raise CommandError(f"Keycloak could not take the seed people: {exc}") from exc
    return {"seed people with a Keycloak account (P25a)": made}


def _agency(actor, name: str, **extra):
    return directory.create_agency(
        actor,
        name=name,
        jurisdiction="Testland",
        records_law="TESTLAW",
        recipient_office="Records Unit",
        mailing_street="1 Test Street",
        mailing_city_state_zip="Testville, TL 00000",
        **extra,
    )


def _message(actor, reference: str, subject: str, **extra):
    return sources_service.register_source(
        actor,
        source_type="postmark_email",
        raw_storage_url=f"seed://{reference}.json",
        external_ref=f"seed-{reference}",
        raw_metadata={"from": "records@test-agency.example.org", "subject": subject},
        **extra,
    )


def _ahead(now, steps: int):
    """A synthetic date some way off. Not a clock of any kind: just somewhere to put a dot."""
    return (now + steps * timedelta(hours=24)).date()


def seed() -> dict[str, int]:
    now = timezone.now()
    admin = _person(MARKER_EMAIL, "Seed Admin", "admin", is_staff=True)
    # Before P20h a member of staff with the (retired) Director mark; a staff attorney now.
    director = _person("seed-director@example.org", "Seed Director", "staff_attorney")
    staff = _person("seed-staff@example.org", "Seed Staff", "staff")
    requester = _person("seed-requester@example.org", "Seed Requester", "requester")
    me, system = Actor.human(admin), Actor.for_system("seed_synthetic")

    by_email = _agency(
        me,
        "Test Agency A (email)",
        short_name="TAA",
        default_request_email="records@test-agency-a.example.org",
        filing_method="email",
    )
    directory.verify_request_email(me, by_email.pk, "Synthetic: verified by the seed.")
    by_portal = _agency(
        me,
        "Test Agency B (web form)",
        short_name="TAB",
        portal_url="https://forms.example.org/test-agency-b",
        filing_method="portal",
        portal_kind="Web form",
    )
    by_mail = _agency(me, "Test Agency C (post)", short_name="TAC", filing_method="mail")
    undecided = _agency(me, "Test Agency D (no filing method)", short_name="TAD")
    directory.create_contact(me, by_email.pk, name="Test Custodian", is_primary=True)

    def reserve(agency, subject, **extra):
        return requests_service.reserve_request(
            me,
            agency_id=agency.pk,
            requester_user_id=requester.pk,
            subject=subject,
            request_text=f"SYNTHETIC. {subject}.",
            items=[
                requests_service.NewItem("1", "Synthetic item one"),
                requests_service.NewItem("2", "Synthetic item two", sort_order=1),
            ],
            **extra,
        )

    def filed(agency, subject, days_ago, **extra):
        request = reserve(agency, subject, **extra)
        filing_service.file_request(
            me, request.pk, filed_at=now - timedelta(days=days_ago), channel="email"
        )
        return request

    pending = reserve(by_portal, "Synthetic: reserved, not yet filed")
    fresh = filed(by_email, "Synthetic: filed this week", 2)
    overdue = filed(by_email, "Synthetic: filed long ago, no reply", 90)
    answered = filed(by_mail, "Synthetic: acknowledged, fee quoted", 20)
    conflicted = filed(by_portal, "Synthetic: two dates for one thing", 10)
    sealed = filed(
        by_email, "Synthetic: PRIVILEGED, shared with one person", 5, sensitivity="privileged"
    )
    requests_service.assign_request(me, fresh.pk, staff.pk)
    access_service.grant_access(me, sealed.pk, director.pk, "Synthetic: second chair.")

    def mail(request, reference, subject, body, **extra):
        source = _message(system, reference, subject)
        return events_service.record_event(
            system,
            request.pk,
            event_type="correspondence",
            channel="email",
            direction="inbound",
            occurred_at=now - timedelta(hours=3),
            display_body=body,
            source_id=source.pk,
            **extra,
        )

    acknowledgment = mail(
        answered,
        "ack",
        "RE: Synthetic request",
        "Synthetic: we acknowledge your request.",
        status="acknowledged",
    )
    mail(fresh, "new", "RE: Synthetic request", "Synthetic: a reply nobody has looked at yet.")
    fees_service.record_fee(
        me,
        answered.pk,
        status="assessed",
        amount_cents=2500,
        source_event_id=acknowledgment.pk,
        description="Synthetic: copying estimate.",
    )

    # A deadline of every kind the table holds, and one pair that disagrees.
    kinds = list(DeadlineDefinition.objects.order_by("sort_order").values_list("code", flat=True))
    for offset, kind in enumerate(kinds):
        deadlines_service.open_deadline(
            me,
            answered.pk,
            deadline_type=kind,
            due_at=_ahead(now, 30 + offset),
            basis="agency_commitment",
            note="Synthetic.",
        )
    for ahead in (12, 26):
        deadlines_service.open_deadline(
            me,
            conflicted.pk,
            deadline_type="production",
            due_at=_ahead(now, ahead),
            basis="agency_commitment",
        )

    tasks_service.create_task(
        me, fresh.pk, "manual", "Synthetic: call the records unit.", assigned_to_user_id=staff.pk
    )
    tasks_service.create_task(me, overdue.pk, "manual", "Synthetic: nobody has this yet.")
    tasks_service.create_task(system, overdue.pk, "review_source", "Synthetic: opened by a job.")

    for reason in sources_service.FLAG_REASONS:
        sources_service.flag_source(
            system,
            _message(system, f"flag-{reason}", f"Synthetic: flagged {reason}").pk,
            f"Synthetic: flagged as {reason}.",
            reason,
        )
    sources_service.resolve_without_attaching(
        me, _message(system, "auto-reply", "Synthetic: out of office").pk, "automated_reply", None
    )

    shell = library_service.create_shell(
        name="Synthetic letter",
        letterhead="SYNTHETIC TEST UNION\n{{FILING_DATE}}\nVIA {{CHANNEL_METHOD}}",
        opening_body="{{RECIPIENT_OFFICE}}\n{{AGENCY_FULL}}\n\nUnder TESTLAW, we request:",
        closing_body="Please reply to the address this letter came from.",
        is_default=True,
    )
    block = library_service.create_block(
        name="Synthetic definitions", body="'Record' means any writing."
    )
    template = library_service.create_template(
        name="Synthetic widget records",
        jurisdiction="Testland",
        legal_basis="TESTLAW",
        shell_id=shell.pk,
        created_by_user_id=admin.pk,
    )
    for number in ("1", "2"):
        library_service.add_template_item(
            template.pk, item_number=number, body=f"Synthetic ask {number}", sort_order=int(number)
        )
    library_service.save_template(me, template.pk, "Synthetic: first version.")
    library_service.set_template_blocks(template.pk, [block.pk], sort_orders=[50])
    library_service.save_template(me, template.pk, "Synthetic: definitions after the last item.")

    draft = drafts_service.create_draft(from_template_id=template.pk, created_by_user_id=admin.pk)
    reserved = drafts_service.add_recipient(draft.pk, agency_id=by_email.pk)
    drafts_service.add_recipient(draft.pk, agency_id=by_portal.pk)
    drafts_service.add_recipient(draft.pk, agency_id=undecided.pk)
    drafting_filing.reserve_recipient(me, reserved.pk)

    # The request filed long ago has lapsed by now; let the sweep say so, as it would overnight.
    swept = sweeps.sweep_overdue_deadlines()
    added = seed_roles(admin)
    finished = seed_setup()
    chat = seed_chat()

    return {
        **added,
        **finished,
        **chat,
        "people": 4 + added["people (P20h)"],
        "agencies": 4,
        "requests": len([pending, fresh, overdue, answered, conflicted, sealed]) + 1,
        "deadlines the sweep marked missed": swept.missed,
        "deadline kinds": len(kinds),
        "templates": 1,
        "drafts": 1,
    }


def seed_roles(admin: User) -> dict[str, int]:
    """P20h: a person in each new role, and what their screens need (239-241, 230-238).

    A limited-scope attorney given two requests and the intern's waiting letter; an intern who made
    one request (filed by staff on their behalf) and has one letter waiting for approval and one
    sent back with a note; the staff attorney who sent it back, named the legal director. All of it
    through the services, as the people themselves would do it. Synthetic names, example.org."""
    now = timezone.now()
    me = Actor.human(admin)
    director = User.objects.filter(email="seed-director@example.org").first()
    if director is not None and director.app_roles.filter(role="staff").exists():
        set_role(admin, director, "staff_attorney")
    counsel = _person("seed-counsel@example.org", "Seed Counsel", "limited_attorney")
    intern = _person(INTERN_EMAIL, "Seed Intern", "intern")
    by_email = Agency.objects.get(name="Test Agency A (email)")
    template = LetterTemplate.objects.get(name="Synthetic widget records")
    address = by_email.default_request_email or "records@test-agency-a.example.org"

    made = requests_service.reserve_request(
        Actor.human(intern),
        agency_id=by_email.pk,
        requester_user_id=intern.pk,
        subject="Synthetic: made by an intern",
        request_text="SYNTHETIC. Made by an intern.",
        items=[requests_service.NewItem("1", "Synthetic item one")],
    )
    filing_service.file_request(me, made.pk, filed_at=now - timedelta(hours=72), channel="email")

    def cover(subject: str) -> approval_service.Cover:
        return approval_service.Cover(
            subject=subject,
            body_text="SYNTHETIC. To the records unit: the request is attached.",
            to=[Address(address)],
        )

    def letter(subject: str):
        draft = drafts_service.create_draft(
            from_template_id=template.pk, created_by_user_id=intern.pk
        )
        drafts_service.update_draft(draft.pk, subject=subject)
        recipient = drafts_service.add_recipient(draft.pk, agency_id=by_email.pk)
        return approval_service.submit_letter(
            intern, recipient.pk, cover=cover("Synthetic records request")
        )

    waiting = letter("Synthetic: widget records, waiting for approval")
    sent_back = letter("Synthetic: widget records, sent back")
    reviewer = director or admin
    approval_service.decide(
        reviewer, sent_back.pk, "sent_back", "Synthetic: add a second item, then submit it again."
    )

    for subject in ("Synthetic: filed this week", "Synthetic: filed long ago, no reply"):
        given = Request.objects.get(subject=subject)
        access_service.grant_access(me, given.pk, counsel.pk, "Synthetic: given to the attorney.")
    assert waiting.request_id is not None
    access_service.grant_access(me, waiting.request_id, counsel.pk, "Synthetic: to approve.")
    shared = Request.objects.get(subject="Synthetic: acknowledged, fee quoted")
    access_service.grant_access(me, shared.pk, intern.pk, "Synthetic: given to the intern.")
    if director is not None:
        Setting.objects.update_or_create(
            key="org.legal_director", defaults={"value": str(director.pk)}
        )
    return {
        "people (P20h)": 2,
        "intern-made requests": 3,
        "letters waiting for approval": 1,
        "letters sent back": 1,
        "grants": 4,
    }


def _signature_png(width: int = 240, height: int = 64) -> bytes:
    """A synthetic drawn signature: a looping line in the signature ink on a clear ground, as a
    real PNG built here (no image library), so no seed person's signature is anyone's hand."""
    import math

    ink = (0x1F, 0x35, 0x66)
    pixels = bytearray(width * height * 4)
    for step in range(width * 6):
        x = 12 + step * (width - 24) / (width * 6)
        y = height / 2 + math.sin(x / 9) * 14 + math.sin(x / 23) * 6
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                px, py = int(x) + dx, int(y) + dy
                if 0 <= px < width and 0 <= py < height:
                    at = (py * width + px) * 4
                    pixels[at : at + 4] = bytes((*ink, 255))
    rows = b"".join(
        b"\x00" + bytes(pixels[row * width * 4 : (row + 1) * width * 4]) for row in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        signatures_service.PNG_MAGIC
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def seed_setup() -> dict[str, int]:
    """P20i: every seed person has finished the new-user setup, the way a person would: a drawn
    signature (synthetic), each agreement in force accepted, then Finish. The newcomer, an
    intern who has not started it, is for the setup's own screens (006-012)."""
    finished = 0
    # A deactivated seed person cannot sign or sign in; they are asked if they come back.
    people = User.objects.filter(
        email__startswith="seed-", email__endswith="@example.org", is_active=True
    )
    for person in people.exclude(email=NEWCOMER_EMAIL).order_by("email"):
        if person.setup_completed_at is not None and not agreements_service.pending(person):
            continue
        if signatures_service.get(person.pk) is None:
            signatures_service.save(person, person.pk, _signature_png(), source="drawn")
        for agreement in agreements_service.pending(person):
            agreements_service.accept(
                person, agreement.pk, ip_address="127.0.0.1", user_agent="seed_synthetic"
            )
        setup_service.finish(person)
        finished += 1
    added = 0
    if not User.objects.filter(email=NEWCOMER_EMAIL).exists():
        _person(NEWCOMER_EMAIL, "Seed Newcomer", "intern", title="Legal Intern")
        added = 1
    return {"people who finished the setup (P20i)": finished, "newcomers (P20i)": added}


def seed_chat() -> dict[str, int]:
    """P22: team chat with something in it (018-022), through the chat services, as the people
    would write it. Every name and word is the seed's own: channels called seed-*, messages that
    say they are synthetic. The administrator has read all but the last two messages of one
    channel and the last message from the member of staff, so the panel shows unread ones."""
    admin = User.objects.get(email=MARKER_EMAIL)
    # Three seed people who can still sign in (a development database may have deactivated one).
    wanted = [
        "seed-staff@example.org",
        "seed-director@example.org",
        INTERN_EMAIL,
        "seed-counsel@example.org",
        "seed-requester@example.org",
    ]
    active = {u.email: u for u in User.objects.filter(email__in=wanted, is_active=True)}
    staff, director, intern = [active[e] for e in wanted if e in active][:3]
    lobby = chat_channels.create_channel(admin, name=CHAT_LOBBY, private=False)
    workroom = chat_channels.create_channel(staff, name="seed-workroom", private=False)
    private = chat_channels.create_channel(director, name="seed-private-room", private=True)
    for channel in (lobby, workroom):
        for person in (director, staff, intern, admin):
            chat_channels.join(person, channel.pk)
    chat_channels.add_members(director, private.pk, [admin.pk, staff.pk])
    named = Request.objects.order_by("created_at").first()
    tracking = f" {named.tracking_number}" if named else ""

    def say(person, channel, text):
        return chat_messages.post(person, channel.pk, body=text)

    say(staff, lobby, "Seed message: the synthetic web form wants a date range first.")
    say(director, lobby, "Seed reply: use the seed's own dates for it.")
    say(admin, lobby, f"Seed note: filed{tracking} on the synthetic form.")
    say(staff, lobby, "Seed message: the confirmation is in the synthetic notes.")
    say(director, private, "Seed message in the private room.")
    chat_messages.mark_read(admin, lobby.pk)
    chat_messages.mark_read(admin, private.pk)
    chat_messages.mark_read(admin, workroom.pk)
    say(staff, workroom, "Seed message: two synthetic letters are ready to check.")
    say(intern, workroom, "Seed message: the second one is the test agency's.")
    with_staff = chat_channels.open_direct(admin, [staff.pk])
    with_director = chat_channels.open_direct(admin, [director.pk])
    with_intern = chat_channels.open_direct(admin, [intern.pk])
    say(intern, with_intern, "Seed message: the synthetic draft is back with notes.")
    say(director, with_director, "Seed message: approved, synthetically.")
    say(staff, with_staff, "Seed message: could you look at the synthetic production first?")
    say(admin, with_staff, "Seed reply: on it; the synthetic logs cover one month.")
    for room in (with_intern, with_director, with_staff):
        chat_messages.mark_read(admin, room.pk)
    say(staff, with_staff, "Seed message: a follow-up, then?")
    return {
        "chat channels (P22)": 3,
        "chat direct conversations (P22)": 3,
        "chat memberships (P22)": ChatMembership.objects.count(),
    }
