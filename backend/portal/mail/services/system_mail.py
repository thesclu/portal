"""The portal's own mail: invites, password resets, the daily summary (P15a, P15b).

Plain text always, and an HTML part as well when the caller has one (P20f: the summary and the
invite, built by `core.services.emails`). From `PORTAL["SYSTEM_EMAIL_FROM"]`, through the same
transport as everything else, so `MAIL_SEND_ENABLED` governs it: off, the provider's test token
accepts and discards. Nothing here is a records source: these messages are about the portal, not
about any request.
"""

import logging

from django.conf import settings

from portal.core.services.settings import get_setting
from portal.mail.services import transport

logger = logging.getLogger(__name__)


def send_system_email(
    to: str, subject: str, text: str, html: str | None = None
) -> transport.SendResult:
    org = str(get_setting("org.name") or "").strip()
    sender = str(settings.PORTAL["SYSTEM_EMAIL_FROM"])
    payload = {
        "From": f"{org} Portal <{sender}>" if org else sender,
        "To": to,
        "Subject": subject,
        "TextBody": text,
        "MessageStream": str(settings.PORTAL.get("POSTMARK_MESSAGE_STREAM") or "outbound"),
    }
    if html:
        payload["HtmlBody"] = html
    result = transport.send(payload)
    logger.info("system mail to %s: %s%s", to, subject, " (test token)" if result.test_send else "")
    return result


def site_url(path: str) -> str:
    return f"{settings.PORTAL['PUBLIC_URL']}{path}"
