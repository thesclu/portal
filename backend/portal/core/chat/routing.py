"""Team chat's websocket address (P22), mounted by `portal/asgi.py`. Under /api/ so the
development proxy (Vite) and a production reverse proxy hand it on with the API."""

from django.urls import re_path

from portal.core.chat.consumers import ChatConsumer

SOCKET_PATH = "api/core/chat/socket"

websocket_urlpatterns = [re_path(rf"^{SOCKET_PATH}$", ChatConsumer.as_asgi())]
