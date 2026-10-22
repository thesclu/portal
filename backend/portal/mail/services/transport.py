"""The provider. One function, `send`, that hands Postmark a message and returns its id.

SENDING IS OFF UNLESS `PORTAL["MAIL_SEND_ENABLED"]` IS TRUE. When it is off, every send goes
through Postmark's test token, which validates and accepts the message and delivers nothing; the
message is marked `test_send` so nobody mistakes it for delivered. The real token is used only
when sending is on. Tests replace `_post`; nothing in the test suite reaches the network.

Errors come in two kinds: the provider refused the message (a 4xx with Postmark's ErrorCode: a
bad address, an unverified sender), which is permanent and marks the message failed; and
everything else (network, 5xx), which is transient and the job retries.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from django.conf import settings

ENDPOINT = "https://api.postmarkapp.com/email"
TEST_TOKEN = "POSTMARK_API_TEST"
TIMEOUT_SECONDS = 30


class TransportError(Exception):
    def __init__(self, detail: str, *, permanent: bool) -> None:
        super().__init__(detail)
        self.permanent = permanent


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    test_send: bool
    response: dict[str, Any]


def enabled() -> bool:
    return bool(settings.PORTAL.get("MAIL_SEND_ENABLED"))


def _token() -> str:
    if not enabled():
        return TEST_TOKEN
    token = str(settings.PORTAL.get("POSTMARK_SERVER_TOKEN") or "")
    if not token:
        raise TransportError(
            "Sending is switched on but POSTMARK_SERVER_TOKEN is not set.", permanent=True
        )
    return token


def _post(token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """The one network call. Returns (status, decoded body)."""
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        return exc.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TransportError(f"Could not reach the mail provider: {exc}", permanent=False) from exc


def send(payload: dict[str, Any]) -> SendResult:
    token = _token()
    status, body = _post(token, payload)
    if status == 200 and body.get("ErrorCode", 0) == 0:
        return SendResult(
            provider_message_id=str(body.get("MessageID") or ""),
            test_send=token == TEST_TOKEN,
            response=body,
        )
    detail = str(body.get("Message") or f"The mail provider answered {status}.")
    code = body.get("ErrorCode")
    permanent = 400 <= status < 500 and status != 429
    raise TransportError(f"{detail} (code {code})" if code else detail, permanent=permanent)
