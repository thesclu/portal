"""`storage.safe_filename`: the one rule for what an uploaded or ingested file is called.

A file name is attacker-controlled text that ends up in three places: the database row, the stored
path and the download header. Convention 14 says the name is made safe once, when the file is
brought in, so the rule has to be IDEMPOTENT (making a safe name safe again changes nothing, which
is what lets the three places agree on one string) and its promises have to hold for the output,
not just for the inputs somebody thought of. P8b review F6 found two promises that did not:
a leading dot survived (`". .htaccess"` came out as `".htaccess"`, which the second pass then
turned into `"htaccess"`), and the length cap counted characters, so a name of 300 CJK characters
fit the cap and was 459 bytes as a stored path, over the 255 bytes a Linux filesystem allows one
path segment.

Pure functions: nothing here touches the database or the disk.
"""

import random
import re
import unicodedata

import pytest
from portal.records import storage

# A stored segment is "<16 hex>-<name>". Linux filesystems (ext4, xfs) cap one segment at 255 BYTES.
SEGMENT_BYTES = 255
PREFIX_BYTES = len("0123456789abcdef-")
_DROPPED = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}

# Names from the review's probes, plus the same trick with other separators.
LEADING_DOT_NAMES = [
    (". .htaccess", "htaccess"),
    (".. ..htaccess", "htaccess"),
    (" . .bashrc", "bashrc"),
    (". . . .env", "env"),
    ("\t.  .profile", "profile"),
    (".hidden.pdf", "hidden.pdf"),
]

# Names that stay as they are, for every kind of reason a name is odd.
ODD_NAMES = [
    *[given for given, _ in LEADING_DOT_NAMES],
    "",
    "   ",
    ".",
    "..",
    "...",
    ". . .",
    " .. ",
    "../../etc/passwd",
    "..\\..\\windows\\evil.exe",
    "report\x00.pdf",
    "a‮fdp.exe",
    # A zero-width space between a letter and its accent: dropping it leaves a string that is not
    # in NFC, so a second pass would recompose it.
    "e​́.pdf",
    "á​̖.pdf",
    "Å.pdf",  # ANGSTROM SIGN, which NFC turns into a different code point
    ";.pdf",
    "bad<name>:with|chars?.pdf",
    "x" * 400 + ".pdf",
    "x" * 400 + "." + "y" * 40,
    "報告" * 150 + ".pdf",
    "報" * 300,
    "😀" * 100 + ".png",
    "a" * 140 + "報告書類.pdf",
    "x" * 300 + ".報告書類",
    "." + "x" * 400,
    ". " * 100 + "name.pdf",
    "name" + ". " * 100,
    "Re: report (final).pdf",
]


def _short(value) -> str:
    """A readable test id for a name that may be hundreds of characters long."""
    return repr(value)[:40]


@pytest.mark.parametrize(("given", "kept"), LEADING_DOT_NAMES, ids=_short)
def test_a_leading_dot_never_survives_however_it_is_spaced(given, kept) -> None:
    assert storage.safe_filename(given) == kept


@pytest.mark.parametrize("given", ODD_NAMES, ids=_short)
def test_applying_it_to_its_own_output_changes_nothing(given) -> None:
    once = storage.safe_filename(given)
    assert storage.safe_filename(once) == once


@pytest.mark.parametrize("given", ODD_NAMES, ids=_short)
def test_every_name_it_returns_keeps_its_promises(given) -> None:
    _check_promises(given, storage.safe_filename(given))


def test_the_cap_is_in_bytes_so_a_cjk_name_fits_one_path_segment() -> None:
    name = storage.safe_filename("報告" * 150 + ".pdf")
    assert name.endswith(".pdf")
    assert len(name.encode()) <= storage.MAX_NAME_BYTES
    # The characters-not-bytes cap this replaced would have left 150 characters, 442 bytes.
    assert len(name) < 150
    assert PREFIX_BYTES + len(name.encode()) <= SEGMENT_BYTES


def test_a_name_is_never_cut_through_a_character() -> None:
    # Four bytes each: the cut falls at 146 bytes, in the middle of the 37th emoji.
    name = storage.safe_filename("😀" * 100 + ".png")
    assert name.endswith(".png")
    assert set(name[: -len(".png")]) == {"😀"}
    assert len(name.encode()) <= storage.MAX_NAME_BYTES


def test_ascii_names_are_cut_exactly_where_they_always_were() -> None:
    cut = storage.safe_filename("x" * 400 + ".pdf")
    assert cut.endswith(".pdf") and len(cut.encode()) == storage.MAX_NAME_BYTES == 150
    assert storage.safe_filename(None) == "file"
    assert storage.safe_filename("Re: report (final).pdf") == "Re_ report (final).pdf"


def test_an_extension_too_long_to_keep_is_cut_with_the_rest() -> None:
    name = storage.safe_filename("x" * 300 + ".報告書類" + "報" * 40)
    assert len(name.encode()) <= storage.MAX_NAME_BYTES
    assert storage.safe_filename(name) == name


def test_the_fallback_is_cleaned_too_so_the_result_is_never_a_hidden_name() -> None:
    assert storage.safe_filename("", fallback="..") == "file"
    assert storage.safe_filename("..", fallback=". .hidden") == "hidden"
    out = storage.safe_filename(None, fallback="a/../b.pdf")
    assert out == "b.pdf" and storage.safe_filename(out) == out


def test_a_seeded_sweep_of_hostile_names_finds_no_name_that_breaks_a_promise() -> None:
    """Forty thousand names made of the pieces that have caused trouble, in random order: every
    separator, dot, space, control, format and combining character, a lone surrogate, characters
    NFC rewrites, and one-, two-, three- and four-byte letters. Seeded, so a failure reproduces."""
    pieces = [
        ".",
        "..",
        ". ",
        " ",
        "/",
        "\\",
        "a",
        "b",
        "é",
        "é",
        "́",
        "̖",
        "​",
        "‮",
        "\x00",
        "\n",
        "\t",
        " ",
        "　",
        " ",
        "<",
        ">",
        ":",
        '"',
        "|",
        "?",
        "*",
        "報",
        "告",
        "😀",
        "̈́",
        ";",
        "Å",
        "ﬁ",
        "\ud800",
        ".pdf",
        "_",
        "-",
    ]
    rng = random.Random(20261004)
    for _ in range(40_000):
        count = rng.choice([0, 1, 2, 3, 5, 8, 14, 60, 200])
        given = "".join(rng.choice(pieces) for _ in range(count))
        _check_promises(given, storage.safe_filename(given))


def _check_promises(given: str, out: str) -> None:
    where = f"{given!r} -> {out!r}"
    assert out, where
    assert storage.safe_filename(out) == out, f"not idempotent: {where}"
    assert "/" not in out and "\\" not in out, where
    assert out not in {".", ".."}, where
    assert out[0] not in ". " and out[-1] not in ". ", f"leading or trailing dot/space: {where}"
    assert len(out.encode()) <= storage.MAX_NAME_BYTES, f"over the byte cap: {where}"
    assert PREFIX_BYTES + len(out.encode()) <= SEGMENT_BYTES, where
    assert unicodedata.normalize("NFC", out) == out, f"not NFC: {where}"
    assert not any(unicodedata.category(c) in _DROPPED for c in out), where
    assert not re.search(r"\s{2,}|[^\S ]", out), f"whitespace other than single spaces: {where}"
    assert not set(out) & set('<>:"|?*'), where
