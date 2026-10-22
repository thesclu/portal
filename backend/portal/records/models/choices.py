"""The closed vocabularies schema.sql enforces with CHECK constraints, as Django choices.

Statuses, event types and deadline kinds are deliberately not here: they are rows in
status_definitions, event_definitions and deadline_definitions, and adding one is an insert.
(Deadline kinds used to be a CHECK and a choices class here; records_db.0007 made them rows, and
keeping a Python copy of a list the database no longer closes would be a second place for it to
drift.)
"""

from django.db import models


class PortalAutomation(models.TextChoices):
    NONE = "none", "None"
    FILING_ONLY = "filing_only", "Filing only"
    FILING_AND_SYNC = "filing_and_sync", "Filing and sync"


class RecordsRole(models.TextChoices):
    """The six roles (P20h, DECISIONS 37a, 42; records_db.0020 widens the CHECK). What each may
    see and do is decided by the groups in `portal/records/roles.py`, never by a comparison with
    one of these written somewhere else: a check written for `staff` must deny an intern."""

    ADMIN = "admin", "Admin"
    STAFF = "staff", "Staff"
    STAFF_ATTORNEY = "staff_attorney", "Staff attorney"
    LIMITED_ATTORNEY = "limited_attorney", "Attorney, limited scope"
    INTERN = "intern", "Intern"
    REQUESTER = "requester", "Requester"


class Origin(models.TextChoices):
    FILED = "filed", "Filed through the portal"
    IMPORTED = "imported", "Imported"


class Sensitivity(models.TextChoices):
    PUBLIC = "public", "Public"
    INTERNAL = "internal", "Internal"
    PRIVILEGED = "privileged", "Privileged"


class SourceType(models.TextChoices):
    POSTMARK_EMAIL = "postmark_email", "Postmark email"
    IMPORTED_EML = "imported_eml", "Imported .eml"
    PORTAL_CAPTURE = "portal_capture", "Portal capture"
    SCANNED_MAIL = "scanned_mail", "Scanned mail"
    OTHER = "other", "Other"
    # records_db.0013: the copy of a message the portal sent (P11).
    SENT_EMAIL = "sent_email", "Sent email"


class AttachmentRole(models.TextChoices):
    ATTACHMENT = "attachment", "Attachment"
    PRODUCED_RECORD = "produced_record", "Produced record"
    SUPPORTING_DOCUMENT = "supporting_document", "Supporting document"


class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    PORTAL = "portal", "Portal"
    MAIL = "mail", "Mail"
    CALL = "call", "Call"
    SYSTEM = "system", "System"
    # records_db.0007: "Meeting or visit". The only channel not in schema.sql's original CHECK.
    IN_PERSON = "in_person", "In person"


class Direction(models.TextChoices):
    OUTBOUND = "outbound", "Outbound"
    INBOUND = "inbound", "Inbound"
    INTERNAL = "internal", "Internal"


class DeadlineBasis(models.TextChoices):
    STATUTORY = "statutory", "Statutory"
    AGENCY_COMMITMENT = "agency_commitment", "Agency commitment"
    # records_db.0015 (P14): a date we set for ourselves, "follow up by".
    INTERNAL = "internal", "Our own date"


class DeadlineStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SATISFIED = "satisfied", "Satisfied"
    MISSED = "missed", "Missed"
    SUPERSEDED = "superseded", "Superseded"
    WAIVED = "waived", "Waived"


class StatusReason(models.TextChoices):
    EVENT = "event", "Event"
    DEADLINE_EXPIRED = "deadline_expired", "Deadline expired"
    MANUAL_OVERRIDE = "manual_override", "Manual override"


class FeeStatus(models.TextChoices):
    ASSESSED = "assessed", "Assessed"
    WAIVED = "waived", "Waived"
    WAITING_ON_PAYMENT = "waiting_on_payment", "Waiting on payment"
    PAID = "paid", "Paid"
    DISPUTED = "disputed", "Disputed"
