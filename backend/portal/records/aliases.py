"""The external reference: a random grouped local part at the inbound mail domain.

The tracking number (REQ-2026-000042) is internal and sequential -- publishing it would leak how
many requests exist and invite guessing at neighbours. What the agency sees is this alias instead,
which is random, per request, and assigned at reservation so a letter can name it before it is
sent. The alphabet drops 0/1/i/l/o so a number read off a printed letter cannot be mistyped into
someone else's request.
"""

import re
import secrets

from django.conf import settings

ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
_GROUP = 4
_GROUPS = 3
ALIAS_RE = re.compile(rf"^[{ALPHABET}]{{{_GROUP}}}(?:-[{ALPHABET}]{{{_GROUP}}}){{{_GROUPS - 1}}}@")


def new_local_part() -> str:
    chars = [secrets.choice(ALPHABET) for _ in range(_GROUP * _GROUPS)]
    return "-".join(
        "".join(chars[start : start + _GROUP]) for start in range(0, len(chars), _GROUP)
    )


def new_alias_email() -> str:
    return f"{new_local_part()}@{settings.PORTAL['INBOUND_EMAIL_DOMAIN']}"


def normalize_address(s: str) -> str:
    """`"Records Officer" <Clerk@Agency.GOV>` -> `clerk@agency.gov`.

    Total: this parses `From:` headers off inbound mail, which P5 does not control and which
    arrives malformed often enough to matter, so it returns a sensibly normalised string for any
    input rather than raising. `find`/`rfind` return -1 instead of raising when nothing matches,
    unlike `index`/`rindex` -- the previous version paired `rindex("<")` (last `<`) with
    `index(">", ...)` (first `>` at or after it), which raises `ValueError` whenever a `>`
    appears before the last `<` and none after it, e.g. `"a>b <c@d.com"`.
    """
    address = s.strip()
    open_pos = address.rfind("<")
    if open_pos != -1:
        close_pos = address.find(">", open_pos)
        if close_pos != -1:
            address = address[open_pos + 1 : close_pos]
    return address.strip().strip('"').strip().lower()
