"""Background jobs of the records app. Procrastinate finds this module by its name.

Inbound mail's job, and the four that run on a clock. The periodic ones are thin wrappers: what
they do, and why, is in `services/sweeps.py`. Each gets the scheduled moment from procrastinate as
`timestamp`, and each is safe to run twice.

Run them with `just worker` (all queues). Nothing here sends mail.
"""

import logging
from uuid import UUID

from procrastinate import JobContext, RetryStrategy

from portal.jobs.app import app
from portal.records.inbound import ingest
from portal.records.services import sweeps

logger = logging.getLogger(__name__)

INGEST_ATTEMPTS = 4


@app.task(
    name="records.ingest_postmark_source",
    queue="inbound",
    pass_context=True,
    retry=RetryStrategy(max_attempts=INGEST_ATTEMPTS, exponential_wait=5),
)
def ingest_postmark_source(context: JobContext, source_id: str) -> None:
    """Store one delivered message's files and read its text. Safe to run twice.

    The message is already on its request (the webhook placed it), so a failure here loses
    nothing but the extraction. It is retried; when the last attempt fails the message is flagged
    so a person knows its files are missing, and the job ends quietly rather than failing again.
    """
    try:
        ingest.ingest_source(UUID(source_id))
    except Exception:
        attempts_so_far = getattr(context.job, "attempts", 0) + 1
        if attempts_so_far < INGEST_ATTEMPTS:
            raise
        logger.exception("ingest of source %s failed for the last time; flagged", source_id)
        ingest.flag_failed(UUID(source_id))


@app.periodic(cron="0 * * * *")
@app.task(name="records.sweep_overdue_deadlines", queue="periodic")
def sweep_overdue_deadlines(timestamp: int) -> None:
    """Hourly: mark lapsed deadlines missed, on each agency's own date."""
    sweeps.sweep_overdue_deadlines()


@app.periodic(cron="15 3 * * *")
@app.task(name="records.production_review_timeout", queue="periodic")
def production_review_timeout(timestamp: int) -> None:
    """Nightly: a production nobody reviewed within the configured window is marked unreviewed."""
    sweeps.production_review_timeout()


@app.periodic(cron="30 3 * * *")
@app.task(name="records.send_auto_followups", queue="periodic")
def send_auto_followups(timestamp: int) -> None:
    """Nightly: open follow-up tasks where one is due. Opens tasks; sends nothing."""
    sweeps.send_auto_followups()


@app.periodic(cron="*/20 * * * *")
@app.task(name="records.requeue_stalled_mail", queue="periodic")
def requeue_stalled_mail(timestamp: int) -> None:
    """Every twenty minutes: defer again the extraction of mail the worker never got to."""
    for source_id in sweeps.stalled_mail():
        ingest_postmark_source.defer(source_id=str(source_id))
