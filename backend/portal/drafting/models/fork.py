"""P21c (`drafting_db.0006`): the webhooks the drafting tool delivered, one row per event id.

Insert-only at the grant level (SELECT and INSERT): a delivery is recorded once and never
rewritten. The id is the fork's event id (`X-SCLU-Delivery`, the same on every retry), so a
second delivery of the same event finds its row and changes nothing.
"""

from django.db import models
from django.utils import timezone

from portal.drafting.models._base import DraftingModel


class ForkEvent(DraftingModel):
    id = models.TextField(primary_key=True)
    event_type = models.TextField()
    # The document the event is about (master, agency letter or template), when it names one.
    document_id = models.UUIDField(null=True, blank=True)
    # The event's own `created_at`, on the fork's clock: events can arrive out of order.
    occurred_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    # The event's `data` as delivered (no event carries document text or a filename).
    data = models.JSONField(default=dict)

    class Meta(DraftingModel.Meta):
        db_table = 'drafting"."fork_events'

    def __str__(self) -> str:
        return f"{self.event_type} {self.id}"
