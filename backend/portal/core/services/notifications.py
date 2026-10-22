"""Notifications (P15b): what a person is told, where, and whether it is also emailed.

A notification is a row for one person. The bell shows the unread ones; the person's
preferences say which kinds are also emailed as they happen, and whether they get the daily
summary. Mentions are one kind: `@name` in a note, matched against active people's names.
Nothing here decides anything about a request.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from portal.core.models import Notification, User
from portal.core.services import emails
from portal.core.services.settings import get_setting

logger = logging.getLogger(__name__)

KINDS = (
    Notification.MENTION,
    Notification.ASSIGNED,
    Notification.BOUNCE,
    Notification.DEADLINE,
    Notification.REVIEW,
    Notification.FLAGGED,
    Notification.ARRIVED,
)
# Email as it happens? Mentions, assignments, bounces and sign-off asks do; the noisy two do not.
DEFAULT_PREFERENCES: dict[str, Any] = {
    "email": {
        Notification.MENTION: True,
        Notification.ASSIGNED: True,
        Notification.BOUNCE: True,
        Notification.DEADLINE: True,
        Notification.REVIEW: True,
        Notification.FLAGGED: False,
        Notification.ARRIVED: False,
    },
    "daily_summary": True,
}
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z][\w.'-]*(?:\s[A-Z][\w'-]*)?)")


def preferences(user: User) -> dict[str, Any]:
    stored = user.notification_preferences or {}
    email = {**DEFAULT_PREFERENCES["email"], **(stored.get("email") or {})}
    return {
        "email": {k: bool(email.get(k)) for k in KINDS},
        "daily_summary": bool(stored.get("daily_summary", DEFAULT_PREFERENCES["daily_summary"])),
    }


def set_preferences(
    user: User, *, email: dict[str, bool] | None, daily_summary: bool | None
) -> dict[str, Any]:
    current = preferences(user)
    if email is not None:
        for key in email:
            if key not in KINDS:
                raise ValueError(f"Unknown notification kind {key!r}.")
        current["email"] = {**current["email"], **{k: bool(v) for k, v in email.items()}}
    if daily_summary is not None:
        current["daily_summary"] = bool(daily_summary)
    user.notification_preferences = current
    user.save(update_fields=["notification_preferences"])
    return current


def _email(user: User, kind: str, text: str, link: str, mail: "Mail | None" = None) -> None:
    from portal.mail.services.system_mail import send_system_email, site_url

    try:
        if mail is not None:
            send_system_email(user.email, mail.subject, mail.text, mail.html)
            return
        send_system_email(
            user.email,
            text[:120],
            f"{text}\n\nOpen it: {site_url(link)}\n\n"
            "Change what you get in Settings → Notifications.",
        )
    except Exception:  # noqa: BLE001 -- the row exists; mail is extra
        logger.exception("could not email notification to %s", user.email)


@dataclass(frozen=True)
class Mail:
    """A notification's own email, when its kind has a drawn one (P22: the mention, 228)."""

    subject: str
    text: str
    html: str | None = None


@transaction.atomic
def notify(
    user_id: UUID,
    kind: str,
    text: str,
    *,
    link: str = "",
    subject_type: str = "",
    subject_id: UUID | None = None,
    request_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    email: bool = True,
    mail: Mail | None = None,
) -> Notification | None:
    """Tell one person. Nobody is told about their own act; an inactive person is told nothing.
    One unread notification per (person, kind, subject): a second is not written.

    The row is always written. It is also emailed when the person's preference for `kind` says
    so AND the caller allows it: `email=False` keeps it to the bell (an assignment made without
    "Tell them by email" ticked). `email=True` never overrides a preference that is off."""
    if kind not in KINDS:
        raise ValueError(f"Unknown notification kind {kind!r}.")
    if actor_user_id is not None and actor_user_id == user_id:
        return None
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None
    if subject_id is not None:
        duplicate = Notification.objects.filter(
            user=user, kind=kind, subject_type=subject_type, subject_id=subject_id, read_at=None
        ).exists()
        if duplicate:
            return None
    row = Notification.objects.create(
        user=user,
        kind=kind,
        text=text,
        link=link,
        subject_type=subject_type,
        subject_id=subject_id,
        request_id=request_id,
    )
    if email and preferences(user)["email"].get(kind):
        _email(user, kind, text, link, mail)
        Notification.objects.filter(pk=row.pk).update(emailed_at=timezone.now())
    return row


def notify_many(user_ids, kind: str, text: str, **extra) -> int:
    return sum(1 for uid in set(user_ids) if notify(uid, kind, text, **extra) is not None)


def staff_who_want(kind: str) -> list[UUID]:
    """Admins, staff and staff attorneys whose preference for `kind` is on (the two opt-in kinds,
    both about the Inbox's triage). Not a limited-scope attorney or an intern: unplaced mail is not
    theirs to read (P20h, 37a), and a flag's note could name what they may not see."""
    from portal.records import roles

    out = []
    for user in User.objects.filter(is_active=True).prefetch_related("app_roles"):
        held = {r.role for r in user.app_roles.all() if r.app == "records"}
        if held & set(roles.FULL_STAFF) and preferences(user)["email"].get(kind):
            out.append(user.pk)
    return out


def mentioned(text: str, *, exclude: UUID | None = None) -> list[User]:
    """The active people `@name`d in a note: a first name, or a full name, case-insensitively.
    A name several people share matches them all; that is what was typed."""
    found = {m.group(1).strip().lower() for m in _MENTION.finditer(text or "")}
    if not found:
        return []
    out: list[User] = []
    for user in User.objects.filter(is_active=True).order_by("name"):
        if exclude is not None and user.pk == exclude:
            continue
        full = user.name.strip().lower()
        first = full.split(" ")[0] if full else ""
        if full in found or (first and first in found):
            out.append(user)
    return out


def unread(user: User) -> list[Notification]:
    return list(Notification.objects.filter(user=user, read_at=None).order_by("-created_at"))


def recent(user: User, *, limit: int = 50) -> list[Notification]:
    return list(Notification.objects.filter(user=user).order_by("-created_at")[:limit])


def mark_read(user: User, ids: list[UUID] | None = None) -> int:
    rows = Notification.objects.filter(user=user, read_at=None)
    if ids is not None:
        rows = rows.filter(pk__in=ids)
    return rows.update(read_at=timezone.now())


# ---- the clock: deadline reminders and the daily summary --------------------------------------


def deadline_reminders(now: datetime | None = None) -> int:
    """Tell the person a request is assigned to about each active deadline that is within the
    warning window. Once per deadline per person: the unread-duplicate rule keeps it so."""
    from portal.records.models import Deadline

    now = now or timezone.now()
    ahead = int(get_setting("notifications.deadline_warning_days") or 0)
    horizon = (now + timedelta(days=ahead)).date()
    told = 0
    rows = (
        Deadline.objects.filter(status="active", due_at__lte=horizon)
        .select_related("request")
        .exclude(request__assigned_to__isnull=True)
    )
    for deadline in rows:
        request = deadline.request
        when = "today" if deadline.due_at <= now.date() else f"on {deadline.due_at:%b %-d}"
        if notify(
            request.assigned_to_id,
            Notification.DEADLINE,
            f"{deadline.deadline_type} due for {request.tracking_number} {when}",
            link=f"/requests/{request.pk}",
            subject_type="deadline",
            subject_id=deadline.pk,
            request_id=request.pk,
        ):
            told += 1
    return told


def _local(now: datetime, user: User) -> datetime:
    """`now` on the person's own clock; UTC when their zone is not one we know."""
    try:
        return now.astimezone(ZoneInfo(user.timezone or "UTC"))
    except Exception:  # noqa: BLE001 -- a bad zone string means UTC
        return now


def summary_data(user: User, *, now: datetime | None = None) -> emails.DailySummary | None:
    """The day's picture for one person, as the Home screen would show it, split by the person's
    own date: overdue, due today (deadlines and their tasks), the rest of the due-soon window, and
    their other open tasks. None for a requester, and when there is nothing to say."""
    from portal.core.services.identity import records_role_of
    from portal.records import roles
    from portal.records.services import overview
    from portal.records.services.access import Viewer

    now = now or timezone.now()
    role = records_role_of(user)
    if role not in roles.TEAM:
        return None
    home = overview.home(Viewer(user_id=user.pk, role=role), now=now)
    local = _local(now, user)
    today = local.date()

    def line(row: overview.DeadlineRow, *, late: bool = False) -> emails.DeadlineLine:
        behind = (today - row.deadline.due_at).days
        return emails.DeadlineLine(
            agency=row.request.agency.name,
            kind=row.kind_label,
            tracking_number=row.request.tracking_number,
            due=row.deadline.due_at,
            # Home calls it overdue on the agency's calendar, so it is at least one behind on
            # the reader's too, whatever their zone.
            late_by=max(behind, 1) if late else 0,
        )

    def task_line(item: overview.HomeTask) -> emails.TaskLine:
        return emails.TaskLine(text=item.task.description, agency=item.request.agency.name)

    def due_today(item: overview.HomeTask) -> bool:
        due = item.task.due_at
        return due is not None and due.astimezone(local.tzinfo).date() == today

    summary = emails.DailySummary(
        first_name=user.name.split(" ")[0],
        subject=f"Your day · {local:%A, %B %-d}",
        overdue=[line(r, late=True) for r in home.overdue],
        due_today=[line(r) for r in home.due_soon if r.deadline.due_at <= today],
        due_soon=[line(r) for r in home.due_soon if r.deadline.due_at > today],
        tasks_today=[task_line(t) for t in home.assigned_tasks if due_today(t)],
        other_tasks=[task_line(t) for t in home.assigned_tasks if not due_today(t)],
        flagged=home.flagged.total,
        access_asks=_open_asks() if role == roles.ADMIN else (),
        mentions=_unread_mentions(user, local),
    )
    return None if summary.is_empty else summary


def _unread_mentions(user: User, local: datetime) -> list[tuple[str, str]]:
    """The mentions the person has not opened, newest first (P22; 228's mention block): what the
    bell says ("Seed Staff mentioned you in #filing", never the message's words) and when, as a
    time on their own clock when it was today and a date otherwise."""
    from portal.core.services.emails import MAX_ROWS

    rows = Notification.objects.filter(user=user, kind=Notification.MENTION, read_at=None).order_by(
        "-created_at"
    )[:MAX_ROWS]
    out = []
    for row in rows:
        at = row.created_at.astimezone(local.tzinfo)
        when = f"{at:%-I:%M %p}" if at.date() == local.date() else f"{at:%b} {at.day}"
        out.append((row.text, when))
    return out


def _open_asks() -> list[tuple[str, str]]:
    """Every undecided ask for access, oldest first, as (who asked, tracking number): what an
    administrator decides (P20h; 42e). The summary job runs under the system context, so the
    tracking number is readable; an administrator sees every request anyway."""
    from portal.records.models import AccessRequest

    rows = (
        AccessRequest.objects.filter(decided_at__isnull=True)
        .select_related("request", "user")
        .order_by("created_at")
    )
    return [(row.user.name, row.request.tracking_number) for row in rows]


def admin_ids() -> list[UUID]:
    """Every active administrator (`is_staff`, kept in step with the records role `admin`)."""
    return list(User.objects.filter(is_active=True, is_staff=True).values_list("pk", flat=True))


def summary_text(user: User, *, now: datetime | None = None) -> str | None:
    """The summary's plain text, or None when there is nothing to say."""
    summary = summary_data(user, now=now)
    return None if summary is None else emails.daily_summary_text(summary, emails.brand())


def send_daily_summaries(now: datetime | None = None) -> int:
    """Run every hour: each person whose local hour is the summary hour, who wants it, and who
    has not had today's, gets it. Nothing is sent twice: the last send is kept in the person's
    preferences."""
    from portal.mail.services.system_mail import send_system_email

    now = now or timezone.now()
    hour = int(get_setting("notifications.daily_summary_hour") or 7)
    sent = 0
    brand: emails.Brand | None = None
    for user in User.objects.filter(is_active=True):
        prefs = preferences(user)
        if not prefs["daily_summary"]:
            continue
        local = _local(now, user)
        if local.hour != hour:
            continue
        stored = user.notification_preferences or {}
        if stored.get("summary_sent_on") == local.date().isoformat():
            continue
        summary = summary_data(user, now=now)
        stored["summary_sent_on"] = local.date().isoformat()
        user.notification_preferences = {**stored}
        user.save(update_fields=["notification_preferences"])
        if summary is None:
            continue
        brand = brand or emails.brand()
        text, html = emails.daily_summary(summary, brand)
        try:
            send_system_email(user.email, summary.subject, text, html)
            sent += 1
        except Exception:  # noqa: BLE001
            logger.exception("daily summary to %s failed", user.email)
    return sent
