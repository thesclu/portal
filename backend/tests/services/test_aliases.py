"""`normalize_address` parses `From:` headers off inbound mail -- untrusted, and malformed often
enough that it must never raise. No DB needed; these are pure-function cases."""

import pytest
from portal.records.aliases import normalize_address


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Records Officer" <Clerk@Agency.GOV>', "clerk@agency.gov"),
        ("no-brackets@x.org", "no-brackets@x.org"),
        ("", ""),
    ],
)
def test_normalize_address_well_formed_and_trivial_cases(raw, expected) -> None:
    assert normalize_address(raw) == expected


def test_normalize_address_never_raises_on_a_stray_close_bracket_before_the_open_one() -> None:
    """D2: `address.index(">", address.rindex("<"))` raised ValueError here -- the only ">" in
    the string sits BEFORE the last "<", so searching for ">" starting at the "<" position finds
    nothing. Total now: no crash, and the malformed input is returned lowered/trimmed rather than
    a guess at what was meant."""
    assert normalize_address("a>b <c@d.com") == "a>b <c@d.com"


def test_normalize_address_never_raises_on_an_unclosed_bracket() -> None:
    assert normalize_address("<no-close@x.org") == "<no-close@x.org"
