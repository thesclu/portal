"""Agencies and the people who work at them. Directory rows, not ledger rows.

They are still written only here and still inside `writable()`: an agency's records_law decides
which statutory clock every one of its requests races, so an unaudited edit to it silently moves
deadlines.

An agency also carries how it is filed with and where a letter to it goes: `filing_method`
(`email`, `portal` or `mail`, the same words a drafting recipient's channel uses), `portal_kind`
(the label beside a portal) and the postal address. The two vocabularies are checked here against
Python tuples, not by CHECK constraints (BUILD_PLAN rule 5): a CHECK that holds a list of words is
what cost the one non-additive migration this schema has had.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from portal.records.actors import Actor
from portal.records.models import Agency, AgencyContact, writable
from portal.records.services import audit as audit_service
from portal.records.services.common import (
    InvalidTransition,
    NotFound,
    ServiceError,
    require_human,
)

FILING_METHODS = ("email", "portal", "mail")
PORTAL_KINDS = ("GovQA", "NextRequest", "Web form")

AGENCY_FIELDS = {
    "name",
    "jurisdiction",
    "records_law",
    "portal_automation",
    "default_request_email",
    "portal_url",
    "notes",
    "short_name",
    "recipient_office",
    "mailing_street",
    "mailing_city_state_zip",
    "filing_method",
    "portal_kind",
}
# `request_email_verified_at` is deliberately not editable: it is moved only by the two code paths
# below that know what it means (a changed address clears it; `verify_request_email` sets it).
CONTACT_FIELDS = {
    "name",
    "email",
    "phone",
    "title",
    "notes",
    "is_primary",
    "copy_on_every_request",
    "bad_address_at",
    "bad_address_note",
}


def _check_filing(filing_method: str | None, portal_kind: str | None) -> None:
    """Validate the filing fields as they will BE, not as the caller happened to send them."""
    if filing_method is not None and filing_method not in FILING_METHODS:
        raise ServiceError(
            f"Unknown filing method {filing_method!r}; expected one of {FILING_METHODS}."
        )
    if portal_kind is not None and portal_kind not in PORTAL_KINDS:
        raise ServiceError(f"Unknown portal kind {portal_kind!r}; expected one of {PORTAL_KINDS}.")
    if portal_kind is not None and filing_method != "portal":
        raise ServiceError(
            "A portal kind only applies to an agency whose filing method is 'portal'."
        )


@transaction.atomic
def create_agency(
    actor: Actor,
    *,
    name: str,
    jurisdiction: str,
    records_law: str | None = None,
    portal_automation: str | None = None,
    default_request_email: str | None = None,
    portal_url: str | None = None,
    notes: str | None = None,
    short_name: str | None = None,
    recipient_office: str | None = None,
    mailing_street: str | None = None,
    mailing_city_state_zip: str | None = None,
    filing_method: str | None = None,
    portal_kind: str | None = None,
    imported_from: str | None = None,
) -> Agency:
    """A new agency. `imported_from` (P23b) is the file the importer made it from, kept so the
    merge dialog can say "added by import"; a person adding one by hand leaves it out."""
    _check_filing(filing_method, portal_kind)
    with writable():
        return Agency.objects.create(
            name=name,
            jurisdiction=jurisdiction,
            records_law=records_law,
            portal_automation=portal_automation,
            default_request_email=default_request_email,
            portal_url=portal_url,
            notes=notes,
            short_name=short_name,
            recipient_office=recipient_office,
            mailing_street=mailing_street,
            mailing_city_state_zip=mailing_city_state_zip,
            filing_method=filing_method,
            portal_kind=portal_kind,
            imported_from=(imported_from or "").strip() or None,
        )


def _update_audited(
    actor: Actor,
    model: Any,
    entity_type: str,
    entity_id: UUID,
    allowed: set[str],
    note: str,
    fields: dict[str, Any],
    *,
    validate: Callable[[Any, dict[str, Any]], None] | None = None,
    derive: Callable[[Any, dict[str, Any]], dict[str, Any]] | None = None,
    before_update: Callable[[Any, dict[str, Any]], None] | None = None,
) -> Any:
    """Apply the fields that actually change, one audit line each.

    The row is read under `FOR UPDATE`, so two edits to one row (or an edit and `verify_...`)
    cannot interleave. Hooks, all optional, all given the row as it was and the changes as sent:
    `validate` may refuse by looking at the resulting state, `derive` returns further columns that
    move as a consequence (they are audited too, and need not be in `allowed` because the caller
    cannot name them), and `before_update` runs just before the write.
    """
    require_human(actor, f"Editing {entity_type}")
    if not note or not note.strip():
        raise ServiceError(f"A {entity_type} edit needs a note saying why.")
    unknown = set(fields) - allowed
    if unknown:
        raise ServiceError(f"{entity_type}: not editable: {sorted(unknown)}.")
    row = model.objects.select_for_update().filter(pk=entity_id).first()
    if row is None:
        raise NotFound(f"No {entity_type} row {entity_id}.")
    changed = {f: v for f, v in fields.items() if getattr(row, f) != v}
    if validate is not None:
        validate(row, changed)
    if derive is not None:
        changed.update(derive(row, changed))
    if not changed:
        return row
    if before_update is not None:
        before_update(row, changed)
    model.objects.filter(pk=entity_id).update(**changed)
    for field, value in changed.items():
        audit_service.record_audit(
            actor,
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            old_value=getattr(row, field),
            new_value=value,
            note=note,
        )
    return model.objects.get(pk=entity_id)


def _validate_agency(row: Agency, changed: dict[str, Any]) -> None:
    _check_filing(
        changed.get("filing_method", row.filing_method),
        changed.get("portal_kind", row.portal_kind),
    )


def _agency_consequences(row: Agency, changed: dict[str, Any]) -> dict[str, Any]:
    """A changed request address is no longer the one somebody verified."""
    if "default_request_email" in changed and row.request_email_verified_at is not None:
        return {"request_email_verified_at": None}
    return {}


@transaction.atomic
def update_agency(actor: Actor, agency_id: UUID, note: str, **fields: Any) -> Agency:
    return _update_audited(
        actor,
        Agency,
        "agencies",
        agency_id,
        AGENCY_FIELDS,
        note,
        fields,
        validate=_validate_agency,
        derive=_agency_consequences,
    )


@transaction.atomic
def verify_request_email(actor: Actor, agency_id: UUID, note: str) -> Agency:
    """Someone confirmed the request address works: stamp it as verified now.

    Human only and audited, like every other change to how an agency is reached. The row is locked
    so an address being replaced at the same moment cannot end up stamped as verified. An agency
    with no address has nothing to verify.
    """
    require_human(actor, "Verifying a request address")
    if not note or not note.strip():
        raise ServiceError("Verifying a request address needs a note saying how.")
    agency = Agency.objects.select_for_update().filter(pk=agency_id).first()
    if agency is None:
        raise NotFound(f"No agency {agency_id}.")
    if not agency.default_request_email:
        raise ServiceError(f"{agency.name} has no request email address to verify.")
    verified_at = timezone.now()
    Agency.objects.filter(pk=agency_id).update(request_email_verified_at=verified_at)
    audit_service.record_audit(
        actor,
        entity_type="agencies",
        entity_id=agency_id,
        field="request_email_verified_at",
        old_value=agency.request_email_verified_at,
        new_value=verified_at,
        note=note,
    )
    return Agency.objects.get(pk=agency_id)


def _clear_other_primaries(actor: Actor, agency_id: UUID, keep_contact_id: UUID | None) -> None:
    """Make room for a new primary contact: lock the agency, then un-primary whoever held it.

    The agency row is locked FIRST, then the current primary is looked up. Two people making two
    different contacts primary at once then take turns: the second finds the first's primary and
    clears it, instead of both inserting and one dying on the unique index. The order inside this
    function matters -- clear the old one before the caller sets the new one, because the index is
    checked statement by statement, not at commit.
    """
    if Agency.objects.select_for_update().filter(pk=agency_id).first() is None:
        raise NotFound(f"No agency {agency_id}.")
    previous = AgencyContact.objects.filter(agency_id=agency_id, is_primary=True)
    if keep_contact_id is not None:
        previous = previous.exclude(pk=keep_contact_id)
    for contact in previous:
        AgencyContact.objects.filter(pk=contact.pk).update(is_primary=False)
        audit_service.record_audit(
            actor,
            entity_type="agency_contacts",
            entity_id=contact.pk,
            field="is_primary",
            old_value=True,
            new_value=False,
            note="Another contact was made the primary one.",
        )


@transaction.atomic
def create_contact(
    actor: Actor,
    agency_id: UUID,
    *,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    title: str | None = None,
    notes: str | None = None,
    is_primary: bool = False,
    copy_on_every_request: bool = False,
) -> AgencyContact:
    if is_primary:
        # Un-primarying the previous one is an audited change to an existing row, so it needs a
        # person to answer for it, which a plain creation never did.
        require_human(actor, "Making a contact the primary one")
        _clear_other_primaries(actor, agency_id, None)
    with writable():
        return AgencyContact.objects.create(
            agency_id=agency_id,
            name=name,
            email=email,
            phone=phone,
            title=title,
            notes=notes,
            is_primary=is_primary,
            copy_on_every_request=copy_on_every_request,
        )


@transaction.atomic
def update_contact(actor: Actor, contact_id: UUID, note: str, **fields: Any) -> AgencyContact:
    """Edit a contact. `is_primary=True` makes it the agency's one primary contact, and clears the
    previous primary in the same transaction."""

    def make_room(row: AgencyContact, changed: dict[str, Any]) -> None:
        if changed.get("is_primary") is True:
            _clear_other_primaries(actor, row.agency_id, row.pk)

    return _update_audited(
        actor,
        AgencyContact,
        "agency_contacts",
        contact_id,
        CONTACT_FIELDS,
        note,
        fields,
        before_update=make_room,
    )


# ---- merging (P16, DECISIONS 2 and 21) ---------------------------------------------------------


@transaction.atomic
def merge_agencies(actor: Actor, *, keep_id: UUID, merge_id: UUID, note: str) -> Agency:
    """Mark `merge_id` as the same agency as `keep_id`. Nothing else moves: its requests keep
    their agency_id (which is deliberately not updatable), its contacts stay on it, and reads
    that want "the kept agency and everything merged into it" ask for that. Undone by
    `unmerge_agency`."""
    user_id = require_human(actor, "Merging agencies")
    if not note or not note.strip():
        raise ServiceError("Say why these are the same agency.")
    if keep_id == merge_id:
        raise ServiceError("Pick two different agencies.")
    rows = {a.pk: a for a in Agency.objects.select_for_update().filter(pk__in=[keep_id, merge_id])}
    keep, merge = rows.get(keep_id), rows.get(merge_id)
    if keep is None or merge is None:
        raise NotFound("No such agency.")
    if keep.merged_into_id is not None:
        raise InvalidTransition(f"{keep.name} is itself merged into another agency.")
    if merge.merged_into_id is not None:
        raise InvalidTransition(f"{merge.name} is already merged.")
    if Agency.objects.filter(merged_into_id=merge_id).exists():
        raise InvalidTransition(
            f"Other agencies are merged into {merge.name}; unmerge those first."
        )
    now = timezone.now()
    with writable():
        Agency.objects.filter(pk=merge_id).update(
            merged_into_id=keep_id, merged_at=now, merged_by_id=user_id, merge_note=note.strip()
        )
    audit_service.record_audit(
        actor,
        entity_type="agencies",
        entity_id=merge_id,
        field="merged_into_agency_id",
        old_value=None,
        new_value=keep_id,
        note=note.strip(),
    )
    return Agency.objects.get(pk=merge_id)


@transaction.atomic
def unmerge_agency(actor: Actor, agency_id: UUID, note: str) -> Agency:
    require_human(actor, "Unmerging an agency")
    if not note or not note.strip():
        raise ServiceError("Say why this is its own agency after all.")
    row = Agency.objects.select_for_update().filter(pk=agency_id).first()
    if row is None:
        raise NotFound(f"No agency {agency_id}.")
    if row.merged_into_id is None:
        raise InvalidTransition(f"{row.name} is not merged.")
    was = row.merged_into_id
    with writable():
        Agency.objects.filter(pk=agency_id).update(
            merged_into_id=None, merged_at=None, merged_by_id=None, merge_note=None
        )
    audit_service.record_audit(
        actor,
        entity_type="agencies",
        entity_id=agency_id,
        field="merged_into_agency_id",
        old_value=was,
        new_value=None,
        note=note.strip(),
    )
    return Agency.objects.get(pk=agency_id)


def same_agency_ids(agency_id: UUID) -> list[UUID]:
    """The agency and every agency merged into it: the read-side answer to "the same agency"."""
    return [
        agency_id,
        *Agency.objects.filter(merged_into_id=agency_id).values_list("pk", flat=True),
    ]
