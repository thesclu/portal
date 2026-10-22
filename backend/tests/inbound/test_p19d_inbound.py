"""P19d: the constant check on mail that came in runs after the transaction, flags a clear
mismatch for a person, never touches a privileged request, and does nothing when it is off."""

import json
from unittest import mock

from django.db import connection
from portal.core.ai import tasks
from portal.core.ai.executors import ExecutorResult
from portal.core.models import AiRun, Setting
from portal.records.actors import Actor
from portal.records.inbound import ingest
from portal.records.models import Source

from tests.inbound.test_inbound import EML


class FakeExecutor:
    name = "fake"
    model = "fake-1"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def run(self, *, system, prompt, schema):
        self.calls.append(prompt)
        return ExecutorResult(text=json.dumps(self.reply), data=self.reply, model=self.model)


def _jobs():
    with connection.cursor() as cursor:
        cursor.execute("select task_name, args from procrastinate_jobs order by id")
        return [
            (name, args if isinstance(args, dict) else json.loads(args)) for name, args in cursor
        ]


def test_arrival_queues_the_check_after_commit_unless_the_general_tier_is_off(
    db, request_one, staff_user, media, django_capture_on_commit_callbacks
):
    data = EML % {b"alias": request_one.inbound_alias_email.encode()}
    with django_capture_on_commit_callbacks(execute=True):
        result = ingest.import_eml(Actor.human(staff_user), data)
    queued = [j for j in _jobs() if j[0] == "ai.check_inbound"]
    assert queued == [("ai.check_inbound", {"source_id": str(result.source.pk)})]
    Setting.objects.update_or_create(key="ai.general_provider", defaults={"value": "off"})
    other = EML.replace(b"reply-1", b"reply-2") % {
        b"alias": request_one.inbound_alias_email.encode()
    }
    with django_capture_on_commit_callbacks(execute=True):
        ingest.import_eml(Actor.human(staff_user), other)
    assert len([j for j in _jobs() if j[0] == "ai.check_inbound"]) == 1


def test_a_clear_mismatch_is_flagged_for_a_person_and_a_match_is_only_logged(
    db, request_one, staff_user, media
):
    data = EML % {b"alias": request_one.inbound_alias_email.encode()}
    source = ingest.import_eml(Actor.human(staff_user), data).source
    fake = FakeExecutor({"relates": "yes", "confidence": 0.9, "kind": "acknowledgment"})
    with mock.patch("portal.core.ai.executors.for_tier", return_value=fake):
        run = tasks.check_inbound(source.pk)
    assert run.status == "done" and run.tier == "general" and run.subject_id == source.pk
    assert "We acknowledge receipt" in fake.calls[0] and "First request" in fake.calls[0]
    assert Source.objects.get(pk=source.pk).flagged_for_review is False
    fake = FakeExecutor({"relates": "no", "confidence": 0.8, "reason": "Different matter."})
    with mock.patch("portal.core.ai.executors.for_tier", return_value=fake):
        tasks.check_inbound(source.pk)
    row = Source.objects.get(pk=source.pk)
    assert row.flagged_for_review and row.flag_reason == "assistant"
    assert "Different matter." in row.flag_note


def test_a_privileged_request_is_never_sent(db, sealed_request, staff_user, media):
    data = EML % {b"alias": sealed_request.inbound_alias_email.encode()}
    source = ingest.import_eml(Actor.human(staff_user), data).source
    fake = FakeExecutor({"relates": "no", "confidence": 1})
    with mock.patch("portal.core.ai.executors.for_tier", return_value=fake):
        assert tasks.check_inbound(source.pk) is None
    assert fake.calls == [] and not AiRun.objects.exists()
