"""Search across everything (BUILD_PLAN P15c, design 1y "Search results"): requests, the mail
that came in, the mail that went out, produced records and agencies, each group scoped by the
same visibility rule its own screen uses. File contents are not searched (that needs extracted
text); file names and notes are.
"""

from dataclasses import dataclass, field

from django.db.models import Q

from portal.mail.models import Message
from portal.records.models import Agency, Request
from portal.records.services import access
from portal.records.services import inbox as inbox_service
from portal.records.services import produced as produced_service
from portal.records.services import requests as requests_service
from portal.records.services.access import Viewer
from portal.records.services.inbox import InboxRow
from portal.records.services.produced import ProducedRow


@dataclass
class Results:
    q: str
    requests: list[Request] = field(default_factory=list)
    requests_total: int = 0
    received: list[InboxRow] = field(default_factory=list)
    received_total: int = 0
    sent: list[Message] = field(default_factory=list)
    sent_total: int = 0
    records: list[ProducedRow] = field(default_factory=list)
    records_total: int = 0
    agencies: list[Agency] = field(default_factory=list)
    agencies_total: int = 0


def search(viewer: Viewer, q: str, *, limit: int = 5) -> Results:
    text = (q or "").strip()
    out = Results(q=text)
    if not text:
        return out

    requests = access.scope(viewer, requests_service.docket(q=text))
    requests += [
        r
        for r in access.scope(
            viewer,
            list(
                Request.objects.filter(request_text__icontains=text)
                .exclude(pk__in=[r.pk for r in requests])
                .select_related("agency")
                .order_by("-created_at", "tracking_number")
            ),
        )
    ]
    out.requests, out.requests_total = requests[:limit], len(requests)

    page = inbox_service.inbox(viewer, "all", q=text, limit=limit)
    out.received, out.received_total = page.rows, page.total

    sent = (
        Message.objects.filter(access.visible_q(viewer, "request__"))
        .filter(Q(subject__icontains=text) | Q(body_text__icontains=text))
        .exclude(status="draft")
        .select_related("request")
        .prefetch_related("recipients", "files")
        .order_by("-updated_at")
    )
    out.sent_total = sent.count()
    out.sent = list(sent[:limit])

    records = produced_service.produced_records(viewer, q=text)
    out.records, out.records_total = records[:limit], len(records)

    agencies = Agency.objects.filter(
        Q(name__icontains=text)
        | Q(short_name__icontains=text)
        | Q(jurisdiction__icontains=text)
        | Q(notes__icontains=text)
    ).order_by("name")
    out.agencies_total = agencies.count()
    out.agencies = list(agencies[:limit])
    return out
