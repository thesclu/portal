"""One readable diff line per audited change, and the gate that keeps an anonymous one out.

Lives in its own module, not in `edits.py` where it began, because `edits` imports `status` and
`status` imports `tasks`: a tasks service that wants to audit its own edits cannot import `edits`
without closing that loop. `edits` re-exports `record_audit` so every existing caller still works.
"""

from typing import Any
from uuid import UUID

from portal.records.actors import Actor
from portal.records.models import AuditLog, writable
from portal.records.services.common import NotAllowed


def record_audit(
    actor: Actor,
    *,
    entity_type: str,
    entity_id: UUID,
    field: str,
    old_value: Any,
    new_value: Any,
    note: str,
) -> AuditLog:
    """One readable diff line. Shared by every audited write, not only `edit_with_audit`.

    Gated here, not only at `edit_with_audit`, so every caller -- present and future -- inherits
    the ban: an audit_log row with a null `edited_by_id` would mean nobody is answerable for it.
    """
    if actor.user_id is None:
        raise NotAllowed(
            f"Recording an audit entry requires a human actor; got the system actor "
            f"{actor.system!r}."
        )
    with writable():
        return AuditLog.objects.create(
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=f"{entity_type}.{field}",
            old_value=None if old_value is None else str(old_value),
            new_value=None if new_value is None else str(new_value),
            edited_by_id=actor.user_id,
            note=note,
        )


def lines_for(entity_type: str, entity_id: UUID) -> list[AuditLog]:
    """Every audit line about one row, oldest first, with who wrote it. Read only: a task's
    history (P20e) is its assign, edit and reopen lines; its creation and completion are on the
    task row itself."""
    return list(
        AuditLog.objects.filter(entity_type=entity_type, entity_id=entity_id)
        .select_related("edited_by")
        .order_by("edited_at", "id")
    )
