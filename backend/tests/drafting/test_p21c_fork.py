"""P21c: the portal's side of the seam with the drafting tool (DECISIONS 49).

No fork runs: its API is `tests/drafting/fork_fake.FakeFork` at the HTTP layer (an httpx
MockTransport), answering with the bodies `apps/mike/SCLU.md` documents and checking every call as
the fork's gate does. Synthetic people, agencies and letters only.

  - the client: the allow-list refuses before sending; typed errors; retries only where safe,
    each attempt signed afresh;
  - Settings: off by default; the switch alone is not enough without the secrets; the addresses
    editable, the secrets only reported as set; with it off the portal does exactly what it did;
  - the flow: stage 1 makes the wave as the person who clicked (with its first access list),
    stage 3 refuses a letter with a blank and then merges with the reserved values and writes the
    request's words from the agency's .docx, stage 4 writes them again when the letter changed and
    files as before, an email filing carries the fork's PDF;
  - the webhooks: signature, window, once per event id, out-of-order versions, the Files tab and
    the Suggestions badge;
  - ending sessions in the fork when the portal ends sign-ins, by a retried job;
  - a wave's access list from the portal's own visibility rule, and the changes that resend it.
"""

import json
import time
import uuid

import httpx
import pytest
from django.db import connection
from django.test import Client
from portal.core.models import Setting
from portal.core.services import identity
from portal.drafting import docx_text, mike
from portal.drafting.models import ForkEvent, LetterDraft, LetterRecipient, LetterTemplate
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing
from portal.drafting.services import fork
from portal.records.actors import Actor
from portal.records.models import AuditLog, Request
from portal.records.services import requests as requests_service

from tests.drafting import fork_fake
from tests.drafting.fork_fake import FakeFork, webhook, webhook_headers
from tests.p20h_helpers import give

DRAFTING = "/api/drafting"


def post_json(client: Client, path: str, payload: dict | None = None, **extra):
    return client.post(
        path, data=json.dumps(payload or {}, default=str), content_type="application/json", **extra
    )


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _person(role: str, label: str | None = None, **extra):
    label = label or role
    return identity.create_user(
        email=f"p21c-{label}-{uuid.uuid4().hex[:6]}@example.org",
        name=f"Synthetic {label.replace('_', ' ').title()}",
        records_role=role,
        is_staff=role == "admin",
        **extra,
    )


def _jobs(task: str) -> list[dict]:
    with connection.cursor() as cur:
        cur.execute(
            "select args, queueing_lock, status from procrastinate_jobs where task_name = %s "
            "order by id",
            [task],
        )
        return [
            {"args": json.loads(a) if isinstance(a, str) else a, "lock": lock, "status": s}
            for a, lock, s in cur.fetchall()
        ]


@pytest.fixture
def fork_template(db, staff_user):
    """A template the drafting tool keeps (the portal's row is its mirror)."""
    return LetterTemplate.objects.create(
        name="Synthetic ALPR request",
        fork_template_id=uuid.uuid4(),
        created_by_user_id=staff_user.pk,
    )


@pytest.fixture
def staff_client(db, staff_user):
    return _client(staff_user)


def _use(client: Client, body: dict) -> dict:
    answer = post_json(client, f"{DRAFTING}/drafts", body)
    assert answer.status_code == 201, answer.content
    return answer.json()


@pytest.fixture
def fork_draft(fork_on, staff_client, fork_template) -> LetterDraft:
    made = _use(staff_client, {"from_template_id": str(fork_template.pk)})
    return LetterDraft.objects.get(pk=made["id"])


# ---- the client --------------------------------------------------------------------------------


def _client_for(handler, **kw) -> mike.ForkClient:
    return mike.ForkClient(
        fork_fake.API_URL,
        fork_fake.SERVICE_SECRET,
        fork_fake.ISSUER,
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        **kw,
    )


PERSON = mike.PortalPerson(sub=str(uuid.uuid4()), sclu_role="staff", email="s@example.org")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/single-documents/0f9e8d7c-1111-4222-8333-444455556666"),
        ("GET", "/sclu/waves/"),
        ("GET", "/SCLU/waves/0f9e8d7c"),
        ("GET", "/projects"),
        ("POST", "/projects/0f9e/documents/0f9e/copy"),
        ("PUT", "/sclu/waves/0f9e8d7c/access/"),
        ("GET", "/single-documents/0f9e8d7c/display?version_id=0f9e&x=1"),
        ("GET", "/single-documents/../sclu/waves"),
        ("POST", "/sclu/library/templates"),
        ("POST", "/sclu/portal/end-sessions"),
        ("HEAD", "/sclu/waves/0f9e8d7c"),
    ],
)
def test_only_the_allow_list_leaves_the_portal(method, path) -> None:
    sent = []
    client = _client_for(lambda r: sent.append(r) or httpx.Response(200, json={}))
    with pytest.raises(mike.RouteNotAllowed):
        client.call(method, path, PERSON)
    assert sent == []


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (403, {"code": "service_calls_off", "detail": "off"}, mike.ServiceCallsOff),
        (401, {"code": "invalid_signature", "detail": "x"}, mike.InvalidSignature),
        (401, {"code": "stale_timestamp", "detail": "x"}, mike.StaleTimestamp),
        (409, {"code": "replayed", "detail": "x"}, mike.Replayed),
        (409, {"code": "wave_key_conflict", "detail": "x"}, mike.WaveKeyConflict),
        (404, {"detail": "Document not found"}, mike.NoAccess),
        (403, {"detail": "viewer"}, mike.RoleRefused),
        (403, {"code": "portal_role_refused", "detail": "x"}, mike.RoleRefused),
        (403, {"code": "service_route_not_allowed", "detail": "x"}, mike.RouteNotAllowed),
        (
            422,
            {
                "code": "access_person_refused",
                "reason": "portal_role_refused",
                "index": 1,
                "detail": "x",
            },
            mike.AccessPersonRefused,
        ),
        (400, {"detail": "not_docx"}, mike.ForkRefused),
    ],
)
def test_each_documented_refusal_is_its_own_error_and_is_not_retried(status, body, kind) -> None:
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(status, json=body)

    with pytest.raises(kind) as caught:
        _client_for(handler).read_wave(PERSON, "0f9e8d7c-1111-4222-8333-444455556666")
    assert caught.value.status == status and len(sent) == 1


def test_a_transient_failure_is_retried_signed_afresh_on_a_safe_route() -> None:
    answers = [
        httpx.Response(503, json={"code": "internal_error"}),
        httpx.Response(200, json={"wave": {}}),
    ]
    sent: list[httpx.Request] = []

    def handler(request):
        sent.append(request)
        return answers.pop(0)

    _client_for(handler).read_wave(PERSON, "0f9e8d7c-1111-4222-8333-444455556666")
    assert len(sent) == 2
    first, second = (r.headers for r in sent)
    assert first["X-SCLU-Service-Nonce"] != second["X-SCLU-Service-Nonce"]
    assert first["X-SCLU-Service-Signature"] != second["X-SCLU-Service-Signature"]


def test_a_wave_in_progress_is_waited_for_and_a_long_wait_is_not(monkeypatch) -> None:
    waited: list[float] = []
    answers = [
        httpx.Response(409, json={"code": "wave_in_progress"}, headers={"Retry-After": "5"}),
        httpx.Response(201, json={"created": True, "wave": {"id": "w"}}),
    ]
    client = mike.ForkClient(
        fork_fake.API_URL,
        fork_fake.SERVICE_SECRET,
        fork_fake.ISSUER,
        transport=httpx.MockTransport(lambda r: answers.pop(0)),
        sleep=waited.append,
    )
    assert client.create_wave(PERSON, key="k", name="n") == (True, {"id": "w"})
    assert waited == [5.0]

    slow = _client_for(
        lambda r: httpx.Response(429, json={"code": "rate_limited"}, headers={"Retry-After": "900"})
    )
    with pytest.raises(mike.RateLimited):
        slow.read_wave(PERSON, "0f9e8d7c-1111-4222-8333-444455556666")


def test_a_route_not_documented_as_safe_to_repeat_is_sent_once(monkeypatch) -> None:
    once = tuple(
        mike.Route(r.name, r.method, r.pattern, idempotent=False) if r.name == "merge" else r
        for r in mike.ROUTES
    )
    monkeypatch.setattr(mike, "ROUTES", once)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(502, json={})

    with pytest.raises(mike.ForkServerError):
        _client_for(handler).merge(PERSON, "0f9e8d7c-1111-4222-8333-444455556666", [])
    assert len(sent) == 1


def test_no_answer_is_unreachable_and_retried_then_raised() -> None:
    sent = []

    def handler(request):
        sent.append(request)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(mike.ForkUnreachable) as caught:
        _client_for(handler).read_wave(PERSON, "0f9e8d7c-1111-4222-8333-444455556666")
    assert caught.value.transient and len(sent) == 3


def test_the_person_is_named_as_keycloak_names_them(db, staff_user) -> None:
    """P25b (the fork's SCLU.md 16): the realm as issuer, the person's Keycloak sub, and the
    portal's own id as `sclu_uid`, so a call lands on the fork person their sign-in there makes."""
    from portal.core.services.settings import organisation_name

    person = mike.PortalPerson.of(staff_user)
    assert staff_user.keycloak_sub
    assert (person.sub, person.sclu_uid, person.sclu_role, person.email, person.name) == (
        staff_user.keycloak_sub,
        str(staff_user.pk),
        "staff",
        staff_user.email,
        staff_user.name,
    )
    assert person.sclu_org == organisation_name()
    client = mike.ForkClient(fork_fake.API_URL, fork_fake.SERVICE_SECRET, fork_fake.ISSUER)
    claims = client.person_claims(person)
    assert list(claims)[:4] == ["iss", "sub", "sclu_uid", "sclu_role"]
    assert claims["iss"] == fork_fake.ISSUER == fork.config().issuer


def test_a_person_without_a_keycloak_account_cannot_be_named(db, staff_user) -> None:
    type(staff_user).objects.filter(pk=staff_user.pk).update(keycloak_sub=None)
    staff_user.refresh_from_db()
    with pytest.raises(mike.PersonNotLinked):
        mike.PortalPerson.of(staff_user)
    refused = fork.in_words(mike.PersonNotLinked(None, "person_not_linked", "x"))
    assert "still being set up" in str(refused)


# ---- Settings ---------------------------------------------------------------------------------


def test_off_by_default_and_the_switch_alone_is_not_enough(db, settings, admin_user) -> None:
    assert Setting.objects.get(pk="drafting.fork_enabled").value is False
    assert fork.in_use() is False
    Setting.objects.filter(pk="drafting.fork_enabled").update(value=True)
    assert fork.config().enabled and not fork.in_use()  # no secrets, no addresses
    settings.DRAFTING = {
        **settings.DRAFTING,
        "API_URL": fork_fake.API_URL,
        "WEB_URL": fork_fake.WEB_URL,
        "SERVICE_SECRET": fork_fake.SERVICE_SECRET,
        "WEBHOOK_SECRET": fork_fake.WEBHOOK_SECRET,
    }
    # P25b: two secrets are all the server needs (the end-sessions one is gone).
    assert fork.in_use()
    settings.DRAFTING = {**settings.DRAFTING, "WEBHOOK_SECRET": "too-short"}
    assert not fork.in_use()
    settings.DRAFTING = {**settings.DRAFTING, "WEBHOOK_SECRET": fork_fake.WEBHOOK_SECRET}
    assert fork.in_use()
    assert _client(admin_user).get(f"{DRAFTING}/fork").json() == {
        "in_use": True,
        "web_url": fork_fake.WEB_URL,
        "web_origin": fork_fake.WEB_URL,
    }


def test_settings_edit_the_switch_and_addresses_and_only_say_whether_secrets_are_set(
    fork_configured, admin_user, staff_user
) -> None:
    admin = _client(admin_user)

    def put(key, value):
        return admin.put(
            f"/api/core/settings/{key}",
            data=json.dumps({"value": value}),
            content_type="application/json",
        )

    assert put("drafting.fork_api_url", "ftp://nope").status_code == 422
    assert put("drafting.fork_api_url", "https://user:pw@fork.test").status_code == 422
    assert put("drafting.fork_web_url", "https://fork.test/draft?x=1").status_code == 422
    assert put("drafting.fork_letterhead_id", "not-an-id").status_code == 422
    assert put("drafting.fork_api_url", "http://backend:3001/").json()["value"] == (
        "http://backend:3001"
    )
    letterhead = str(uuid.uuid4())
    assert put("drafting.fork_letterhead_id", letterhead).status_code == 200
    assert put("drafting.fork_enabled", True).status_code == 200
    staff_put = _client(staff_user).put(
        "/api/core/settings/drafting.fork_enabled",
        data=json.dumps({"value": False}),
        content_type="application/json",
    )
    assert staff_put.status_code == 403
    system = admin.get("/api/core/system")
    assert system.status_code == 200
    drafting = system.json()["drafting"]
    assert drafting["enabled"] and drafting["in_use"]
    assert drafting["api_url"] == "http://backend:3001"
    assert drafting["api_url_default"] == fork_fake.API_URL
    assert drafting["web_url"] == fork_fake.WEB_URL
    assert drafting["service_secret_set"] and drafting["webhook_secret_set"]
    assert "end_sessions_secret_set" not in drafting  # P25b: Keycloak ends the tool's sessions
    assert drafting["webhook_url"].endswith("/api/drafting/fork/webhook")
    text = system.content.decode()
    for secret in (fork_fake.SERVICE_SECRET, fork_fake.WEBHOOK_SECRET):
        assert secret not in text
    listed = admin.get("/api/core/settings").content.decode()
    assert fork_fake.SERVICE_SECRET not in listed


def test_with_the_switch_off_the_portal_drafts_as_before_and_calls_nothing(
    fork_configured, fake_fork, staff_client, fork_template, template, agency, staff_user
) -> None:
    """The secrets are set but the switch is off: no call reaches the drafting tool through stage
    1, stage 3 and stage 4, and nothing joins a draft to it."""
    for body in (
        {"from_template_id": str(fork_template.pk)},
        {"from_template_id": str(template.pk)},
        {"subject": "From scratch"},
    ):
        made = _use(staff_client, body)
        assert made["fork_wave_id"] is None
    draft = LetterDraft.objects.get(
        pk=_use(staff_client, {"from_template_id": str(template.pk)})["id"]
    )
    recipient = drafts_service.add_recipient(
        draft.pk,
        agency_id=agency.pk,
        channel="email",
        placeholder_overrides={"RECIPIENT_OFFICE": "Records Unit"},
    )
    reserved = post_json(staff_client, f"{DRAFTING}/recipients/{recipient.pk}/reserve")
    assert reserved.status_code == 200, reserved.content
    filed = post_json(
        staff_client, f"{DRAFTING}/recipients/{recipient.pk}/file", {"channel": "email"}
    )
    assert filed.status_code == 200 and filed.json()["status"] == "filed"
    assert fake_fork.calls == []
    assert staff_client.get(f"{DRAFTING}/fork").json() == {
        "in_use": False,
        "web_url": "",
        "web_origin": "",
    }
    assert post_json(staff_client, f"{DRAFTING}/drafts/{draft.pk}/fork/merge").status_code == 409


def test_pages_may_frame_only_the_portal_and_the_drafting_tool_while_in_use(
    fork_configured, db
) -> None:
    from portal.drafting.middleware import frame_src

    assert frame_src() == "frame-src 'self'"
    Setting.objects.update_or_create(key="drafting.fork_enabled", defaults={"value": True})
    assert frame_src() == f"frame-src 'self' {fork_fake.WEB_URL}"
    Setting.objects.update_or_create(
        key="drafting.fork_web_url", defaults={"value": "https://portal.example.test/draft"}
    )
    assert frame_src() == "frame-src 'self' https://portal.example.test"
    # Every HTML page the server sends carries it (the frontend's index is one); JSON does not,
    # and a page that already says its own policy keeps it.
    from django.http import HttpResponse
    from django.test import RequestFactory
    from portal.drafting.middleware import FrameSourceMiddleware

    def page(content_type: str, policy: str | None = None):
        def view(request):
            response = HttpResponse("<html></html>", content_type=content_type)
            if policy:
                response["Content-Security-Policy"] = policy
            return response

        return FrameSourceMiddleware(view)(RequestFactory().get("/docket"))

    assert page("text/html; charset=utf-8")["Content-Security-Policy"] == (
        "frame-src 'self' https://portal.example.test"
    )
    assert "Content-Security-Policy" not in page("application/json")
    assert page("text/html", "frame-ancestors 'none'")["Content-Security-Policy"] == (
        "frame-ancestors 'none'"
    )
    sign_in = Client().get("/o/authorize/")
    assert sign_in.get(
        "Content-Security-Policy", "frame-src 'self' https://portal.example.test"
    ) == ("frame-src 'self' https://portal.example.test")
    assert "Content-Security-Policy" not in Client().get("/api/core/auth/csrf")


# ---- stage 1: the wave ------------------------------------------------------------------------


def test_use_makes_the_wave_as_the_person_who_clicked(
    fork_on, staff_client, staff_user, fork_template
) -> None:
    made = _use(staff_client, {"from_template_id": str(fork_template.pk)})
    [call] = fork_on.calls
    assert (call.method, call.path) == ("POST", "/sclu/waves")
    assert call.body["key"] == made["id"]
    assert call.body["from"] == {
        "kind": "template",
        "template_id": str(fork_template.fork_template_id),
    }
    # As Keycloak names them (P25b): their Keycloak sub, and the portal id beside it.
    assert call.person["sub"] == staff_user.keycloak_sub and call.person["sclu_role"] == "staff"
    assert call.person["sclu_uid"] == str(staff_user.pk)
    assert call.body["access"] == {"people": [], "privileged": False}
    wave = fork_on.waves[made["id"]]
    draft = LetterDraft.objects.get(pk=made["id"])
    assert str(draft.fork_wave_id) == wave["id"]
    assert str(draft.fork_folder_id) == wave["folder_id"]
    assert str(draft.fork_master_document_id) == wave["master_document_id"]
    assert str(draft.fork_master_version_id) == wave["master_version_id"]
    assert made["fork_master_document_id"] == wave["master_document_id"]


def test_blank_takes_the_usual_letterhead_and_a_past_request_its_master(
    fork_on, staff_client, staff_user, agency, fork_template
) -> None:
    letterhead = str(uuid.uuid4())
    Setting.objects.update_or_create(
        key="drafting.fork_letterhead_id", defaults={"value": letterhead}
    )
    _use(staff_client, {"subject": "A letter from scratch"})
    assert fork_on.calls[-1].body["from"] == {"kind": "blank", "letterhead_id": letterhead}

    past = LetterDraft.objects.get(
        pk=_use(staff_client, {"from_template_id": str(fork_template.pk)})["id"]
    )
    recipient = drafts_service.add_recipient(past.pk, agency_id=agency.pk, channel="email")
    drafting_filing.reserve_recipient(Actor.human(staff_user), recipient.pk)
    request_id = LetterRecipient.objects.get(pk=recipient.pk).request_id
    _use(staff_client, {"from_request_id": str(request_id)})
    creates = [c for c in fork_on.calls if c.path == "/sclu/waves"]
    assert creates[-1].body["from"] == {
        "kind": "master",
        "document_id": str(past.fork_master_document_id),
    }


def test_a_source_only_the_portal_has_stays_in_the_portals_editor(
    fork_on, staff_client, template
) -> None:
    made = _use(staff_client, {"from_template_id": str(template.pk)})
    assert made["fork_wave_id"] is None and fork_on.calls == []


@pytest.mark.parametrize(
    ("step", "status"),
    [
        ((409, {"code": "template_archived", "detail": "Template archived"}), 422),
        ((404, {"detail": "Template not found"}), 422),
        (httpx.ConnectError("down"), 503),
        ((403, {"code": "service_calls_off", "detail": "off"}), 422),
    ],
)
def test_a_wave_the_fork_refuses_keeps_no_draft(
    fork_on, staff_client, fork_template, step, status
) -> None:
    fork_on.script = [step] * 3
    before = LetterDraft.objects.count()
    answer = post_json(
        staff_client, f"{DRAFTING}/drafts", {"from_template_id": str(fork_template.pk)}
    )
    assert answer.status_code == status, answer.content
    assert "drafting tool" in answer.json()["detail"]
    assert LetterDraft.objects.count() == before


# ---- stage 3: reserve and the merge -----------------------------------------------------------


def test_reserve_refuses_a_letter_with_a_blank_and_reserves_nothing(
    fork_draft, staff_client, agency, fork_on
) -> None:
    recipient = drafts_service.add_recipient(fork_draft.pk, agency_id=agency.pk, channel="email")
    fork_on.unfilled = ["RECIPIENT_OFFICE"]
    answer = post_json(staff_client, f"{DRAFTING}/recipients/{recipient.pk}/reserve")
    assert answer.status_code == 422
    assert "RECIPIENT_OFFICE" in answer.json()["detail"]
    assert LetterRecipient.objects.get(pk=recipient.pk).request_id is None
    preview = fork_on.calls[-1]
    assert preview.path.endswith("/preview") and preview.body["format"] == "docx"
    assert preview.body["values"]["AGENCY_FULL"] == agency.name


def test_reserve_merges_with_the_reserved_values_and_writes_the_requests_words(
    fork_draft, staff_client, staff_user, agency, fork_on
) -> None:
    fork_on.master_words = [
        "To {{AGENCY_FULL}}, {{RECIPIENT_OFFICE}}",
        "Our reference {{TRACKING_NUMBER}}; reply to {{REPLY_TO}}.",
    ]
    master = fork_on.documents[str(fork_draft.fork_master_document_id)]
    master.versions[-1] = (master.versions[-1][0], list(fork_on.master_words))
    recipient = drafts_service.add_recipient(fork_draft.pk, agency_id=agency.pk, channel="email")
    answer = post_json(staff_client, f"{DRAFTING}/recipients/{recipient.pk}/reserve")
    assert answer.status_code == 200, answer.content
    row = LetterRecipient.objects.get(pk=recipient.pk)
    request = Request.objects.get(pk=row.request_id)
    merge = next(c for c in fork_on.calls if c.path.endswith("/merge"))
    [entry] = merge.body["documents"]
    assert entry["key"] == str(recipient.pk)
    values = entry["values"]
    assert values["AGENCY_FULL"] == agency.name and values["CHANNEL_METHOD"] == "EMAIL"
    assert values["TRACKING_NUMBER"] == request.tracking_number
    assert values["REPLY_TO"] == request.inbound_alias_email
    assert merge.person["sclu_uid"] == str(staff_user.pk)
    assert merge.person["sub"] == staff_user.keycloak_sub
    assert row.fork_document_id is not None and row.fork_version_number == 1
    assert request.request_text == (
        f"To {agency.name}, Records Unit\nOur reference {request.tracking_number}; "
        f"reply to {request.inbound_alias_email}."
    )
    audit = AuditLog.objects.get(entity_id=request.pk, field_name="requests.request_text")
    assert audit.note == fork.TEXT_NOTE
    assert answer.json()["fork_document_id"] == str(row.fork_document_id)


def test_a_failed_merge_leaves_the_reservation_and_is_made_again(
    fork_draft, staff_client, agency, fork_on
) -> None:
    recipient = drafts_service.add_recipient(fork_draft.pk, agency_id=agency.pk, channel="email")
    fork_on.script = [
        None,
        httpx.ConnectError("down"),
        httpx.ConnectError("down"),
        httpx.ConnectError("down"),
    ]
    answer = post_json(staff_client, f"{DRAFTING}/recipients/{recipient.pk}/reserve")
    assert answer.status_code == 200, answer.content
    row = LetterRecipient.objects.get(pk=recipient.pk)
    assert row.request_id is not None and row.fork_document_id is None
    again = post_json(staff_client, f"{DRAFTING}/drafts/{fork_draft.pk}/fork/merge")
    assert again.status_code == 200 and again.json() == {"made": [str(recipient.pk)], "failed": []}
    assert LetterRecipient.objects.get(pk=recipient.pk).fork_document_id is not None
    # Made already: nothing left to make, and an agency letter is never merged over.
    assert (
        post_json(staff_client, f"{DRAFTING}/drafts/{fork_draft.pk}/fork/merge").json()["made"]
        == []
    )


def test_a_fork_letter_is_not_reserved_while_the_switch_is_off(
    fork_draft, staff_client, agency
) -> None:
    recipient = drafts_service.add_recipient(fork_draft.pk, agency_id=agency.pk, channel="email")
    Setting.objects.filter(pk="drafting.fork_enabled").update(value=False)
    answer = post_json(staff_client, f"{DRAFTING}/recipients/{recipient.pk}/reserve")
    assert answer.status_code == 409 and "switched off" in answer.json()["detail"]
    assert LetterRecipient.objects.get(pk=recipient.pk).request_id is None


# ---- stage 4: filing --------------------------------------------------------------------------


def _reserved(fork_draft, staff_user, agency, channel="mail"):
    recipient = drafts_service.add_recipient(fork_draft.pk, agency_id=agency.pk, channel=channel)
    drafting_filing.reserve_recipient(Actor.human(staff_user), recipient.pk)
    return LetterRecipient.objects.get(pk=recipient.pk)


def test_filing_writes_the_changed_letters_words_first_then_files_as_before(
    fork_draft, staff_client, staff_user, agency, fork_on
) -> None:
    row = _reserved(fork_draft, staff_user, agency)
    words_before = Request.objects.get(pk=row.request_id).request_text
    version = fork_on.revise(str(row.fork_document_id), ["An edited letter for this agency only."])
    answer = post_json(staff_client, f"{DRAFTING}/recipients/{row.pk}/file", {"channel": "mail"})
    assert answer.status_code == 200, answer.content
    request = Request.objects.get(pk=row.request_id)
    assert request.current_status_id == "filed"
    assert request.request_text == "An edited letter for this agency only." != words_before
    row.refresh_from_db()
    assert str(row.fork_version_id) == version and row.fork_version_number == 2
    assert row.status == LetterRecipient.FILED
    assert (
        AuditLog.objects.filter(entity_id=request.pk, field_name="requests.request_text").count()
        == 2
    )


def test_filing_an_unchanged_letter_writes_nothing_more(
    fork_draft, staff_client, staff_user, agency, fork_on
) -> None:
    row = _reserved(fork_draft, staff_user, agency)
    edits = AuditLog.objects.filter(
        entity_id=row.request_id, field_name="requests.request_text"
    ).count()
    assert (
        post_json(
            staff_client, f"{DRAFTING}/recipients/{row.pk}/file", {"channel": "mail"}
        ).status_code
        == 200
    )
    assert (
        AuditLog.objects.filter(
            entity_id=row.request_id, field_name="requests.request_text"
        ).count()
        == edits
    )


def test_the_letter_file_comes_from_the_fork_as_the_person_asking(
    fork_draft, staff_client, staff_user, agency, fork_on
) -> None:
    row = _reserved(fork_draft, staff_user, agency)
    pdf = staff_client.get(f"{DRAFTING}/drafts/{fork_draft.pk}/fork/file?recipient_id={row.pk}")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert pdf["Content-Disposition"].endswith('.pdf"')
    word = staff_client.get(
        f"{DRAFTING}/drafts/{fork_draft.pk}/fork/file?recipient_id={row.pk}&format=docx"
    )
    assert word.status_code == 200 and word.content.startswith(b"PK")
    master = staff_client.get(f"{DRAFTING}/drafts/{fork_draft.pk}/fork/file")
    assert master.status_code == 200
    assert (
        fork_on.calls[-1].path == f"/single-documents/{fork_draft.fork_master_document_id}/display"
    )


def test_an_email_filing_carries_the_forks_pdf_and_files_that_version(
    fork_draft, staff_user, agency, fork_on, monkeypatch, media
) -> None:
    from portal.mail.models import MessageKind, MessageStatus
    from portal.mail.services import compose, transport
    from portal.mail.services import send as send_service

    posted = []
    monkeypatch.setattr(
        transport,
        "_post",
        lambda token, payload: (
            posted.append(payload) or (200, {"ErrorCode": 0, "MessageID": "pm-p21c"})
        ),
    )
    monkeypatch.setattr(send_service, "_defer", lambda message_id, send_after: None)
    row = _reserved(fork_draft, staff_user, agency, channel="email")
    fork_on.revise(str(row.fork_document_id), ["The letter as sent."])
    actor = Actor.human(staff_user)
    message = compose.create_draft(
        actor,
        row.request_id,
        kind=MessageKind.FILING,
        subject="Our request",
        body_text="Please find our request attached.",
        to=[agency.default_request_email],
        letter_recipient_id=row.pk,
        attach_letter=True,
    )
    send_service.queue(actor, message.pk, delay_seconds=0)
    done = send_service.deliver(message.pk)
    assert done.outcome == "sent", done.message.error
    [payload] = posted
    import base64

    [attached] = [
        a for a in payload["Attachments"] if a["Name"] == send_service.letter_name(message)
    ]
    assert base64.b64decode(attached["Content"]) == fork_fake.pdf("The letter as sent.")
    request = Request.objects.get(pk=row.request_id)
    assert request.current_status_id == "filed"
    assert request.request_text == "The letter as sent."
    assert done.message.status == MessageStatus.SENT


def test_an_email_filing_waits_while_the_fork_is_down(
    fork_draft, staff_user, agency, fork_on, monkeypatch, media
) -> None:
    from portal.mail.models import MessageKind
    from portal.mail.services import compose, transport
    from portal.mail.services import send as send_service

    monkeypatch.setattr(send_service, "_defer", lambda message_id, send_after: None)
    row = _reserved(fork_draft, staff_user, agency, channel="email")
    actor = Actor.human(staff_user)
    message = compose.create_draft(
        actor,
        row.request_id,
        kind=MessageKind.FILING,
        subject="Our request",
        to=[agency.default_request_email],
        letter_recipient_id=row.pk,
        attach_letter=True,
    )
    send_service.queue(actor, message.pk, delay_seconds=0)
    fork_on.script = [httpx.ConnectError("down")] * 3
    with pytest.raises(transport.TransportError) as caught:
        send_service.deliver(message.pk)
    assert caught.value.permanent is False


# ---- the webhooks -----------------------------------------------------------------------------


def _deliver(raw: bytes, *, ts: int | None = None, secret: str = fork_fake.WEBHOOK_SECRET, **extra):
    headers = webhook_headers(raw, ts=int(time.time()) if ts is None else ts, secret=secret)
    headers.update(extra)
    return Client().post(
        f"{DRAFTING}/fork/webhook", data=raw, content_type="application/json", **headers
    )


def test_a_signed_version_event_moves_the_mirror_once(
    fork_draft, staff_user, agency, fork_on
) -> None:
    row = _reserved(fork_draft, staff_user, agency)
    version = str(uuid.uuid4())
    raw = webhook(
        "document.version_created",
        {
            "document_id": str(row.fork_document_id),
            "version_id": version,
            "version_number": 2,
            "source": "user_upload",
            "content_sha256": "ab" * 32,
            "project_id": str(uuid.uuid4()),
            "folder_id": str(uuid.uuid4()),
            "library_kind": "file",
        },
    )
    first = _deliver(raw)
    assert first.status_code == 200 and first.json() == {"status": "applied"}
    row.refresh_from_db()
    assert (str(row.fork_version_id), row.fork_version_number) == (version, 2)
    # The same delivery again (a retry, or a replay inside the window): acknowledged, not applied.
    LetterRecipient.objects.filter(pk=row.pk).update(fork_version_number=1)
    again = _deliver(raw)
    assert again.status_code == 200 and again.json() == {"status": "duplicate"}
    assert LetterRecipient.objects.get(pk=row.pk).fork_version_number == 1
    assert ForkEvent.objects.filter(id=json.loads(raw)["id"]).count() == 1


def test_an_older_version_arriving_late_does_not_move_it_back(
    fork_draft, staff_user, agency, fork_on
) -> None:
    row = _reserved(fork_draft, staff_user, agency)
    LetterRecipient.objects.filter(pk=row.pk).update(fork_version_number=3)
    raw = webhook(
        "document.version_created",
        {
            "document_id": str(row.fork_document_id),
            "version_id": str(uuid.uuid4()),
            "version_number": 2,
        },
    )
    assert _deliver(raw).json() == {"status": "applied"}
    assert LetterRecipient.objects.get(pk=row.pk).fork_version_number == 3


def test_a_merged_event_fills_in_a_letter_the_merge_answer_lost(
    fork_draft, staff_user, agency, fork_on
) -> None:
    recipient = drafts_service.add_recipient(fork_draft.pk, agency_id=agency.pk, channel="mail")
    document = str(uuid.uuid4())
    raw = webhook(
        "document.merged",
        {
            "document_id": document,
            "version_id": str(uuid.uuid4()),
            "version_number": 1,
            "key": str(recipient.pk),
            "status": "created",
            "master_document_id": str(fork_draft.fork_master_document_id),
            "master_version_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "folder_id": str(uuid.uuid4()),
            "unfilled_count": 0,
        },
    )
    assert _deliver(raw).status_code == 200
    assert str(LetterRecipient.objects.get(pk=recipient.pk).fork_document_id) == document
    # Another master's merge naming this recipient changes nothing.
    other = webhook(
        "document.merged",
        {
            "document_id": str(uuid.uuid4()),
            "version_number": 1,
            "key": str(recipient.pk),
            "master_document_id": str(uuid.uuid4()),
        },
    )
    _deliver(other)
    assert str(LetterRecipient.objects.get(pk=recipient.pk).fork_document_id) == document


@pytest.mark.parametrize(
    "case", ["no secret", "bad signature", "stale", "unsigned", "mismatch", "not json"]
)
def test_a_delivery_not_to_be_trusted_is_refused_and_writes_nothing(
    case, fork_configured, db, settings
) -> None:
    raw = webhook("document.created", {"document_id": str(uuid.uuid4())})
    if case == "no secret":
        settings.DRAFTING = {**settings.DRAFTING, "WEBHOOK_SECRET": ""}
        answer = _deliver(raw)
        assert answer.status_code == 503
    elif case == "bad signature":
        answer = _deliver(raw, secret="x" * 64)
        assert answer.status_code == 401
    elif case == "stale":
        answer = _deliver(raw, ts=int(time.time()) - 310)
        assert answer.status_code == 401
    elif case == "unsigned":
        answer = Client().post(
            f"{DRAFTING}/fork/webhook", data=raw, content_type="application/json"
        )
        assert answer.status_code == 401
    elif case == "mismatch":
        answer = _deliver(raw, HTTP_X_SCLU_EVENT="document.merged")
        assert answer.status_code == 400
    else:
        import hashlib
        import hmac

        bad = b"not json"
        ts = str(int(time.time()))
        signature = hmac.new(
            fork_fake.WEBHOOK_SECRET.encode(), f"{ts}.".encode() + bad, hashlib.sha256
        ).hexdigest()
        answer = Client().post(
            f"{DRAFTING}/fork/webhook",
            data=bad,
            content_type="application/json",
            HTTP_X_SCLU_TIMESTAMP=ts,
            HTTP_X_SCLU_SIGNATURE=f"v1={signature}",
        )
        assert answer.status_code == 400
    assert ForkEvent.objects.count() == 0


def test_the_window_is_five_minutes_either_way(fork_configured, db) -> None:
    # 290 and 310, not 299 and 301: a second can tick between signing and checking.
    for skew in (-290, 290):
        raw = webhook("document.created", {"document_id": str(uuid.uuid4())})
        assert _deliver(raw, ts=int(time.time()) + skew).status_code == 200
    raw = webhook("document.created", {"document_id": str(uuid.uuid4())})
    assert _deliver(raw, ts=int(time.time()) + 310).status_code == 401


def test_comments_and_checks_feed_the_suggestions_badge_and_the_files_tab(
    fork_draft, staff_client, staff_user, agency, fork_on
) -> None:
    row = _reserved(fork_draft, staff_user, agency)
    doc = str(row.fork_document_id)
    for _ in range(3):
        _deliver(webhook("comment.created", {"document_id": doc, "comment_id": uuid.uuid4().hex}))
    _deliver(webhook("comment.resolved", {"document_id": doc}))
    _deliver(
        webhook(
            "verification.completed",
            {"document_id": doc, "checked": 2, "verified": 2},
            created_at="2026-10-17T13:00:00.000Z",
        )
    )
    _deliver(webhook("comment.created", {"document_id": str(fork_draft.fork_master_document_id)}))
    detail = staff_client.get(f"{DRAFTING}/drafts/{fork_draft.pk}").json()
    assert detail["fork_suggestions"] == 1
    assert detail["recipients"][0]["fork_suggestions"] == 2
    files = staff_client.get(f"{DRAFTING}/requests/{row.request_id}/fork/letter").json()
    assert files["in_use"] is True
    [letter] = files["letters"]
    assert letter["recipient_id"] == str(row.pk) and letter["document_id"] == doc
    assert letter["open_suggestions"] == 2 and letter["filed"] is False
    assert letter["verification"]["checked"] == 2 and letter["verification"]["verified"] == 2
    assert letter["filename"].endswith(".pdf")


def test_the_files_tab_is_the_requests_own_scope(fork_draft, staff_user, agency, fork_on) -> None:
    row = _reserved(fork_draft, staff_user, agency)
    outsider = _person("limited_attorney")
    assert (
        _client(outsider).get(f"{DRAFTING}/requests/{row.request_id}/fork/letter").status_code
        == 403
    )
    give(row.request_id, outsider.pk)
    assert (
        _client(outsider).get(f"{DRAFTING}/requests/{row.request_id}/fork/letter").status_code
        == 200
    )


# ---- the Templates frame's mirror -----------------------------------------------------------


def test_a_template_made_in_the_frame_is_mirrored_for_stage_1(fork_on, staff_client, db) -> None:
    template_id = str(uuid.uuid4())
    made = post_json(
        staff_client,
        f"{DRAFTING}/fork/templates",
        {"template_id": template_id, "name": "Synthetic letter", "event": "created"},
    )
    assert made.status_code == 200 and made.json()["fork_template_id"] == template_id
    renamed = post_json(
        staff_client,
        f"{DRAFTING}/fork/templates",
        {"template_id": template_id, "name": "Renamed", "archived": True, "event": "archived"},
    )
    assert renamed.json()["name"] == "Renamed" and renamed.json()["archived_at"] is not None
    assert LetterTemplate.objects.filter(fork_template_id=template_id).count() == 1
    intern = _person("intern")
    assert (
        post_json(
            _client(intern), f"{DRAFTING}/fork/templates", {"template_id": template_id, "name": "x"}
        ).status_code
        == 403
    )


# ---- ending sessions in the fork: Keycloak's now (P25b) ------------------------------------------


@pytest.mark.parametrize("change", ["sign-out", "deactivate", "role", "keycloak"])
def test_the_portal_sends_the_tool_nothing_when_sign_ins_end(
    change, fork_on, admin_user, django_capture_on_commit_callbacks
) -> None:
    """Signing out, a deactivation, a role change, Keycloak's own logout: the portal no longer
    calls the tool (its end-sessions call is retired). Keycloak's back-channel logout reaches the
    tool instead, and the tool reads a changed role from Keycloak itself (the fork's SCLU.md 16)."""
    from portal.core.services import signins
    from portal.jobs.app import app as jobs_app

    person = _person("staff")
    with django_capture_on_commit_callbacks(execute=True):
        if change == "sign-out":
            assert _client(person).post("/api/core/auth/logout").status_code == 200
        elif change == "deactivate":
            identity.deactivate_user(admin_user, person)
        elif change == "role":
            identity.set_role(admin_user, person, "limited_attorney")
        else:
            signins.end_sign_ins(person.pk, signins.KEYCLOAK_LOGOUT)
    assert "drafting.end_fork_sessions" not in jobs_app.tasks
    assert _jobs("drafting.end_fork_sessions") == []
    assert not hasattr(mike, "end_sessions") and not hasattr(fork, "end_sessions")
    assert not [c for c in fork_on.calls if "end-sessions" in c.path]


def test_deactivating_ends_the_persons_keycloak_sessions_which_reaches_the_tool(
    db, admin_user, django_capture_on_commit_callbacks
) -> None:
    """What the tool now relies on: a deactivation owes Keycloak "end every session" (P25a), and
    Keycloak tells each application signed in, the drafting tool among them, by back-channel
    logout. A role change to requester switches the account off, which ends them too."""
    from portal.core.models import KeycloakChange

    person = _person("intern")
    identity.deactivate_user(admin_user, person)
    owed = list(KeycloakChange.objects.filter(user_id=person.pk).values_list("action", flat=True))
    assert KeycloakChange.END_SESSIONS in owed


# ---- a wave's access list (SCLU.md 15) ----------------------------------------------------------


def _subs(access: fork.WaveAccess) -> set[str]:
    """The portal ids on a list (each person also carries their Keycloak sub)."""
    return {p.sclu_uid for p in access.people}


def test_the_list_is_the_portals_own_rule(fork_on, staff_user, agency, db) -> None:
    owner = Actor.human(staff_user)
    limited_given = _person("limited_attorney", "limited-given")
    limited_not = _person("limited_attorney", "limited-not")
    intern = _person("intern")
    other_staff = _person("staff", "other-staff")
    admin = _person("admin")
    requester = _person("requester")
    # A draft started from an ordinary request given to one limited-scope attorney.
    request = requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=requester.pk,
        subject="Ordinary",
        request_text="x",
    )
    give(request.pk, limited_given.pk)
    draft = drafts_service.create_draft(
        from_request_id=request.pk, created_by_user_id=staff_user.pk
    )
    access = fork.wave_access(draft)
    assert access.privileged is False
    assert _subs(access) == {str(limited_given.pk)}  # staff and admins are the fork's broad roles
    assert str(requester.pk) not in _subs(access) and str(limited_not.pk) not in _subs(access)
    assert str(intern.pk) not in _subs(access)
    for person in (limited_given, limited_not, intern, other_staff, admin):
        from portal.drafting.api.permissions import draft_visible_to
        from portal.records.services.access import Viewer

        role = identity.records_role_of(person)
        seen = draft_visible_to(Viewer(person.pk, role), draft)
        in_list = str(person.pk) in _subs(access)
        broad = role in ("admin", "staff", "staff_attorney")
        assert seen == (in_list or broad), (role, seen, in_list)


def test_a_person_not_in_keycloak_yet_is_left_off_until_linked(
    fork_on, staff_user, agency, db, django_capture_on_commit_callbacks
) -> None:
    """P25b: the tool names people as Keycloak does, so someone the portal has not synced to
    Keycloak yet cannot be on a list (nor signed in there). Linking their account (the sync job's
    `_link`) sends the lists again."""
    from portal.core.keycloak import admin as kc_admin

    owner = Actor.human(staff_user)
    limited = _person("limited_attorney", "limited-unlinked")
    type(limited).objects.filter(pk=limited.pk).update(keycloak_sub=None)
    limited.refresh_from_db()
    requester = _person("requester")
    request = requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=requester.pk,
        subject="Ordinary",
        request_text="x",
    )
    give(request.pk, limited.pk)
    draft = drafts_service.create_draft(
        from_request_id=request.pk, created_by_user_id=staff_user.pk
    )
    LetterDraft.objects.filter(pk=draft.pk).update(
        fork_wave_id=uuid.uuid4(), fork_master_document_id=uuid.uuid4()
    )
    assert str(limited.pk) not in _subs(fork.wave_access(draft))
    before = len(_jobs("drafting.sync_wave_access"))
    with django_capture_on_commit_callbacks(execute=True):
        kc_admin._link(limited, "kc-now-linked")
    assert len(_jobs("drafting.sync_wave_access")) > before
    access = fork.wave_access(draft)
    assert str(limited.pk) in _subs(access)
    assert {p.sub for p in access.people if p.sclu_uid == str(limited.pk)} == {"kc-now-linked"}
    # A Keycloak account re-created (a new sub): the lists go again, naming the new sub.
    with connection.cursor() as cur:
        cur.execute("delete from procrastinate_jobs where task_name = 'drafting.sync_wave_access'")
    with django_capture_on_commit_callbacks(execute=True):
        kc_admin._link(limited, "kc-re-created")
    assert len(_jobs("drafting.sync_wave_access")) == 1
    access = fork.wave_access(draft)
    assert {p.sub for p in access.people if p.sclu_uid == str(limited.pk)} == {"kc-re-created"}


def test_the_list_is_signed_only_as_someone_keycloak_knows(fork_on, staff_user, db) -> None:
    """P25b: the tool names the signer by their Keycloak sub, so an administrator without one is
    passed over for one who has it (never a list dropped for want of a signer)."""
    from portal.drafting.services.fork import _signer_for_access

    unlinked = _person("admin", "admin-unlinked")
    type(unlinked).objects.filter(pk=unlinked.pk).update(keycloak_sub=None)
    _person("admin", "admin-linked")

    class _Draft:
        created_by_user_id = staff_user.pk

    signer = _signer_for_access(_Draft(), unlinked.pk)
    assert signer is not None and signer.keycloak_sub and signer.pk != unlinked.pk
    assert mike.PortalPerson.of(signer).sub == signer.keycloak_sub


def test_an_intern_sees_the_requests_interns_made(fork_on, staff_user, agency, db) -> None:
    intern_author = _person("intern", "intern-author")
    other_intern = _person("intern", "other-intern")
    request = requests_service.reserve_request(
        Actor.human(intern_author),
        agency_id=agency.pk,
        requester_user_id=intern_author.pk,
        subject="An intern's request",
        request_text="x",
    )
    draft = drafts_service.create_draft(
        from_request_id=request.pk, created_by_user_id=intern_author.pk
    )
    assert {str(intern_author.pk), str(other_intern.pk)} <= _subs(fork.wave_access(draft))


def test_a_privileged_wave_lists_only_who_the_portal_lets_in(
    fork_on, staff_user, agency, db
) -> None:
    creator = staff_user
    other_staff = _person("staff", "other-staff")
    admin = _person("admin")
    draft = drafts_service.create_draft(
        subject="Sealed", sensitivity="privileged", created_by_user_id=creator.pk
    )
    access = fork.wave_access(draft)
    assert access.privileged is True
    assert _subs(access) == {str(creator.pk)}  # admins are let in by the fork; other staff are not
    assert str(other_staff.pk) not in _subs(access) and str(admin.pk) not in _subs(access)
    # A privileged request the draft names makes the wave privileged too.
    request = requests_service.reserve_request(
        Actor.human(creator),
        agency_id=agency.pk,
        requester_user_id=creator.pk,
        subject="Sealed request",
        request_text="x",
        sensitivity="privileged",
    )
    named = drafts_service.create_draft(
        from_request_id=request.pk, sensitivity="internal", created_by_user_id=creator.pk
    )
    assert fork.wave_access(named).privileged is True


def test_a_requester_is_never_on_a_list(fork_on, staff_user, agency, db) -> None:
    requester = _person("requester")
    request = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=requester.pk,
        subject="Theirs",
        request_text="x",
    )
    draft = drafts_service.create_draft(
        from_request_id=request.pk, created_by_user_id=staff_user.pk
    )
    assert all(p.sclu_role != "requester" for p in fork.wave_access(draft).people)
    assert str(requester.pk) not in _subs(fork.wave_access(draft))


def test_the_first_list_goes_with_the_wave(fork_on, staff_user, agency) -> None:
    limited = _person("limited_attorney")
    request = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Given",
        request_text="x",
    )
    give(request.pk, limited.pk)
    made = _use(_client(staff_user), {"from_request_id": str(request.pk)})
    assert made["fork_wave_id"] is None  # written in the portal: no master to start from
    past_draft = LetterDraft.objects.get(pk=made["id"])
    LetterDraft.objects.filter(pk=past_draft.pk).update(
        fork_wave_id=uuid.uuid4(), fork_master_document_id=uuid.uuid4()
    )
    recipient = drafts_service.add_recipient(past_draft.pk, agency_id=agency.pk, channel="mail")
    LetterRecipient.objects.filter(pk=recipient.pk).update(request_id=request.pk)
    again = _use(_client(staff_user), {"from_request_id": str(request.pk)})
    create = [c for c in fork_on.calls if c.path == "/sclu/waves"][-1]
    assert create.body["key"] == again["id"]
    assert [p["sclu_uid"] for p in create.body["access"]["people"]] == [str(limited.pk)]
    assert [p["sub"] for p in create.body["access"]["people"]] == [f"kc-{limited.pk}"]
    assert create.body["access"]["people"][0]["sclu_role"] == "limited_attorney"


@pytest.mark.parametrize(
    "change", ["role", "grant", "deactivate", "sensitivity", "draft_sensitivity"]
)
def test_what_moves_who_may_see_a_request_resends_its_waves_list(
    change, fork_draft, staff_user, admin_user, agency, django_capture_on_commit_callbacks
) -> None:
    from portal.records.services import edits as edits_service

    from tests.p20h_helpers import giver

    row = _reserved(fork_draft, staff_user, agency)  # the draft names its request now
    person = _person("limited_attorney")
    giver()  # made now, so making the giving administrator is not what is counted below
    with connection.cursor() as cur:
        cur.execute("delete from procrastinate_jobs where task_name = 'drafting.sync_wave_access'")
    with django_capture_on_commit_callbacks(execute=True):
        if change == "role":
            identity.set_role(admin_user, person, "intern")
        elif change == "grant":
            give(row.request_id, person.pk)
        elif change == "deactivate":
            identity.deactivate_user(admin_user, person)
        elif change == "sensitivity":
            edits_service.edit_with_audit(
                Actor.human(staff_user),
                "requests",
                row.request_id,
                "sensitivity",
                "privileged",
                "Sealed.",
            )
        else:
            _client(staff_user).patch(
                f"{DRAFTING}/drafts/{fork_draft.pk}",
                data=json.dumps({"sensitivity": "privileged"}),
                content_type="application/json",
            )
    jobs = _jobs("drafting.sync_wave_access")
    assert [j["args"]["draft_id"] for j in jobs] == [str(fork_draft.pk)]
    assert jobs[0]["lock"] == f"wave-access:{fork_draft.pk}"


def test_syncs_are_coalesced_per_wave_and_the_job_puts_the_whole_list(
    fork_draft, staff_user, agency, fork_on
) -> None:
    with connection.cursor() as cur:
        cur.execute("delete from procrastinate_jobs where task_name = 'drafting.sync_wave_access'")
    assert fork.enqueue_access_sync([fork_draft.pk]) == 1
    assert fork.enqueue_access_sync([fork_draft.pk]) == 0
    assert len(_jobs("drafting.sync_wave_access")) == 1
    row = _reserved(fork_draft, staff_user, agency)
    limited = _person("limited_attorney")
    give(row.request_id, limited.pk)
    answer = fork.push_wave_access(fork_draft.pk)
    assert answer["people"] == 1 and answer["added"] == 1
    put = fork_on.calls[-1]
    assert (put.method, put.path) == ("PUT", f"/sclu/waves/{fork_draft.fork_wave_id}/access")
    assert put.body == {
        "people": [fork.client().person_claims(mike.PortalPerson.of(limited))],
        "privileged": False,
    }
    assert put.person["sclu_role"] == "admin"  # signed as an administrator
    # The same list again changes nothing; a refused list is logged, not retried.
    assert fork.push_wave_access(fork_draft.pk)["added"] == 0
    fork_on.script = [
        (
            422,
            {
                "code": "access_person_refused",
                "reason": "portal_role_refused",
                "index": 0,
                "detail": "x",
            },
        )
    ]
    assert fork.push_wave_access(fork_draft.pk) is None
    fork_on.script = [httpx.ConnectError("down")] * 3
    with pytest.raises(mike.ForkUnreachable):
        fork.push_wave_access(fork_draft.pk)


def test_nothing_is_synced_while_the_switch_is_off(
    fork_draft, staff_user, admin_user, django_capture_on_commit_callbacks
) -> None:
    Setting.objects.filter(pk="drafting.fork_enabled").update(value=False)
    with connection.cursor() as cur:
        cur.execute("delete from procrastinate_jobs where task_name = 'drafting.sync_wave_access'")
    with django_capture_on_commit_callbacks(execute=True):
        identity.set_role(admin_user, _person("staff"), "intern")
    assert _jobs("drafting.sync_wave_access") == []
    assert fork.push_wave_access(fork_draft.pk) is None


# ---- the words of a .docx -------------------------------------------------------------------


def _docx(
    document_xml: str, *, numbering: str | None = None, footnotes: str | None = None
) -> bytes:
    import io
    import zipfile

    w = f'xmlns:w="{fork_fake.W}"'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "word/document.xml", f"<w:document {w}><w:body>{document_xml}</w:body></w:document>"
        )
        if numbering:
            archive.writestr("word/numbering.xml", f"<w:numbering {w}>{numbering}</w:numbering>")
        if footnotes:
            archive.writestr("word/footnotes.xml", f"<w:footnotes {w}>{footnotes}</w:footnotes>")
    return out.getvalue()


def test_the_words_of_a_docx_with_numbering_footnotes_tables_and_tracked_changes() -> None:
    numbering = (
        '<w:abstractNum w:abstractNumId="0">'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        '<w:lvlText w:val="%1."/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/>'
        '<w:lvlText w:val="(%2)"/></w:lvl>'
        '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
    )

    def item(text, level=0):
        return (
            f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="1"/></w:numPr></w:pPr>'
            f"<w:r><w:t>{text}</w:t></w:r></w:p>"
        )

    body = (
        "<w:p><w:r><w:t>To the </w:t></w:r><w:ins><w:r><w:t>Records </w:t></w:r></w:ins>"
        "<w:del><w:r><w:delText>Old </w:delText></w:r></w:del><w:r><w:t>Unit</w:t></w:r></w:p>"
        + item("First ask")
        + item("A part of it", 1)
        + item("Second ask")
        + '<w:p><w:r><w:t>Cited</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r></w:p>'
        + "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t><w:tab/><w:t>two</w:t></w:r></w:p>"
        + "</w:tc></w:tr></w:tbl>"
        + "<w:p/><w:p/><w:p><w:r><w:t>Sincerely,</w:t><w:br/><w:t>A. Person</w:t></w:r></w:p>"
    )
    footnotes = (
        '<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:t>---</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="2"><w:p><w:r><w:t>Test Code section 1 (synthetic).</w:t></w:r></w:p>'
        "</w:footnote>"
    )
    words = docx_text.extract(_docx(body, numbering=numbering, footnotes=footnotes))
    assert words == (
        "To the Records Unit\n1. First ask\n(a) A part of it\n2. Second ask\nCited[1]\n"
        "Cell\ttwo\n\nSincerely,\nA. Person\n\n[1] Test Code section 1 (synthetic)."
    )


def test_what_is_not_a_docx_is_refused() -> None:
    with pytest.raises(docx_text.NotALetter):
        docx_text.extract(b"%PDF-1.6")
    with pytest.raises(docx_text.NotALetter):
        docx_text.extract(_docx("<w:p>"))


def test_a_fake_fork_check_the_signatures_too() -> None:
    """The stand-in refuses a call signed with another secret, as the fork's gate does."""
    fake = FakeFork()
    client = mike.ForkClient(
        fork_fake.API_URL, "z" * 48, fork_fake.ISSUER, transport=fake.transport
    )
    with pytest.raises(mike.InvalidSignature):
        client.read_wave(PERSON, "0f9e8d7c-1111-4222-8333-444455556666")


# ---- as the roles the servers really have ------------------------------------------------------


@pytest.mark.app_role
def test_the_webhook_works_as_portal_app_with_no_context(
    fork_draft, staff_user, agency, fork_on
) -> None:
    """The web server's connection gets no row-level context for a call with no session (the
    webhook's); everything it touches is the drafting schema's, which portal_app may write."""
    row = _reserved(fork_draft, staff_user, agency)
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.context', '', true)")
    raw = webhook(
        "document.version_replaced",
        {
            "document_id": str(row.fork_document_id),
            "version_id": str(row.fork_version_id),
            "version_number": 1,
            "content_sha256": "cd" * 32,
        },
    )
    assert _deliver(raw).json() == {"status": "applied"}
    assert ForkEvent.objects.filter(document_id=row.fork_document_id).count() == 1


@pytest.mark.app_role
def test_the_access_job_works_as_portal_app(fork_draft, staff_user, agency, fork_on) -> None:
    """The worker runs as portal_app with the system context (records_db.0017)."""
    row = _reserved(fork_draft, staff_user, agency)
    limited = _person("limited_attorney")
    give(row.request_id, limited.pk)
    answer = fork.push_wave_access(fork_draft.pk)
    assert answer is not None and answer["people"] == 1


def test_the_webhook_needs_no_session_and_no_csrf_token(fork_configured, db) -> None:
    """The fork's server calls it: its signature is its whole authentication."""
    raw = webhook("document.created", {"document_id": str(uuid.uuid4())})
    headers = webhook_headers(raw, ts=int(time.time()))
    strict = Client(enforce_csrf_checks=True)
    answer = strict.post(
        f"{DRAFTING}/fork/webhook", data=raw, content_type="application/json", **headers
    )
    assert answer.status_code == 200 and answer.json() == {"status": "applied"}
    assert ForkEvent.objects.filter(id=json.loads(raw)["id"]).exists()


def test_an_interns_resubmission_reads_the_letter_again_from_the_drafting_tool(
    fork_draft, staff_user, agency, fork_on
) -> None:
    """P20h's "Edit letter" then submit again freezes the letter again; for a letter written in
    the drafting tool that is its words there, never the portal draft's (which holds none)."""
    row = _reserved(fork_draft, staff_user, agency)
    request = Request.objects.get(pk=row.request_id)
    assert drafting_filing.refresh_reserved(Actor.human(staff_user), row.pk) is False
    assert Request.objects.get(pk=request.pk).request_text == request.request_text
    fork_on.revise(str(row.fork_document_id), ["Rewritten after the approver sent it back."])
    assert drafting_filing.refresh_reserved(Actor.human(staff_user), row.pk) is True
    assert Request.objects.get(pk=request.pk).request_text == (
        "Rewritten after the approver sent it back."
    )
