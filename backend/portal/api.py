"""The one NinjaAPI instance. Routers register here; frontend/openapi.json is exported from it."""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from portal.core.api.activity import router as activity_router
from portal.core.api.agreements import router as agreements_router
from portal.core.api.ai import router as ai_router
from portal.core.api.auth import router as auth_router
from portal.core.api.chat import router as chat_router
from portal.core.api.notifications import router as notifications_router
from portal.core.api.presence import router as presence_router
from portal.core.api.reviews import router as reviews_router
from portal.core.api.rules import router as rules_router
from portal.core.api.settings import router as settings_router
from portal.core.api.setup import router as setup_router
from portal.core.api.signatures import router as signatures_router
from portal.core.api.suggestions import router as suggestions_router
from portal.core.api.system import router as system_router
from portal.core.api.users import router as users_router
from portal.core.rls import (
    REVERIFY_CODE,
    REVERIFY_DETAIL,
    SETUP_REQUIRED_CODE,
    SETUP_REQUIRED_DETAIL,
    ReverifyRequired,
    SetupRequired,
    session_auth,
)
from portal.drafting.api import register as register_drafting_api
from portal.mail.api import register as register_mail_api
from portal.records.api import register as register_records_api

api = NinjaAPI(
    title="SCLU Portal API",
    version="0.1.0",
    auth=session_auth,
    urls_namespace="api",
)


def _setup_required(request: HttpRequest, exc: SetupRequired) -> HttpResponse:
    """P23a: the new-user setup holds this person (portal.core.rls). A code the client branches
    on (it goes to /setup) and a fixed sentence; nothing about the route that was asked for."""
    body = {"detail": SETUP_REQUIRED_DETAIL, "code": SETUP_REQUIRED_CODE}
    return api.create_response(request, body, status=403)


def _reverify_required(request: HttpRequest, exc: ReverifyRequired) -> HttpResponse:
    """P25a: the passkey check is due. A code the client branches on (it sends the person
    through Keycloak again and back to the page) and a fixed sentence."""
    body = {"detail": REVERIFY_DETAIL, "code": REVERIFY_CODE}
    return api.create_response(request, body, status=401)


api.add_exception_handler(SetupRequired, _setup_required)
api.add_exception_handler(ReverifyRequired, _reverify_required)
api.add_router("/core/auth", auth_router)
api.add_router("/core/users", users_router)
# P20f: a person's own signature, /core/users/{user_id}/signature...
api.add_router("/core/users", signatures_router)
api.add_router("/core/settings", settings_router)
api.add_router("/core/system", system_router)
api.add_router("/core/reviews", reviews_router)
api.add_router("/core", rules_router)
api.add_router("/core/notifications", notifications_router)
api.add_router("/core/presence", presence_router)
api.add_router("/core/activity", activity_router)
api.add_router("/core/ai", ai_router)
api.add_router("/core/suggestions", suggestions_router)
# P20i: the new-user setup and the agreements it asks for.
api.add_router("/core/setup", setup_router)
api.add_router("/core/agreements", agreements_router)
# P22: team chat (its websocket is portal/core/chat/routing.py, outside ninja).
api.add_router("/core/chat", chat_router)
register_records_api(api)
register_drafting_api(api)
register_mail_api(api)
