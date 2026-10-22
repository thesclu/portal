"""P27d: "Ask a teammate" about a whole request (069), beside the Inbox's question about a message.

A question is put to one person who can already open what it is about: asking is not sharing, the
same rule assigning work keeps (`access.require_assignee_can_view`)."""

import json

import pytest
from django.test import Client
from portal.core.models import Review
from portal.records.actors import Actor
from portal.records.services import requests as requests_service

URL = "/api/core/reviews"


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _ask(client: Client, request_id, assigned_to, note: str = "Is this the right agency?"):
    return client.post(
        URL,
        data=json.dumps(
            {
                "subject_type": "request",
                "subject_id": str(request_id),
                "kind": "question",
                "note": note,
                "assigned_to_user_id": str(assigned_to.pk),
            }
        ),
        content_type="application/json",
    )


def _reserve(staff_user, agency, subject: str, **extra):
    return requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject=subject,
        request_text="Synthetic: all records about widgets.",
        **extra,
    )


@pytest.fixture
def ordinary(db, staff_user, agency):
    return _reserve(staff_user, agency, "Synthetic: widget records")


@pytest.fixture
def sealed(db, staff_user, agency):
    return _reserve(
        staff_user, agency, "Synthetic: sealed widget records", sensitivity="privileged"
    )


def test_a_question_about_a_request_reaches_the_teammate_who_answers_it(
    staff_user, colleague_user, ordinary
) -> None:
    asked = _ask(_client(staff_user), ordinary.pk, colleague_user)
    assert asked.status_code == 201, asked.content
    body = asked.json()
    assert body["subject_type"] == "request" and body["kind"] == "question"
    assert body["request_id"] == str(ordinary.pk)
    assert body["title"] == "Synthetic: widget records"
    assert body["tracking_number"] == ordinary.tracking_number

    teammate = _client(colleague_user)
    pending = teammate.get(f"{URL}/pending").json()
    assert [row["id"] for row in pending] == [body["id"]]
    opened = teammate.get(f"{URL}/{body['id']}").json()
    assert opened["can_decide"] is True
    # The request's history of questions shows it (its `request_id` list is approvals only, the
    # request page's strip).
    listed = teammate.get(URL, {"subject_type": "request", "subject_id": str(ordinary.pk)}).json()
    assert body["id"] in [row["id"] for row in listed]
    answered = teammate.post(
        f"{URL}/{body['id']}/decide",
        data=json.dumps({"decision": "answered", "comment": "Yes, it is."}),
        content_type="application/json",
    )
    assert answered.status_code == 200, answered.content
    assert answered.json()["decision"] == "answered"


def test_a_question_needs_a_teammate_and_words(staff_user, colleague_user, ordinary) -> None:
    client = _client(staff_user)
    assert _ask(client, ordinary.pk, colleague_user, note="  ").status_code == 422
    assert _ask(client, ordinary.pk, staff_user).status_code == 422  # ask someone else
    assert not Review.objects.filter(subject_type="request").exists()


def test_asking_is_not_sharing_a_privileged_request(
    staff_user, colleague_user, admin_user, sealed
) -> None:
    """The asker sees the sealed request; the colleague does not, so they cannot be asked about it
    (they would be told of something they cannot open). An administrator, who sees every request,
    can be."""
    client = _client(staff_user)
    refused = _ask(client, sealed.pk, colleague_user)
    assert refused.status_code in (409, 422), refused.content
    assert "Colleague Person" in refused.json()["detail"]
    assert not Review.objects.filter(subject_id=sealed.pk).exists()
    assert _ask(client, sealed.pk, admin_user).status_code == 201


def test_someone_who_cannot_see_the_request_cannot_ask_about_it(
    staff_user, colleague_user, sealed
) -> None:
    refused = _ask(_client(colleague_user), sealed.pk, staff_user)
    assert refused.status_code == 403
    assert refused.json()["code"] == "privileged"
    assert not Review.objects.filter(subject_id=sealed.pk).exists()


def test_a_teammate_cannot_be_asked_about_a_message_on_a_request_they_cannot_open(
    staff_user, colleague_user, sealed
) -> None:
    """The same rule for the Inbox's question about a message (a source) placed on a privileged
    request: it was not checked before P27d."""
    from portal.records.models import Event
    from portal.records.services import events as events_service
    from portal.records.services import sources as sources_service

    actor = Actor.human(staff_user)
    source = sources_service.register_source(
        actor,
        source_type="postmark_email",
        raw_storage_url="test://p27d",
        external_ref="p27d-sealed-message",
        raw_metadata={"subject": "Synthetic"},
    )
    events_service.record_event(
        actor,
        sealed.pk,
        event_type="correspondence",
        channel="email",
        direction="inbound",
        occurred_at=sealed.created_at,
        display_body="Synthetic reply.",
        source_id=source.pk,
    )
    assert Event.objects.filter(source_id=source.pk).exists()
    asked = _client(staff_user).post(
        URL,
        data=json.dumps(
            {
                "subject_type": "source",
                "subject_id": str(source.pk),
                "kind": "question",
                "note": "Is this a denial?",
                "assigned_to_user_id": str(colleague_user.pk),
            }
        ),
        content_type="application/json",
    )
    assert asked.status_code in (409, 422), asked.content
    assert not Review.objects.filter(subject_id=source.pk).exists()


def test_a_question_goes_only_to_someone_on_the_team(staff_user, ordinary) -> None:
    """Found by P27's review: an active person with no team role (a requester) was accepted."""
    from portal.core.services.identity import create_user

    outsider = create_user(
        email="requester-p27d@example.org", name="Req Uester", records_role="requester"
    )
    refused = _ask(_client(staff_user), ordinary.pk, outsider)
    assert refused.status_code == 422, refused.content
    assert "not on the team" in refused.json()["detail"]
    assert not Review.objects.filter(subject_id=ordinary.pk).exists()


def test_a_question_about_a_sealed_letter_goes_only_to_someone_who_can_open_it(
    staff_user, colleague_user, admin_user
) -> None:
    """The same rule for a letter (a draft): a privileged draft is its creator's and the
    administrators'."""
    from portal.drafting.services import drafts as drafts_service

    draft = drafts_service.create_draft(
        subject="Synthetic: sealed letter", created_by_user_id=staff_user.pk
    )
    drafts_service.update_draft(draft.pk, sensitivity="privileged")

    def ask(user):
        return _client(staff_user).post(
            URL,
            data=json.dumps(
                {
                    "subject_type": "draft",
                    "subject_id": str(draft.pk),
                    "kind": "question",
                    "note": "Is this ready?",
                    "assigned_to_user_id": str(user.pk),
                }
            ),
            content_type="application/json",
        )

    refused = ask(colleague_user)
    assert refused.status_code == 422, refused.content
    assert "cannot open this letter" in refused.json()["detail"]
    assert ask(admin_user).status_code == 201
