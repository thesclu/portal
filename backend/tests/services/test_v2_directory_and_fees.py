"""The agency directory's v2 fields, the primary contact, the request address's verification,
and the two new fee fields."""

import uuid
from datetime import UTC, datetime

import pytest
from portal.records.models import Agency, AgencyContact, AuditLog, Fee
from portal.records.services import directory
from portal.records.services import fees as fees_service
from portal.records.services.common import NotAllowed, NotFound, ServiceError

pytestmark = pytest.mark.usefixtures("as_each_role")


def _audit(entity_id, field, table="agencies"):
    return list(AuditLog.objects.filter(entity_id=entity_id, field_name=f"{table}.{field}"))


# ---- the agency's address and filing method ----------------------------------------------------


def test_an_agency_carries_its_postal_address_and_filing_method(staff_actor) -> None:
    agency = directory.create_agency(
        staff_actor,
        name="Addressed Agency",
        jurisdiction="Testland",
        short_name="ADDR",
        recipient_office="Records Unit",
        mailing_street="1 Example Way",
        mailing_city_state_zip="Testville, TL 00000",
        filing_method="portal",
        portal_kind="GovQA",
    )
    again = Agency.objects.get(pk=agency.pk)
    assert (again.short_name, again.recipient_office) == ("ADDR", "Records Unit")
    assert (again.mailing_street, again.mailing_city_state_zip) == (
        "1 Example Way",
        "Testville, TL 00000",
    )
    assert (again.filing_method, again.portal_kind) == ("portal", "GovQA")


def test_the_filing_vocabularies_are_checked_in_the_service(staff_actor) -> None:
    assert directory.FILING_METHODS == ("email", "portal", "mail")
    with pytest.raises(ServiceError, match="filing method"):
        directory.create_agency(
            staff_actor, name="X", jurisdiction="Testland", filing_method="carrier_pigeon"
        )
    with pytest.raises(ServiceError, match="portal kind"):
        directory.create_agency(
            staff_actor,
            name="X",
            jurisdiction="Testland",
            filing_method="portal",
            portal_kind="NotAVendor",
        )
    with pytest.raises(ServiceError, match="only applies"):
        directory.create_agency(
            staff_actor,
            name="X",
            jurisdiction="Testland",
            filing_method="email",
            portal_kind="GovQA",
        )


def test_an_edit_is_checked_against_the_resulting_filing_state(staff_actor, agency) -> None:
    directory.update_agency(
        staff_actor, agency.pk, "moved to a portal", filing_method="portal", portal_kind="GovQA"
    )
    # Switching away from the portal must say what becomes of the portal kind.
    with pytest.raises(ServiceError, match="only applies"):
        directory.update_agency(staff_actor, agency.pk, "back to email", filing_method="email")
    directory.update_agency(
        staff_actor, agency.pk, "back to email", filing_method="email", portal_kind=None
    )
    assert Agency.objects.get(pk=agency.pk).portal_kind is None
    with pytest.raises(ServiceError, match="filing method"):
        directory.update_agency(staff_actor, agency.pk, "typo", filing_method="fax")


def test_request_email_verified_at_is_not_directly_editable(staff_actor, agency) -> None:
    with pytest.raises(ServiceError, match="not editable"):
        directory.update_agency(
            staff_actor, agency.pk, "backdating", request_email_verified_at=datetime.now(UTC)
        )


# ---- the request address and its verification --------------------------------------------------


def test_changing_the_request_address_clears_its_verification_and_audits_that(
    staff_actor, agency
) -> None:
    directory.update_agency(
        staff_actor, agency.pk, "first address", default_request_email="records@example.org"
    )
    verified = directory.verify_request_email(staff_actor, agency.pk, "test message delivered")
    assert verified.request_email_verified_at is not None

    # Editing something else leaves it verified.
    directory.update_agency(staff_actor, agency.pk, "fixed a typo", notes="Open weekdays.")
    assert Agency.objects.get(pk=agency.pk).request_email_verified_at is not None

    changed = directory.update_agency(
        staff_actor, agency.pk, "they moved mailboxes", default_request_email="new@example.org"
    )
    assert changed.request_email_verified_at is None
    (cleared,) = [
        line for line in _audit(agency.pk, "request_email_verified_at") if line.new_value is None
    ]
    assert cleared.old_value == str(verified.request_email_verified_at)
    assert cleared.note == "they moved mailboxes"


def test_setting_an_address_for_the_first_time_has_nothing_to_clear(staff_actor, agency) -> None:
    directory.update_agency(
        staff_actor, agency.pk, "first address", default_request_email="records@example.org"
    )
    assert _audit(agency.pk, "request_email_verified_at") == []


def test_verifying_needs_an_address_a_person_and_a_note(staff_actor, system_actor, agency) -> None:
    with pytest.raises(ServiceError, match="no request email"):
        directory.verify_request_email(staff_actor, agency.pk, "tried")
    directory.update_agency(
        staff_actor, agency.pk, "address", default_request_email="records@example.org"
    )
    with pytest.raises(NotAllowed):
        directory.verify_request_email(system_actor, agency.pk, "automated")
    with pytest.raises(ServiceError, match="note"):
        directory.verify_request_email(staff_actor, agency.pk, " ")
    with pytest.raises(NotFound):
        directory.verify_request_email(staff_actor, uuid.uuid4(), "why")


# ---- the primary contact -----------------------------------------------------------------------


def _primaries(agency):
    return set(
        AgencyContact.objects.filter(agency_id=agency.pk, is_primary=True).values_list(
            "name", flat=True
        )
    )


def test_making_a_contact_primary_clears_the_previous_one_in_the_same_transaction(
    staff_actor, agency
) -> None:
    first = directory.create_contact(staff_actor, agency.pk, name="First", is_primary=True)
    second = directory.create_contact(staff_actor, agency.pk, name="Second")
    assert _primaries(agency) == {"First"}

    directory.update_contact(staff_actor, second.pk, "she is the records officer", is_primary=True)

    assert _primaries(agency) == {"Second"}
    (lost,) = _audit(first.pk, "is_primary", "agency_contacts")
    assert (lost.old_value, lost.new_value) == ("True", "False")
    (gained,) = _audit(second.pk, "is_primary", "agency_contacts")
    assert (gained.old_value, gained.new_value) == ("False", "True")


def test_creating_a_primary_contact_also_clears_the_previous_one(staff_actor, agency) -> None:
    directory.create_contact(staff_actor, agency.pk, name="First", is_primary=True)
    directory.create_contact(staff_actor, agency.pk, name="Second", is_primary=True)
    assert _primaries(agency) == {"Second"}


def test_a_primary_is_per_agency_and_may_be_given_up(staff_actor, agency) -> None:
    other = directory.create_agency(staff_actor, name="Other Agency", jurisdiction="Testland")
    mine = directory.create_contact(staff_actor, agency.pk, name="Mine", is_primary=True)
    directory.create_contact(staff_actor, other.pk, name="Theirs", is_primary=True)
    assert _primaries(agency) == {"Mine"} and _primaries(other) == {"Theirs"}
    directory.update_contact(staff_actor, mine.pk, "no longer the main contact", is_primary=False)
    assert _primaries(agency) == set()


def test_making_a_creation_primary_needs_a_person(system_actor, agency) -> None:
    with pytest.raises(NotAllowed):
        directory.create_contact(system_actor, agency.pk, name="X", is_primary=True)


# ---- fees --------------------------------------------------------------------------------------


def test_a_fee_carries_a_description_and_a_payment_method(reserved, staff_actor) -> None:
    fee = fees_service.record_fee(
        staff_actor,
        reserved.pk,
        status="assessed",
        amount_cents=4250,
        description="Duplication, 425 pages",
    )
    assert (fee.description, fee.payment_method) == ("Duplication, 425 pages", None)
    fee = fees_service.update_fee(
        staff_actor, fee.pk, status="paid", note="check mailed", payment_method="Check #1041"
    )
    assert (fee.status, fee.payment_method) == ("paid", "Check #1041")
    assert fee.description == "Duplication, 425 pages"
    (line,) = _audit(fee.pk, "payment_method", "fees")
    assert (line.old_value, line.new_value, line.note) == (None, "Check #1041", "check mailed")


def test_a_fee_update_that_leaves_the_new_fields_out_leaves_them_alone(
    reserved, staff_actor
) -> None:
    fee = fees_service.record_fee(
        staff_actor,
        reserved.pk,
        status="assessed",
        description="Duplication",
        payment_method="Check #1041",
    )
    fee = fees_service.update_fee(staff_actor, fee.pk, status="waiting_on_payment", note="sent")
    assert (fee.description, fee.payment_method) == ("Duplication", "Check #1041")
    fee = fees_service.update_fee(
        staff_actor, fee.pk, status="waiting_on_payment", note="cleared", payment_method=None
    )
    assert (fee.description, fee.payment_method) == ("Duplication", None)
    assert Fee.objects.get(pk=fee.pk).payment_method is None


def test_a_fee_update_that_leaves_paid_at_and_waiver_reason_out_leaves_them_alone(
    reserved, staff_actor
) -> None:
    """F3: they used to read "not mentioned" as "set to None", so editing only the payment method
    of a paid fee erased the date it was paid."""
    paid_at = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    fee = fees_service.record_fee(
        staff_actor, reserved.pk, status="waived", waiver_reason="public interest"
    )
    fee = fees_service.update_fee(
        staff_actor, fee.pk, status="paid", paid_at=paid_at, note="cheque cleared"
    )
    assert (fee.paid_at, fee.waiver_reason) == (paid_at, "public interest")

    fee = fees_service.update_fee(
        staff_actor, fee.pk, status="paid", payment_method="Check #1041", note="method"
    )

    assert (fee.paid_at, fee.waiver_reason, fee.payment_method) == (
        paid_at,
        "public interest",
        "Check #1041",
    )
    assert len(_audit(fee.pk, "paid_at", "fees")) == 1  # the one that set it, none for this edit
    assert _audit(fee.pk, "waiver_reason", "fees") == []


def test_an_explicit_none_still_clears_paid_at_and_waiver_reason(reserved, staff_actor) -> None:
    paid_at = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    fee = fees_service.record_fee(
        staff_actor, reserved.pk, status="waived", waiver_reason="public interest"
    )
    fee = fees_service.update_fee(
        staff_actor, fee.pk, status="paid", paid_at=paid_at, note="cheque cleared"
    )

    fee = fees_service.update_fee(
        staff_actor,
        fee.pk,
        status="assessed",
        paid_at=None,
        waiver_reason=None,
        note="the cheque bounced and the waiver was withdrawn",
    )

    assert (fee.paid_at, fee.waiver_reason) == (None, None)
    (cleared,) = [line for line in _audit(fee.pk, "paid_at", "fees") if line.old_value is not None]
    assert cleared.new_value is None
    (withdrawn,) = _audit(fee.pk, "waiver_reason", "fees")
    assert (withdrawn.old_value, withdrawn.new_value) == ("public interest", None)
