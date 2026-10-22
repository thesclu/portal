"""The portal as an ASGI application (P22): Django for HTTP, Django Channels for team chat's
websocket. `manage.py runserver` serves it in development (daphne's runserver, because `daphne`
is first in INSTALLED_APPS); production needs an ASGI server (daphne, or uvicorn) in front of
it, and Redis for the channel layer once there is more than one process (`REDIS_URL`).

The websocket is authenticated by the session cookie and refused unless its Origin is one of
ALLOWED_HOSTS, so another site's page cannot open it with the person's cookie.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portal.settings")
# Django is set up before anything imports a model (the consumer does).
django_application = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from portal.core.chat.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_application,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
