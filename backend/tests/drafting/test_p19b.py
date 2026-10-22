"""P19b: rich text, prose, footnotes and the page (HTML and PDF) for drafts and templates."""

import json

from django.test import Client
from portal.drafting import render
from portal.drafting.models import LetterDraftItem, LetterShell
from portal.drafting.services import filing as filing_service
from portal.drafting.services import letter as letter_service
from portal.mail import pdf
from portal.records.models import Request

DOC = {
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Records about "},
                {"type": "text", "text": "widgets", "marks": [{"type": "bold"}]},
                {"type": "footnoteRef", "attrs": {"marker": "1"}},
                {"type": "text", "text": " from "},
                {"type": "placeholder", "attrs": {"name": "AGENCY_FULL"}},
            ],
        },
        {
            "type": "orderedList",
            "attrs": {"start": 1},
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "one"}]}
                    ],
                },
                {
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "two"}]}
                    ],
                },
            ],
        },
        {
            "type": "table",
            "content": [
                {
                    "type": "tableRow",
                    "content": [
                        {
                            "type": "tableHeader",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "a"}]}
                            ],
                        },
                        {
                            "type": "tableCell",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "b"}]}
                            ],
                        },
                    ],
                }
            ],
        },
    ],
}


def test_the_document_serialises_to_words_and_to_html() -> None:
    text = render.rich_text(DOC)
    assert text.startswith("Records about widgets[1] from {{AGENCY_FULL}}")
    assert "\n\n1. one\n\n2. two\n\na | b" in text
    html = render.rich_html(DOC, {"AGENCY_FULL": "Test Agency"})
    assert "<strong>widgets</strong>" in html and '<sup class="fn">1</sup>' in html
    assert "Test Agency" in html and "placeholder" not in html
    assert "<ol><li><p>one</p></li>" in html and "<th><p>a</p></th>" in html
    standing = render.rich_html(DOC, {})
    assert '<span class="placeholder">{{AGENCY_FULL}}</span>' in standing
    assert render.rich_text("nonsense") == "" and render.rich_html(None) == ""


def test_a_letter_with_documents_renders_as_text_a_page_and_a_pdf() -> None:
    letter = render.letter_from_parts(
        subject="Widgets",
        shell=render.Shell(
            letterhead="SCLU\n1 Street",
            opening_body="To the {{RECIPIENT_OFFICE}}:",
            closing_body="Sincerely,",
        ),
        items=[
            render.Item(number="1", body="", rich=DOC),
            render.Item(number="2", body="Plain ask", sort_order=1),
        ],
        citations=[render.Citation(marker="1", citation_text="A citation the person typed")],
        blocks=[render.Block(name="", body="A paragraph of prose.", sort_order=5)],
    )
    text = render.render_text(letter, {"AGENCY_FULL": "Test Agency", "RECIPIENT_OFFICE": "Records"})
    assert "1. Records about widgets[1] from Test Agency" in text
    assert "2. Plain ask\n\nA paragraph of prose.\n\n[1] A citation the person typed" in text
    snap = render.snapshot(letter)
    assert snap["version"] == 3
    assert any(s["rich"] == DOC for s in snap["sections"])
    html = render.render_html(
        letter, {"AGENCY_FULL": "Test Agency"}, render.PageLayout(), for_pdf=True
    )
    assert "<pdf:pagenumber/>" in html and "Re: Widgets" in html and "footnotes" in html
    data = pdf.pdf_from_html(html)
    assert data.startswith(b"%PDF") and pdf.page_count(data) >= 1
    screen = render.render_html(letter, {}, render.PageLayout(seal_position="none"), for_pdf=False)
    assert "<pdf:pagenumber/>" not in screen and ".page {" in screen


def test_the_editor_saves_pieces_and_the_words_follow(db, draft, agency, staff_user) -> None:
    before = list(LetterDraftItem.objects.filter(draft_id=draft.pk).order_by("sort_order"))
    assert before, "the draft fixture starts with items"
    keep = before[0]
    view = letter_service.save_draft(
        draft.pk,
        subject="Widgets, revised",
        pieces=[
            {"id": keep.pk, "kind": "item", "item_number": "1", "body_rich": DOC},
            {
                "kind": "prose",
                "body_rich": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Between the asks."}],
                        }
                    ],
                },
            },
            {"kind": "item", "item_number": "2", "body": "A plain ask"},
            {"kind": "item", "item_number": "2a", "body": "A sub-ask", "parent_index": 2},
        ],
        citations=[{"marker": "1", "citation_text": "Typed citation", "item_id": keep.pk}],
    )
    assert view.subject == "Widgets, revised"
    assert [p.kind for p in view.pieces] == ["item", "prose", "item", "item"]
    assert view.pieces[0].id == keep.pk and view.pieces[0].body.startswith("Records about widgets")
    assert view.pieces[1].item_number == "" and view.pieces[1].body == "Between the asks."
    assert view.pieces[3].parent_item_id == view.pieces[2].id
    assert [p.sort_order for p in view.pieces] == [0, 1, 2, 3]
    assert LetterDraftItem.objects.filter(draft_id=draft.pk).count() == 4
    assert [c.marker for c in view.citations] == ["1"]
    # The letter itself reads the words; the prose sits where it was placed.
    text = filing_service.preview_for_agency(draft.pk, agency.pk).text
    assert "1. Records about widgets[1] from" in text and "Between the asks." in text
    assert text.index("Between the asks.") < text.index("2. A plain ask")
    # Removing a piece from the list removes the row; a piece without a number is refused.
    view = letter_service.save_draft(
        draft.pk, pieces=[{"id": keep.pk, "item_number": "1", "body": "x"}]
    )
    assert [p.id for p in view.pieces] == [keep.pk]
    import pytest
    from portal.records.services.common import ServiceError

    with pytest.raises(ServiceError, match="number"):
        letter_service.save_draft(draft.pk, pieces=[{"kind": "item", "body": "no number"}])


def test_the_routes_give_the_letter_the_page_and_the_pdf(db, draft, agency, staff_user) -> None:
    client = Client()
    client.force_login(staff_user)
    base = f"/api/drafting/drafts/{draft.pk}"
    got = client.get(f"{base}/letter").json()
    assert got["subject"] == draft.subject and got["pieces"] and got["shell"]["name"]
    saved = client.put(
        f"{base}/letter",
        data=json.dumps({"pieces": [{"kind": "item", "item_number": "1", "body_rich": DOC}]}),
        content_type="application/json",
    )
    assert saved.status_code == 200 and saved.json()["pieces"][0]["body"].startswith(
        "Records about"
    )
    page = client.get(f"{base}/html?agency_id={agency.pk}")
    assert page.status_code == 200 and page["Content-Type"].startswith("text/html")
    assert agency.name in page.content.decode()
    paper = client.get(f"{base}/pdf?agency_id={agency.pk}")
    assert paper.status_code == 200 and paper["Content-Type"] == "application/pdf"
    assert int(paper["X-Page-Count"]) >= 1 and paper.content.startswith(b"%PDF")
    # The seal: an image on the letterhead, then on the page.
    shell = LetterShell.objects.get(pk=draft.shell_id)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    from django.core.files.uploadedfile import SimpleUploadedFile

    up = client.post(
        f"/api/drafting/shells/{shell.pk}/seal",
        {"file": SimpleUploadedFile("seal.png", png, content_type="image/png")},
    )
    assert up.status_code == 200 and up.json()["has_seal"] is True
    assert client.get(f"/api/drafting/shells/{shell.pk}/seal").status_code == 200
    assert "data:image/png;base64" in client.get(f"{base}/html").content.decode()
    bad = client.post(
        f"/api/drafting/shells/{shell.pk}/seal",
        {"file": SimpleUploadedFile("seal.txt", b"no", content_type="text/plain")},
    )
    assert bad.status_code == 422
    # Templates have the same doors.
    tid = draft.source_template_id
    assert client.get(f"/api/drafting/templates/{tid}/letter").json()["blocks"] is not None
    assert client.get(f"/api/drafting/templates/{tid}/pdf")["Content-Type"] == "application/pdf"


def test_reserving_a_draft_with_documents_freezes_words_not_documents(
    db, draft, recipient, staff_actor, staff_user
) -> None:
    letter_service.save_draft(
        draft.pk, pieces=[{"kind": "item", "item_number": "1", "body_rich": DOC}]
    )
    row = filing_service.reserve_recipient(
        staff_actor, recipient.pk, requester_user_id=staff_user.pk
    )
    text = Request.objects.get(pk=row.request_id).request_text
    assert "1. Records about widgets[1] from" in text and "{" not in text.replace("{{", "")
