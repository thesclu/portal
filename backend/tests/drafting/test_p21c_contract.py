"""P21c: the portal's client signs exactly as the drafting tool's own reference client does.

The reference client is the Python block printed in the fork's contract, `apps/mike/SCLU.md`
section 14.6 (marked `<!-- sclu-service-client -->`); the fork's own test runs that very block
against its gate. Here it is read from the file at test time and the portal's client
(`portal.drafting.mike.ForkClient`) is held to it: the same four headers for the same request,
timestamp and nonce, the same person header, the same access list, the same bodies for a wave
made with a first list and a list replaced (SCLU.md 15.4). Since P25b the person is named as
Keycloak names them (the realm as issuer, their Keycloak sub, the portal id as `sclu_uid` after
`sub`, SCLU.md 16), byte for byte the same on both sides. Skipped where the fork's working copy is
not beside the portal.
"""

import os
import re
import sys
import types
from pathlib import Path

import httpx
import pytest
from portal.drafting import mike

SCLU_MD = Path(
    os.environ.get("SCLU_FORK_CONTRACT") or Path(__file__).resolve().parents[4] / "mike" / "SCLU.md"
)
BASE = "http://fork.test/api"
SECRET = "c" * 48
ISSUER = "https://portal.example.test/auth/realms/sclu"
TS = 1760707200
NONCE = "3q0Pj2vT7c9cUQXqXFzI0wq1n5bq0d7h"


def _reference():
    if not SCLU_MD.is_file():
        pytest.skip(f"the fork's contract is not at {SCLU_MD}")
    text = SCLU_MD.read_text(encoding="utf-8")
    found = re.search(r"<!-- sclu-service-client -->\s*```python\n(.*?)\n```", text, re.S)
    assert found, "SCLU.md has no marked reference client block"
    module = types.ModuleType("sclu_reference_client")
    sys.modules[module.__name__] = module  # dataclasses read their module's namespace
    try:
        exec(compile(found.group(1), str(SCLU_MD), "exec"), module.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(module.__name__, None)
    return module


@pytest.fixture(scope="module")
def ref():
    return _reference()


PEOPLE = [
    dict(
        sub="kc-0f6e-0001",
        sclu_role="intern",
        email="i@example.org",
        name="Synthetic Intern",
        sclu_org="Students' Civil Liberties Union",
        sclu_uid="7d1c0f6e-0000-4000-8000-000000000001",
    ),
    dict(
        sub="kc-0f6e-0002",
        sclu_role="limited_attorney",
        email="l@example.org",
        name="Ünïcode Attorney",
        sclu_org="",
        sclu_uid="7d1c0f6e-0000-4000-8000-000000000002",
    ),
    # Without a portal id (the field is optional on the wire): the same on both sides too.
    dict(sub="kc-0f6e-0003", sclu_role="admin", email="a@example.org"),
]


def test_the_signed_headers_are_the_reference_clients(ref) -> None:
    theirs = ref.ForkServiceClient(BASE, SECRET, ISSUER)
    ours = mike.ForkClient(BASE, SECRET, ISSUER)
    cases = [
        ("GET", "/sclu/waves/0f9e8d7c-1111-4222-8333-444455556666", b""),
        (
            "GET",
            "/single-documents/0f9e8d7c-1111-4222-8333-444455556666/file?version_id=ab-12",
            b"",
        ),
        (
            "POST",
            "/single-documents/0f9e8d7c-1111-4222-8333-444455556666/merge",
            b'{"documents":[]}',
        ),
        ("PUT", "/sclu/waves/0f9e8d7c-1111-4222-8333-444455556666/access", b'{"people":[]}'),
    ]
    for spec in PEOPLE:
        for method, path, body in cases:
            a = theirs.sign(method, path, body, ref.PortalPerson(**spec), timestamp=TS, nonce=NONCE)
            b = ours.sign(method, path, body, mike.PortalPerson(**spec), timestamp=TS, nonce=NONCE)
            assert a == b, (method, path, spec["sclu_role"])
        assert theirs.person_header(ref.PortalPerson(**spec)) == ours.person_header(
            mike.PortalPerson(**spec)
        )


def test_the_access_list_is_the_reference_clients(ref) -> None:
    theirs = ref.ForkServiceClient(BASE, SECRET, ISSUER)
    ours = mike.ForkClient(BASE, SECRET, ISSUER)
    for privileged in (False, True):
        assert theirs.access_payload(
            [ref.PortalPerson(**p) for p in PEOPLE], privileged=privileged
        ) == ours.access_payload([mike.PortalPerson(**p) for p in PEOPLE], privileged=privileged)


def _captured(ref):
    """The reference client with its transport replaced: each call's method, path and payload."""
    sent = []

    def send(self, method, path, person, payload=None):
        sent.append((method, path, payload))
        body = b'{"created": true, "wave": {"id": "w"}}' if path == "/sclu/waves" else b"{}"
        return ref.ForkResponse(200, {}, body)

    client = ref.ForkServiceClient(BASE, SECRET, ISSUER)
    client.send = types.MethodType(send, client)
    return client, sent


def test_a_wave_with_its_first_list_and_a_list_replaced_send_the_reference_bodies(ref) -> None:
    import json

    theirs, sent = _captured(ref)
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/sclu/waves"):
            return httpx.Response(201, json={"created": True, "wave": {"id": "w"}})
        return httpx.Response(200, json={})

    ours = mike.ForkClient(BASE, SECRET, ISSUER, transport=httpx.MockTransport(handle))
    person_spec = PEOPLE[0]
    people = PEOPLE[:2]
    wave = "0f9e8d7c-1111-4222-8333-444455556666"

    theirs.create_wave(
        ref.PortalPerson(**person_spec),
        key="draft-1",
        name="Synthetic wave",
        template_id="abc",
        access=[ref.PortalPerson(**p) for p in people],
        privileged=True,
    )
    ours.create_wave(
        mike.PortalPerson(**person_spec),
        key="draft-1",
        name="Synthetic wave",
        template_id="abc",
        access=[mike.PortalPerson(**p) for p in people],
        privileged=True,
    )
    theirs.set_wave_access(ref.PortalPerson(**person_spec), wave, [ref.PortalPerson(**people[1])])
    ours.set_wave_access(mike.PortalPerson(**person_spec), wave, [mike.PortalPerson(**people[1])])
    theirs.create_wave(
        ref.PortalPerson(**person_spec), key="draft-2", name="Blank", letterhead_id="ff"
    )
    ours.create_wave(
        mike.PortalPerson(**person_spec), key="draft-2", name="Blank", letterhead_id="ff"
    )

    assert [(m, p) for m, p, _ in sent] == [
        (r.method, r.url.raw_path.decode().removeprefix("/api")) for r in calls
    ]
    for (_, _, payload), request in zip(sent, calls, strict=True):
        assert json.loads(request.content) == payload
        # Our raw bytes are what the reference would have signed (compact JSON).
        assert request.content == json.dumps(payload, separators=(",", ":")).encode()


def test_what_our_client_sends_carries_the_reference_signature(ref) -> None:
    """End to end: the headers our client puts on the wire, for a fixed clock and nonce, are the
    reference client's for the same request."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"%PDF-")

    ours = mike.ForkClient(
        BASE,
        SECRET,
        ISSUER,
        transport=httpx.MockTransport(handle),
        clock=lambda: TS,
        nonce=lambda: NONCE,
    )
    person = PEOPLE[0]
    document = "0f9e8d7c-1111-4222-8333-444455556666"
    version = "0f9e8d7c-1111-4222-8333-999999999999"
    ours.fetch_pdf(mike.PortalPerson(**person), document, version)
    path = f"/single-documents/{document}/display?version_id={version}"
    expected = ref.ForkServiceClient(BASE, SECRET, ISSUER).sign(
        "GET", path, b"", ref.PortalPerson(**person), timestamp=TS, nonce=NONCE
    )
    request = seen[0]
    assert str(request.url) == BASE + path
    for name, value in expected.items():
        assert request.headers[name] == value
    assert request.headers["User-Agent"] == "sclu-portal-service/1"
