"""P20f: Settings → System also says how outgoing mail and the jobs are doing: whether sending
is on, the last send, failed and waiting messages, bounces in the last twenty-four hours, and
failed job runs inside the mail window. Counts only."""

from datetime import timedelta

from django.db import connection
from django.test import Client
from django.utils import timezone
from portal.core.models import Setting
from portal.mail.services import compose, delivery, send


def _draft(actor, a_request, subject: str = "About our request"):
    return compose.create_draft(
        actor, a_request.pk, subject=subject, body_text="Hello.", to=["records@agency.example.org"]
    )


def _bounce(event_id: int, when) -> dict:
    return {
        "RecordType": "Bounce",
        "ID": event_id,
        "MessageID": "pm-0001",
        "Recipient": "records@agency.example.org",
        "Description": "The address does not exist.",
        "BouncedAt": when.isoformat(),
    }


def _failed_job(task_name: str, failed_at) -> None:
    """A job row as procrastinate leaves a failed one, and the event that dates the failure.
    Written by hand: procrastinate's Django models are read-only."""
    with connection.cursor() as cursor:
        cursor.execute(
            "insert into procrastinate_jobs (queue_name, task_name, args, status) "
            "values ('default', %s, '{}', 'failed') returning id",
            [task_name],
        )
        job_id = cursor.fetchone()[0]
        cursor.execute(
            "insert into procrastinate_events (job_id, type, at) values (%s, 'failed', %s)",
            [job_id, failed_at],
        )


def test_the_system_screen_counts_outgoing_mail_bounces_and_failed_jobs(
    actor, a_request, provider, media, admin_user, staff_user, settings
) -> None:
    from portal.jobs.app import app

    Setting.objects.update_or_create(
        key="core.system_mail_window_days", defaults={"value": 7, "description": "t"}
    )
    now = timezone.now()
    client = Client()
    client.force_login(admin_user)

    body = client.get("/api/core/system").json()
    assert body["outgoing"] == {
        "sending_enabled": False,
        "last_sent_at": None,
        "failed": 0,
        "queued": 0,
    }
    assert body["bounces"] == {"window_hours": 24, "count": 0, "last_at": None}
    assert body["failed_jobs"] == 0 and all(j["failed_recent"] == 0 for j in body["jobs"])

    # One sent, one the provider refused, one waiting in its undo window.
    sent = _draft(actor, a_request)
    send.queue(actor, sent.pk, delay_seconds=0)
    assert send.deliver(sent.pk).outcome == "sent"
    refused = _draft(actor, a_request, "Second")
    send.queue(actor, refused.pk, delay_seconds=0)
    provider.status, provider.body = 422, {"ErrorCode": 300, "Message": "Invalid 'To' address"}
    assert send.deliver(refused.pk).outcome == "failed"
    provider.status, provider.body = 200, {"ErrorCode": 0, "MessageID": "pm-0003"}
    send.queue(actor, _draft(actor, a_request, "Third").pk, delay_seconds=30)

    # Two bounces inside the last twenty-four hours, one before them; a delivery is no bounce.
    assert delivery.record(_bounce(1, now - timedelta(hours=1))) == "recorded"
    assert delivery.record(_bounce(2, now - timedelta(hours=5))) == "recorded"
    assert delivery.record(_bounce(3, now - timedelta(hours=30))) == "recorded"
    delivered = {
        "RecordType": "Delivery",
        "MessageID": "pm-0001",
        "Recipient": "records@agency.example.org",
        "DeliveredAt": now.isoformat(),
    }
    assert delivery.record(delivered) == "recorded"

    periodic = sorted(p.task.name for p in app.periodic_registry.periodic_tasks.values())[0]
    _failed_job(periodic, now - timedelta(hours=2))
    _failed_job(periodic, now - timedelta(hours=24 * 30))  # outside the window
    _failed_job("mail.deliver", now - timedelta(hours=3))  # not periodic; still a failed job

    body = client.get("/api/core/system").json()
    outgoing = body["outgoing"]
    assert outgoing["sending_enabled"] is False
    assert outgoing["last_sent_at"] is not None
    assert (outgoing["failed"], outgoing["queued"]) == (1, 1)
    assert body["bounces"]["count"] == 2 and body["bounces"]["last_at"] is not None
    jobs = {j["name"]: j for j in body["jobs"]}
    assert jobs[periodic]["failed_recent"] == 1
    assert sum(j["failed_recent"] for j in body["jobs"]) == 1
    assert body["failed_jobs"] == 2
    # Nothing about any message's request or subject is in the read.
    text = str(body)
    assert a_request.tracking_number not in text and "About our request" not in text
    # The window is the setting's: widened, the old failure counts too.
    Setting.objects.filter(key="core.system_mail_window_days").update(value=60)
    assert client.get("/api/core/system").json()["failed_jobs"] == 3

    settings.PORTAL = {**settings.PORTAL, "MAIL_SEND_ENABLED": True}
    assert client.get("/api/core/system").json()["outgoing"]["sending_enabled"] is True

    other = Client()
    other.force_login(staff_user)
    assert other.get("/api/core/system").status_code == 403
