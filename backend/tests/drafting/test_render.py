"""The renderer, exercised without a database -- it has none of Django in it, which is the point.

The load-bearing test here is `test_the_text_and_the_snapshot_cannot_describe_different_letters`:
if those two ever diverge, the revision snapshot stops being evidence of what was sent.
"""

import json
import subprocess
import sys
from pathlib import Path

from portal.drafting import render

REPO_DIR = Path(__file__).resolve().parents[3]


def _letter() -> render.Letter:
    return render.letter_from_parts(
        subject="Widget inspection records",
        shell=render.Shell(
            letterhead="SOUTHERN CALIFORNIA TEST UNION",
            opening_body="To {{RECIPIENT_OFFICE}} at {{AGENCY_FULL}}: under TESTLAW,",
            closing_body="Please direct replies to the address above.",
        ),
        items=[
            render.Item("10", "Tenth ask", sort_order=9),
            render.Item("2", "Second ask", sort_order=1),
            render.Item("2a", "Second ask, part a", parent_number="2", sort_order=2),
        ],
        citations=[render.Citation("1", "Test Code section 1000 et seq.", sort_order=0)],
        blocks=[render.Block("Record definition", "'Record' means any writing.", sort_order=0)],
        meta={"legal_basis": "TESTLAW", "template": "Widgets"},
    )


PLACEHOLDERS = render.agency_placeholders(
    agency_name="Test Agency",
    agency_abbreviation="TA",
    recipient_office="Records Unit",
    address="1 Test Way",
)


def test_items_render_in_sort_order_not_in_string_order() -> None:
    text = render.render_text(_letter(), PLACEHOLDERS)
    assert text.index("2. Second ask") < text.index("10. Tenth ask")


def test_placeholders_resolve_from_the_directory_and_overrides_win() -> None:
    values = render.agency_placeholders(
        agency_name="Test Agency",
        recipient_office="Records Unit",
        overrides={"RECIPIENT_OFFICE": "Office of the Custodian"},
    )
    text = render.render_text(_letter(), values)
    assert "To Office of the Custodian at Test Agency:" in text
    assert "{{" not in text.split("Please direct")[0].replace("{{ADDRESS}}", "")


def test_resolve_is_single_pass_and_does_not_double_substitute() -> None:
    """F4: looping over `placeholders` and doing one `str.replace` per name re-scans whatever a
    prior replacement just wrote. Here AGENCY_FULL resolves to the literal text `{{ADDRESS}}`;
    with a per-key `.replace()` loop, the later ADDRESS pass would find that freshly-written text
    and substitute it too, order-dependently. A single regex pass over the original text must
    not."""
    text = "Reach us at {{AGENCY_FULL}}."
    placeholders = {"AGENCY_FULL": "{{ADDRESS}}", "ADDRESS": "123 Main St"}
    assert render.resolve(text, placeholders) == "Reach us at {{ADDRESS}}."


def test_an_unknown_placeholder_is_left_visible_rather_than_blanked() -> None:
    letter = render.letter_from_parts(
        subject="S", shell=render.Shell(opening_body="Dear {{NOBODY}},"), items=[], citations=[]
    )
    assert "{{NOBODY}}" in render.render_text(letter, PLACEHOLDERS)
    assert render.unresolved_placeholders(letter, PLACEHOLDERS) == ("NOBODY",)


def test_agency_placeholders_drops_the_defaults_it_has_no_value_for() -> None:
    """F3: an agency with no verified email, no portal URL and no contact used to resolve
    RECIPIENT_OFFICE and ADDRESS to `""`, which is a silent, invisible version of the exact
    mistake `resolve()`'s docstring says it refuses to make. AGENCY_ABBR still falls back to
    agency_name (never blank on its own), so only the genuinely-blank three are checked here."""
    values = render.agency_placeholders(
        agency_name="Test Agency", agency_abbreviation="", recipient_office="", address=""
    )
    assert values == {"AGENCY_FULL": "Test Agency", "AGENCY_ABBR": "Test Agency"}


def test_the_four_placeholders_all_survive_as_unresolved_when_blank() -> None:
    """Every one of the four defaults, not just ADDRESS -- the preview that motivated this fix
    was `To  at No Contact PD`, RECIPIENT_OFFICE and ADDRESS both invisible."""
    letter = render.letter_from_parts(
        subject="S",
        shell=render.Shell(opening_body="To {{RECIPIENT_OFFICE}} at {{AGENCY_FULL}}: {{ADDRESS}}"),
        items=[],
        citations=[],
    )
    blank = render.agency_placeholders(
        agency_name="", agency_abbreviation="", recipient_office="", address=""
    )
    assert blank == {}
    text = render.render_text(letter, blank)
    assert "{{RECIPIENT_OFFICE}}" in text and "{{AGENCY_FULL}}" in text and "{{ADDRESS}}" in text
    assert render.unresolved_placeholders(letter, blank) == (
        "ADDRESS",
        "AGENCY_FULL",
        "RECIPIENT_OFFICE",
    )


def test_an_override_that_resolves_blank_is_unresolved_too() -> None:
    """Not just the defaults -- an explicit override of `null` (stored as `""`) must not silently
    blank a placeholder either."""
    values = render.agency_placeholders(
        agency_name="Test Agency",
        recipient_office="Records Unit",
        overrides={"RECIPIENT_OFFICE": None},
    )
    assert "RECIPIENT_OFFICE" not in values
    letter = render.letter_from_parts(
        subject="S",
        shell=render.Shell(opening_body="Dear {{RECIPIENT_OFFICE}},"),
        items=[],
        citations=[],
    )
    assert render.unresolved_placeholders(letter, values) == ("RECIPIENT_OFFICE",)


def test_the_snapshot_keeps_placeholders_literal() -> None:
    """One snapshot serves a whole wave, so it cannot hold any one recipient's resolution."""
    bodies = [s["body"] for s in render.snapshot(_letter())["sections"]]
    assert any("{{AGENCY_FULL}}" in b for b in bodies)


def test_the_text_and_the_snapshot_cannot_describe_different_letters() -> None:
    """Both serialisers consume the same `sections()` tuple. This asserts the consequence:
    every section's body appears in the rendered text, in the snapshot's own array order."""
    letter = _letter()
    text = render.render_text(letter)  # no placeholders, so bodies appear verbatim
    positions = []
    for section in render.snapshot(letter)["sections"]:
        assert section["body"] in text, section
        positions.append(text.index(section["body"]))
    assert positions == sorted(positions), positions
    # letterhead, subject, opening, one block, three items, one citation, closing
    assert len(positions) == len(letter.sections()) == 9


def test_the_snapshot_is_identical_across_runs_and_across_input_order() -> None:
    """The revision-reuse comparison is `stored.content == snapshot(letter)`. That is only
    meaningful if an unchanged template serialises identically -- including when the rows come
    back from the database in a different order, which nothing guarantees they will not."""
    first = _letter()
    shuffled = render.letter_from_parts(
        subject=first.subject,
        shell=first.shell,
        items=tuple(reversed(first.items)),
        citations=first.citations,
        blocks=first.blocks,
        meta=dict(reversed(list(first.meta.items()))),
    )
    assert render.snapshot(first) == render.snapshot(shuffled)
    assert json.dumps(render.snapshot(first), sort_keys=True) == json.dumps(
        render.snapshot(shuffled), sort_keys=True
    )


def test_ties_on_sort_order_still_have_one_total_order() -> None:
    """Two items sharing a sort_order must not be able to swap places between runs."""
    letter = render.letter_from_parts(
        subject="S",
        shell=None,
        items=[render.Item("b", "B body"), render.Item("a", "A body")],
        citations=[],
    )
    assert [s.label for s in letter.sections()] == ["", "a", "b"]


def test_a_snapshot_survives_a_json_round_trip_unchanged() -> None:
    """jsonb normalises object key order but preserves array order, so this is the real
    round trip the reuse comparison depends on."""
    snap = render.snapshot(_letter())
    assert json.loads(json.dumps(snap)) == snap


def test_render_module_does_not_import_django() -> None:
    """In a fresh interpreter: the rest of this suite imports Django, so an in-process
    `'django' in sys.modules` check would answer collection order, not this module's imports."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import portal.drafting.render, sys; print('django' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_DIR / "backend",
        check=True,
    )
    assert result.stdout.strip() == "False", result.stdout + result.stderr
