"""Background jobs of the drafting app (P21c). Procrastinate finds this module by its name.

It talks to the drafting tool and is safe to run again: a wave's access list is replaced whole
(SCLU.md 15.4). A transient failure (the fork down, a 5xx, a timeout) is raised for the retry;
anything a retry would not fix is logged by the service and the job ends. (P25b: the job that ended
a person's sessions in the fork is gone; Keycloak's back-channel logout does that.)
"""

import logging
from uuid import UUID

from procrastinate import RetryStrategy

from portal.drafting.services import fork
from portal.jobs.app import app

logger = logging.getLogger(__name__)


@app.task(
    name="drafting.sync_wave_access",
    queue="drafting",
    retry=RetryStrategy(max_attempts=8, exponential_wait=10),
)
def sync_wave_access(draft_id: str, actor_user_id: str | None = None) -> None:
    """Send one wave's access list. Deferred with a queueing lock per wave, so changes that land
    while one waits are carried by it (it reads the list when it runs)."""
    fork.push_wave_access(UUID(draft_id), UUID(actor_user_id) if actor_user_id else None)
