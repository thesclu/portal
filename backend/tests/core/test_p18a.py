"""P18a: every setting editable by an administrator with its type kept, the follow-up switch and
templates as settings, and the activity line a change leaves."""

import json

from django.test import Client
from portal.core.models import Activity, Setting
from portal.core.services.settings import defaults, get_setting
from portal.mail.models import Message
from portal.mail.services import followups
from portal.records.actors import Actor
from portal.records.models import DeadlineDefinition
from portal.records.services import requests as requests_service
from portal.records.services import sweeps

from tests.drafting.conftest import sendable_agency


def _put(client: Client, key: str, value):
    return client.put(
        f"/api/core/settings/{key}",
        data=json.dumps({"value": value}),
        content_type="application/json",
    )


def test_every_default_is_listed_and_an_administrator_changes_it_in_its_own_type(
    admin_user, staff_user
) -> None:
    admin, staff = Client(), Client()
    admin.force_login(admin_user)
    staff.force_login(staff_user)
    listed = {row["key"] for row in staff.get("/api/core/settings").json()}
    assert listed == set(defaults())
    assert "records.followups_send_enabled" in listed and "records.followup_body" in listed
    assert _put(staff, "org.name", "x").status_code == 403
    # Each kind keeps its kind.
    assert _put(admin, "records.followups_send_enabled", "yes").status_code == 422
    assert _put(admin, "records.followups_send_enabled", True).status_code == 200
    assert _put(admin, "records.due_soon_window_days", "7").status_code == 422
    assert _put(admin, "records.due_soon_window_days", True).status_code == 422
    assert _put(admin, "records.due_soon_window_days", -1).status_code == 422
    assert _put(admin, "records.due_soon_window_days", 10).status_code == 200
    assert _put(admin, "org.signature", 5).status_code == 422
    assert _put(admin, "org.signature", "A line\nAnother").status_code == 200
    assert _put(admin, "nothing.here", 1).status_code == 404
    assert get_setting("records.due_soon_window_days") == 10
    assert get_setting("records.followups_send_enabled") is True
    # A change is an activity line with the old and new value; a same-value put is not.
    changed = Activity.objects.filter(action="setting_changed").order_by("at")
    assert [a.data["key"] for a in changed] == [
        "records.followups_send_enabled",
        "records.due_soon_window_days",
        "org.signature",
    ]
    assert changed[1].data == {"key": "records.due_soon_window_days", "old": 7, "new": 10}
    _put(admin, "records.due_soon_window_days", 10)
    assert Activity.objects.filter(action="setting_changed").count() == 3


def test_the_follow_up_body_is_the_template_filled_from_the_ledger(staff_user, agency) -> None:
    actor = Actor.human(staff_user)
    request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Widget logs",
        request_text="B.",
    )
    request.agency = agency
    kind = DeadlineDefinition.objects.exclude(overdue_status__isnull=True).first()
    fields = sweeps.followup_placeholders(request, kind)
    assert fields["tracking_number"] == request.tracking_number and fields["agency"] == agency.name
    assert fields["reference"] == "" and fields["sent_on"] == "" and fields["expected_by"] == ""
    assert fields["reply_to"] == request.inbound_alias_email
    # The shipped template, then one a person edited, then one with a typo in it.
    default = sweeps.render_followup(str(get_setting("records.followup_body")), fields)
    assert default.startswith("We are writing to follow up on our records request, ")
    assert f'"{request.subject}"' in default and "{" not in default
    Setting.objects.update_or_create(
        key="records.followup_body",
        defaults={
            "value": "Hello {agency}, about {tracking_number} ({deadline_kind}) {typo}.",
            "description": "t",
        },
    )
    text = sweeps._followup_text(request, kind)
    draft = sweeps.followup_draft(text)
    assert draft.startswith(f"Hello {agency.name}, about {request.tracking_number} (")
    assert "{typo}" in draft and f"Reply to: {request.inbound_alias_email}" in draft
    assert text.startswith(f"Follow up with {agency.name} on {request.tracking_number}:")


def test_the_sender_obeys_the_setting_and_uses_the_subject_template(
    staff_user, monkeypatch
) -> None:
    from portal.mail.services import send

    monkeypatch.setattr(send, "_defer", lambda message_id, send_after: None)
    actor = Actor.human(staff_user)
    agency = sendable_agency(actor, "Followed-up agency")
    request = requests_service.reserve_request(
        actor,
        agency_id=agency.pk,
        requester_user_id=staff_user.pk,
        subject="Overdue",
        request_text="Body.",
    )
    request.agency = agency
    kind = DeadlineDefinition.objects.exclude(overdue_status__isnull=True).first()
    followups.send_followup(Actor.for_system("overdue_sweep"), request, kind, "Please reply.")
    assert Message.objects.count() == 0
    Setting.objects.update_or_create(
        key="records.followups_send_enabled", defaults={"value": True, "description": "t"}
    )
    Setting.objects.update_or_create(
        key="records.followup_subject",
        defaults={"value": "Still waiting: {tracking_number} with {agency}", "description": "t"},
    )
    followups.send_followup(Actor.for_system("overdue_sweep"), request, kind, "Please reply.")
    message = Message.objects.get()
    assert message.subject == f"Still waiting: {request.tracking_number} with {agency.name}"
    assert message.body_text == "Please reply."
