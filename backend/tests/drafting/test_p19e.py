"""P19e backend: the picker's columns, the docket's item count, a mailed letter's tracking
number, and the letter's file name."""

from datetime import UTC, datetime

from django.test import Client
from portal.core.models import Setting
from portal.drafting.services import filing as filing_service
from portal.drafting.services import letter as letter_service
from portal.records.models import Event

WHEN = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def test_the_picker_columns_and_the_docket_item_count(
    db, template, draft, recipient, staff_actor, staff_user
) -> None:
    client = Client()
    client.force_login(staff_user)
    row = next(
        t for t in client.get("/api/drafting/templates").json() if t["id"] == str(template.pk)
    )
    assert row["item_count"] >= 1 and row["last_used_at"] is None
    filing_service.file_recipient(
        staff_actor, recipient.pk, filed_at=WHEN, requester_user_id=staff_user.pk
    )
    row = next(
        t for t in client.get("/api/drafting/templates").json() if t["id"] == str(template.pk)
    )
    assert row["last_used_at"] is not None and row["filed_count"] == 1
    docket = client.get("/api/records/requests").json()["items"]
    assert (
        docket
        and all("item_count" in r for r in docket)
        and max(r["item_count"] for r in docket) >= 1
    )


def test_a_mailed_letter_keeps_its_tracking_number_and_names_its_file(
    db, draft, recipient, staff_actor, staff_user
) -> None:
    Setting.objects.update_or_create(
        key="org.short_name", defaults={"value": "SCLU", "description": "t"}
    )
    name = letter_service.letter_filename(recipient)
    assert name.startswith("SCLU_TESTLAW_") and name.endswith(".pdf") and " " not in name
    client = Client()
    client.force_login(staff_user)
    listed = client.get(f"/api/drafting/drafts/{draft.pk}").json()["recipients"][0]
    assert listed["letter_filename"] == name
    filed = client.post(
        f"/api/drafting/recipients/{recipient.pk}/file",
        data='{"tracking_number": "9400 1000 0000 0000 0000 00", "channel": "mail"}',
        content_type="application/json",
    )
    assert filed.status_code == 200, filed.content
    event = Event.objects.filter(
        request_id=filed.json()["request_id"], event_type_id="submission"
    ).get()
    assert "Tracking number 9400 1000 0000 0000 0000 00" in event.display_body
    # The file name carries the filing date once filed.
    after = client.get(f"/api/drafting/drafts/{draft.pk}").json()["recipients"][0][
        "letter_filename"
    ]
    today = filing_service.letter_date(recipient.agency, datetime.now(tz=UTC))
    assert after.endswith(f"{today:%Y-%m-%d}.pdf")


def test_the_file_name_carries_the_agencys_date_not_utcs(db, recipient) -> None:
    """P27: 11:30 PM in California is the next morning in UTC; the letter is dated by the
    agency's calendar (`{{FILING_DATE}}`), and so is its file."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    evening = datetime(2026, 10, 21, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert evening.astimezone(UTC).date().isoformat() == "2026-10-22"
    recipient.filed_at = evening
    name = letter_service.letter_filename(recipient)
    assert name.endswith("_2026-10-21.pdf"), name
    recipient.filed_at = evening + timedelta(hours=1)
    assert letter_service.letter_filename(recipient).endswith("_2026-10-22.pdf")
