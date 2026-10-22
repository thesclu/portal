from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpRequest, HttpResponseBase
from django.urls import path, re_path

from portal.api import api
from portal.health import healthz
from portal.mail.webhooks import postmark_delivery
from portal.records.inbound.postmark import inbound as postmark_inbound


def spa(request: HttpRequest, path: str = "") -> HttpResponseBase:
    """Every path that is not the API's is the frontend's: hand over its index.html and let the
    client-side router read the address. Never cached, so a deploy is seen on the next load; the
    hashed files under /assets are WhiteNoise's and are cached hard."""
    index = settings.FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise Http404("The frontend has not been built (frontend/dist is missing).")
    response = FileResponse(index.open("rb"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("healthz", healthz),
    path("webhooks/postmark/inbound/", postmark_inbound),
    path("webhooks/postmark/delivery/", postmark_delivery),
    # Last, and only for paths that are nobody else's. A typo under /api stays a 404 from the
    # server, not a page the client cannot explain. P25a: /auth is Keycloak's (the proxy sends it
    # there before this server sees it; never the SPA's even if it does not).
    re_path(
        r"^(?!api/|admin/|healthz$|webhooks/|static/|assets/|auth/)(?P<path>.*)$",
        spa,
        name="spa",
    ),
]
