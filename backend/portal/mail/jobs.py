"""Background jobs of the mail app. Procrastinate finds this module by its name."""

import logging
from uuid import UUID

from procrastinate import RetryStrategy

from portal.jobs.app import app
from portal.mail.services import send as send_service
from portal.mail.services.transport import TransportError

logger = logging.getLogger(__name__)


@app.task(
    name="mail.deliver_message",
    queue="mail",
    retry=RetryStrategy(max_attempts=5, exponential_wait=10),
)
def deliver_message(message_id: str) -> None:
    """Hand one queued message to the provider. Safe to run twice: a message that is not queued
    is left alone. A transient provider failure is raised, so the job retries; a refusal is
    recorded on the message and the job ends."""
    try:
        done = send_service.deliver(UUID(message_id))
    except TransportError as exc:
        if exc.permanent:
            logger.error("message %s refused by the provider: %s", message_id, exc)
            return
        raise
    if done.outcome == "early":
        deliver_message.configure(schedule_at=done.message.send_after).defer(message_id=message_id)


@app.periodic(cron="*/15 * * * *")
@app.task(name="mail.requeue_stuck_messages", queue="periodic")
def requeue_stuck_messages(timestamp: int) -> None:
    """A queued message whose window closed long ago and that no job delivered (a worker died
    mid-run) is deferred again. Idempotent: delivery itself skips anything not queued."""
    for message_id in send_service.stuck():
        deliver_message.defer(message_id=str(message_id))
