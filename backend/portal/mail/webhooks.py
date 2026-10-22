"""Postmark's delivery webhook: Delivery, Bounce and SpamComplaint, one call each.

Authenticated like the inbound one (HTTP Basic with POSTMARK_INBOUND_SECRET; an empty secret
refuses everyone). Every well-formed call is 200: the provider retries on anything else, and a
call about a message that is not ours, or one already recorded, is simply answered as such.
"""

import json

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from portal.core import rls
from portal.mail.services import delivery
from portal.records.inbound.postmark import _authorised


@csrf_exempt
@require_POST
def postmark_delivery(request: HttpRequest) -> HttpResponse:
    rls.set_system_context()
    if not _authorised(request):
        return JsonResponse({"detail": "Unauthorized."}, status=401)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"detail": "Not JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Not a webhook body."}, status=400)
    return JsonResponse({"status": delivery.record(payload)})
