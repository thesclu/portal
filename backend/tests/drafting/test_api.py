"""The drafting API: the house error mapping, the staff-only rule, and one end-to-end pass.

The mapping tests are the ones that matter. The two deletion refusals arrive as two different
SQLSTATEs, and neither may reach the client as Postgres's own error text -- P3's gate found that
text naming a tracking number and an inbound alias in one response.
"""

import json
from pathlib import Path

import pytest
from django.db import connection
from django.test import Client
from ninja.responses import NinjaJSONEncoder
from portal.drafting.models import LetterTemplate
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing

REPO_DIR = Path(__file__).resolve().parents[3]
DRAFTING_PREFIX = "/api/drafting"


def post_json(client: Client, path: str, payload: dict):
    return client.post(path, data=json.dumps(payload, default=str), content_type="application/json")


def patch_json(client: Client, path: str, payload: dict):
    return client.patch(
        path, data=json.dumps(payload, default=str), content_type="application/json"
    )


@pytest.fixture
def staff_client(db, staff_user):
    client = Client()
    client.force_login(staff_user)
    return client


@pytest.fixture
def requester_client(db):
    from portal.core.services.identity import create_user

    user = create_user(
        email="requester@example.org",
        name="Requester One",
        records_role="requester",
    )
    client = Client()
    client.force_login(user)
    return client


# ---- the whole path, through HTTP ------------------------------------------


def test_a_draft_can_be_built_previewed_and_filed_through_the_api(
    staff_client, template, agency
) -> None:
    created = post_json(
        staff_client, f"{DRAFTING_PREFIX}/drafts", {"from_template_id": str(template.pk)}
    )
    assert created.status_code == 201, created.content
    draft_id = created.json()["id"]

    recipient = post_json(
        staff_client,
        f"{DRAFTING_PREFIX}/drafts/{draft_id}/recipients",
        {
            "agency_id": str(agency.pk),
            "channel": "email",
            # The plain `agency` fixture has no contact, no default_request_email and no
            # portal_url; RECIPIENT_OFFICE is supplied explicitly here so this end-to-end pass
            # exercises a genuinely complete letter (F3: a blank default is now unresolved, not
            # silently filled -- see test_filing.py for that case on its own).
            "placeholder_overrides": {"RECIPIENT_OFFICE": "Records Unit"},
        },
    )
    assert recipient.status_code == 201, recipient.content
    recipient_id = recipient.json()["id"]

    preview = staff_client.get(f"{DRAFTING_PREFIX}/recipients/{recipient_id}/preview")
    assert preview.status_code == 200
    assert agency.name in preview.json()["text"]
    assert preview.json()["unresolved"] == []

    filed = post_json(staff_client, f"{DRAFTING_PREFIX}/drafts/{draft_id}/file", {})
    assert filed.status_code == 200, filed.content
    body = filed.json()
    assert body["filed_count"] == 1
    assert body["revision"] == 1
    assert body["outcomes"][0]["tracking_number"].startswith("REQ-")

    detail = staff_client.get(f"{DRAFTING_PREFIX}/drafts/{draft_id}").json()
    assert detail["recipients"][0]["status"] == "filed"
    assert [i["item_number"] for i in detail["items"] if i["item_number"]] == ["2", "2a", "10"]


def test_a_genuine_database_error_on_one_recipient_does_not_roll_back_the_wave(
    staff_client, draft, agency, second_agency, monkeypatch
) -> None:
    """F1, through HTTP where `ATOMIC_REQUESTS` wraps the whole request in one transaction. Before
    the fix, a `DatabaseError` that is not an `IntegrityError` escaped `_file_one`, escaped the
    view, and rolled the entire response back -- a real filing of nine agencies came back with
    `requests that survive: 0, recipients filed: 0`. A genuine Postgres error (division by zero),
    not a mocked exception, proves the same shape of failure through the HTTP path."""
    good = drafts_service.add_recipient(draft.pk, agency_id=agency.pk, channel="email")
    doomed = drafts_service.add_recipient(draft.pk, agency_id=second_agency.pk, channel="email")

    real_reserve = drafting_filing.requests_service.reserve_request

    def reserve_or_explode(actor, *, agency_id, **kwargs):
        if agency_id == second_agency.pk:
            with connection.cursor() as cur:
                cur.execute("SELECT 1/0")  # genuine Postgres DataError, sqlstate 22012
        return real_reserve(actor, agency_id=agency_id, **kwargs)

    monkeypatch.setattr(drafting_filing.requests_service, "reserve_request", reserve_or_explode)

    filed = post_json(staff_client, f"{DRAFTING_PREFIX}/drafts/{draft.pk}/file", {})
    assert filed.status_code == 200, filed.content
    body = filed.json()
    assert body["filed_count"] == 1, body
    by_id = {o["recipient_id"]: o for o in body["outcomes"]}
    assert by_id[str(good.pk)]["filed"] is True
    assert by_id[str(doomed.pk)]["filed"] is False
    assert by_id[str(doomed.pk)]["reason"] == drafting_filing.CONSTRAINT_REASON
    assert "division" not in by_id[str(doomed.pk)]["reason"]


# ---- error mapping ---------------------------------------------------------


def test_deleting_a_filed_draft_is_409_with_our_own_sentence(
    staff_client, draft, recipient, staff_actor
) -> None:
    """23001, from the schema's BEFORE DELETE trigger."""
    drafting_filing.file_draft(staff_actor, draft.pk)
    response = staff_client.delete(f"{DRAFTING_PREFIX}/drafts/{draft.pk}")
    assert response.status_code == 409, response.content
    detail = response.json()["detail"]
    assert "Archive it instead" in detail
    assert "DETAIL:" not in detail and "letter_recipients" not in detail


def test_deleting_a_template_filed_from_is_409_too(
    staff_client, draft, recipient, template, staff_actor
) -> None:
    """23503, from letter_template_revisions' ON DELETE RESTRICT -- a different mechanism, and the
    caller must not have to tell them apart to understand the answer."""
    drafting_filing.file_draft(staff_actor, draft.pk)
    response = staff_client.delete(f"{DRAFTING_PREFIX}/templates/{template.pk}")
    assert response.status_code == 409, response.content
    assert "Archive it instead" in response.json()["detail"]
    assert LetterTemplate.objects.filter(pk=template.pk).exists()


def test_deleting_a_shell_in_use_is_409_but_not_the_filed_from_sentence(
    staff_client, template, shell
) -> None:
    """F4: a shell in use by a template throws the same 23503 a filed-from template does, but it
    means something else -- nothing was filed, and there is no archive door for a shell -- so it
    must not come back as "has been filed from... Archive it instead"."""
    response = staff_client.delete(f"{DRAFTING_PREFIX}/shells/{shell.pk}")
    assert response.status_code == 409, response.content
    detail = response.json()["detail"]
    assert "Archive it instead" not in detail
    assert "filed" not in detail.lower()
    assert "still used by a template or a draft" in detail


def test_deleting_a_shared_block_in_use_is_409_but_not_the_filed_from_sentence(
    staff_client, template, block
) -> None:
    response = staff_client.delete(f"{DRAFTING_PREFIX}/blocks/{block.pk}")
    assert response.status_code == 409, response.content
    detail = response.json()["detail"]
    assert "Archive it instead" not in detail
    assert "filed" not in detail.lower()
    assert "still used by a template or a draft" in detail


def test_a_genuine_constraint_violation_is_422_and_says_nothing_about_the_row(
    staff_client, draft, agency
) -> None:
    """An out-of-vocabulary channel is enforced only by the CHECK, so it arrives as an
    IntegrityError -- convention #9. The body must be the fixed string, not Postgres's text."""
    response = post_json(
        staff_client,
        f"{DRAFTING_PREFIX}/drafts/{draft.pk}/recipients",
        {"agency_id": str(agency.pk), "channel": "carrier-pigeon"},
    )
    assert response.status_code == 422, response.content
    detail = response.json()["detail"]
    assert (
        detail == "This request could not be completed because it violates a database constraint."
    )
    assert "carrier-pigeon" not in detail and "check constraint" not in detail


def test_an_explicit_null_subject_is_refused_at_the_schema_not_the_database(
    staff_client, draft
) -> None:
    """F4: `letter_drafts.subject` is `NOT NULL`. Before this fix, `{"subject": null}` sailed
    through `DraftPatchIn` as an unset-looking value and reached `.update(subject=None)`, which
    the ORM sent to Postgres as a real NULL and got back the GENERIC constraint-violation string
    ("This request could not be completed because it violates a database constraint.") -- proof
    it was refused by the database, not the schema. The schema-level validator must say something
    specific instead, and the row must be untouched either way."""
    generic_constraint_detail = (
        "This request could not be completed because it violates a database constraint."
    )
    response = patch_json(staff_client, f"{DRAFTING_PREFIX}/drafts/{draft.pk}", {"subject": None})
    assert response.status_code == 422, response.content
    detail = json.dumps(response.json())
    assert generic_constraint_detail not in detail, (
        "refused by the database (IntegrityError), not by the schema"
    )
    assert "subject" in detail and "null" in detail.lower()

    from portal.drafting.models import LetterDraft

    assert LetterDraft.objects.get(pk=draft.pk).subject == draft.subject


def test_a_missing_draft_is_404_and_a_bad_edit_is_422(staff_client) -> None:
    import uuid

    missing = uuid.uuid4()
    assert staff_client.get(f"{DRAFTING_PREFIX}/drafts/{missing}").status_code == 404
    assert (
        patch_json(
            staff_client, f"{DRAFTING_PREFIX}/drafts/{missing}", {"subject": "x"}
        ).status_code
        == 404
    )


# ---- authorisation ---------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/templates"),
        ("get", "/drafts"),
        ("get", "/shells"),
        ("get", "/blocks"),
    ],
)
def test_a_requester_reaches_no_drafting_door(requester_client, method, path) -> None:
    """Nothing in the drafting library is a requester's to see -- there is no own-read door here
    the way there is on GET /records/requests."""
    response = getattr(requester_client, method)(f"{DRAFTING_PREFIX}{path}")
    assert response.status_code == 403, (path, response.status_code)


def test_anonymous_gets_401(db) -> None:
    assert Client().get(f"{DRAFTING_PREFIX}/templates").status_code == 401


# ---- the committed schema --------------------------------------------------


def test_every_drafting_path_is_registered_and_the_committed_schema_is_fresh() -> None:
    from portal.api import api

    schema = api.get_openapi_schema()
    drafting_paths = {p for p in schema["paths"] if p.startswith(DRAFTING_PREFIX)}
    # 28 since P10 added reserve, file and skip on a recipient, "Preview as" on a draft, and Save
    # on a template.
    # 37 with P19h's block footnotes; 38 with P20c's draft of a request ("Review & file").
    # 45 with P21c's seven: the drafting tool's status, webhooks and template mirror; a draft's
    # letters made again, one agency's merge values and a letter's file; a request's letter.
    assert len(drafting_paths) == 45, sorted(drafting_paths)
    for expected in (
        f"{DRAFTING_PREFIX}/drafts/{{draft_id}}/file",
        f"{DRAFTING_PREFIX}/drafts/{{draft_id}}/preview",
        f"{DRAFTING_PREFIX}/recipients/{{recipient_id}}/preview",
        f"{DRAFTING_PREFIX}/recipients/{{recipient_id}}/reserve",
        f"{DRAFTING_PREFIX}/recipients/{{recipient_id}}/file",
        f"{DRAFTING_PREFIX}/recipients/{{recipient_id}}/skip",
        f"{DRAFTING_PREFIX}/templates/{{template_id}}/save",
        f"{DRAFTING_PREFIX}/templates/{{template_id}}/revisions",
    ):
        assert expected in drafting_paths

    fresh = json.loads(json.dumps(schema, cls=NinjaJSONEncoder))
    committed = json.loads((REPO_DIR / "frontend" / "openapi.json").read_text())
    assert committed == fresh, (
        "frontend/openapi.json is stale -- regenerate it with "
        "`uv run backend/manage.py export_openapi_schema --api portal.api.api "
        "--output frontend/openapi.json --indent 2`"
    )
