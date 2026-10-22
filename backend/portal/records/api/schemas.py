"""Request/response shapes for the records API. In/Out split so a write payload never leaks a
cache column (current_status, current_status_changed_at, next_deadline_at -- schema trigger only).
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema

# ---- agencies --------------------------------------------------------------


class AgencyOut(Schema):
    id: UUID
    name: str
    jurisdiction: str
    records_law: str | None
    portal_automation: str | None
    default_request_email: str | None
    portal_url: str | None
    notes: str | None
    created_at: datetime
    short_name: str | None
    recipient_office: str | None
    mailing_street: str | None
    mailing_city_state_zip: str | None
    filing_method: str | None
    portal_kind: str | None
    request_email_verified_at: datetime | None
    # P16: set when this agency was merged into another; the other is the one to use.
    merged_into_agency_id: UUID | None
    merged_at: datetime | None
    # P23b: the file the importer created it from (172 "added by import"); null when added by hand.
    imported_from: str | None = None


class AgencyRowOut(AgencyOut):
    """A row of the Agencies list (163): the agency and two figures over the requests the caller
    may see, the ones of the agencies merged into it included. `open_requests` counts those not
    closed, denied or withdrawn; `requests_total` every one (the "Most requests" order); the
    median is calendar dates from filing to the first determination, as Reports count it, over
    every year."""

    open_requests: int = 0
    requests_total: int = 0
    median_days_to_determination: int | None = None


class AgencyImportFieldOut(Schema):
    key: str
    label: str


class AgencyImportRowOut(Schema):
    """One row of an uploaded agency list as the import would treat it (168)."""

    number: int
    values: dict[str, str]
    status: Literal["new", "match", "problem"]
    problems: list[str]
    match_agency_id: UUID | None
    match_agency_name: str | None
    # The matching agency's blank fields this row would fill in if the person chooses to.
    fills: list[str]


class AgencyImportPreviewOut(Schema):
    filename: str
    headers: list[str]
    # Each column of the file and the field it is read as (null: not imported).
    mapping: dict[str, str | None]
    fields: list[AgencyImportFieldOut]
    rows: list[AgencyImportRowOut]
    new: int
    matches: int
    problems: int


class AgencyImportResultOut(Schema):
    created: int
    updated: int
    skipped: int
    problems: int
    summary: str


class ContactPatchIn(Schema):
    """Edit a contact (171). A field left out is left alone; every change is an audit line
    carrying `note`."""

    note: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    title: str | None = None
    notes: str | None = None
    is_primary: bool | None = None
    copy_on_every_request: bool | None = None


class ClosureOut(Schema):
    id: UUID
    agency_id: UUID
    agency_name: str
    starts_on: date
    ends_on: date
    reason: str
    citation: str
    created_at: datetime
    voided_at: datetime | None
    void_note: str | None


class ClosuresIn(Schema):
    """One closure per agency named. The citation is the person's; the portal fills nothing."""

    agency_ids: list[UUID]
    starts_on: date
    ends_on: date
    reason: str
    citation: str


class ClosuresAddedOut(Schema):
    closures: list[ClosureOut]
    flagged_deadlines: int
    tasks_raised: int


class MergeIn(Schema):
    keep_id: UUID
    merge_id: UUID
    note: str


class NoteIn(Schema):
    note: str


class AgencyIn(Schema):
    name: str
    jurisdiction: str
    records_law: str | None = None
    portal_automation: str | None = None
    default_request_email: str | None = None
    portal_url: str | None = None
    notes: str | None = None
    short_name: str | None = None
    recipient_office: str | None = None
    mailing_street: str | None = None
    mailing_city_state_zip: str | None = None
    filing_method: str | None = None
    portal_kind: str | None = None


class AgencyPatchIn(Schema):
    note: str
    name: str | None = None
    jurisdiction: str | None = None
    records_law: str | None = None
    portal_automation: str | None = None
    default_request_email: str | None = None
    portal_url: str | None = None
    notes: str | None = None
    short_name: str | None = None
    recipient_office: str | None = None
    mailing_street: str | None = None
    mailing_city_state_zip: str | None = None
    filing_method: str | None = None
    portal_kind: str | None = None


class ContactOut(Schema):
    id: UUID
    agency_id: UUID
    name: str
    email: str | None
    phone: str | None
    title: str | None
    notes: str | None
    is_primary: bool
    # P11: in Cc on every message to the agency; the bad-address mark.
    copy_on_every_request: bool = False
    bad_address_at: datetime | None = None
    bad_address_note: str | None = None
    created_at: datetime


class ContactIn(Schema):
    name: str
    email: str | None = None
    phone: str | None = None
    title: str | None = None
    notes: str | None = None
    is_primary: bool = False
    copy_on_every_request: bool = False


# ---- requests ---------------------------------------------------------------


class NewItemIn(Schema):
    item_number: str
    description: str
    parent_item_number: str | None = None
    sort_order: int = 0
    provision: str | None = None


class ReserveRequestIn(Schema):
    agency_id: UUID
    requester_user_id: UUID
    subject: str
    request_text: str
    items: list[NewItemIn] = []
    sensitivity: str = "internal"


class RequestOut(Schema):
    id: UUID
    tracking_number: str
    agency_id: UUID
    requester_user_id: UUID
    inbound_alias_email: str | None
    subject: str
    request_text: str
    current_status: str
    current_status_changed_at: datetime | None
    next_deadline_at: date | None
    sensitivity: str
    assigned_to_user_id: UUID | None
    filed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # The agency's own number for the request, from its acknowledgment ("Agency ref", 032).
    agency_reference_number: str | None = None
    # Whether the automatic follow-ups run for this request (the request page's "Turn off
    # automatic follow-ups", 088); changed through the audited edit door.
    auto_followups_enabled: bool = True


class DocketRowOut(Schema):
    id: UUID
    tracking_number: str
    inbound_alias_email: str | None
    subject: str
    agency_name: str
    current_status: str
    current_status_changed_at: datetime | None
    next_deadline_at: date | None
    # next_deadline_at minus today on the agency's own calendar: positive is time left, negative
    # is time overdue, null when there is no active deadline or the agency has no calendar mapped.
    days_to_next_deadline: int | None
    filed_at: datetime | None
    sensitivity: str
    assigned_to_user_id: UUID | None
    # P19e: how many items the request asks for (the picker's "Contents").
    item_count: int = 0


class RequestsPage(Schema):
    items: list[DocketRowOut]
    total: int
    limit: int
    offset: int


class DueOnDateOut(Schema):
    date: date
    count: int


class DocketSummaryOut(Schema):
    """The stat strip (025), counted under the same visibility rule as the list. The three status
    lists are what the matching boxes count, so the screen filters the list by exactly them."""

    # The viewer's civil date, in their own time zone, that `overdue` and the week are read from.
    today: date
    overdue: int
    # Monday to Friday of the viewer's current week: requests whose next deadline is that date.
    due_by_date: list[DueOnDateOut]
    producing: int
    producing_unreviewed: int
    waiting_on_agency: int
    assigned_to_me: int
    all_open: int
    producing_statuses: list[str]
    unreviewed_statuses: list[str]
    waiting_statuses: list[str]


class DocketHistoryPointOut(Schema):
    date: date
    overdue: int
    open: int


class DocketHistoryOut(Schema):
    span: int
    points: list[DocketHistoryPointOut]


class RequestItemOut(Schema):
    id: UUID
    item_number: str
    description: str
    parent_item_id: UUID | None
    sort_order: int
    provision: str | None
    # P12: the newest production review's reading of this item, or null before any review.
    disposition: str | None = None
    # P20c: what that review said about it, for the item's hover (063, 064): when the production
    # it read arrived, the exemption the agency cited, and the reviewer's note.
    disposition_at: datetime | None = None
    exemption_cited: str | None = None
    disposition_note: str | None = None


class EventOut(Schema):
    id: UUID
    request_id: UUID
    occurred_at: datetime
    event_type: str
    channel: str
    direction: str
    actor_user_id: UUID | None
    actor_agency_contact_id: UUID | None
    display_body: str
    source_id: UUID | None
    related_deadline_id: UUID | None
    voided_at: datetime | None
    void_reason: str | None


class DeadlineOut(Schema):
    id: UUID
    request_id: UUID
    request_item_id: UUID | None
    deadline_type: str
    legal_basis: str | None
    provision: str | None
    basis: str
    due_at: date
    status: str
    source_event_id: UUID | None
    satisfied_by_event_id: UUID | None
    superseded_by_deadline_id: UUID | None
    flagged_for_review: bool
    flag_note: str | None
    rule_ref: str | None


class StatusChangeOut(Schema):
    id: UUID
    request_id: UUID
    status: str
    effective_at: datetime
    triggering_event_id: UUID | None
    triggering_deadline_id: UUID | None
    reason: str
    note: str | None
    voided_at: datetime | None


class TaskOut(Schema):
    id: UUID
    request_id: UUID
    task_type: str
    description: str
    due_at: datetime | None
    completed_at: datetime | None
    completed_by_id: UUID | None
    assigned_to_user_id: UUID | None
    created_by_user_id: UUID | None
    created_by_system: str | None
    category: str | None
    created_at: datetime
    # The request it is on, named: a task list is read without opening each request.
    tracking_number: str
    agency_name: str
    # P20e: the Tasks list draws the request as "REQ-… · subject" (173).
    request_subject: str = ""
    # P23b: the message the task is about (a bounce's sent message, the message a task was made
    # or recorded from), so "Retry send" opens that message. Null for any other task.
    source_id: UUID | None = None


class TaskNoteOut(Schema):
    """A note on a task (177 "Write a note…"). A voided note is still listed, with who voided it,
    when and why."""

    id: UUID
    task_id: UUID
    author_user_id: UUID
    author_name: str
    body: str
    created_at: datetime
    voided_at: datetime | None
    voided_by_name: str | None
    void_reason: str | None


class TaskNoteIn(Schema):
    body: str


class VoidTaskNoteIn(Schema):
    reason: str


class TaskHistoryOut(Schema):
    """One change to a task, from the audit log (177, 178): who, which field, from what to what,
    and the note they gave. Creation and completion are on the task itself."""

    at: datetime
    actor_id: UUID | None
    actor_name: str | None
    field: str
    old_value: str | None
    new_value: str | None
    note: str


class FeeOut(Schema):
    id: UUID
    request_id: UUID
    source_event_id: UUID | None
    amount_cents: int | None
    status: str
    waiver_reason: str | None
    due_at: date | None
    paid_at: datetime | None
    description: str | None
    payment_method: str | None
    created_at: datetime


class RequestDetailOut(RequestOut):
    agency_name: str
    items: list[RequestItemOut]
    events: list[EventOut]
    deadlines: list[DeadlineOut]
    status_history: list[StatusChangeOut]
    tasks: list[TaskOut]
    fees: list[FeeOut]
    # P12: live links in either direction, and the files tied to the request by a review.
    links: list["LinkOut"] = []
    file_links: list["FileLinkOut"] = []
    # P14: appeals and search challenges on this request.
    challenges: list["ChallengeOut"] = []


class FileRequestIn(Schema):
    filed_at: datetime
    channel: str
    source_id: UUID | None = None
    request_provision: str | None = None
    item_provisions: dict[UUID, list[str]] | None = None


class FilingPreviewIn(Schema):
    """What filing would be told (083), to ask what it would open before filing: the moment,
    and the provisions as `FileRequestIn` takes them."""

    filed_at: datetime
    request_provision: str | None = None
    item_provisions: dict[UUID, list[str]] | None = None


class PlannedDeadlineOut(Schema):
    """A deadline filing would open (083 "Determination due Sep 18 · …"): from the rules, with
    the legal basis as the rule states it."""

    deadline_type: str
    due_at: date
    legal_basis: str
    provision: str | None
    request_item_id: UUID | None
    # The rule's own citation, as entered with the rule (the rules file is the only source).
    citation: str = ""


class ProvisionGapOut(Schema):
    """An item's provision with no rule in force: filing opens an "enter rules" task for it."""

    deadline_type: str
    provision: str
    request_item_id: UUID | None


class FilingPreviewOut(Schema):
    deadlines: list[PlannedDeadlineOut]
    unresolved: list[ProvisionGapOut]
    # Why nothing could be computed (no rule or calendar for the agency's law); filing then opens
    # one "enter rules" task.
    problem: str | None = None


class StatusIn(Schema):
    status: str
    note: str


class AssignIn(Schema):
    """`user_id: null` (or leaving it out) unassigns."""

    user_id: UUID | None = None


class AssignRequestIn(AssignIn):
    """Assigning a request (036). `notify` is "Tell them by email": the new assignee is always
    told in the portal, and by email too only when this is true and their own preference for
    assignments allows it. Left out, it is false."""

    notify: bool | None = None


class NarrowIn(Schema):
    edits: dict[UUID, str]
    note: str


class AddItemIn(Schema):
    item_number: str
    description: str
    parent_item_number: str | None = None
    sort_order: int = 0
    provision: str | None = None
    note: str


class EditIn(Schema):
    entity_type: str
    entity_id: UUID
    field: str
    new_value: Any
    note: str
    status: str | None = None


class AuditLogOut(Schema):
    id: UUID
    entity_type: str
    entity_id: UUID
    field_name: str
    old_value: str | None
    new_value: str | None
    edited_by_id: UUID | None
    note: str
    edited_at: datetime


# ---- events -------------------------------------------------------------


class EventIn(Schema):
    event_type: str
    channel: str
    direction: str
    occurred_at: datetime
    display_body: str
    source_id: UUID | None = None
    actor_user_id: UUID | None = None
    actor_agency_contact_id: UUID | None = None
    related_deadline_id: UUID | None = None
    status: str | None = None
    note: str | None = None


class VoidIn(Schema):
    reason: str


class StatusDefOut(Schema):
    code: str
    label: str
    sort_order: int
    is_terminal: bool


class EventDefOut(Schema):
    code: str
    label: str
    sort_order: int


class DeadlineKindOut(Schema):
    code: str
    label: str
    sort_order: int
    overdue_status: str | None


class TaskTypeOut(Schema):
    code: str
    category: str


class ClassificationKindOut(Schema):
    """One item of the "Record as..." / "Log a response" menu, from the table that also drives the
    preview and the write."""

    kind: str
    label: str
    event_type: str
    status: str | None
    task_type: str | None
    # The row goes through override_status: a person, with a note that says why.
    through_override: bool
    note_required: bool
    takes_fee: bool
    takes_records: bool


class DefinitionsOut(Schema):
    statuses: list[StatusDefOut]
    event_types: list[EventDefOut]
    deadline_kinds: list[DeadlineKindOut]
    task_types: list[TaskTypeOut]
    task_categories: list[str]
    flag_reasons: list[str]
    resolutions: list[str]
    inbox_buckets: list[str]
    classifications: list[ClassificationKindOut]


# ---- deadlines ------------------------------------------------------------


class OpenDeadlineIn(Schema):
    deadline_type: str
    due_at: date
    basis: str
    legal_basis: str | None = None
    provision: str | None = None
    request_item_id: UUID | None = None
    source_event_id: UUID | None = None
    rule_ref: str | None = None
    supersedes: UUID | None = None
    note: str | None = None


class SatisfyDeadlineIn(Schema):
    satisfied_by_event_id: UUID


class WaiveDeadlineIn(Schema):
    note: str


class ResolveDeadlineIn(Schema):
    """The deadline in the path is the one that STANDS; this names the one that is dropped."""

    drop_deadline_id: UUID
    note: str


# ---- sources ----------------------------------------------------------------


class SourceOut(Schema):
    id: UUID
    source_type: str
    external_ref: str | None
    message_id: str | None
    raw_metadata: dict[str, Any] | None
    raw_storage_url: str
    flagged_for_review: bool
    flag_note: str | None
    flag_reason: str | None
    assigned_to_user_id: UUID | None
    resolution: str | None
    resolution_note: str | None
    reviewed_at: datetime | None
    reviewed_by_id: UUID | None
    captured_at: datetime


class SourcesPage(Schema):
    items: list[SourceOut]
    total: int
    limit: int
    offset: int


class AssignSourceIn(Schema):
    request_id: UUID
    mode: Literal["also_applies", "belongs_here_instead"]
    from_event_id: UUID | None = None
    # P23b (075 "Move it" · Reason): why it is moved, written with the move onto the event it
    # leaves, where the people on that request read it.
    reason: str | None = None


class AttachmentOut(Schema):
    id: UUID
    source_id: UUID
    filename: str
    mime_type: str | None
    size_bytes: int | None
    role: str
    # Set when a file is brought in; null when its pages cannot be counted.
    page_count: int | None
    checksum_sha256: str | None
    created_at: datetime


class FlagSourceIn(Schema):
    note: str
    reason: str = "other"


class ResolveSourceIn(Schema):
    """The note is required for `other` and optional otherwise."""

    resolution: str
    note: str | None = None


class SourceNoteIn(Schema):
    body: str


class SourceNoteOut(Schema):
    id: UUID
    source_id: UUID
    author_user_id: UUID
    body: str
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime


class SourceRecordsIn(Schema):
    """Exactly these attachments of the message are the records; one not named stops being one."""

    record_attachment_ids: list[UUID]


class DocumentIn(Schema):
    """The form fields of POST /requests/{id}/documents; the files travel beside them."""

    source_type: str
    received_at: datetime
    display_body: str = ""
    # Only needed when `source_type` is `other`; otherwise the channel follows from what it is.
    channel: str | None = None
    # P23b (102 "From"): who at the agency sent it, one of the agency's contacts.
    contact_id: UUID | None = None


class DocumentOut(Schema):
    source: SourceOut
    attachments: list[AttachmentOut]
    event: EventOut


# ---- the inbox ----------------------------------------------------------------------------------


class InboxCountsOut(Schema):
    """Messages in each bucket. Not narrowed by the search or the filters."""

    flagged: int
    new: int
    all: int


class InboxEventOut(Schema):
    """Where a message landed: one non-voided event of it, with its request and agency."""

    id: UUID
    request_id: UUID
    tracking_number: str
    agency_id: UUID
    agency_name: str
    event_type: str
    channel: str
    direction: str
    occurred_at: datetime
    display_body: str


class InboxRowOut(Schema):
    source: SourceOut
    attachments: list[AttachmentOut]
    events: list[InboxEventOut]
    note_count: int
    assignee_name: str | None
    # P23b. Who last moved it to Flagged and when (140): a person's name, or a process's ("Incoming
    # mail"). Null when nothing recorded it.
    flagged_by: str | None = None
    flagged_at: datetime | None = None
    # P23b. Who recorded what its first event means, and when (143).
    recorded_by: str | None = None
    recorded_at: datetime | None = None


class InboxItemOut(InboxRowOut):
    """One message as an Inbox row (P23b), read by its id, with the buckets it is in now: a
    message opened by its address beyond the loaded page, or one that left the list it was
    opened from (140), or the full-page message (107 "Open full page")."""

    buckets: list[str]


class InboxOut(Schema):
    bucket: str
    counts: InboxCountsOut
    # Messages in this bucket that match the filters, before paging.
    total: int
    limit: int
    offset: int
    items: list[InboxRowOut]


# ---- "Record as..." -----------------------------------------------------------------------


class PlannedTaskOut(Schema):
    task_type: str
    description: str
    due_at: datetime | None
    # Who opens it: the classification itself, or the status change it appends.
    opened_by: Literal["classify", "status_chokepoint"]


class ClassifyPreviewOut(Schema):
    """What recording this event as this kind will do. `deadlines` are the request's active ones as
    they stand; classifying changes none of them."""

    kind: str
    label: str
    event_id: UUID
    request_id: UUID
    event_type_from: str
    event_type_to: str
    status_from: str
    status_to: str | None
    status_reason: Literal["event", "manual_override"] | None
    deadlines: list[DeadlineOut]
    task: PlannedTaskOut | None
    note_required: bool
    takes_fee: bool
    takes_records: bool


class FeeEstimateIn(Schema):
    amount_cents: int | None = None
    description: str | None = None
    due_at: date | None = None


class ClassifyIn(Schema):
    kind: str
    note: str | None = None
    fee: FeeEstimateIn | None = None
    record_attachment_ids: list[UUID] = []
    agency_reference_number: str | None = None
    task_due_at: datetime | None = None


class ClassifyOut(Schema):
    event: EventOut
    request: RequestOut
    status_change: StatusChangeOut | None
    fee: FeeOut | None
    task: TaskOut | None
    attachments: list[AttachmentOut]


# ---- Home, Calendar, Rules ----------------------------------------------------------------


class DeadlineRowOut(Schema):
    """A deadline with the request it belongs to and how far off it is. Home lists active ones;
    the Calendar also lists satisfied, waived and missed ones, so `status` says which."""

    deadline: DeadlineOut
    status: str
    request_id: UUID
    tracking_number: str
    subject: str
    agency_id: UUID
    agency_name: str
    kind_label: str
    # due_at minus today on the agency's own calendar: positive is time left, negative overdue.
    days_from_today: int


class HomeTaskOut(Schema):
    task: TaskOut
    tracking_number: str
    agency_name: str


class HomeOut(Schema):
    overdue: list[DeadlineRowOut]
    due_soon: list[DeadlineRowOut]
    # How far ahead `due_soon` looked, from a settings row.
    due_soon_window_days: int
    assigned_tasks: list[HomeTaskOut]
    flagged: InboxOut


class HolidayOut(Schema):
    date: date
    # What an administrator typed in Settings -> Rules & calendars, or null. Never invented.
    name: str | None


class CalendarHolidaysOut(Schema):
    key: str
    timezone: str
    # `date.weekday()` numbers: Monday is zero.
    weekend: list[int]
    holidays: list[HolidayOut]


class CalendarOut(Schema):
    start: date
    end: date
    deadlines: list[DeadlineRowOut]
    calendars: list[CalendarHolidaysOut]


class RuleOut(Schema):
    id: str
    law: str
    provision: str
    deadline_type: str
    count: int
    unit: str
    roll_forward_on_non_business_day: bool
    citation: str
    effective_from: date
    effective_to: date | None


class RuleCalendarOut(Schema):
    key: str
    timezone: str
    laws: list[str]
    holiday_count: int
    last_holiday: date | None
    # Every holiday from the calendar's own today on; the screen shows the first few.
    upcoming_holidays: list[HolidayOut]
    weekend: list[int]
    holidays: list[HolidayOut]


class MissingRuleOut(Schema):
    """An open enter_deadline_rules task: a provision a request names that no rule covers."""

    task: TaskOut
    tracking_number: str
    agency_name: str


class RulesOut(Schema):
    rules: list[RuleOut]
    calendars: list[RuleCalendarOut]
    missing: list[MissingRuleOut]


# ---- tasks --------------------------------------------------------------


class TaskIn(Schema):
    request_id: UUID
    task_type: str
    description: str
    due_at: datetime | None = None
    assigned_to_user_id: UUID | None = None
    # P23b: the message the task is about (the request page's "make a task from this message").
    # It must be on `request_id`.
    source_id: UUID | None = None


class TaskPatchIn(Schema):
    """A field left out is left alone; `due_at: null` clears the date."""

    note: str
    due_at: datetime | None = None
    description: str | None = None


class ReopenTaskIn(Schema):
    note: str


# ---- fees -------------------------------------------------------------------


class FeeIn(Schema):
    status: str
    amount_cents: int | None = None
    source_event_id: UUID | None = None
    waiver_reason: str | None = None
    due_at: date | None = None
    description: str | None = None
    payment_method: str | None = None


class FeePatchIn(Schema):
    """PATCH: a field left out is left alone, and only an explicit null clears it. `status` and
    `note` are required."""

    status: str
    paid_at: datetime | None = None
    waiver_reason: str | None = None
    note: str
    description: str | None = None
    payment_method: str | None = None


# ---- the agency detail page -----------------------------------------------------------------


class ResponseRecordOut(Schema):
    """Computed from events and deadlines on every call; stored nowhere. The date-based figures
    are null for an agency whose law has no calendar mapped."""

    median_days_to_determination: float | None
    determinations_on_time: int | None
    determinations_total: int | None
    extensions_taken: int


class AgencyDetailOut(AgencyOut):
    requests: list[DocketRowOut]
    response_record: ResponseRecordOut
    # P16: the agencies merged into this one (their requests are in `requests`), and the days
    # this office is closed, coming ones first.
    merged_from: list[AgencyOut]
    closures: list[ClosureOut]


class AccessIn(Schema):
    """Share a privileged request with `user_id`, a member of staff. `note` says why."""

    user_id: UUID
    note: str | None = None


class AccessOut(Schema):
    """One person's access to one privileged request. `revoked_at` is null while it is in force."""

    id: UUID
    request_id: UUID
    user_id: UUID
    user_name: str
    granted_by_user_id: UUID | None
    note: str | None
    granted_at: datetime
    revoked_at: datetime | None
    revoked_by_user_id: UUID | None


class UnlistedOut(Schema):
    """A privileged request the caller cannot see: enough to ask for it or add oneself, no more."""

    request_id: UUID
    tracking_number: str


class CandidateOut(Schema):
    """A request a flagged message may also be about."""

    request_id: UUID
    tracking_number: str
    subject: str
    agency_id: UUID
    agency_name: str
    agency_reference_number: str | None


class MessageHeaderOut(Schema):
    name: str
    value: str


class MessageOut(Schema):
    """A stored message, read for a person. Plain text only; markup is never passed through."""

    readable: bool
    from_addr: str = ""
    # The sender's display name, as the message gives it (P20d).
    from_name: str = ""
    to: list[str] = []
    cc: list[str] = []
    subject: str = ""
    date: datetime | None = None
    body: str = ""
    # Every header of the original in its own order ("Show original", P20d). Postmark hands over
    # From, To, Cc, Subject and Date as fields, not headers; they are added after its own.
    headers: list[MessageHeaderOut] = []


# ---- P12: production review, produced records, request links --------------------------------


class ItemReviewIn(Schema):
    request_item_id: UUID
    disposition: str
    exemption_cited: str | None = None
    cited_where: str | None = None
    note: str | None = None


class FileLinkIn(Schema):
    attachment_id: UUID
    request_item_id: UUID | None = None


class ReviewIn(Schema):
    event_id: UUID
    rows: list[ItemReviewIn]
    files: list[FileLinkIn] = []
    note: str | None = None


class ItemReviewOut(Schema):
    request_item_id: UUID
    item_number: str
    disposition: str
    exemption_cited: str | None
    cited_where: str | None
    note: str | None


class ReviewOut(Schema):
    id: UUID
    request_id: UUID
    event_id: UUID
    reviewed_by_user_id: UUID
    reviewed_at: datetime
    note: str | None
    # What the review concluded: partial_production, or production_received.
    outcome: str
    status_change_id: UUID | None
    supersedes_review_id: UUID | None
    rows: list[ItemReviewOut]


class ReviewSheetOut(Schema):
    """What a review of this event starts from: the event's files, every item with the
    disposition that stands for it today, and what a review with every item produced means."""

    event_id: UUID
    files: list[AttachmentOut]
    items: list[RequestItemOut]
    # Keyed by item id and by attachment id, as strings: JSON keys are strings.
    current: dict[str, ItemReviewOut]
    linked_files: dict[str, UUID | None]


class FileLinkOut(Schema):
    id: UUID
    attachment_id: UUID
    request_id: UUID
    request_item_id: UUID | None
    review_id: UUID | None
    created_at: datetime


class ProducedRecordOut(Schema):
    attachment: AttachmentOut
    request_id: UUID
    tracking_number: str
    subject: str
    agency_id: UUID
    agency_name: str
    item_id: UUID | None
    item_number: str | None
    item_description: str | None


class ProducedRecordsOut(Schema):
    items: list[ProducedRecordOut]
    total: int


class LinkIn(Schema):
    to_request_id: UUID
    kind: str
    reason: str


class LinkOut(Schema):
    id: UUID
    from_request_id: UUID
    from_tracking_number: str
    from_subject: str
    to_request_id: UUID
    to_tracking_number: str
    to_subject: str
    kind: str
    reason: str
    created_by_user_id: UUID
    created_at: datetime
    voided_at: datetime | None
    void_reason: str | None


class VoidLinkIn(Schema):
    reason: str


# ---- P13: asking for access to a privileged request -------------------------------------------


class AccessRequestOut(Schema):
    id: UUID
    request_id: UUID
    tracking_number: str
    user_id: UUID
    user_name: str
    reason: str
    created_at: datetime
    decided_at: datetime | None
    decided_by_user_id: UUID | None
    decision: str | None
    decision_note: str | None


class AskAccessIn(Schema):
    reason: str


class DecideAccessIn(Schema):
    allow: bool
    note: str | None = None


# ---- P14: appeals and search challenges ---------------------------------------------------------


class ChallengeOut(Schema):
    id: UUID
    request_id: UUID
    kind: str
    request_item_id: UUID | None
    review_item_id: UUID | None
    basis: str
    follow_up_by: date | None
    message_id: UUID | None
    created_by_user_id: UUID
    created_at: datetime
    sent_at: datetime | None
    event_id: UUID | None
    followup_deadline_id: UUID | None
    outcome: str | None
    outcome_note: str | None
    resolved_at: datetime | None
    resolved_by_user_id: UUID | None


class ChallengeIn(Schema):
    """Open a challenge and, unless `with_letter` is false, start its letter as a mail draft on
    the request, addressed to the agency, for the person to write."""

    kind: str
    basis: str
    request_item_id: UUID | None = None
    review_item_id: UUID | None = None
    follow_up_by: date | None = None
    with_letter: bool = True
    subject: str | None = None


class ChallengeSentIn(Schema):
    sent_at: datetime | None = None
    channel: str = "mail"
    note: str = ""


class ChallengeFollowUpIn(Schema):
    follow_up_by: date | None


class ChallengeOutcomeIn(Schema):
    outcome: str
    note: str = ""
