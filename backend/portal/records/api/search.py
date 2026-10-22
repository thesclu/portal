"""P15c: search across everything, for the results screen and the palette."""

from django.http import HttpRequest
from ninja import Query, Router, Schema

from portal.mail.api.messages import message_out
from portal.mail.api.schemas import MailMessageOut
from portal.records.api.agencies import _agency_out
from portal.records.api.inbox import inbox_out
from portal.records.api.permissions import require_team, viewer
from portal.records.api.schemas import AgencyOut, DocketRowOut, InboxRowOut, ProducedRecordOut
from portal.records.api.serializers import attachment_out, docket_row_out
from portal.records.services import inbox as inbox_service
from portal.records.services import search as search_service

router = Router(tags=["search"])


class SearchOut(Schema):
    # The query is not echoed: the census treats anything the caller typed as a possible leak.
    requests: list[DocketRowOut]
    requests_total: int
    received: list[InboxRowOut]
    received_total: int
    sent: list[MailMessageOut]
    sent_total: int
    records: list[ProducedRecordOut]
    records_total: int
    agencies: list[AgencyOut]
    agencies_total: int


@router.get("/search", response=SearchOut)
def search_everything(
    request: HttpRequest, q: str = "", limit: int = Query(5, ge=1, le=50)
) -> SearchOut:
    require_team(request)
    found = search_service.search(viewer(request), q, limit=limit)
    received = inbox_out(
        inbox_service.InboxPage(
            bucket="all",
            rows=found.received,
            total=found.received_total,
            counts={"all": 0, "new": 0, "flagged": 0},  # the bell on Inbox is not this screen's
        ),
        limit=limit,
        offset=0,
    ).items
    return SearchOut(
        requests=[docket_row_out(r) for r in found.requests],
        requests_total=found.requests_total,
        received=received,
        received_total=found.received_total,
        sent=[message_out(m) for m in found.sent],
        sent_total=found.sent_total,
        records=[
            ProducedRecordOut(
                attachment=attachment_out(r.attachment),
                request_id=r.request_id,
                tracking_number=r.tracking_number,
                subject=r.subject,
                agency_id=r.agency_id,
                agency_name=r.agency_name,
                item_id=r.item.pk if r.item else None,
                item_number=r.item.item_number if r.item else None,
                item_description=r.item.description if r.item else None,
            )
            for r in found.records
        ],
        records_total=found.records_total,
        agencies=[_agency_out(a) for a in found.agencies],
        agencies_total=found.agencies_total,
    )
