"""P19d: the assistant. Contracts that refuse privileged drafts and log only sizes and hashes,
a fake executor behind the run service, the four tasks, the local checks, the rail, the cards a
person decides, notes, the daily limit, and the doors answering "off" rather than failing."""

import json
from unittest import mock

import pytest
from django.test import Client
from portal.core.ai import checks, contracts, service, tasks
from portal.core.ai.executors import DRAFTING, ExecutorError, ExecutorResult, NotConfigured
from portal.core.models import Activity, AiRun, AiSuggestion, DraftNote, Setting
from portal.drafting.models import LetterDraft, LetterDraftItem
from portal.drafting.services import drafts as drafts_service
from portal.records.services.common import InvalidTransition

pytestmark = pytest.mark.usefixtures("db")


class FakeExecutor:
    name = "fake"

    def __init__(self, reply=None, *, fail: str | None = None) -> None:
        self.model = "fake-1"
        self.reply = reply or {}
        self.fail = fail
        self.calls: list[dict] = []

    def run(self, *, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if self.fail:
            raise ExecutorError(self.fail)
        return ExecutorResult(
            text=json.dumps(self.reply),
            data=self.reply,
            model=self.model,
            tokens_in=10,
            tokens_out=5,
        )


def _set(key: str, value) -> None:
    Setting.objects.update_or_create(key=key, defaults={"value": value})


def _use(fake):
    return mock.patch("portal.core.ai.executors.for_tier", return_value=fake)


def _post(client: Client, path: str, payload=None):
    return client.post(path, data=json.dumps(payload or {}), content_type="application/json")


# ---- contracts ----------------------------------------------------------------------------------


def test_the_letter_contract_carries_the_letter_and_logs_only_sizes_and_hashes(draft, recipient):
    ctx = contracts.letter_context(draft, question="Is this too broad?")
    assert "Widget camera footage" in ctx.text() and "Is this too broad?" in ctx.text()
    pieces = ctx.parts["pieces"]
    assert all(p["id"] and p["kind"] for p in pieces)
    summary = ctx.summary()
    assert set(summary) >= {"letter_text", "pieces", "question"}
    for part in summary.values():
        assert set(part) == {"chars", "sha256"}
    assert "Widget" not in json.dumps(summary)


def test_a_privileged_draft_is_refused_before_anything_is_assembled(draft):
    LetterDraft.objects.filter(pk=draft.pk).update(sensitivity="privileged")
    draft.refresh_from_db()
    with pytest.raises(contracts.Refused):
        contracts.letter_context(draft)
    _set("ai.exclude_privileged", False)
    assert contracts.letter_context(draft).parts["letter_text"]


# ---- the run service and the tasks -------------------------------------------------------------


def test_suggest_makes_cards_and_a_run_that_keeps_no_prompt_unless_asked(draft, staff_user):
    item = LetterDraftItem.objects.filter(draft_id=draft.pk).order_by("sort_order").first()
    fake = FakeExecutor(
        {
            "suggestions": [
                {
                    "piece_id": str(item.pk),
                    "kind": "scope",
                    "title": "Narrow item 2",
                    "text": "Footage of every camera is broad.",
                    "replacement_text": "Footage from the lobby camera",
                },
                {"kind": "nonsense", "title": "", "text": ""},
                {"kind": "nonsense", "title": "Kept", "text": "Unknown kinds fall to other."},
            ]
        }
    )
    with _use(fake):
        answer = tasks.suggest_letter(staff_user, draft)
    assert [s.kind for s in answer.suggestions] == ["scope", "other"]
    assert answer.suggestions[0].item_id == item.pk
    assert answer.suggestions[0].replacement_text == "Footage from the lobby camera"
    run = AiRun.objects.get(pk=answer.run_id)
    assert run.status == "done" and run.provider == "fake" and run.tier == DRAFTING
    assert run.prompt == "" and run.tokens_in == 10 and run.requested_by_id == staff_user.pk
    assert run.context_summary["letter_text"]["chars"] > 0
    assert "Widget camera footage" in fake.calls[0]["prompt"]
    _set("ai.keep_prompts", True)
    with _use(fake):
        answer = tasks.ask_letter(staff_user, draft, "Why item 10?")
    run = AiRun.objects.get(pk=answer.run_id)
    assert "Why item 10?" in run.prompt and run.task == "ask_letter"


def test_a_failed_provider_leaves_a_failed_run_and_no_cards(draft, staff_user):
    with _use(FakeExecutor(fail="the wire fell over")), pytest.raises(ExecutorError):
        tasks.suggest_letter(staff_user, draft)
    run = AiRun.objects.get()
    assert run.status == "failed" and "wire" in run.error and not AiSuggestion.objects.exists()


def test_the_daily_limit_counts_runs_and_refused_ones_do_not_count(draft, staff_user):
    _set("ai.daily_run_limit", 1)
    with _use(FakeExecutor({"answer": "fine"})):
        tasks.ask_letter(staff_user, draft, "ok?")
        with pytest.raises(service.OverLimit):
            tasks.ask_letter(staff_user, draft, "again?")
    _set("ai.daily_run_limit", 2)
    with (
        mock.patch("portal.core.ai.executors.for_tier", side_effect=NotConfigured("off")),
        pytest.raises(NotConfigured),
    ):
        tasks.ask_letter(staff_user, draft, "still?")
    assert AiRun.objects.filter(status="refused").count() == 0  # refused before a row exists
    with _use(FakeExecutor({"answer": "fine"})):
        tasks.ask_letter(staff_user, draft, "and now?")


def test_pick_offers_master_letters_and_past_requests(template, staff_user):
    fake = FakeExecutor(
        {
            "matches": [{"kind": "master_letter", "id": str(template.pk), "name": template.name}],
            "proposed_items": ["Widget maintenance logs"],
            "note": "",
        }
    )
    with _use(fake):
        found = tasks.pick_letter(staff_user, "widget maintenance")
    assert found["matches"][0]["id"] == str(template.pk)
    assert found["proposed_items"] == ["Widget maintenance logs"]
    assert "Widget inspection records" in fake.calls[0]["prompt"]


def test_a_card_is_applied_or_dismissed_once(draft, staff_user, colleague_user):
    with _use(FakeExecutor({"suggestions": [{"kind": "wording", "title": "T", "text": "x"}]})):
        card = tasks.suggest_letter(staff_user, draft).suggestions[0]
    done = tasks.decide_suggestion(colleague_user, card.pk, applied=True)
    assert done.status == "applied" and done.decided_by_id == colleague_user.pk
    assert Activity.objects.filter(action="suggestion_applied").count() == 1
    with pytest.raises(InvalidTransition):
        tasks.decide_suggestion(colleague_user, card.pk, applied=False)
    assert tasks.open_suggestions(draft.pk) == []


# ---- checks -------------------------------------------------------------------------------------


def test_checks_find_house_style_straight_quotes_and_orphan_citations(draft, recipient):
    _set("ai.house_style", "utilize => use\nin order to => to")
    item = LetterDraftItem.objects.filter(draft_id=draft.pk).order_by("sort_order").first()
    drafts_service.update_draft_item(item.pk, body='We utilize "widgets" in order to count')
    found = checks.checks_for(draft)
    kinds = {c.kind for c in found}
    assert {"house_style", "citation"} <= kinds
    titles = {c.title for c in found}
    assert "“utilize” → use" in titles and "Straight quote" in titles
    assert any(c.piece_id == item.pk for c in found)
    assert any("Footnote 1" in c.title for c in found)


# ---- the doors ----------------------------------------------------------------------------------


def test_the_rail_lists_checks_cards_and_notes_and_says_whether_the_assistant_is_on(
    draft, recipient, staff_user
):
    client = Client()
    client.force_login(staff_user)
    base = f"/api/core/ai/drafts/{draft.pk}"
    note = _post(client, f"{base}/notes", {"body": "Ask legal about item 10"})
    assert note.status_code == 200 and note.json()["author_name"] == "Staff Person"
    assert _post(client, f"{base}/notes", {"body": "  "}).status_code == 422
    with _use(FakeExecutor({"suggestions": [{"kind": "scope", "title": "T", "text": "x"}]})):
        body = _post(client, f"{base}/suggest").json()
    assert body["status"] == "ok" and len(body["suggestions"]) == 1
    rail = client.get(f"{base}/rail").json()
    assert rail["assistant"] == "on"
    assert [n["body"] for n in rail["notes"]] == ["Ask legal about item 10"]
    assert [s["title"] for s in rail["suggestions"]] == ["T"]
    assert any(c["kind"] == "citation" for c in rail["checks"])
    card = rail["suggestions"][0]["id"]
    assert _post(client, f"/api/core/ai/suggestions/{card}/dismiss").json()["status"] == "dismissed"
    assert _post(client, f"/api/core/ai/notes/{note.json()['id']}/void").status_code == 204
    rail = client.get(f"{base}/rail").json()
    assert rail["notes"] == [] and rail["suggestions"] == []
    assert DraftNote.objects.get().voided_at is not None


def test_the_doors_answer_off_refused_and_failed_rather_than_erroring(draft, staff_user):
    client = Client()
    client.force_login(staff_user)
    base = f"/api/core/ai/drafts/{draft.pk}"
    _set("ai.drafting_provider", "off")
    body = _post(client, f"{base}/ask", {"question": "hi"}).json()
    assert body["status"] == "off" and client.get(f"{base}/rail").json()["assistant"] == "off"
    assert _post(client, "/api/core/ai/pick", {"question": "hi"}).json()["status"] == "off"
    _set("ai.drafting_provider", "gemini")
    LetterDraft.objects.filter(pk=draft.pk).update(sensitivity="privileged")
    with _use(FakeExecutor({"answer": "no"})):
        body = _post(client, f"{base}/ask", {"question": "hi"}).json()
    assert body["status"] == "refused" and not AiRun.objects.exists()
    assert client.get(f"{base}/rail").json()["assistant"] == "privileged"
    LetterDraft.objects.filter(pk=draft.pk).update(sensitivity="internal")
    with _use(FakeExecutor(fail="down")):
        body = _post(client, f"{base}/ask", {"question": "hi"}).json()
    assert body["status"] == "failed" and "down" in body["message"]


def test_the_run_log_is_for_administrators_and_shows_no_content(draft, staff_user, admin_user):
    with _use(FakeExecutor({"answer": "yes"})):
        tasks.ask_letter(staff_user, draft, "Secret question about widgets")
    staff, admin = Client(), Client()
    staff.force_login(staff_user)
    admin.force_login(admin_user)
    assert staff.get("/api/core/ai/runs").status_code == 403
    rows = admin.get("/api/core/ai/runs").json()
    assert len(rows) == 1 and rows[0]["task"] == "ask_letter" and rows[0]["status"] == "done"
    assert "widgets" not in json.dumps(rows)
    assert rows[0]["requested_by_name"] == "Staff Person"
