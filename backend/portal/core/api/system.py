"""Settings -> System: is mail arriving, is mail going out, are the jobs running, how far does
the calendar reach.

A read over rows that already exist. There is no job-run table: a periodic job's last run is the
newest row procrastinate itself keeps for it, and its next run is computed from its schedule; a
job's failures are its `failed` rows, dated by procrastinate's own events table. Outgoing mail is
counted from the messages and the provider's delivery events (P20f), counts only: nothing here
names a request or carries a subject, since the request behind a message may be privileged.
Like the received count, the mail counts are of what the administrator may see: row-level
security (mail_db.0002) leaves out the messages of a privileged request they are not on.
P21b adds the tools registered to sign people in through the portal: names, callback addresses
and when, never a client id or secret. P21c adds the drafting tool's seam: the switch, its
addresses, whether each of its three secrets is set (never a value), the webhook address. Portal
administrators only.
"""

from datetime import UTC, date, datetime, timedelta

from croniter import croniter
from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from ninja import Router, Schema
from procrastinate.contrib.django.models import (
    ProcrastinateEvent,
    ProcrastinateJob,
    ProcrastinatePeriodicDefer,
)

from portal.core.api.users import require_admin
from portal.core.services.settings import get_setting
from portal.jobs.app import app
from portal.mail.models import DeliveryEvent, Message, MessageStatus
from portal.mail.services import transport
from portal.records.models import Source
from portal.records.services import registry
from portal.records.services.common import rule_data

router = Router(tags=["system"])

MAIL_SOURCE_TYPES = ("postmark_email", "imported_eml")
# What the delivery webhook stores as `record_type` for a bounce (mail.services.delivery).
BOUNCE_RECORD_TYPE = "Bounce"
# How far back the System screen counts bounces: the last twenty-four hours.
BOUNCE_WINDOW_HOURS = 24


class MailOut(Schema):
    window_days: int
    received: int
    last_received_at: datetime | None
    # Delivered messages whose files the worker has not extracted yet.
    awaiting_extraction: int


class OutgoingMailOut(Schema):
    # Whether messages really go out (`MAIL_SEND_ENABLED`), as GET /api/mail/sending says.
    sending_enabled: bool
    # The newest message the provider accepted.
    last_sent_at: datetime | None
    # Messages whose send failed, within the mail window (`window_days` above).
    failed: int
    # Messages waiting to go, or going, now.
    queued: int


class BouncesOut(Schema):
    window_hours: int
    # Bounces the provider reported within the window, and the newest one. Counts only.
    count: int
    last_at: datetime | None


class JobOut(Schema):
    name: str
    schedule: str
    last_run_at: datetime | None
    last_status: str | None
    next_run_at: datetime
    # Runs of this job that failed within the mail window. No error text: it may carry request
    # data.
    failed_recent: int


class SystemCalendarOut(Schema):
    key: str
    last_holiday: date | None


class SignInOut(Schema):
    """P25a: the sign-in, Keycloak's. Where it is (the realm's public address), where an
    administrator opens its own console, whether the server holds both client secrets (never the
    secrets), and the changes still owed to it (`KeycloakChange`: pending, failed)."""

    issuer: str
    admin_console_url: str
    client_secret_set: bool
    admin_secret_set: bool
    pending_changes: int
    failed_changes: int


class LastExportOut(Schema):
    """The newest registry export (P23b; 199 "Last registry export · Dana Nolan · CSV"): who,
    the format, when, how many requests and the range. No request is named."""

    who: str
    format: str
    at: datetime
    count: int | None
    range: str


class DraftingToolOut(Schema):
    """P21c: the drafting tool's seam, for Settings → System. Whether the switch is on and the
    tool is in use (the switch, both addresses and both secrets), each address in force and the
    server's default under it, and whether each secret is set: never a secret's value. The address
    the fork's webhooks are to be sent to, and when the last one arrived. (P25b: the tool signs in
    through Keycloak; the end-sessions secret is gone.)"""

    enabled: bool
    in_use: bool
    api_url: str
    api_url_default: str
    web_url: str
    web_url_default: str
    service_secret_set: bool
    webhook_secret_set: bool
    webhook_url: str
    last_webhook_at: datetime | None = None


class SystemOut(Schema):
    now: datetime
    # Where request addresses live. Read-only: changing it would orphan every address already
    # given to an agency.
    inbound_email_domain: str
    mail: MailOut
    jobs: list[JobOut]
    calendars: list[SystemCalendarOut]
    # P20f
    outgoing: OutgoingMailOut
    bounces: BouncesOut
    # Failed runs of any job within the mail window.
    failed_jobs: int
    # P21b
    sign_in: SignInOut
    # P23b
    last_export: LastExportOut | None = None
    # P21c
    drafting: DraftingToolOut | None = None


def _failed_jobs(since: datetime) -> dict[str, int]:
    """Jobs whose status is `failed` and whose failure falls on or after `since`, by task name.
    The job row carries no run time; procrastinate's events table dates the failure."""
    failed_ids = ProcrastinateEvent.objects.filter(type="failed", at__gte=since).values("job_id")
    rows = (
        ProcrastinateJob.objects.filter(status="failed", pk__in=failed_ids)
        .values_list("task_name", flat=True)
        .order_by()
    )
    counts: dict[str, int] = {}
    for name in rows:
        counts[name] = counts.get(name, 0) + 1
    return counts


def _outgoing(since: datetime) -> OutgoingMailOut:
    """Counts only, no subject or request, over the messages the caller may see."""
    return OutgoingMailOut(
        sending_enabled=transport.enabled(),
        last_sent_at=Message.objects.filter(sent_at__isnull=False)
        .order_by("-sent_at")
        .values_list("sent_at", flat=True)
        .first(),
        # A failed send is written with `updated_at` (mail.services.send), so that is when it
        # failed; a message created inside the window has a later `updated_at` too.
        failed=Message.objects.filter(status=MessageStatus.FAILED, updated_at__gte=since).count(),
        queued=Message.objects.filter(
            status__in=(MessageStatus.QUEUED, MessageStatus.SENDING)
        ).count(),
    )


def _bounces(now: datetime) -> BouncesOut:
    rows = DeliveryEvent.objects.filter(
        record_type=BOUNCE_RECORD_TYPE,
        occurred_at__gte=now - timedelta(hours=BOUNCE_WINDOW_HOURS),
    )
    return BouncesOut(
        window_hours=BOUNCE_WINDOW_HOURS,
        count=rows.count(),
        last_at=rows.order_by("-occurred_at").values_list("occurred_at", flat=True).first(),
    )


@router.get("", response=SystemOut)
def get_system(request: HttpRequest) -> SystemOut:
    require_admin(request)
    now = timezone.now()
    window = int(get_setting("core.system_mail_window_days"))
    since = now - timedelta(days=window)
    mail = Source.objects.filter(source_type__in=MAIL_SOURCE_TYPES)
    newest = mail.order_by("-captured_at").values_list("captured_at", flat=True).first()
    failed = _failed_jobs(since)
    jobs = []
    for periodic in sorted(
        app.periodic_registry.periodic_tasks.values(), key=lambda p: p.task.name
    ):
        name = periodic.task.name
        last = (
            ProcrastinatePeriodicDefer.objects.filter(task_name=name)
            .exclude(defer_timestamp__isnull=True)
            .order_by("-defer_timestamp")
            .first()
        )
        last_job = ProcrastinateJob.objects.filter(pk=last.job_id).first() if last else None
        jobs.append(
            JobOut(
                name=name,
                schedule=periodic.cron,
                last_run_at=datetime.fromtimestamp(last.defer_timestamp, UTC) if last else None,
                last_status=last_job.status if last_job else None,
                next_run_at=croniter(periodic.cron, now).get_next(datetime),
                failed_recent=failed.get(name, 0),
            )
        )
    return SystemOut(
        now=now,
        inbound_email_domain=str(settings.PORTAL["INBOUND_EMAIL_DOMAIN"]),
        mail=MailOut(
            window_days=window,
            received=mail.filter(captured_at__gte=since).count(),
            last_received_at=newest,
            awaiting_extraction=mail.filter(
                source_type="postmark_email", ingested_at__isnull=True
            ).count(),
        ),
        jobs=jobs,
        calendars=[
            SystemCalendarOut(key=key, last_holiday=max(calendar.holidays, default=None))
            for key, calendar in sorted(rule_data().calendars.items())
        ],
        outgoing=_outgoing(since),
        bounces=_bounces(now),
        failed_jobs=sum(failed.values()),
        sign_in=_sign_in(),
        last_export=(lambda found: LastExportOut(**found) if found else None)(
            registry.last_export()
        ),
        drafting=_drafting(),
    )


def _sign_in() -> SignInOut:
    from portal.core.models import KeycloakChange

    conf = settings.KEYCLOAK
    owed = KeycloakChange.objects.exclude(state=KeycloakChange.DONE)
    return SignInOut(
        issuer=conf["ISSUER"],
        admin_console_url=conf["ADMIN_CONSOLE_URL"],
        client_secret_set=bool(conf["CLIENT_SECRET"]),
        admin_secret_set=bool(conf["ADMIN_CLIENT_SECRET"]),
        pending_changes=owed.filter(state=KeycloakChange.PENDING).count(),
        failed_changes=owed.filter(state=KeycloakChange.FAILED).count(),
    )


def _drafting() -> DraftingToolOut:
    from portal.drafting.models import ForkEvent
    from portal.drafting.services import fork

    cfg = fork.config()
    secrets = cfg.secrets_set
    return DraftingToolOut(
        enabled=cfg.enabled,
        in_use=cfg.in_use,
        api_url=cfg.api_url,
        api_url_default=cfg.api_url_default,
        web_url=cfg.web_url,
        web_url_default=cfg.web_url_default,
        service_secret_set=secrets["service"],
        webhook_secret_set=secrets["webhook"],
        webhook_url=f"{settings.PORTAL['PUBLIC_URL']}/api/drafting/fork/webhook",
        last_webhook_at=ForkEvent.objects.order_by("-received_at")
        .values_list("received_at", flat=True)
        .first(),
    )
