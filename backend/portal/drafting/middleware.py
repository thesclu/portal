"""P21c: which pages the portal's own pages may frame.

Every HTML page the portal serves (the built frontend's index, which every screen is, and the
server-rendered sign-in pages) says `Content-Security-Policy: frame-src 'self'`, and, while the
drafting tool is in use, its pages' origin too (Settings → System, `drafting.fork_web_url`). Only
`frame-src`: the other kinds of content are not narrowed by this part. A response that already
carries a policy is left as it is. (In development Vite serves the frontend itself, so this header
is the production server's.)
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class FrameSourceMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if "Content-Security-Policy" in response or not response.get("Content-Type", "").startswith(
            "text/html"
        ):
            return response
        response["Content-Security-Policy"] = frame_src()
        return response


def frame_src() -> str:
    from portal.drafting.services import fork

    sources = ["'self'"]
    cfg = fork.config()
    if cfg.in_use and cfg.web_origin:
        sources.append(cfg.web_origin)
    return "frame-src " + " ".join(sources)
