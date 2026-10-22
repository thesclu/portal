"""Replay a saved Postmark inbound payload through the real webhook, then run its job inline.

    just webhook-replay path/to/payload.json

For development and for checking a payload Postmark's dashboard shows as failed. It goes through
the same view the live webhook does, with the configured secret, so what it prints is what would
have happened.
"""

import base64
import json
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory

from portal.records.inbound import ingest
from portal.records.inbound.postmark import inbound
from portal.records.models import Source


class Command(BaseCommand):
    help = "Post a saved Postmark inbound JSON file to the webhook and run its ingest job."

    def add_arguments(self, parser) -> None:
        parser.add_argument("file", type=Path)

    def handle(self, *args, **options) -> None:
        secret = settings.PORTAL.get("POSTMARK_INBOUND_SECRET") or ""
        if not secret:
            raise CommandError("POSTMARK_INBOUND_SECRET is not set; the webhook refuses everyone.")
        path: Path = options["file"]
        if not path.is_file():
            raise CommandError(f"No such file: {path}")
        token = base64.b64encode(secret.encode()).decode()
        # The view itself, called directly. Going through a test client would send
        # `Host: testserver`, which only pytest allows, and would follow the HTTPS redirect of
        # a production configuration.
        request = RequestFactory().post(
            "/webhooks/postmark/inbound/",
            data=path.read_bytes(),
            content_type="application/json",
            headers={"Authorization": f"Basic {token}"},
        )
        response = inbound(request)
        self.stdout.write(f"webhook: {response.status_code} {response.content.decode()}")
        if response.status_code != 200:
            raise CommandError("The webhook did not accept the payload.")
        source_id = UUID(json.loads(response.content)["source_id"])
        result = ingest.ingest_source(source_id)
        source = Source.objects.get(pk=source_id)
        self.stdout.write(f"source:  {source.pk}")
        self.stdout.write(
            f"flagged: {source.flagged_for_review}"
            + (f" ({source.flag_reason}: {source.flag_note})" if source.flagged_for_review else "")
        )
        self.stdout.write(f"files:   {len(result.attachments)}")
        if result.event is not None:
            self.stdout.write(
                f"event:   {result.event.pk} on {result.event.request.tracking_number}"
            )
        else:
            self.stdout.write("event:   none")
        if not result.ingested:
            self.stdout.write("job:     already ingested; nothing done this time")
