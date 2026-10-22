"""Background jobs of the core app (P15b): deadline reminders once a day, and the daily summary
checked every hour, since each person gets it at the summary hour in their own time zone. P25a:
the changes the portal owes Keycloak, applied per person and swept every hour, and the sign-in
rules written to Keycloak every hour (so Settings wins over the realm file's defaults)."""

import logging

from procrastinate import RetryStrategy

from portal.core.services import notifications
from portal.jobs.app import app

logger = logging.getLogger(__name__)


@app.periodic(cron="0 6 * * *")
@app.task(name="core.deadline_reminders", queue="periodic")
def deadline_reminders(timestamp: int) -> None:
    told = notifications.deadline_reminders()
    logger.info("deadline reminders: %d sent", told)


@app.periodic(cron="5 * * * *")
@app.task(name="core.daily_summaries", queue="periodic")
def daily_summaries(timestamp: int) -> None:
    sent = notifications.send_daily_summaries()
    logger.info("daily summaries: %d sent", sent)


@app.task(
    name="core.keycloak_changes",
    queue="keycloak",
    retry=RetryStrategy(max_attempts=8, exponential_wait=3),
)
def apply_keycloak_changes(user_id: str = "") -> None:
    """P25a: apply one person's pending Keycloak changes (or the realm's, `user_id` empty), in
    order. Deferred with a lock per person, so their changes never run at once or out of order;
    a change Keycloak could not take raises for the retry (`sync.ChangeFailed`)."""
    from uuid import UUID

    from portal.core.keycloak import sync

    applied = sync.apply_pending(UUID(user_id) if user_id else None)
    if applied:
        logger.info("keycloak: %d change(s) applied for %s", applied, user_id or "the realm")


@app.periodic(cron="40 * * * *")
@app.task(name="core.keycloak_sweep", queue="periodic")
def keycloak_sweep(timestamp: int) -> None:
    """P25a: hourly, every failed or forgotten Keycloak change is tried again, and the sign-in
    rules are written again (the realm file, applied on Keycloak's start, carries the defaults)."""
    from portal.core.keycloak import sync
    from portal.core.models import KeycloakChange

    sync.enqueue(None, KeycloakChange.POLICY)
    taken = sync.sweep()
    logger.info("keycloak sweep: %d taken up", taken)


@app.task(name="ai.check_inbound", queue="inbound")
def check_inbound(source_id: str) -> None:
    """P19d: the assistant's constant check on mail that came in, on the general tier."""
    from uuid import UUID

    from portal.core.ai import tasks
    from portal.core.ai.executors import ExecutorError

    try:
        tasks.check_inbound(UUID(source_id))
    except ExecutorError as exc:  # off, no key, or the provider failed: the mail is unaffected
        logger.info("inbound check skipped for %s: %s", source_id, exc)
