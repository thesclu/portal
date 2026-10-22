"""core 0003 seeded `records.default_sensitivity`, and seeding never overwrites an edited value."""

from portal.core.models import Setting
from portal.core.services.settings import defaults, get_setting, seed_defaults
from portal.records.models import Sensitivity


def test_default_sensitivity_is_a_seeded_setting_with_a_value_a_request_can_hold(db) -> None:
    row = Setting.objects.get(pk="records.default_sensitivity")
    assert row.value in Sensitivity.values
    assert row.value == defaults()["records.default_sensitivity"]["value"] == "internal"
    assert row.description


def test_reseeding_leaves_an_edited_value_alone_and_restores_a_missing_row(db) -> None:
    Setting.objects.filter(pk="records.default_sensitivity").update(value="public")
    seed_defaults()
    assert get_setting("records.default_sensitivity") == "public"

    Setting.objects.filter(pk="records.default_sensitivity").delete()
    assert seed_defaults() >= 1
    assert get_setting("records.default_sensitivity") == "internal"
