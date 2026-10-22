"""P19h: a block's own footnotes renumbered after the letter's, a letterhead's own blocks
copied into new templates and scratch letters, "Revision N" on the templates list, fixes on
checks, and the decided cards in the rail."""

import json

from django.test import Client
from portal.core.ai import checks
from portal.core.models import Setting
from portal.drafting import render
from portal.drafting.models import LetterDraftCitation, LetterDraftItem, SharedBlockCitation
from portal.drafting.services import drafts as drafts_service
from portal.drafting.services import library as library_service
from portal.drafting.services.common import letter_of_template

RICH = {
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "'Record' means any writing"},
                {"type": "footnoteRef", "attrs": {"marker": "1"}},
                {"type": "text", "text": "."},
            ],
        }
    ],
}


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def _patch(client, path, payload):
    return client.patch(path, data=json.dumps(payload), content_type="application/json")


def test_renumber_footnotes_rewrites_text_and_document():
    body, rich = render.renumber_footnotes("See [1] and [2].", RICH, {"1": "3"})
    assert body == "See [3] and [2]."
    assert rich["content"][0]["content"][1]["attrs"]["marker"] == "3"
    assert RICH["content"][0]["content"][1]["attrs"]["marker"] == "1"  # untouched
    assert render.renumber_footnotes("x [1]", None, {}) == ("x [1]", None)


def test_a_blocks_footnotes_follow_the_templates_own_and_reach_the_draft(
    db, template, block, staff_user
):
    library_service.update_block(block.pk, body="'Record' means any writing[1].", body_rich=RICH)
    library_service.add_block_citation(block.pk, marker="1", citation_text="Test Code section 7")
    letter = letter_of_template(template)
    assert [c.marker for c in letter.citations] == ["1", "2"]
    assert letter.citations[1].citation_text == "Test Code section 7"
    resolved = next(b for b in letter.blocks if b.name == block.name)
    assert resolved.body == "'Record' means any writing[2]."
    assert resolved.rich["content"][0]["content"][1]["attrs"]["marker"] == "2"
    draft = drafts_service.create_draft(
        from_template_id=template.pk, created_by_user_id=staff_user.pk
    )
    markers = sorted(
        LetterDraftCitation.objects.filter(draft_id=draft.pk).values_list("marker", flat=True)
    )
    assert markers == ["1", "2"]
    prose = LetterDraftItem.objects.get(draft_id=draft.pk, item_number="")
    assert (
        "[2]" in prose.body
        and prose.body_rich["content"][0]["content"][1]["attrs"]["marker"] == "2"
    )


def test_a_letterheads_blocks_start_new_templates_and_scratch_letters(db, shell, block, staff_user):
    library_service.add_block_citation(block.pk, marker="1", citation_text="Test Code section 7")
    library_service.set_shell_blocks(shell.pk, [block.pk])
    assert library_service.shell_block_ids(shell.pk) == [block.pk]
    created = library_service.create_template(
        name="On the letterhead", shell_id=shell.pk, created_by_user_id=staff_user.pk
    )
    assert [link.shared_block_id for link in created.template_blocks.all()] == [block.pk]
    scratch = drafts_service.create_draft(
        subject="From nothing", shell_id=shell.pk, created_by_user_id=staff_user.pk
    )
    rows = list(LetterDraftItem.objects.filter(draft_id=scratch.pk))
    assert len(rows) == 1 and rows[0].kind == "prose" and "Record" in rows[0].body
    assert list(
        LetterDraftCitation.objects.filter(draft_id=scratch.pk).values_list("marker", flat=True)
    ) == ["1"]
    # The letterhead's list is copied, not linked: changing it later leaves the template alone.
    library_service.set_shell_blocks(shell.pk, [])
    assert created.template_blocks.count() == 1


def test_the_block_routes_and_the_letterhead_picker(db, shell, block, staff_user):
    client = Client()
    client.force_login(staff_user)
    added = _post(
        client,
        f"/api/drafting/blocks/{block.pk}/citations",
        {"marker": "1", "citation_text": "Test Code section 7"},
    )
    assert added.status_code == 201, added.content
    assert [c["marker"] for c in added.json()["citations"]] == ["1"]
    again = _post(
        client,
        f"/api/drafting/blocks/{block.pk}/citations",
        {"marker": "1", "citation_text": "Twice"},
    )
    assert again.status_code >= 400
    shells = _patch(client, f"/api/drafting/shells/{shell.pk}", {"block_ids": [str(block.pk)]})
    assert shells.status_code == 200 and shells.json()["block_ids"] == [str(block.pk)]
    listed = next(b for b in client.get("/api/drafting/blocks").json() if b["id"] == str(block.pk))
    assert listed["used_in_letterheads"] == 1
    refused = client.delete(f"/api/drafting/blocks/{block.pk}")
    assert refused.status_code >= 400  # a letterhead still adds it
    _patch(client, f"/api/drafting/shells/{shell.pk}", {"block_ids": []})
    cite = added.json()["citations"][0]["id"]
    assert client.delete(f"/api/drafting/block-citations/{cite}").status_code == 204
    assert not SharedBlockCitation.objects.filter(shared_block_id=block.pk).exists()
    assert client.delete(f"/api/drafting/blocks/{block.pk}").status_code == 204


def test_the_templates_list_shows_the_latest_revision(db, template, staff_user):
    client = Client()
    client.force_login(staff_user)
    row = next(
        t for t in client.get("/api/drafting/templates").json() if t["id"] == str(template.pk)
    )
    assert row["latest_revision"] is None
    saved = _post(client, f"/api/drafting/templates/{template.pk}/save", {"summary": "First"})
    assert saved.status_code == 200, saved.content
    row = next(
        t for t in client.get("/api/drafting/templates").json() if t["id"] == str(template.pk)
    )
    assert row["latest_revision"] == 1
    detail = client.get(f"/api/drafting/templates/{template.pk}").json()
    assert detail["latest_revision"] == 1


def test_checks_carry_a_fix_and_the_rail_lists_decided_cards(db, draft, staff_user):
    Setting.objects.update_or_create(key="ai.house_style", defaults={"value": "utilize => use"})
    item = (
        LetterDraftItem.objects.filter(draft_id=draft.pk, kind="item")
        .order_by("sort_order")
        .first()
    )
    drafts_service.update_draft_item(item.pk, body='We utilize "widgets"')
    found = {c.title: c for c in checks.checks_for(draft)}
    assert found["“utilize” → use"].fix == checks.Fix("word", "utilize", "use")
    assert found["Straight quote"].fix == checks.Fix("quotes")
    client = Client()
    client.force_login(staff_user)
    rail = client.get(f"/api/core/ai/drafts/{draft.pk}/rail").json()
    fixes = {c["title"]: c["fix"] for c in rail["checks"]}
    assert fixes["“utilize” → use"] == {"mode": "word", "find": "utilize", "replace": "use"}
    assert rail["decided"] == []
    from portal.core.ai import tasks
    from portal.core.models import AiRun, AiSuggestion

    run = AiRun.objects.create(task="suggest_letter", tier="drafting", provider="fake", model="x")
    card = AiSuggestion.objects.create(
        run=run, draft_id=draft.pk, kind="wording", title="Shorter", text="Cut it."
    )
    tasks.decide_suggestion(staff_user, card.pk, applied=False)
    rail = client.get(f"/api/core/ai/drafts/{draft.pk}/rail").json()
    assert rail["suggestions"] == []
    assert [(d["title"], d["status"], d["decided_by_name"]) for d in rail["decided"]] == [
        ("Shorter", "dismissed", "Staff Person")
    ]
    assert rail["decided"][0]["decided_at"]
