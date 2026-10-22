"""What a production deploy of team chat's live delivery must have (P23a), under
`manage.py check --deploy`.

A message posted through one web process reaches the sockets held by another only through the
channel layer, and the in-memory layer (used when REDIS_URL is unset) reaches its own process
alone. The image's web mode starts WEB_CONCURRENCY worker processes (default 3), so production
with more than one and no REDIS_URL would deliver each message live to about a third of the open
tabs, silently (the rest would catch up by polling). That is refused. With exactly one process
it is a warning: right only if no second web container is ever started beside it.
"""

from typing import Any

from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=True)
def chat_deploy_checks(app_configs: Any, **kwargs: Any) -> list[checks.CheckMessage]:
    if settings.DEBUG or settings.REDIS_URL:
        return []
    if settings.WEB_CONCURRENCY > 1:
        return [
            checks.Error(
                f"REDIS_URL is not set and WEB_CONCURRENCY is {settings.WEB_CONCURRENCY}: team "
                "chat's messages would reach only the tabs connected to the process that took "
                "them.",
                hint="Set REDIS_URL (e.g. redis://redis:6379/0), or WEB_CONCURRENCY=1 for a "
                "single web process.",
                id="portal.E102",
            )
        ]
    return [
        checks.Warning(
            "REDIS_URL is not set: team chat's live delivery works only while this is the one "
            "web process.",
            hint="Set REDIS_URL before running a second web process or container.",
            id="portal.W102",
        )
    ]
