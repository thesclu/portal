"""A stand-in for the drafting tool's API at the HTTP layer (an `httpx.MockTransport` handler), for
the P21c tests: no fork runs. Its answers are the bodies `apps/mike/SCLU.md` documents (14.4,
14.5, 15.4), and it checks every call as the fork's gate does (14.4): the four headers, the
seven-line HMAC, the allow-list, a fresh nonce, the person header named as Keycloak names the
person (16: the realm, their Keycloak sub, the portal id). Synthetic data only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

SERVICE_SECRET = "s" * 40 + "-synthetic-service-secret"
WEBHOOK_SECRET = "w" * 40 + "-synthetic-webhook-secret"
API_URL = "http://fork.test/api"
WEB_URL = "http://fork-web.test"
# The realm the tests' Keycloak names (tests/conftest.py), as the fork's SCLU_KEYCLOAK_ISSUER.
ISSUER = "https://keycloak.invalid/auth/realms/sclu"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(paragraphs: list[str]) -> bytes:
    """A minimal .docx whose body is these paragraphs (what `docx_text.extract` reads)."""
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}">'
        f"<w:body>{body}</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return out.getvalue()


def pdf(text: str) -> bytes:
    return b"%PDF-1.6 synthetic " + text.encode()


@dataclass
class Call:
    method: str
    path: str
    headers: dict[str, str]
    person: dict[str, Any] | None
    body: Any


@dataclass
class Document:
    id: str
    versions: list[tuple[str, list[str]]]  # (version id, paragraphs)
    key: str | None = None  # the merge key that made it (an agency letter)

    @property
    def current(self) -> tuple[str, list[str]]:
        return self.versions[-1]


_ID = r"[0-9a-f-]+"
_ALLOWED = [
    ("POST", re.compile(r"/sclu/waves")),
    ("GET", re.compile(rf"/sclu/waves/{_ID}")),
    ("PUT", re.compile(rf"/sclu/waves/{_ID}/access")),
    ("POST", re.compile(rf"/single-documents/{_ID}/merge")),
    ("GET", re.compile(rf"/single-documents/{_ID}/display(\?version_id={_ID})?")),
    ("GET", re.compile(rf"/single-documents/{_ID}/file(\?version_id={_ID})?")),
    ("GET", re.compile(rf"/single-documents/{_ID}")),
    ("GET", re.compile(rf"/single-documents/{_ID}/versions")),
    ("POST", re.compile(rf"/single-documents/{_ID}/preview")),
]


@dataclass
class FakeFork:
    calls: list[Call] = field(default_factory=list)
    waves: dict[str, dict[str, Any]] = field(default_factory=dict)  # by key
    documents: dict[str, Document] = field(default_factory=dict)
    access: dict[str, dict[str, Any]] = field(default_factory=dict)  # wave id -> last list
    nonces: set[str] = field(default_factory=set)
    # (status, body) answered to the next calls in order, or an exception to raise
    script: list[Any] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)  # what a preview reports
    master_words: list[str] = field(default_factory=lambda: ["{{AGENCY_FULL}}", "Item 1."])

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self, method: str | None = None) -> list[str]:
        return [c.path for c in self.calls if method is None or c.method == method]

    # ---- the gate (14.4) -------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        parts = urlsplit(str(request.url))
        assert parts.scheme + "://" + parts.netloc + "/api" == API_URL, str(request.url)
        path = parts.path.removeprefix("/api") + (f"?{parts.query}" if parts.query else "")
        raw = request.content
        # P25b: the end-sessions call (13.6 b) is gone; a portal that still sent it would be
        # answered as any route off the allow-list is.
        headers = {k.lower(): v for k, v in request.headers.items()}
        person_value = headers.get("x-sclu-service-person", "")
        string = "\n".join(
            [
                "sclu-service/1",
                request.method,
                path,
                headers.get("x-sclu-service-timestamp", ""),
                headers.get("x-sclu-service-nonce", ""),
                person_value,
                hashlib.sha256(raw).hexdigest(),
            ]
        )
        expected = (
            "v1=" + hmac.new(SERVICE_SECRET.encode(), string.encode(), hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, headers.get("x-sclu-service-signature", "")):
            return self._json(401, {"code": "invalid_signature", "detail": "Invalid signature"})
        if not any(m == request.method and p.fullmatch(path) for m, p in _ALLOWED):
            return self._json(403, {"code": "service_route_not_allowed", "detail": "Not allowed"})
        nonce = headers["x-sclu-service-nonce"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce), nonce
        if nonce in self.nonces:
            return self._json(409, {"code": "replayed", "detail": "Replayed"})
        self.nonces.add(nonce)
        padded = person_value + "=" * (-len(person_value) % 4)
        person = json.loads(base64.urlsafe_b64decode(padded))
        body = json.loads(raw) if raw else None
        self.calls.append(Call(request.method, path, headers, person, body))
        if person.get("iss") != ISSUER:
            # The fork accepts only the realm it signs people in through (SCLU_KEYCLOAK_ISSUER).
            return self._json(400, {"code": "wrong_issuer", "detail": "Another issuer"})
        if person.get("sclu_role") not in (
            "admin",
            "staff",
            "staff_attorney",
            "limited_attorney",
            "intern",
        ):
            return self._json(403, {"code": "portal_role_refused", "detail": "Refused"})
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            if step is not None:
                status, answer, *extra = step
                return self._json(status, answer, headers=extra[0] if extra else None)
        return self._route(request.method, path, person, body)

    # ---- the jobs (14.5, 15.4) ---------------------------------------------------------------

    def _route(self, method: str, path: str, person: dict[str, Any], body: Any) -> httpx.Response:
        segments = urlsplit(path).path.strip("/").split("/")
        query = parse_qs(urlsplit(path).query)
        if path == "/sclu/waves":
            return self._create_wave(person, body)
        if segments[:2] == ["sclu", "waves"] and len(segments) == 4 and method == "PUT":
            return self._set_access(segments[2], body)
        if segments[:2] == ["sclu", "waves"] and len(segments) == 3:
            wave = next((w for w in self.waves.values() if w["id"] == segments[2]), None)
            if wave is None:
                return self._json(404, {"detail": "Wave not found"})
            return self._json(200, {"wave": wave, "documents": []})
        doc = self.documents.get(segments[1]) if len(segments) > 1 else None
        if doc is None:
            return self._json(404, {"detail": "Document not found"})
        action = segments[2] if len(segments) > 2 else ""
        if action == "merge":
            return self._merge(doc, body)
        if action == "preview":
            headers = {
                "X-SCLU-Unfilled": ",".join(self.unfilled),
                "X-SCLU-Filled": "3",
                "X-SCLU-Preview-Cache": "miss",
            }
            content = docx(doc.current[1]) if body["format"] == "docx" else pdf("preview")
            return httpx.Response(200, content=content, headers=headers)
        if action == "versions":
            return self._json(
                200,
                {
                    "current_version_id": doc.current[0],
                    "versions": [
                        {"id": vid, "version_number": i + 1, "source": "generated"}
                        for i, (vid, _) in enumerate(doc.versions)
                    ],
                },
            )
        version = query.get("version_id", [doc.current[0]])[0]
        words = dict(doc.versions).get(version)
        if words is None:
            return self._json(404, {"detail": "No file available"})
        if action == "display":
            return httpx.Response(
                200, content=pdf(" | ".join(words)), headers={"Content-Type": "application/pdf"}
            )
        if action == "file":
            return httpx.Response(200, content=docx(words))
        return self._json(200, {"id": doc.id})

    def _create_wave(self, person: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        key = body["key"]
        if key in self.waves:
            return self._json(200, {"created": False, "wave": self.waves[key]})
        master_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        self.documents[master_id] = Document(master_id, [(version_id, list(self.master_words))])
        source = body["from"]
        wave = {
            "id": str(uuid.uuid4()),
            "key": key,
            "name": body["name"],
            "org_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "folder_id": str(uuid.uuid4()),
            "master_document_id": master_id,
            "master_version_id": version_id,
            "source": {
                "kind": source["kind"],
                "document_id": source.get("template_id") or source.get("document_id"),
                "version_id": None,
                "letterhead_id": source.get("letterhead_id"),
            },
            "created_by": str(uuid.uuid4()),
            "created_at": "2026-10-17T19:02:11.123+00:00",
        }
        self.waves[key] = wave
        if "access" in body:
            refused = self._refuse_list(body["access"])
            if refused is not None:
                del self.waves[key]
                return refused
            self.access[wave["id"]] = body["access"]
        return self._json(201, {"created": True, "wave": wave})

    def _refuse_list(self, payload: dict[str, Any]) -> httpx.Response | None:
        for index, entry in enumerate(payload.get("people", [])):
            if entry.get("sclu_role") not in (
                "admin",
                "staff",
                "staff_attorney",
                "limited_attorney",
                "intern",
            ):
                return self._json(
                    422,
                    {
                        "code": "access_person_refused",
                        "reason": "portal_role_refused",
                        "index": index,
                        "detail": "A person on the list is refused.",
                    },
                )
        return None

    def _set_access(self, wave_id: str, body: dict[str, Any]) -> httpx.Response:
        if not any(w["id"] == wave_id for w in self.waves.values()):
            return self._json(404, {"detail": "Wave not found"})
        refused = self._refuse_list(body)
        if refused is not None:
            return refused
        before = {p["sub"] for p in self.access.get(wave_id, {}).get("people", [])}
        after = {p["sub"] for p in body["people"]}
        self.access[wave_id] = body
        return self._json(
            200,
            {
                "wave_id": wave_id,
                "privileged": bool(body.get("privileged")),
                "people": len(after),
                "added": len(after - before),
                "removed": len(before - after),
                "set_at": "2026-10-17T19:56:13.761219+00:00",
            },
        )

    def _merge(self, master: Document, body: dict[str, Any]) -> httpx.Response:
        out = []
        for entry in body["documents"]:
            values = entry["values"]
            words = [_fill(w, values) for w in master.current[1]]
            existing = next((d for d in self.documents.values() if d.key == entry["key"]), None)
            if existing is None:
                doc = Document(str(uuid.uuid4()), [(str(uuid.uuid4()), words)], key=entry["key"])
                self.documents[doc.id] = doc
                status = "created"
            elif existing.current[1] == words:
                doc, status = existing, "unchanged"
            else:
                existing.versions.append((str(uuid.uuid4()), words))
                doc, status = existing, "replaced"
            unfilled = sorted(set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", " ".join(words))))
            out.append(
                {
                    "key": entry["key"],
                    "status": status,
                    "document_id": doc.id,
                    "version_id": doc.current[0],
                    "version_number": len(doc.versions),
                    "filename": f"Wave master - {values.get('AGENCY_ABBR', entry['key'])}.docx",
                    "unfilled": unfilled,
                    "filled": 3,
                }
            )
        return self._json(
            200,
            {
                "master_document_id": master.id,
                "master_version_id": master.current[0],
                "project_id": str(uuid.uuid4()),
                "folder_id": str(uuid.uuid4()),
                "documents": out,
            },
        )

    def revise(self, document_id: str, words: list[str]) -> str:
        """A new current version of a letter, as the fork's editor would make one."""
        version_id = str(uuid.uuid4())
        self.documents[document_id].versions.append((version_id, words))
        return version_id

    @staticmethod
    def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})


def _fill(text: str, values: dict[str, Any]) -> str:
    def sub(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return match.group(0) if value in (None, "") else str(value)

    return re.sub(r"\{\{([A-Z0-9_]+)\}\}", sub, text)


def webhook(
    event_type: str,
    data: dict[str, Any],
    *,
    event_id: str | None = None,
    created_at: str = "2026-10-17T12:00:00.000Z",
) -> bytes:
    return json.dumps(
        {
            "id": event_id or uuid.uuid4().hex,
            "type": event_type,
            "created_at": created_at,
            "data": data,
        },
        separators=(",", ":"),
    ).encode()


def webhook_headers(raw: bytes, *, ts: int, secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    event = json.loads(raw)
    sig = "v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return {
        "HTTP_X_SCLU_EVENT": event["type"],
        "HTTP_X_SCLU_DELIVERY": event["id"],
        "HTTP_X_SCLU_TIMESTAMP": str(ts),
        "HTTP_X_SCLU_SIGNATURE": sig,
        "HTTP_USER_AGENT": "sclu-drafting-webhooks/1",
    }
