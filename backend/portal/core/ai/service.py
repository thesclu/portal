"""One door for every run (DECISIONS 27): the contract's context, the tier's executor, the run
log with what was sent, the daily limit, and the answer. Nothing here changes a letter."""

import logging
from datetime import timedelta
from typing import Any

from django.utils import timezone

from portal.core.ai import executors
from portal.core.ai.contracts import Context
from portal.core.ai.executors import ExecutorError, ExecutorResult, NotConfigured
from portal.core.models import AiRun, User
from portal.core.services.settings import get_setting
from portal.records.services.common import ServiceError

logger = logging.getLogger(__name__)


class OverLimit(ServiceError):
    """Today's run limit is spent."""


def _check_limit() -> None:
    limit = int(get_setting("ai.daily_run_limit") or 0)
    if limit <= 0:
        return
    since = timezone.now() - timedelta(hours=24)
    used = AiRun.objects.filter(created_at__gte=since).exclude(status="refused").count()
    if used >= limit:
        raise OverLimit(f"The assistant has run {used} times today; the limit is {limit}.")


def run(
    task: str,
    tier: str,
    context: Context,
    *,
    system: str,
    prompt: str,
    schema: dict[str, Any] | None,
    subject_type: str = "",
    subject_id=None,
    requested_by: User | None = None,
) -> tuple[AiRun, ExecutorResult]:
    """Run one task on its tier, logging what was sent and what came back. Raises
    `NotConfigured` (off or no key), `OverLimit`, or `ExecutorError`; the run row records each."""
    _check_limit()
    executor = executors.for_tier(tier)
    full_prompt = f"{context.text()}\n\n{prompt}"
    row = AiRun.objects.create(
        task=task,
        tier=tier,
        provider=executor.name,
        model=getattr(executor, "model", "") or "",
        subject_type=subject_type,
        subject_id=subject_id,
        requested_by=requested_by,
        context_summary=context.summary(),
        prompt=full_prompt if bool(get_setting("ai.keep_prompts")) else "",
    )
    try:
        result = executor.run(system=system, prompt=full_prompt, schema=schema)
    except NotConfigured as exc:
        AiRun.objects.filter(pk=row.pk).update(
            status="refused", error=str(exc), finished_at=timezone.now()
        )
        raise
    except ExecutorError as exc:
        AiRun.objects.filter(pk=row.pk).update(
            status="failed", error=str(exc), finished_at=timezone.now()
        )
        raise
    AiRun.objects.filter(pk=row.pk).update(
        status="done",
        model=result.model,
        output=result.data if result.data is not None else {"text": result.text[:20000]},
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        finished_at=timezone.now(),
    )
    return AiRun.objects.get(pk=row.pk), result
