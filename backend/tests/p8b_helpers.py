"""Small builders for the P8b tests: a message, and the event that attaches it to a request.

Plain functions rather than fixtures because each test wants a different mix (flagged or not,
attached or not, one source on three requests), and a fixture per mix would be a fixture per test.
Everything goes through the services, as the application does.
"""

from datetime import UTC, datetime
from itertools import count

from portal.records.models import Source
from portal.records.services import events as events_service
from portal.records.services import sources as sources_service

_serial = count(1)
RECEIVED_AT = datetime(2026, 3, 2, 17, 30, tzinfo=UTC)


def make_source(
    actor,
    *,
    flag: tuple[str, str] | None = None,
    source_type: str = "postmark_email",
    metadata: dict | None = None,
    captured_at: datetime | None = None,
) -> Source:
    """An unattached message, optionally flagged with (reason, note)."""
    source = sources_service.register_source(
        actor,
        source_type=source_type,
        raw_storage_url=f"test://raw-{next(_serial)}.json",
        external_ref=f"test-msg-{next(_serial)}",
        raw_metadata=metadata,
        captured_at=captured_at,
    )
    if flag is not None:
        reason, note = flag
        source = sources_service.flag_source(actor, source.pk, note, reason)
    return source


def attach(
    actor,
    request,
    source,
    *,
    event_type: str = "correspondence",
    direction: str = "inbound",
    channel: str = "email",
    body: str = "A message about the request.",
):
    """An event that puts `source` on `request`."""
    return events_service.record_event(
        actor,
        request.pk,
        event_type=event_type,
        channel=channel,
        direction=direction,
        occurred_at=RECEIVED_AT,
        display_body=body,
        source_id=source.pk,
    )


def add_file(actor, source, name="letter.pdf", role="attachment"):
    return sources_service.add_attachment(
        actor,
        source.pk,
        filename=name,
        storage_url=f"test://files/{name}",
        mime_type="application/pdf",
        size_bytes=100,
        role=role,
    )
