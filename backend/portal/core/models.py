"""Platform identity and settings. These tables live in the `core` schema.

A person is one uuid everywhere: `core.User` is the login account, and the
records app's own `records.users` row (its view of the same person, with the
records-app role) shares that uuid. See core.services.identity.
"""

import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils.timezone import now as tz_now


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def _create(self, email: str, name: str, password: str | None, **extra: Any) -> "User":
        """P25a: the portal holds no passwords; Keycloak does. Every account's password is the
        unusable marker Django keeps for accounts that sign in some other way."""
        if not email:
            raise ValueError("An email address is required.")
        if password:
            raise ValueError("The portal holds no passwords: Keycloak does (P25a).")
        user = self.model(email=self.normalize_email(email), name=name, **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(
        self, email: str, name: str = "", password: str | None = None, **extra: Any
    ) -> "User":
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, name, password, **extra)

    def create_superuser(
        self, email: str, name: str = "", password: str | None = None, **extra: Any
    ) -> "User":
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._create(email, name, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    # P15b: which notifications are also emailed, and the daily summary. Keys are the kinds in
    # `Notification`; absent keys take `notifications.DEFAULT_PREFERENCES`.
    notification_preferences = models.JSONField(default=dict, blank=True)
    is_staff = models.BooleanField(default=False, help_text="Portal administrator.")
    timezone = models.CharField(
        max_length=64,
        default="America/Los_Angeles",
        help_text="IANA zone used to display timestamps to this person.",
    )
    created_at = models.DateTimeField(default=tz_now, editable=False)
    # P20i: the title the person signs with ("Legal Intern"), confirmed at their first sign-in and
    # changed in Settings → Profile. Empty until they give one.
    title = models.CharField(max_length=200, blank=True, default="")
    # P20i: when they finished the new-user setup (v6 006–012). Null until they have; a person
    # with no date is sent to the setup from every page.
    setup_completed_at = models.DateTimeField(null=True, blank=True)
    # P23b: when an administrator deactivated the person (205 "Deactivated Aug 30"); cleared on
    # reactivation. Null for anyone active, and for anyone deactivated before it was kept.
    deactivated_at = models.DateTimeField(null=True, blank=True)
    # P25a: the person's account in Keycloak (its `sub`), which is how a sign-in finds them. Set
    # when the portal creates that account, or at an invited person's first sign-in (matched by
    # the address Keycloak verified). Null for a person with no sign-in (a requester).
    keycloak_sub = models.CharField(max_length=64, unique=True, null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        db_table = "users"

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(default=tz_now, editable=False)

    class Meta:
        db_table = "organizations"

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    created_at = models.DateTimeField(default=tz_now, editable=False)

    class Meta:
        db_table = "memberships"
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="membership_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.organization_id}"


class AppRole(models.Model):
    """The role a person holds in one app of the portal ("records" today; "cases" later)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="app_roles"
    )
    app = models.CharField(max_length=50)
    role = models.CharField(max_length=50)
    # RETIRED by P20h (DECISIONS 42d, 43): the Director mark and the "files without review" mark.
    # The role decides filing and the doors the Director mark guarded are an administrator's.
    # The columns stay, read by nothing, because dropping them would destroy who held them.
    is_director = models.BooleanField(default=False)
    can_file_without_review = models.BooleanField(default=False)
    granted_at = models.DateTimeField(default=tz_now, editable=False)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "app_roles"
        constraints = [models.UniqueConstraint(fields=["user", "app"], name="app_role_unique")]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.app}={self.role}"


class Activity(models.Model):
    """Who did what to what (P15b). Append-only, written inside the service transaction that did
    the thing, by a person or by a named process. `audit_log` cannot hold this: it is shaped as
    a field change and refuses a system author."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    at = models.DateTimeField(default=tz_now, editable=False, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    system = models.CharField(max_length=80, blank=True, default="")
    action = models.CharField(max_length=60, db_index=True)
    subject_type = models.CharField(max_length=40, blank=True, default="")
    subject_id = models.UUIDField(null=True, blank=True)
    request_id = models.UUIDField(null=True, blank=True, db_index=True)
    summary = models.TextField()
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "activity"

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.action}: {self.summary[:60]}"


class Notification(models.Model):
    """One thing a person is told (P15b): a mention, something assigned to them, a bounce, a
    deadline coming up, something waiting for their sign-off. Read when they open it; emailed
    when their preferences say so."""

    MENTION, ASSIGNED, BOUNCE, DEADLINE, REVIEW, FLAGGED, ARRIVED = (
        "mention",
        "assigned",
        "bounce",
        "deadline",
        "review",
        "flagged",
        "arrived",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=20)
    text = models.TextField()
    subject_type = models.CharField(max_length=40, blank=True, default="")
    subject_id = models.UUIDField(null=True, blank=True)
    request_id = models.UUIDField(null=True, blank=True)
    # Where opening it goes, as a path inside the portal.
    link = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(default=tz_now, editable=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        indexes = [models.Index(fields=["user", "read_at", "created_at"])]

    def __str__(self) -> str:
        return f"{self.kind} for {self.user_id}: {self.text[:60]}"


class Setting(models.Model):
    """Operational values that are data, not code: review windows, follow-up cadence, org name."""

    key = models.CharField(primary_key=True, max_length=100)
    value = models.JSONField()
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings"

    def __str__(self) -> str:
        return self.key


class Review(models.Model):
    """One ask for approval, or one question to a teammate (P13, reworked by P20h). Append-only:
    a resubmission is a new row, and the newest row for a subject is what stands.

    `subject_type` names what is reviewed: an intern's message before it is sent (`message`), an
    intern's letter to one agency before it is filed by hand (`recipient`), an inbound message
    a teammate is asked about (`source`), or a request a teammate is asked about (`request`, P27d,
    069's "Ask a teammate"); `draft` is P13's director review, kept for its rows.
    An `approval` is decided by an approver (approved | sent_back) or withdrawn by the intern who
    submitted it (withdrawn); a `question` is answered by the person it was put to (answered).
    The subjects and decisions are free text checked in `core/services/review.py`.
    """

    DRAFT, MESSAGE, SOURCE, RECIPIENT, REQUEST = (
        "draft",
        "message",
        "source",
        "recipient",
        "request",
    )
    APPROVAL, QUESTION = "approval", "question"
    APPROVED, SENT_BACK, ANSWERED, WITHDRAWN = "approved", "sent_back", "answered", "withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject_type = models.CharField(max_length=20)
    subject_id = models.UUIDField()
    # The request the subject belongs to, when it has one (a message or an attached source).
    request_id = models.UUIDField(null=True, blank=True)
    kind = models.CharField(max_length=20, default=APPROVAL)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField(default=tz_now, editable=False)
    note = models.TextField(blank=True, default="")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decision = models.CharField(max_length=20, null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True, default="")

    class Meta:
        db_table = "reviews"
        indexes = [models.Index(fields=["subject_type", "subject_id", "submitted_at"])]

    def __str__(self) -> str:
        return f"{self.kind} of {self.subject_type} {self.subject_id} ({self.decision or 'open'})"


class Suggestion(models.Model):
    """A requester's idea for a request (P13). No agency certainty and no tracking number, so it
    is not a `requests` row; when staff take it up it becomes a letter draft."""

    SUGGESTED, STARTED, DECLINED = "suggested", "started", "declined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    what = models.TextField()
    agency_hint = models.TextField(blank=True, default="")
    why = models.TextField(blank=True, default="")
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="suggestions"
    )
    created_at = models.DateTimeField(default=tz_now, editable=False)
    status = models.CharField(max_length=20, default=SUGGESTED)
    decline_note = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    draft_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "suggestions"

    def __str__(self) -> str:
        return f"{self.what[:40]} ({self.status})"


class Invite(models.Model):
    """An invitation to join (P15a; P25a). The account exists from the moment of the invite,
    inactive, so the person's role and name are decided by the administrator, not the link. The
    link itself is Keycloak's (its "execute actions" email: choose a password, add a passkey);
    `expires_at` is when that link stops working. The invite is accepted at the person's first
    sign-in, which is also when their account is switched on."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invites"
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=tz_now, editable=False)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "invites"

    def __str__(self) -> str:
        return f"invite for {self.user_id}"


class SignInSession(models.Model):
    """One portal session and the Keycloak session it came from (P25a). Written at sign-in, so
    Keycloak's back-channel logout (which names its session, `sid`, or the person, `sub`) can end
    exactly the portal sessions it means, and "sign out everywhere" can end them all. The
    session's tokens are in the session itself; this row holds no secret."""

    session_key = models.CharField(max_length=40, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sign_in_sessions"
    )
    keycloak_sid = models.CharField(max_length=64, db_index=True)
    started_at = models.DateTimeField(default=tz_now)

    class Meta:
        db_table = "sign_in_sessions"

    def __str__(self) -> str:
        return f"session of {self.user_id} ({self.keycloak_sid})"


class KeycloakChange(models.Model):
    """One change the portal owes Keycloak (P25a), written in the same transaction as the change
    in the portal, so a Keycloak that is down or refuses never loses it: a job applies the
    person's pending changes in order (`portal.core.keycloak.sync`), retried with growing waits,
    and an hourly sweep takes up anything still pending or failed. Users shows a person's
    pending or failed change. Never deleted: the rows are the record of what was sent."""

    SYNC = "sync"  # make Keycloak's copy of the person match the portal's (exists, on, details)
    INVITE = "invite"  # Keycloak's email: choose a password, add a passkey, save recovery codes
    PASSWORD_RESET = "password_reset"  # Keycloak's email: choose a new password
    END_SESSIONS = "end_sessions"  # end every Keycloak session the person has, in every app
    RESET_SECOND_STEP = "reset_second_step"  # remove passkeys, app codes and recovery codes
    POLICY = "policy"  # the sign-in rules in Settings, written to the realm (no person)
    ACTIONS = (
        (SYNC, "Update sign-in account"),
        (INVITE, "Send invitation"),
        (PASSWORD_RESET, "Send password reset"),
        (END_SESSIONS, "Sign out everywhere"),
        (RESET_SECOND_STEP, "Reset two-step sign-in"),
        (POLICY, "Apply sign-in rules"),
    )
    PENDING, DONE, FAILED = "pending", "done", "failed"
    STATES = ((PENDING, "Pending"), (DONE, "Done"), (FAILED, "Failed"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="keycloak_changes",
    )
    action = models.CharField(max_length=32, choices=ACTIONS)
    state = models.CharField(max_length=16, choices=STATES, default=PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=tz_now, db_index=True)
    updated_at = models.DateTimeField(default=tz_now)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "keycloak_changes"
        indexes = [models.Index(fields=["user", "state", "created_at"])]

    def __str__(self) -> str:
        return f"{self.action} for {self.user_id or 'the realm'} ({self.state})"


# ---- P18b: the rules and calendars, as tables an administrator edits ----------------------------


class DeadlineRule(models.Model):
    """One effective-dated statutory clock (the shape of `deadline_rules.model.Rule`). The count
    and the citation are a person's: entered on the screen under citation, never by the portal.
    `rule_id` is what `records.deadlines.rule_ref` names, so a rule is retired by an end date and
    never deleted."""

    rule_id = models.CharField(primary_key=True, max_length=200)
    law = models.CharField(max_length=100)
    provision = models.CharField(max_length=200, blank=True, default="")
    deadline_type = models.CharField(max_length=40)
    count = models.PositiveIntegerField()
    unit = models.CharField(max_length=20)
    roll_forward_on_non_business_day = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    citation = models.TextField()
    # The order the rules are listed in: the file's order for the seeded ones (the order owner
    # wrote them in), then each added rule after the last.
    position = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "deadline_rules"
        ordering = ["position", "rule_id"]

    def __str__(self) -> str:
        return self.rule_id


class CalendarDefinition(models.Model):
    """A named jurisdiction calendar: its time zone and which weekdays are the weekend
    (0 is Monday)."""

    key = models.CharField(primary_key=True, max_length=100)
    timezone = models.CharField(max_length=64)
    weekend = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "calendars"
        ordering = ["key"]

    def __str__(self) -> str:
        return self.key


class CalendarHoliday(models.Model):
    calendar = models.ForeignKey(
        CalendarDefinition, on_delete=models.CASCADE, related_name="holidays"
    )
    date = models.DateField()
    name = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "calendar_holidays"
        unique_together = [("calendar", "date")]
        ordering = ["date"]

    def __str__(self) -> str:
        return f"{self.calendar_id} {self.date}"


class LawCalendar(models.Model):
    """Which calendar a records law runs on (`agencies.records_law` → calendar key)."""

    law = models.CharField(primary_key=True, max_length=100)
    calendar = models.ForeignKey(CalendarDefinition, on_delete=models.PROTECT, related_name="laws")

    class Meta:
        db_table = "law_calendars"
        ordering = ["law"]

    def __str__(self) -> str:
        return f"{self.law} → {self.calendar_id}"


class Presence(models.Model):
    """Who is signed in and what they have open (P19a, design 1g/1i "who's online"): one row per
    person, rewritten by a heartbeat while a tab is open. Not a log; the activity log is."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="presence",
    )
    path = models.CharField(max_length=300, blank=True, default="")
    label = models.CharField(max_length=200, blank=True, default="")
    seen_at = models.DateTimeField(default=tz_now)

    class Meta:
        db_table = "presence"

    def __str__(self) -> str:
        return f"{self.user_id} at {self.path}"


class Signature(models.Model):
    """A person's own handwritten signature (P20f, Settings → Profile → Your signature): drawn on
    the pad, which sends a PNG, or uploaded as a PNG or JPEG. One row per person; replacing it
    stores a new file and points the row at it. The organisation's letterhead never carries a
    signature. Not used in letters or mail yet (a later part)."""

    DRAWN, UPLOADED = "drawn", "uploaded"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="signature",
    )
    # Where the bytes live in `default_storage`; a path the service computed, never a name a
    # person chose (see core.services.signatures).
    storage_name = models.TextField()
    content_type = models.TextField()
    source = models.TextField()
    # The uploaded file's name through `safe_filename`; `signature.png` for a drawn one.
    file_name = models.TextField()
    # From the image's own header; null when it could not be read.
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    size = models.PositiveIntegerField()
    sha256 = models.TextField()
    updated_at = models.DateTimeField(default=tz_now)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "signatures"

    def __str__(self) -> str:
        return f"signature of {self.user_id} ({self.source})"


# ---- P19d: the assistant ----------------------------------------------------------------------


class AiRun(models.Model):
    """One run of the assistant: which task, which tier and provider, for what, by whom, what
    was sent (names and sizes, the text only if a setting says so), what came back. The
    sent-data log (DECISIONS 27)."""

    GENERAL = "general"
    DRAFTING = "drafting"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.CharField(max_length=40)
    tier = models.CharField(max_length=20)
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=120, blank=True, default="")
    subject_type = models.CharField(max_length=40, blank=True, default="")
    subject_id = models.UUIDField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=20, default="running")
    context_summary = models.JSONField(default=dict, blank=True)
    prompt = models.TextField(blank=True, default="")
    output = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    tokens_in = models.IntegerField(null=True, blank=True)
    tokens_out = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=tz_now, editable=False, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ai_runs"

    def __str__(self) -> str:
        return f"{self.task} ({self.provider}) {self.status}"


class AiSuggestion(models.Model):
    """A card the assistant put forward about a draft. It changes nothing until a person applies
    it; applying and dismissing are the person's acts."""

    OPEN, APPLIED, DISMISSED = "open", "applied", "dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AiRun, on_delete=models.CASCADE, related_name="suggestions")
    draft_id = models.UUIDField(db_index=True)
    item_id = models.UUIDField(null=True, blank=True)
    kind = models.CharField(max_length=30)
    title = models.CharField(max_length=200)
    text = models.TextField()
    replacement_text = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, default=OPEN)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=tz_now, editable=False)

    class Meta:
        db_table = "ai_suggestions"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.kind}: {self.title}"


class DraftNote(models.Model):
    """A note on a draft, for the team, never sent (the rail's Notes tab)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft_id = models.UUIDField(db_index=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    body = models.TextField()
    created_at = models.DateTimeField(default=tz_now, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "draft_notes"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"note on {self.draft_id}"


# ---- P20i: the agreements signed at first sign-in ---------------------------------------------


class Agreement(models.Model):
    """One published version of an agreement a person signs in the new-user setup (P20i; v6
    010–011 and ARCHITECTURE "Agreements at first sign-in"; DECISIONS 33b, 37e). A version is
    never edited: changing the text publishes the next version, and everyone it applies to is
    shown it again at their next sign-in. The text is an administrator's (Settings →
    Organisation → Agreements); version 1 of each kind is the design's placeholder wording,
    marked `is_placeholder`."""

    CONFIDENTIALITY, INTERN = "confidentiality", "intern"
    KINDS = (CONFIDENTIALITY, INTERN)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    # One clause per paragraph, a blank line between; a clause may open with its lead in `**`
    # (see core.services.agreements.clauses).
    body = models.TextField()
    is_placeholder = models.BooleanField(default=False)
    published_at = models.DateTimeField(default=tz_now)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "agreements"
        constraints = [
            models.UniqueConstraint(fields=["kind", "version"], name="agreement_version_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} v{self.version}"


class AgreementAcceptance(models.Model):
    """A person's acceptance of one version of an agreement (P20i): who, which version, when, the
    signature image they signed with (its stored file, which is never deleted when the signature
    is replaced, and its hash), the address and browser it came from, and the PDF of exactly what
    was signed. Append-only: nothing edits or deletes a row."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agreement = models.ForeignKey(Agreement, on_delete=models.PROTECT, related_name="acceptances")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="agreement_acceptances"
    )
    accepted_at = models.DateTimeField(default=tz_now)
    # The name and title as signed, so a later rename does not rewrite what was signed.
    signed_name = models.CharField(max_length=200)
    signed_title = models.CharField(max_length=200, blank=True, default="")
    signature_storage_name = models.TextField()
    signature_content_type = models.TextField()
    signature_sha256 = models.TextField()
    # The nearest client address, as sign-in reads it, and the browser's own description.
    ip_address = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    pdf_storage_name = models.TextField()
    pdf_sha256 = models.TextField()

    class Meta:
        db_table = "agreement_acceptances"
        constraints = [
            models.UniqueConstraint(
                fields=["agreement", "user"], name="agreement_acceptance_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} accepted {self.agreement_id}"


# ---- P22: team chat ---------------------------------------------------------------------------
#
# Part of the portal, not of one tool (DECISIONS 45). Every row here is kept forever: nothing in
# the portal deletes one (core.0029's triggers refuse a DELETE on every chat table and an UPDATE of
# a message version), a deleted message keeps every version of its text in `ChatMessageVersion`,
# and only the legal director's discovery route (`portal/core/chat/discovery.py`) reads a
# conversation they are not in, or a version, or a deleted message's words.


class ChatChannel(models.Model):
    """A conversation: a named channel (public or private) or a direct conversation between fixed
    people (`kind = dm`). Never deleted; an archived channel is read-only."""

    CHANNEL, DM = "channel", "dm"
    KINDS = (CHANNEL, DM)
    # Plain (the server can read every message) is the only kind this part writes. End-to-end
    # channels come later (DECISIONS 37h, 42d) and nothing here may set `e2ee`.
    NONE, E2EE = "none", "e2ee"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, default=CHANNEL)
    # A channel's name, lower case ("filing"); empty for a direct conversation.
    name = models.CharField(max_length=80, blank=True, default="")
    # Members only. A direct conversation is always private.
    is_private = models.BooleanField(default=False)
    encryption = models.CharField(max_length=10, default=NONE)
    # A direct conversation's people, their ids sorted and joined, so the same people always find
    # the same conversation. Empty for a channel.
    dm_key = models.CharField(max_length=800, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(default=tz_now, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "chat_channels"
        constraints = [
            # P23a (QUESTIONS 30): unique among PUBLIC channels only, so trying a name can never
            # tell anyone that a private channel has it. Private channels may share names with
            # each other (`chat.channels.create_channel` still refuses a private channel the name
            # of a public one, which every person can see anyway).
            models.UniqueConstraint(
                models.functions.Lower("name"),
                condition=models.Q(kind="channel", is_private=False),
                name="chat_public_channel_name_unique",
            ),
            models.UniqueConstraint(
                fields=["dm_key"], condition=models.Q(kind="dm"), name="chat_dm_key_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"#{self.name}" if self.kind == self.CHANNEL else f"dm {self.pk}"


class ChatMembership(models.Model):
    """One person's time in one conversation: a new row each time they join, `left_at` when they
    leave, so who could read what, and when, is never rewritten. `last_read_at` is how far they
    have read (the unread count)."""

    channel = models.ForeignKey(ChatChannel, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    joined_at = models.DateTimeField(default=tz_now)
    left_at = models.DateTimeField(null=True, blank=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        db_table = "chat_memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "user"],
                condition=models.Q(left_at__isnull=True),
                name="chat_membership_one_open",
            ),
        ]
        indexes = [models.Index(fields=["user", "left_at"])]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.channel_id}"


class ChatMessage(models.Model):
    """One message as it stands now. `body` is the current text and is emptied when the message
    is deleted (the words stay in its versions, for the legal director); `changed_at` is the last
    time anything about it changed, which the polling fallback reads."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(ChatChannel, on_delete=models.PROTECT, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=tz_now, editable=False)
    body = models.TextField(blank=True, default="")
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    changed_at = models.DateTimeField(default=tz_now, db_index=True)

    class Meta:
        db_table = "chat_messages"
        indexes = [models.Index(fields=["channel", "created_at"])]

    def __str__(self) -> str:
        return f"message {self.pk} in {self.channel_id}"


class ChatMessageVersion(models.Model):
    """Every text a message has had: as posted, each edit, and its deletion (who deleted it, with
    the text it had). Written once and never changed or removed (core.0029's trigger)."""

    POSTED, EDITED, DELETED = "posted", "edited", "deleted"

    message = models.ForeignKey(ChatMessage, on_delete=models.PROTECT, related_name="versions")
    kind = models.CharField(max_length=10)
    body = models.TextField(blank=True, default="")
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(default=tz_now)

    class Meta:
        db_table = "chat_message_versions"
        ordering = ["at", "pk"]

    def __str__(self) -> str:
        return f"{self.kind} {self.message_id} at {self.at:%Y-%m-%d %H:%M}"


class ChatAttachment(models.Model):
    """A file sent in a conversation. Uploaded first (`message` empty until the message that
    carries it is sent), stored where other files are (`records/storage.py`, under
    `chat/<channel>/`), at most the size `chat.attachment_max_bytes` allows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(ChatChannel, on_delete=models.PROTECT, related_name="+")
    message = models.ForeignKey(
        ChatMessage, null=True, blank=True, on_delete=models.PROTECT, related_name="attachments"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    # Through `safe_filename`, once, when the file came in.
    file_name = models.TextField()
    content_type = models.TextField(blank=True, default="")
    size = models.PositiveBigIntegerField()
    sha256 = models.TextField()
    storage_name = models.TextField()
    created_at = models.DateTimeField(default=tz_now, editable=False)

    class Meta:
        db_table = "chat_attachments"

    def __str__(self) -> str:
        return self.file_name


class ChatMention(models.Model):
    """A person @mentioned in a message (told by the bell and in their daily summary). Kept when
    the message is edited or deleted: it is a record of who was told."""

    message = models.ForeignKey(ChatMessage, on_delete=models.PROTECT, related_name="mentions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=tz_now, editable=False)

    class Meta:
        db_table = "chat_mentions"
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="chat_mention_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.message_id}"
