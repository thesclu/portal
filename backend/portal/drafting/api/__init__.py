"""The drafting routers, mounted at /api/drafting.

One exception handler of its own (the last paragraph). The drafting services raise the records
`ServiceError` family, and `records.api.register` already registers handlers for those plus
`IntegrityError` on the one `NinjaAPI` instance -- so a drafting refusal maps to 404/403/409/422
through exactly the same code, with the same non-leaking `IntegrityError` body. Registering a
second set here would be a parallel idiom that could drift from the first.

The two deletion SQLSTATEs (23001 from the schema's triggers, 23503 from the template's
ON DELETE RESTRICT) are translated one layer down, in `services/common.py`, into an
`InvalidTransition` carrying our own sentence. So "refused because it has been filed from" is a
409 with a message a person can act on, while a genuine constraint bug stays a 422 whose body
says nothing about the failing row and whose traceback goes to the log.

That one handler is the drafting app's own (P21c): `DraftingToolUnavailable`, the drafting tool
down or failing, is a 503 with our sentence, not the family's 422 (nothing the person sent was
wrong). Its refusals (`DraftingToolRefused`) are ordinary `ServiceError`s, 422.
"""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from portal.drafting.api import drafts, fork, library
from portal.drafting.services.fork import DraftingToolUnavailable


def register(api: NinjaAPI) -> None:
    for router in (library, drafts, fork):
        api.add_router("/drafting", router.router)

    def unavailable(request: HttpRequest, exc: DraftingToolUnavailable) -> HttpResponse:
        return api.create_response(
            request, {"detail": str(exc), "code": "drafting_tool_unavailable"}, status=503
        )

    api.add_exception_handler(DraftingToolUnavailable, unavailable)
