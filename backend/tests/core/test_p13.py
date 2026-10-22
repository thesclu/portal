"""P13: review before filing or sending, questions to a teammate, suggestions, and access asks."""

import pytest
from django.utils import timezone
from portal.core.models import Review, Setting
from portal.core.services import identity, review, suggestions
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import filing as drafting_filing
from portal.records.actors import Actor
from portal.records.models import RequestAccess
from portal.records.services import access
from portal.records.services import requests as requests_service
from portal.records.services.common import InvalidTransition, NotAllowed, ServiceError

from tests.drafting.conftest import sendable_agency


@pytest.fixture
def director(db):
    """The teammate a question is put to (any member of staff answers what they are asked)."""
    return identity.create_user(email="director@example.org", name="Dee Rector")


# P20h retired "review before filing" (`drafting.review_required`), the Director mark that
# decided it and "who may file without review": the role decides filing, and an intern's letters
# and messages wait for approval instead. Those tests are in tests/mail/test_p20h_approval.py.


def test_the_role_decides_filing_and_staff_are_never_held(staff_user, agency) -> None:

    actor = Actor.human(staff_user)
    target = sendable_agency(actor, "Reviewed agency")
    draft = drafts_service.create_draft(
        subject="Needs no sign-off", created_by_user_id=staff_user.pk
    )
    recipient = drafts_service.add_recipient(draft.pk, agency_id=target.pk, channel="email")
    assert review.clearance(staff_user, "recipient", recipient.pk, None).required is False
    assert drafting_filing.file_recipient(actor, recipient.pk).status == "filed"
    assert not Setting.objects.filter(key="drafting.review_required").exists()


def test_a_question_goes_to_one_teammate_who_answers_it(staff_user, director) -> None:
    import uuid

    with pytest.raises(ServiceError, match="teammate"):
        review.submit(staff_user, "source", uuid.uuid4(), kind="question", note="?")
    asked = review.submit(
        staff_user,
        "source",
        uuid.uuid4(),
        kind="question",
        note="Is this a denial?",
        assigned_to=director,
    )
    assert [r.pk for r in review.pending_for(director)] == [asked.pk]
    assert review.pending_for(staff_user) == []
    with pytest.raises(NotAllowed):
        review.decide(staff_user, asked.pk, Review.ANSWERED, "no")
    answered = review.decide(director, asked.pk, Review.ANSWERED, "Yes, record it as one.")
    assert answered.comment.startswith("Yes")
    with pytest.raises(InvalidTransition):
        review.decide(director, asked.pk, Review.ANSWERED, "again")


def test_a_suggestion_becomes_a_draft_or_is_declined_with_a_reason(staff_user, db) -> None:
    requester = identity.create_user(
        email="jordan@example.org",
        name="Jordan",
        records_role="requester",
    )
    one = suggestions.suggest(requester, what="Drone use at protests", agency_hint="Campus PD")
    two = suggestions.suggest(requester, what="Something else", why="Because.")
    with pytest.raises(ServiceError):
        suggestions.suggest(requester, what="  ")
    started = suggestions.start(staff_user, one.pk)
    assert started.status == "started" and started.draft_id is not None
    from portal.drafting.models import LetterDraft, LetterDraftItem

    row = LetterDraft.objects.get(pk=started.draft_id)
    assert row.subject == "Drone use at protests" and row.created_by_user_id == staff_user.pk
    assert [i.body for i in LetterDraftItem.objects.filter(draft_id=row.pk)] == [
        "Drone use at protests"
    ]
    with pytest.raises(ServiceError, match="why"):
        suggestions.decline(staff_user, two.pk, " ")
    declined = suggestions.decline(staff_user, two.pk, "Already covered.")
    assert declined.status == "declined" and declined.decline_note == "Already covered."
    with pytest.raises(InvalidTransition):
        suggestions.start(staff_user, two.pk)
    assert [s.status for s in suggestions.mine(requester)] == ["declined", "started"]
    assert suggestions.pending() == []


def test_asking_for_access_and_allowing_it(db, staff_user, admin_user, agency) -> None:
    """P13, with P20h's rulings: only an administrator decides an ask (allowing is giving access),
    and every administrator is told by the bell, never by an email per ask (42e)."""
    from portal.core.models import Notification

    owner = Actor.human(staff_user)
    sealed = requests_service.reserve_request(
        owner,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Sealed",
        request_text="t",
        sensitivity="privileged",
    )
    outsider = identity.create_user(email="out@example.org", name="Out Sider")
    asking = Actor.human(outsider)
    with pytest.raises(ServiceError, match="why"):
        access.ask_access(asking, sealed.pk, " ")
    with pytest.raises(InvalidTransition, match="already see"):
        access.ask_access(owner, sealed.pk, "mine")
    ask = access.ask_access(asking, sealed.pk, "Drafting the appeal.")
    with pytest.raises(InvalidTransition, match="already asked"):
        access.ask_access(asking, sealed.pk, "again")
    told = Notification.objects.filter(subject_type="access_request", subject_id=ask.pk)
    assert [(n.user_id, n.emailed_at) for n in told] == [(admin_user.pk, None)]
    admin = Actor.human(admin_user)
    assert [
        a.pk for a in access.pending_asks_for(access.Viewer(user_id=admin_user.pk, role="admin"))
    ] == [ask.pk]
    assert access.pending_asks_for(access.Viewer(user_id=staff_user.pk, role="staff")) == []
    # Neither the asker nor someone on the list decides it: an administrator does.
    for not_an_admin in (asking, owner):
        with pytest.raises(NotAllowed):
            access.decide_access(not_an_admin, ask.pk, allow=True)
    with pytest.raises(ServiceError, match="declined"):
        access.decide_access(admin, ask.pk, allow=False)
    allowed = access.decide_access(admin, ask.pk, allow=True)
    assert allowed.decision == "allowed" and allowed.decided_at is not None
    assert RequestAccess.objects.filter(
        request_id=sealed.pk, user_id=outsider.pk, revoked_at=None
    ).exists()
    assert access.can_view(access.Viewer(user_id=outsider.pk, role="staff"), sealed)
    with pytest.raises(InvalidTransition):
        access.decide_access(admin, ask.pk, allow=True)
    assert [a.decision for a in access.my_asks(outsider.pk)] == ["allowed"]
    assert timezone.now() >= allowed.decided_at


def test_the_daily_summary_lists_the_open_asks_for_an_administrator_only(
    db, staff_user, admin_user, agency
) -> None:
    from portal.core.services import notifications

    sealed = requests_service.reserve_request(
        Actor.human(staff_user),
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Sealed",
        request_text="t",
        sensitivity="privileged",
    )
    outsider = identity.create_user(email="out@example.org", name="Out Sider")
    access.ask_access(Actor.human(outsider), sealed.pk, "Please.")
    summary = notifications.summary_data(admin_user)
    assert summary is not None
    assert summary.access_asks == [("Out Sider", sealed.tracking_number)]
    text = notifications.summary_text(admin_user)
    assert text is not None and "Asks for access" in text and sealed.tracking_number in text
    staff_summary = notifications.summary_data(staff_user)
    assert staff_summary is None or staff_summary.access_asks == ()
