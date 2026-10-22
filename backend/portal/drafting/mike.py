"""The portal's calls to the drafting tool (the SCLU fork of Mike), as its contract documents them:
`apps/mike/SCLU.md` section 14 (service calls), 15 (a wave's access list), 16 (sign-in through
Keycloak, which names the person in those calls) and 9.3 (the webhooks it sends back). Nothing
here reads the database; the flows that use it are `portal.drafting.services.fork`.

SERVICE CALLS (14.4). Every call names the portal person it is for and is signed with
`DRAFTING_SERVICE_SECRET` (the fork's `SCLU_PORTAL_SERVICE_SECRET`): four headers, a timestamp, a
fresh nonce, the person as base64url JSON of the claims Keycloak's ID token carries for them (P25b,
SCLU.md 16: `iss` the realm, `sub` their Keycloak sub, `sclu_uid` the portal's own id, their role,
address, name and organisation, from the portal's own records, as Keycloak's attributes are), and
`v1=` HMAC-SHA256 over seven lines (scheme, method, path and query as sent, timestamp, nonce, the
person header, the body's SHA-256). The fork runs the call with that person's access and no more,
so a step a person takes is always signed as them, and lands on the fork person their own sign-in
there makes.

Ending a person's sessions in the fork is no longer a call from here: Keycloak's back-channel
logout to the fork does it (P25b; the fork's end-sessions call, 13.6 b, is gone).

ONLY THE ALLOW-LIST (14.2, 15.4). `ROUTES` is the fork's own list, nine method-and-path pairs;
anything else is refused here, before a byte leaves (`RouteNotAllowed`), as the fork would.

RETRIES. A network error, a timeout, a 5xx, a 429 or `409 wave_in_progress` is worth another
attempt, signed afresh, and only on a route the fork documents as safe to repeat (`idempotent`):
a wave is keyed by the draft id (14.5), a merge by each recipient's key with the same request a
no-op (9.2), a wave's list is replaced whole (15.4), reads and the in-memory preview change nothing.
Every route on the list happens to be one of those; the flag is what the retry asks, so a route
added later is not retried until someone says it is safe. Waits are short (this runs inside a
person's request): a `Retry-After` longer than `max_wait` is not waited for.

TYPED ERRORS for the codes the fork documents (`ServiceCallsOff`, `InvalidSignature`,
`StaleTimestamp`, `Replayed`, `WaveKeyConflict`, `WaveInProgress`, `NoAccess` for 404,
`RoleRefused` for 403, `AccessPersonRefused`, `RateLimited`, `ForkUnreachable`,
`ForkServerError`); anything else is `ForkRefused`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlencode

import httpx

SCHEME = "sclu-service/1"
USER_AGENT = "sclu-portal-service/1"
MIN_SECRET_LENGTH = 32
# The webhook signature's window (9.3): five minutes.
SIGNATURE_WINDOW_SECONDS = 300
# The largest webhook body read (events are a few hundred bytes; nothing carries a file).
WEBHOOK_MAX_BYTES = 64 * 1024

_HEX_ID = r"[0-9A-Fa-f-]+"
_VERSION_QUERY = rf"(?:\?version_id={_HEX_ID})?"


@dataclass(frozen=True)
class Route:
    """One entry of the fork's allow-list (14.2, 15.4): its name there, the method, the path (and
    query, for the two exports), and whether the fork documents it as safe to send twice."""

    name: str
    method: str
    pattern: re.Pattern[str]
    idempotent: bool


ROUTES: tuple[Route, ...] = (
    # 14.5: one wave per organisation and key (the portal's draft id); the same key returns it.
    Route("wave.create", "POST", re.compile(r"/sclu/waves"), True),
    Route("wave.read", "GET", re.compile(rf"/sclu/waves/{_HEX_ID}"), True),
    # 15.4: a full replacement; the same list twice changes nothing.
    Route("wave.access", "PUT", re.compile(rf"/sclu/waves/{_HEX_ID}/access"), True),
    # 9.2: per recipient key; the same request again is a no-op (`unchanged`).
    Route("merge", "POST", re.compile(rf"/single-documents/{_HEX_ID}/merge"), True),
    Route(
        "export.pdf",
        "GET",
        re.compile(rf"/single-documents/{_HEX_ID}/display{_VERSION_QUERY}"),
        True,
    ),
    Route(
        "export.docx", "GET", re.compile(rf"/single-documents/{_HEX_ID}/file{_VERSION_QUERY}"), True
    ),
    Route("document.read", "GET", re.compile(rf"/single-documents/{_HEX_ID}"), True),
    Route("document.versions", "GET", re.compile(rf"/single-documents/{_HEX_ID}/versions"), True),
    # 11.6: filled in memory, nothing stored.
    Route("preview", "POST", re.compile(rf"/single-documents/{_HEX_ID}/preview"), True),
)


# ---- errors --------------------------------------------------------------------------------------


@dataclass(eq=False)
class ForkError(Exception):
    """The fork refused, failed or could not be reached. `status` is None when no answer came."""

    status: int | None
    code: str | None
    detail: str
    retry_after: str | None = None
    body: dict[str, Any] = field(default_factory=dict)

    # Worth another attempt, signed afresh.
    transient = False

    def __str__(self) -> str:
        return " ".join(str(p) for p in (self.status, self.code, self.detail) if p)


class ForkUnreachable(ForkError):
    """No answer: the address is wrong, the fork is down, or it took longer than the timeout."""

    transient = True


class ForkServerError(ForkError):
    transient = True


class RateLimited(ForkError):
    transient = True


class WaveInProgress(ForkError):
    """409 `wave_in_progress`: another request is making this wave right now (14.3)."""

    transient = True


class ServiceCallsOff(ForkError):
    """403 `service_calls_off`: the fork has no service secret or issuer. An operator's error."""


class InvalidSignature(ForkError):
    """401 `invalid_signature`: the secrets differ, or a header is missing or malformed."""


class StaleTimestamp(ForkError):
    """401 `stale_timestamp`: the two clocks are more than five minutes apart."""


class Replayed(ForkError):
    """409 `replayed`: the nonce was used before."""


class WaveKeyConflict(ForkError):
    """409 `wave_key_conflict`: this draft's key already names a wave made from another source."""


class NoAccess(ForkError):
    """404: no such thing, or the person may not open it (the fork answers both the same way)."""


class RoleRefused(ForkError):
    """403: the person's role is refused (`portal_role_refused`), or the route refuses it (a
    viewer may not merge)."""


class RouteNotAllowed(ForkError):
    """Not on the allow-list: refused here before sending, or 403 `service_route_not_allowed`."""


class AccessPersonRefused(ForkError):
    """422 `access_person_refused` (15.4): one person on a wave's list cannot be applied, so the
    whole list was refused and nothing changed. `body` has `reason` and `index`."""


class ForkRefused(ForkError):
    """Any other refusal (a 4xx the fork's contract does not name for the portal)."""


class PersonNotLinked(ForkError):
    """The person has no Keycloak account the portal knows of yet (`users.keycloak_sub` empty), so
    the drafting tool cannot be told who they are (it names people as Keycloak does). Refused here,
    before anything is sent."""


_BY_CODE: dict[str, type[ForkError]] = {
    "service_calls_off": ServiceCallsOff,
    "invalid_signature": InvalidSignature,
    "stale_timestamp": StaleTimestamp,
    "replayed": Replayed,
    "wave_key_conflict": WaveKeyConflict,
    "wave_in_progress": WaveInProgress,
    "service_route_not_allowed": RouteNotAllowed,
    "rate_limited": RateLimited,
    "portal_role_refused": RoleRefused,
    "access_person_refused": AccessPersonRefused,
}


def error_for(
    status: int, body: Mapping[str, Any], *, retry_after: str | None = None, reason: str = ""
) -> ForkError:
    """The typed error for a refusal: by the fork's `code` first, then by status."""
    code = body.get("code") if isinstance(body.get("code"), str) else None
    detail = str(body.get("detail") or reason or f"HTTP {status}")
    kind = _BY_CODE.get(code or "")
    if kind is None:
        if status == 404:
            kind = NoAccess
        elif status == 403:
            kind = RoleRefused
        elif status == 429:
            kind = RateLimited
        elif status >= 500:
            kind = ForkServerError
        else:
            kind = ForkRefused
    return kind(status, code, detail, retry_after, dict(body))


# ---- the person ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PortalPerson:
    """A portal person as a call names them: the claims Keycloak's ID token carries for them
    (SCLU.md 14.4 as 16 changes it). `sub` is their Keycloak sub, `sclu_uid` the portal's own id."""

    sub: str
    sclu_role: str
    email: str
    name: str = ""
    sclu_org: str = ""
    sclu_uid: str = ""

    @classmethod
    def of(cls, user: Any) -> PortalPerson:
        """From the portal's own records, as Keycloak's attributes for the person are made
        (`portal.core.keycloak.admin.attributes_for`): the role, the organisation's name, the
        portal id. Raises `PersonNotLinked` for someone without a Keycloak account yet."""
        from portal.core.services.identity import records_role_of
        from portal.core.services.settings import organisation_name

        sub = str(getattr(user, "keycloak_sub", "") or "")
        if not sub:
            raise PersonNotLinked(
                None, "person_not_linked", f"{user.email} has no sign-in account in Keycloak yet"
            )
        return cls(
            sub=sub,
            sclu_role=records_role_of(user) or "",
            email=user.email,
            name=user.name or "",
            sclu_org=organisation_name(),
            sclu_uid=str(user.pk),
        )


@dataclass
class ForkResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body or b"null")

    def header(self, name: str) -> str:
        wanted = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == wanted), "")


def plain_id(value: Any) -> str:
    """An id as it goes into a path: hex digits and dashes only (as the reference client)."""
    text = str(value or "")
    if not text or not all(c in "0123456789abcdefABCDEF-" for c in text):
        raise ValueError(f"not an id: {text!r}")
    return text


def route_for(method: str, path: str) -> Route:
    for route in ROUTES:
        if route.method == method.upper() and route.pattern.fullmatch(path):
            return route
    raise RouteNotAllowed(None, "not_on_allow_list", f"{method.upper()} {path} is not allowed")


def _compact(value: Any, *, ascii_only: bool = True) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=ascii_only).encode("utf-8")


# ---- the client ----------------------------------------------------------------------------------


class ForkClient:
    """Signed service calls to the fork's API at `base_url` (14.4's bases: `http://backend:3001`,
    `https://portal.thesclu.org/api/draft`, `http://localhost:3000/api`)."""

    def __init__(
        self,
        base_url: str,
        secret: str,
        issuer: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        attempts: int = 3,
        max_wait: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: secrets.token_urlsafe(24),
    ) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"the service secret must be at least {MIN_SECRET_LENGTH} characters")
        if not base_url:
            raise ValueError("no address for the drafting tool's API")
        self.base_url = base_url.rstrip("/")
        self._secret = secret.encode("utf-8")
        self.issuer = issuer.rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self.attempts = max(1, attempts)
        self.max_wait = max_wait
        self._sleep = sleep
        self._clock = clock
        self._nonce = nonce

    # ---- signing ---------------------------------------------------------------------------------

    def person_claims(self, person: PortalPerson) -> dict[str, str]:
        """The person header's JSON, and each entry of a wave's access list (15.4): in the
        reference client's order (14.6), `sclu_uid` after `sub`."""
        claims: dict[str, str] = {"iss": self.issuer, "sub": person.sub}
        if person.sclu_uid:
            claims["sclu_uid"] = person.sclu_uid
        claims["sclu_role"] = person.sclu_role
        claims["email"] = person.email
        if person.name:
            claims["name"] = person.name
        if person.sclu_org:
            claims["sclu_org"] = person.sclu_org
        return claims

    def person_header(self, person: PortalPerson) -> str:
        raw = _compact(self.person_claims(person), ascii_only=False)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    def access_payload(
        self, people: Iterable[PortalPerson], *, privileged: bool = False
    ) -> dict[str, Any]:
        return {"people": [self.person_claims(p) for p in people], "privileged": privileged}

    def sign(
        self,
        method: str,
        path: str,
        body: bytes,
        person: PortalPerson,
        *,
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        """The four headers for one request. `path` is the path and query after the API base,
        exactly as sent."""
        ts = str(int(self._clock()) if timestamp is None else timestamp)
        nonce = nonce or self._nonce()
        person_value = self.person_header(person)
        to_sign = "\n".join(
            [
                SCHEME,
                method.upper(),
                path,
                ts,
                nonce,
                person_value,
                hashlib.sha256(body).hexdigest(),
            ]
        )
        signature = hmac.new(self._secret, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "X-SCLU-Service-Timestamp": ts,
            "X-SCLU-Service-Nonce": nonce,
            "X-SCLU-Service-Person": person_value,
            "X-SCLU-Service-Signature": f"v1={signature}",
        }

    # ---- transport -------------------------------------------------------------------------------

    def send(
        self, method: str, path: str, person: PortalPerson, payload: Any = None
    ) -> ForkResponse:
        """One signed request, no retry. Raises a `ForkError` for anything but a 2xx."""
        route_for(method, path)
        body = b"" if payload is None else _compact(payload)
        headers = self.sign(method, path, body, person)
        headers["User-Agent"] = USER_AGENT
        if body:
            headers["Content-Type"] = "application/json"
        try:
            with httpx.Client(
                transport=self._transport, timeout=self.timeout, follow_redirects=False
            ) as http:
                answer = http.request(
                    method.upper(), self.base_url + path, content=body or None, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise ForkUnreachable(None, "timeout", f"no answer within {self.timeout:g}s") from exc
        except httpx.TransportError as exc:
            raise ForkUnreachable(None, "unreachable", type(exc).__name__) from exc
        if 200 <= answer.status_code < 300:
            return ForkResponse(answer.status_code, dict(answer.headers.items()), answer.content)
        raise _refusal(answer)

    def call(
        self, method: str, path: str, person: PortalPerson, payload: Any = None
    ) -> ForkResponse:
        """`send`, retried on a transient failure when the route is safe to repeat."""
        route = route_for(method, path)
        attempts = self.attempts if route.idempotent else 1
        for attempt in range(1, attempts + 1):
            try:
                return self.send(method, path, person, payload)
            except ForkError as error:
                if not error.transient or attempt == attempts:
                    raise
                wait = _wait_for(error, attempt)
                if wait > self.max_wait:
                    raise
                self._sleep(wait)
        raise AssertionError("unreachable")

    # ---- the portal's jobs (14.2, 15.4) ---------------------------------------------------------

    def create_wave(
        self,
        person: PortalPerson,
        *,
        key: str,
        name: str,
        template_id: str | None = None,
        master_id: str | None = None,
        letterhead_id: str | None = None,
        filename: str | None = None,
        access: Iterable[PortalPerson] | None = None,
        privileged: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """Job 1: the wave, its folder and its master. Exactly one of a template, a past master
        or neither (blank, with a letterhead if given). `access`, when given, is the wave's first
        list. Returns (created, wave); the same key again is (False, the same wave)."""
        if template_id and master_id:
            raise ValueError("template_id or master_id, not both")
        source: dict[str, Any]
        if template_id:
            source = {"kind": "template", "template_id": template_id}
        elif master_id:
            source = {"kind": "master", "document_id": master_id}
        else:
            source = {
                "kind": "blank",
                **({"letterhead_id": letterhead_id} if letterhead_id else {}),
            }
        payload: dict[str, Any] = {"key": key, "name": name, "from": source}
        if filename:
            payload["filename"] = filename
        if access is not None:
            payload["access"] = self.access_payload(access, privileged=privileged)
        body = self.call("POST", "/sclu/waves", person, payload).json()
        return bool(body["created"]), body["wave"]

    def set_wave_access(
        self,
        person: PortalPerson,
        wave_id: str,
        people: Iterable[PortalPerson],
        *,
        privileged: bool = False,
    ) -> dict[str, Any]:
        """Job 6: replace who besides the broad roles may open a wave."""
        path = f"/sclu/waves/{plain_id(wave_id)}/access"
        return self.call(
            "PUT", path, person, self.access_payload(people, privileged=privileged)
        ).json()

    def merge(
        self, person: PortalPerson, master_id: str, documents: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Job 2: one document per agency. 200, or 207 when an entry failed (its `error`)."""
        path = f"/single-documents/{plain_id(master_id)}/merge"
        return self.call("POST", path, person, {"documents": documents}).json()

    def fetch_pdf(
        self, person: PortalPerson, document_id: str, version_id: str | None = None
    ) -> ForkResponse:
        path = _with_version(f"/single-documents/{plain_id(document_id)}/display", version_id)
        return self.call("GET", path, person)

    def fetch_docx(
        self, person: PortalPerson, document_id: str, version_id: str | None = None
    ) -> ForkResponse:
        path = _with_version(f"/single-documents/{plain_id(document_id)}/file", version_id)
        return self.call("GET", path, person)

    def read_wave(self, person: PortalPerson, wave_id: str) -> dict[str, Any]:
        return self.call("GET", f"/sclu/waves/{plain_id(wave_id)}", person).json()

    def document(self, person: PortalPerson, document_id: str) -> dict[str, Any]:
        return self.call("GET", f"/single-documents/{plain_id(document_id)}", person).json()

    def versions(self, person: PortalPerson, document_id: str) -> dict[str, Any]:
        path = f"/single-documents/{plain_id(document_id)}/versions"
        return self.call("GET", path, person).json()

    def preview(
        self,
        person: PortalPerson,
        master_id: str,
        values: Mapping[str, str | None],
        fmt: str = "pdf",
    ) -> tuple[bytes, list[str], int]:
        """Job 5: the master filled as one agency, in memory. (bytes, unfilled names, filled)."""
        path = f"/single-documents/{plain_id(master_id)}/preview"
        answer = self.call("POST", path, person, {"values": dict(values), "format": fmt})
        unfilled = [unquote(n) for n in answer.header("x-sclu-unfilled").split(",") if n]
        return answer.body, unfilled, int(answer.header("x-sclu-filled") or 0)


def _with_version(path: str, version_id: str | None) -> str:
    return f"{path}?{urlencode({'version_id': plain_id(version_id)})}" if version_id else path


def _refusal(answer: httpx.Response) -> ForkError:
    try:
        parsed = json.loads(answer.content or b"{}")
    except ValueError:
        parsed = {}
    body = parsed if isinstance(parsed, dict) else {}
    return error_for(
        answer.status_code,
        body,
        retry_after=answer.headers.get("Retry-After"),
        reason=answer.reason_phrase,
    )


def _wait_for(error: ForkError, attempt: int) -> float:
    if error.retry_after:
        try:
            return max(0.0, float(error.retry_after))
        except ValueError:
            pass
    return float(attempt)


# ---- the fork's webhooks, verified (9.3) -----------------------------------------------------


class WebhookRejected(Exception):
    """A delivery that is not to be trusted. `reason` is one of: unsigned, bad_signature, stale."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def webhook_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return f"v1={digest}"


def verify_webhook(
    secret: str,
    *,
    timestamp: str | None,
    signature: str | None,
    raw_body: bytes,
    now: float | None = None,
) -> int:
    """Check a delivery's signature over the exact bytes received, in constant time, and then its
    timestamp (within five minutes of this clock; reported only for a good signature, as the fork
    does). Returns the timestamp."""
    if not timestamp or not signature or not timestamp.isdigit() or len(timestamp) > 12:
        raise WebhookRejected("unsigned")
    expected = webhook_signature(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected.encode(), signature.strip().encode()):
        raise WebhookRejected("bad_signature")
    moment = int(timestamp)
    if abs((time.time() if now is None else now) - moment) > SIGNATURE_WINDOW_SECONDS:
        raise WebhookRejected("stale")
    return moment
