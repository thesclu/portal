"""The agreements a person signs in the new-user setup (P20i; v6 010–011, ARCHITECTURE "Agreements
at first sign-in"; DECISIONS 33b, 37e, 44).

Two kinds: the confidentiality agreement, which every person signs, and "Working as an intern",
which the non-attorney roles sign (`records.roles.NON_ATTORNEY`; administrators count as
attorneys). Each kind is a run of versions in `core.agreements`; the newest is the one in force.
A version is never edited: an administrator publishes the next one, and everyone it applies to is
asked again at their next sign-in, because their acceptance names the old version.

Accepting records who, which version, when, the signature image they signed with (their stored
signature at that moment: its file is never deleted when they replace it, and its hash is kept),
the client address and the browser, and keeps a PDF of exactly what was signed: the agreement's
title, version and text, the signature image, the person and the moment. Each acceptance and each
publication is an activity line. The portal writes no agreement text of its own (33b): version 1
of each kind is the design's placeholder wording, seeded by core.0027 and marked as such.

The text is one clause per paragraph, a blank line between; a clause may open with its lead in
`**` ("**Keep it inside SCLU.** Don't share …"), which the setup screen and the PDF set in bold.
"""

import base64
import hashlib
import html
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, QuerySet, Subquery
from django.utils import timezone

from portal.core.models import Agreement, AgreementAcceptance, User
from portal.core.services import activity
from portal.core.services import signatures as signatures_service
from portal.core.services.identity import records_role_of
from portal.core.services.settings import get_setting
from portal.records import roles
from portal.records.actors import Actor
from portal.records.services.common import InvalidTransition, NotAllowed, NotFound, ServiceError
from portal.records.storage import open_stored

CONFIDENTIALITY, INTERN = Agreement.CONFIDENTIALITY, Agreement.INTERN
KINDS = Agreement.KINDS

# The headings the setup screens draw (010, 011); a kind's name everywhere else.
TITLES = {CONFIDENTIALITY: "Confidentiality agreement", INTERN: "Working as an intern"}

# The longest text an administrator may publish, in characters: a few pages.
MAX_BODY = 20_000


@dataclass(frozen=True)
class Clause:
    """One paragraph: its lead (bold, may be empty) and the rest."""

    lead: str
    text: str


def clauses(body: str) -> list[Clause]:
    """The paragraphs of an agreement's text, each with the lead it opens with in `**`."""
    out: list[Clause] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        paragraph = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if not paragraph:
            continue
        lead, text = "", paragraph
        if paragraph.startswith("**"):
            end = paragraph.find("**", 2)
            if end > 2:
                lead, text = paragraph[2:end].strip(), paragraph[end + 2 :].strip()
        out.append(Clause(lead=lead, text=text))
    return out


def title_of(kind: str) -> str:
    return TITLES.get(kind, kind)


def applies(kind: str, role: str | None) -> bool:
    """Whether a person with this records role is asked to sign this kind."""
    if kind == CONFIDENTIALITY:
        return True
    if kind == INTERN:
        return role in roles.NON_ATTORNEY
    return False


def current(kind: str) -> Agreement | None:
    """The version in force: the newest published."""
    return Agreement.objects.filter(kind=kind).order_by("-version").first()


def applicable(user: User) -> list[Agreement]:
    """The version in force of every kind this person signs, in the order the setup asks."""
    role = records_role_of(user)
    out = []
    for kind in KINDS:
        if not applies(kind, role):
            continue
        agreement = current(kind)
        if agreement is not None:
            out.append(agreement)
    return out


def acceptances_of(user: User, agreements: list[Agreement]) -> dict[UUID, AgreementAcceptance]:
    """This person's acceptances of these versions, by agreement id."""
    rows = AgreementAcceptance.objects.filter(user=user, agreement__in=agreements)
    return {row.agreement_id: row for row in rows}


def pending(user: User) -> list[Agreement]:
    """The versions in force this person has not accepted yet."""
    agreements = applicable(user)
    accepted = acceptances_of(user, agreements)
    return [a for a in agreements if a.pk not in accepted]


def has_pending(user: User, role: str | None) -> bool:
    """Whether `pending(user)` would be non-empty, for a person whose records role is `role`, in
    one query: the setup's hold asks it on every API request (P23a)."""
    kinds = [kind for kind in KINDS if applies(kind, role)]
    if not kinds:
        return False
    newest = Agreement.objects.filter(kind=OuterRef("kind")).order_by("-version").values("version")
    return (
        Agreement.objects.filter(kind__in=kinds, version=Subquery(newest[:1]))
        .exclude(acceptances__user=user)
        .exists()
    )


# ---- accepting ----------------------------------------------------------------------------


def accept(
    user: User, agreement_id: UUID, *, ip_address: str = "", user_agent: str = ""
) -> AgreementAcceptance:
    """`user` agrees to this version, signing with their stored signature. Accepting the same
    version twice answers the first acceptance; a version no longer in force, or a kind this
    person is not asked to sign, is refused."""
    agreement = Agreement.objects.filter(pk=agreement_id).first()
    if agreement is None:
        raise NotFound(f"No agreement {agreement_id}.")
    if not user.is_active:
        raise NotAllowed("This account is not active.")
    if not applies(agreement.kind, records_role_of(user)):
        raise NotAllowed("This agreement is not one you are asked to sign.")
    existing = AgreementAcceptance.objects.filter(agreement=agreement, user=user).first()
    if existing is not None:
        return existing
    in_force = current(agreement.kind)
    if in_force is None or in_force.pk != agreement.pk:
        raise InvalidTransition(
            "A newer version of this agreement has been published. Read it again before agreeing."
        )
    signature = signatures_service.get(user.pk)
    if signature is None:
        raise ServiceError("Draw or upload your signature first: the agreement is signed with it.")
    try:
        with open_stored(signature.storage_name) as handle:
            image = handle.read()
    except FileNotFoundError:
        raise ServiceError("Your signature's image is missing. Draw or upload it again.") from None

    accepted_at = timezone.now()
    row = AgreementAcceptance(
        agreement=agreement,
        user=user,
        accepted_at=accepted_at,
        signed_name=user.name,
        signed_title=user.title,
        signature_storage_name=signature.storage_name,
        signature_content_type=signature.content_type,
        signature_sha256=signature.sha256,
        ip_address=(ip_address or "")[:64],
        user_agent=(user_agent or "")[:1000],
    )
    data = acceptance_pdf(agreement, row, user, image)
    pdf_sha256 = hashlib.sha256(data).hexdigest()
    row.pdf_storage_name = default_storage.save(
        f"agreements/{user.pk}/{agreement.kind}-v{agreement.version}-{row.pk.hex[:12]}.pdf",
        ContentFile(data),
    )
    row.pdf_sha256 = pdf_sha256
    try:
        with transaction.atomic():
            row.save(force_insert=True)
            activity.record(
                Actor.human(user),
                "agreement_accepted",
                f"{user.name} agreed to the {title_of(agreement.kind).lower()} "
                f"(version {agreement.version})",
                subject_type="agreement",
                subject_id=agreement.pk,
                data={
                    "acceptance_id": str(row.pk),
                    "kind": agreement.kind,
                    "version": agreement.version,
                    "signature_sha256": signature.sha256,
                    "pdf_sha256": pdf_sha256,
                    "ip_address": row.ip_address,
                    "user_agent": row.user_agent,
                },
            )
    except IntegrityError:
        # Two clicks at once: the other one won. Its row is the acceptance.
        won = AgreementAcceptance.objects.filter(agreement=agreement, user=user).first()
        if won is None:
            raise
        return won
    return row


# ---- publishing a new version --------------------------------------------------------------


@transaction.atomic
def publish(actor_user: User, kind: str, body: str) -> Agreement:
    """A portal administrator publishes the next version of `kind`. Everyone it applies to is
    asked to agree to it at their next sign-in."""
    if not actor_user.is_staff:
        raise NotAllowed("Only a portal administrator may publish an agreement.")
    if kind not in KINDS:
        raise ServiceError(f"Unknown agreement {kind!r}; expected one of {KINDS}.")
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        raise ServiceError("An agreement needs its text.")
    if len(text) > MAX_BODY:
        raise ServiceError("The agreement is too long to publish.")
    # Every version of the kind is locked, so two publications cannot take the same number.
    versions = list(Agreement.objects.select_for_update().filter(kind=kind).order_by("-version"))
    latest = versions[0] if versions else None
    if latest is not None and latest.body.strip() == text:
        raise ServiceError("This is the text already in force; nothing would change.")
    agreement = Agreement.objects.create(
        kind=kind,
        version=(latest.version if latest else 0) + 1,
        body=text,
        is_placeholder=False,
        published_at=timezone.now(),
        published_by=actor_user,
    )
    activity.record(
        Actor.human(actor_user),
        "agreement_published",
        f"{actor_user.name} published version {agreement.version} of the {title_of(kind).lower()}",
        subject_type="agreement",
        subject_id=agreement.pk,
        data={"kind": kind, "version": agreement.version},
    )
    return agreement


def versions() -> QuerySet[Agreement]:
    """Every version of every kind, newest first within a kind, with how many people signed."""
    return (
        Agreement.objects.select_related("published_by")
        .annotate(signed=Count("acceptances"))
        .order_by("kind", "-version")
    )


# ---- reading acceptances -------------------------------------------------------------------


def may_read(actor_user: User, acceptance: AgreementAcceptance) -> bool:
    """The person who signed it, or a portal administrator."""
    return acceptance.user_id == actor_user.pk or bool(actor_user.is_staff)


def acceptances(
    actor_user: User, *, user_id: UUID | None = None, agreement_id: UUID | None = None
) -> QuerySet[AgreementAcceptance]:
    """The caller's own acceptances; anyone's, or one version's, for an administrator."""
    rows = AgreementAcceptance.objects.select_related("agreement", "user").order_by("-accepted_at")
    wants_other = (user_id is not None and user_id != actor_user.pk) or agreement_id is not None
    if wants_other and not actor_user.is_staff:
        raise NotAllowed("Only a portal administrator may read other people's agreements.")
    if user_id is not None:
        rows = rows.filter(user_id=user_id)
    elif agreement_id is None:
        rows = rows.filter(user_id=actor_user.pk)
    if agreement_id is not None:
        rows = rows.filter(agreement_id=agreement_id)
    return rows


def readable(actor_user: User, acceptance_id: UUID) -> AgreementAcceptance:
    """One acceptance the caller may read. Someone else's is answered exactly as one that does
    not exist, so the answer says nothing about whether it does."""
    row = (
        AgreementAcceptance.objects.select_related("agreement", "user")
        .filter(pk=acceptance_id)
        .first()
    )
    if row is None or not may_read(actor_user, row):
        raise NotFound(f"No signed agreement {acceptance_id}.")
    return row


def pdf_file_name(acceptance: AgreementAcceptance) -> str:
    agreement = acceptance.agreement
    return f"{agreement.kind}-agreement-v{agreement.version}.pdf"


# ---- the signed PDF ------------------------------------------------------------------------


def _zone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _instant(moment: datetime, zone: ZoneInfo) -> str:
    """`Oct 17, 2026, 3:04 PM PDT`, as the screens write an instant."""
    local = moment.astimezone(zone)
    return f"{local:%b} {local.day}, {local.year}, {local:%-I:%M %p} {local.tzname()}"


def acceptance_pdf(
    agreement: Agreement, acceptance: AgreementAcceptance, user: User, image: bytes
) -> bytes:
    """Exactly what was signed, on US Letter: the title, the version, the text as the person read
    it, their signature image, their name, title and address, the moment, and the signing record
    (the hashes, the client address and the browser)."""
    from portal.mail.pdf import pdf_from_html

    zone = _zone(user)
    org = str(get_setting("org.name") or "").strip()
    paragraphs = "".join(
        f'<p class="clause">{f"<b>{html.escape(c.lead)}</b> " if c.lead else ""}'
        f"{html.escape(c.text)}</p>"
        for c in clauses(agreement.body)
    )
    image_uri = (
        f"data:{acceptance.signature_content_type};base64,{base64.b64encode(image).decode()}"
    )
    who = [acceptance.signed_name]
    if acceptance.signed_title:
        who.append(acceptance.signed_title)
    if org:
        who.append(org)
    who.append(user.email)
    when = _instant(acceptance.accepted_at, zone)
    utc = acceptance.accepted_at.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = [
        (
            "Agreement",
            f"{title_of(agreement.kind)}, version {agreement.version} "
            f"(published {_instant(agreement.published_at, zone)})",
        ),
        ("Signed by", f"{acceptance.signed_name} <{user.email}>, account {user.pk}"),
        ("Signed at", f"{when} ({utc})"),
        (
            "Signature image",
            f"{acceptance.signature_content_type}, SHA-256 {acceptance.signature_sha256}",
        ),
        ("Client address", acceptance.ip_address or "not known"),
        ("Browser", acceptance.user_agent or "not known"),
        ("Record", str(acceptance.pk)),
    ]
    record_html = "".join(
        f'<p class="record">{html.escape(label)}: {html.escape(value)}</p>'
        for label, value in record
    )
    title = title_of(agreement.kind)
    # xhtml2pdf styles flat blocks reliably and nested ones poorly, so every line is its own
    # paragraph and the signature sits in a one-column table with the rule on its second row.
    document = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)} · version {agreement.version}</title><style>"
        "@page { size: letter; margin: 0.9in; }"
        "body { font-family: Times-Roman, 'Times New Roman', serif; font-size: 11pt;"
        " color: #111111; }"
        "h1 { font-size: 17pt; margin: 0 0 4pt 0; }"
        ".meta { font-size: 9.5pt; color: #4f5964; margin: 0 0 16pt 0; }"
        ".clause { margin: 0 0 9pt 0; line-height: 1.4; }"
        ".signed { margin-top: 20pt; }"
        ".signature { height: 0.55in; }"
        ".who { font-size: 10.5pt; line-height: 1.35; border-top: 0.6pt solid #999999;"
        " padding-top: 5pt; }"
        ".when { font-size: 10.5pt; margin: 8pt 0 0 0; }"
        ".record-head { margin: 26pt 0 3pt 0; padding-top: 6pt; border-top: 0.6pt solid #999999;"
        " font-size: 8pt; font-weight: bold; color: #4f5964; }"
        ".record { margin: 0; font-size: 8pt; color: #4f5964; line-height: 1.4; }"
        "</style></head><body>"
        f"<h1>{html.escape(title)}</h1>"
        f'<p class="meta">{html.escape(org + " · ") if org else ""}Version {agreement.version}'
        "</p>"
        f"{paragraphs}"
        '<table class="signed" width="3.3in" cellpadding="0" cellspacing="0">'
        f'<tr><td><img class="signature" src="{image_uri}" alt=""/></td></tr>'
        f'<tr><td class="who">{"<br/>".join(html.escape(line) for line in who)}</td></tr>'
        "</table>"
        f'<p class="when">Agreed {html.escape(when)}</p>'
        '<p class="record-head">Signing record</p>'
        f"{record_html}"
        "</body></html>"
    )
    return pdf_from_html(document)
