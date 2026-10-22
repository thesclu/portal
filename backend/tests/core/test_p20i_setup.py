"""P20i: the new-user setup and the agreements it asks for (v6 006–012; DECISIONS 37e, 44).

Who is sent to the setup and who is not; what an acceptance records (the version, the signature
it was signed with, the client address and the browser) and the PDF it keeps; publishing a new
version asks everyone it applies to again; the intern agreement reaches the non-attorney roles
only; a signed PDF is the signer's and an administrator's and nobody else's. Every image is built
in the test; every person is synthetic."""

import hashlib
import io
import uuid

import pytest
from django.core.files.storage import default_storage
from django.test import Client
from portal.core.models import Activity, Agreement, AgreementAcceptance
from portal.core.services import agreements, identity, setup, signatures
from portal.records.services.common import InvalidTransition, NotAllowed
from pypdf import PdfReader

from tests.core.test_p20f_signature import png

ALL_STEPS = ["welcome", "name", "signature", "confidentiality", "intern"]
WHERE = {"REMOTE_ADDR": "203.0.113.7", "HTTP_USER_AGENT": "SyntheticBrowser/1.0 (test)"}

# People are made as production makes them: the setup not finished (tests/conftest.py, P23a).
pytestmark = pytest.mark.unfinished_setup


def _person(role: str, **extra):
    return identity.create_user(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.org",
        name=f"Synthetic {role.replace('_', ' ').title()}",
        records_role=role,
        **extra,
    )


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _sign(user, data: bytes | None = None) -> None:
    signatures.save(user, user.pk, data or png(60, 20), source="drawn")


def _accept_all(user) -> None:
    for agreement in agreements.pending(user):
        agreements.accept(user, agreement.pk, ip_address="198.51.100.1", user_agent="test")


def _finished(role: str, **extra):
    user = _person(role, **extra)
    _sign(user)
    _accept_all(user)
    setup.finish(user)
    user.refresh_from_db()
    return user


# P23a: the server holds an unfinished person at the setup, so the administrator and the member of
# staff these tests act as have been through it for real (signature, agreements, Finish).
@pytest.fixture
def admin_user(db, media):
    return _finished("admin", is_staff=True)


@pytest.fixture
def staff_user(db, media):
    return _finished("staff")


# ---- the seeded versions ----------------------------------------------------------------------


def test_version_one_of_each_is_the_designs_placeholder_wording(db) -> None:
    confidentiality = agreements.current(Agreement.CONFIDENTIALITY)
    intern = agreements.current(Agreement.INTERN)
    assert confidentiality is not None and intern is not None
    assert (confidentiality.version, intern.version) == (1, 1)
    assert confidentiality.is_placeholder and intern.is_placeholder
    first = agreements.clauses(confidentiality.body)
    assert len(first) == 8 and len(agreements.clauses(intern.body)) == 6
    assert first[0].lead == "What’s confidential."
    assert first[-1].text == "Return or delete SCLU materials. This agreement still applies."
    assert agreements.clauses(intern.body)[1].text == "Sign your work as Legal Intern."
    # Only the clause wording the screens draw: never the design notes' draft legal basis.
    for body in (confidentiality.body, intern.body):
        assert "Legal basis" not in body and "Bus. & Prof." not in body and "rule 5.3" not in body


def test_clauses_read_a_bold_lead_and_plain_paragraphs() -> None:
    parsed = agreements.clauses("**Lead.** The rest\nof it.\n\n\nNo lead here.\n\n**Unclosed")
    assert parsed == [
        agreements.Clause("Lead.", "The rest of it."),
        agreements.Clause("", "No lead here."),
        agreements.Clause("", "**Unclosed"),
    ]


# ---- who is sent to the setup -----------------------------------------------------------------


@pytest.mark.app_role
def test_a_fresh_person_is_sent_to_setup_and_a_finished_one_is_not(db, media) -> None:
    user = _person("intern", title="Legal Intern")
    client = _client(user)
    me = client.get("/api/core/auth/me").json()
    assert me["setup_required"] is True and me["title"] == "Legal Intern"
    state = client.get("/api/core/setup").json()
    assert state["required"] is True and state["completed_at"] is None
    assert state["steps"] == ALL_STEPS
    assert [a["kind"] for a in state["agreements"]] == ["confidentiality", "intern"]
    assert all(a["accepted_at"] is None and a["is_placeholder"] for a in state["agreements"])
    assert state["agreements"][0]["clauses"][0]["lead"] == "What’s confidential."

    # Name and title go through the route Settings uses.
    named = client.patch(
        "/api/core/auth/me",
        {"name": "Synthetic Renamed", "title": "  Legal   Intern "},
        content_type="application/json",
    )
    assert named.status_code == 200 and named.json()["title"] == "Legal Intern"

    _sign(user)
    for agreement in state["agreements"]:
        done = client.post(f"/api/core/agreements/{agreement['id']}/accept", **WHERE)
        assert done.status_code == 200, done.content
    # Agreed, not finished: still in the setup, every step still in the run.
    assert client.get("/api/core/setup").json()["steps"] == ALL_STEPS
    assert client.get("/api/core/auth/me").json()["setup_required"] is True

    finished = client.post("/api/core/setup/finish")
    assert finished.status_code == 200, finished.content
    assert finished.json()["setup_required"] is False
    assert client.get("/api/core/auth/me").json()["setup_required"] is False
    after = client.get("/api/core/setup").json()
    assert after["required"] is False and after["steps"] == [] and after["completed_at"]
    assert Activity.objects.filter(action="setup_finished", actor=user).count() == 1


def test_finish_is_refused_until_a_signature_and_every_agreement(db, media) -> None:
    user = _person("staff")
    client = _client(user)
    no_signature = client.post("/api/core/setup/finish")
    assert no_signature.status_code == 422 and "signature" in no_signature.json()["detail"]
    _sign(user)
    no_agreement = client.post("/api/core/setup/finish")
    assert no_agreement.status_code == 422
    assert "confidentiality agreement" in no_agreement.json()["detail"]
    assert "working as an intern" in no_agreement.json()["detail"]
    _accept_all(user)
    assert client.post("/api/core/setup/finish").status_code == 200


# ---- what an acceptance records ---------------------------------------------------------------


@pytest.mark.app_role
def test_an_acceptance_records_the_version_signature_address_and_browser(db, media) -> None:
    user = _person("staff_attorney", title="Staff Attorney")
    _sign(user, png(80, 24))
    signature = signatures.get(user.pk)
    assert signature is not None
    agreement = agreements.current(Agreement.CONFIDENTIALITY)
    assert agreement is not None
    response = _client(user).post(f"/api/core/agreements/{agreement.pk}/accept", **WHERE)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["kind"] == "confidentiality" and body["version"] == 1
    assert body["ip_address"] == "203.0.113.7"
    assert body["user_agent"] == "SyntheticBrowser/1.0 (test)"
    assert body["signed_name"] == user.name and body["signed_title"] == "Staff Attorney"

    row = AgreementAcceptance.objects.get(pk=body["id"])
    assert row.agreement_id == agreement.pk and row.user_id == user.pk
    assert row.signature_sha256 == signature.sha256
    assert row.signature_storage_name == signature.storage_name
    assert row.signature_content_type == "image/png"
    assert default_storage.exists(row.pdf_storage_name) and len(row.pdf_sha256) == 64
    line = Activity.objects.get(action="agreement_accepted", actor=user)
    assert line.subject_id == agreement.pk
    assert line.data["version"] == 1 and line.data["ip_address"] == "203.0.113.7"
    assert line.data["signature_sha256"] == signature.sha256

    # Agreeing twice answers the first acceptance; replacing the signature later does not
    # rewrite what was signed, and the old image is still there.
    again = _client(user).post(f"/api/core/agreements/{agreement.pk}/accept", **WHERE)
    assert again.json()["id"] == body["id"]
    _sign(user, png(90, 30))
    row.refresh_from_db()
    assert row.signature_sha256 == signature.sha256
    assert default_storage.exists(row.signature_storage_name)
    assert AgreementAcceptance.objects.filter(user=user).count() == 1


def test_an_agreement_cannot_be_signed_without_a_signature(db, media) -> None:
    user = _person("staff")
    agreement = agreements.current(Agreement.CONFIDENTIALITY)
    assert agreement is not None
    refused = _client(user).post(f"/api/core/agreements/{agreement.pk}/accept")
    assert refused.status_code == 422 and "signature" in refused.json()["detail"]
    assert not AgreementAcceptance.objects.filter(user=user).exists()


# ---- a new version ----------------------------------------------------------------------------


@pytest.mark.app_role
def test_publishing_a_new_version_shows_that_agreement_again(db, media, admin_user) -> None:
    user = _finished("staff")
    client = _client(user)
    assert client.get("/api/core/auth/me").json()["setup_required"] is False
    old = agreements.current(Agreement.CONFIDENTIALITY)
    assert old is not None

    published = _client(admin_user).post(
        "/api/core/agreements",
        {"kind": "confidentiality", "body": "**Synthetic.** A new clause.\n\nAnother."},
        content_type="application/json",
    )
    assert published.status_code == 200, published.content
    new = published.json()
    assert new["version"] == old.version + 1 and new["is_placeholder"] is False
    assert new["is_current"] is True and new["published_by_name"] == admin_user.name
    assert Activity.objects.filter(action="agreement_published", actor=admin_user).exists()

    # Their next sign-in sends them to that agreement alone; the old version is not signable.
    assert client.get("/api/core/auth/me").json()["setup_required"] is True
    state = client.get("/api/core/setup").json()
    assert state["steps"] == ["confidentiality"]
    assert state["agreements"][0]["version"] == new["version"]
    assert state["agreements"][0]["clauses"] == [
        {"lead": "Synthetic.", "text": "A new clause."},
        {"lead": "", "text": "Another."},
    ]
    with pytest.raises(InvalidTransition):
        agreements.accept(_person("staff"), old.pk)
    stale = client.post(f"/api/core/agreements/{old.pk}/accept")
    # Already accepted by this person: answered with their acceptance, nothing new.
    assert stale.status_code == 200 and stale.json()["version"] == old.version

    assert client.post(f"/api/core/agreements/{new['id']}/accept", **WHERE).status_code == 200
    # Agreed: no longer sent, and the step stays in the run until they finish it.
    assert client.get("/api/core/auth/me").json()["setup_required"] is False
    assert client.get("/api/core/setup").json()["steps"] == ["confidentiality"]
    assert client.post("/api/core/setup/finish").status_code == 200
    assert client.get("/api/core/setup").json()["steps"] == []
    # Finishing again is not a second "finished the setup" line.
    assert Activity.objects.filter(action="setup_finished", actor=user).count() == 1


def test_only_an_administrator_publishes_and_only_a_change(db, admin_user, staff_user) -> None:
    payload = {"kind": "intern", "body": "**Synthetic.** Rules."}
    refused = _client(staff_user).post(
        "/api/core/agreements", payload, content_type="application/json"
    )
    assert refused.status_code == 403
    assert _client(staff_user).get("/api/core/agreements").status_code == 403
    with pytest.raises(NotAllowed):
        agreements.publish(staff_user, "intern", "text")

    admin = _client(admin_user)
    for bad in ({"kind": "intern", "body": "   "}, {"kind": "nda", "body": "x"}):
        assert (
            admin.post("/api/core/agreements", bad, content_type="application/json").status_code
            == 422
        )
    first = admin.post("/api/core/agreements", payload, content_type="application/json")
    assert first.status_code == 200 and first.json()["version"] == 2
    same = admin.post("/api/core/agreements", payload, content_type="application/json")
    assert same.status_code == 422 and "already in force" in same.json()["detail"]

    listed = admin.get("/api/core/agreements").json()
    intern = [row for row in listed if row["kind"] == "intern"]
    assert [row["version"] for row in intern] == [2, 1]
    assert [row["is_current"] for row in intern] == [True, False]
    assert intern[1]["is_placeholder"] is True and intern[0]["title"] == "Working as an intern"


# ---- the intern step --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "asked"),
    [
        ("intern", True),
        ("staff", True),
        ("requester", True),
        ("admin", False),
        ("staff_attorney", False),
        ("limited_attorney", False),
    ],
)
def test_the_intern_step_shows_only_to_the_non_attorney_roles(db, media, role, asked) -> None:
    user = _person(role, is_staff=role == "admin")
    steps = _client(user).get("/api/core/setup").json()["steps"]
    assert ("intern" in steps) is asked
    assert "confidentiality" in steps
    intern = agreements.current(Agreement.INTERN)
    assert intern is not None
    _sign(user)
    answer = _client(user).post(f"/api/core/agreements/{intern.pk}/accept")
    assert answer.status_code == (200 if asked else 403)
    # An attorney finishes with the confidentiality agreement alone.
    if not asked:
        _accept_all(user)
        assert _client(user).post("/api/core/setup/finish").status_code == 200


def test_a_role_that_brings_the_intern_agreement_asks_for_it(db, media, admin_user) -> None:
    user = _finished("staff_attorney")
    assert not setup.required(user)
    identity.set_role(admin_user, user, "intern")
    user.refresh_from_db()
    assert setup.required(user)
    assert setup.state(user).steps == ("intern",)


# ---- the signed PDF ---------------------------------------------------------------------------


def _text(data: bytes) -> str:
    return " ".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_the_pdf_holds_the_text_version_person_and_moment(db, media) -> None:
    user = _person("intern", title="Legal Intern")
    _sign(user)
    agreement = agreements.current(Agreement.CONFIDENTIALITY)
    assert agreement is not None
    client = _client(user)
    accepted = client.post(f"/api/core/agreements/{agreement.pk}/accept", **WHERE).json()
    # P23a: a signed copy is read once the setup is finished (the server holds everything else).
    _accept_all(user)
    setup.finish(user)

    response = client.get(f"/api/core/agreements/acceptances/{accepted['id']}/pdf")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == (
        'attachment; filename="confidentiality-agreement-v1.pdf"'
    )
    data = response.getvalue()
    assert data.startswith(b"%PDF")
    row = AgreementAcceptance.objects.get(pk=accepted["id"])
    assert hashlib.sha256(data).hexdigest() == row.pdf_sha256
    text = _text(data)
    assert "Confidentiality agreement" in text and "Version 1" in text
    assert "Keep it inside SCLU." in text
    assert "Return or delete SCLU materials. This agreement still applies." in text
    assert user.name in text and "Legal Intern" in text and user.email in text
    assert "203.0.113.7" in text and "SyntheticBrowser/1.0" in text
    assert row.signature_sha256 in text
    # The signature image itself is on the page.
    reader = PdfReader(io.BytesIO(data))
    assert any(page.images for page in reader.pages)

    mine = client.get("/api/core/agreements/acceptances").json()
    assert accepted["id"] in [a["id"] for a in mine]
    assert {a["kind"] for a in mine} == {"confidentiality", "intern"}
    assert all(a["user_id"] == str(user.pk) for a in mine)


def test_someone_elses_pdf_is_refused_and_an_administrator_reads_it(
    db, media, admin_user, staff_user
) -> None:
    signer = _person("intern")
    _sign(signer)
    _accept_all(signer)
    setup.finish(signer)
    row = AgreementAcceptance.objects.filter(user=signer).first()
    assert row is not None
    path = f"/api/core/agreements/acceptances/{row.pk}/pdf"

    other = _client(staff_user)
    assert other.get(path).status_code == 404
    # Refused exactly as an acceptance that does not exist.
    assert other.get(f"/api/core/agreements/acceptances/{uuid.uuid4()}/pdf").status_code == 404
    assert other.get(f"/api/core/agreements/acceptances?user_id={signer.pk}").status_code == 403
    assert (
        other.get(f"/api/core/agreements/acceptances?agreement_id={row.agreement_id}").status_code
        == 403
    )
    # Their own list is theirs alone (they have been through the setup themselves).
    assert {a["user_id"] for a in other.get("/api/core/agreements/acceptances").json()} == {
        str(staff_user.pk)
    }

    admin = _client(admin_user)
    read = admin.get(path)
    assert read.status_code == 200 and read.getvalue().startswith(b"%PDF")
    theirs = admin.get(f"/api/core/agreements/acceptances?user_id={signer.pk}").json()
    assert {a["kind"] for a in theirs} == {"confidentiality", "intern"}
    assert all(a["user_name"] == signer.name for a in theirs)
    by_version = admin.get(f"/api/core/agreements/acceptances?agreement_id={row.agreement_id}")
    assert str(signer.pk) in [a["user_id"] for a in by_version.json()]
    assert {a["agreement_id"] for a in by_version.json()} == {str(row.agreement_id)}
    assert _client(signer).get(path).status_code == 200
