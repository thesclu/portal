"""The two purity guarantees this package exists to keep:

1. `deadline_rules` never imports django -- P0's HANDOFF notes it is already a runtime dependency
   of the Django app (`sclu-portal`), so an import error here breaks the app too; staying
   Django-free is what makes it safe to unit test without a database and safe to reuse outside
   this app later.
2. No statutory day count lives anywhere in `src/deadline_rules/**/*.py` -- this is the one
   package whose entire reason for existing is that day counts live in data
   (`data/rules.toml`), not code. If a number like this ever appears in the source, the data
   layer stopped being the only place a legal day count can live.

PATTERNS below are the same shape as backend/tests/test_no_day_counts.py's (P0's review gate
rewrote that scanner after the original version missed four real violation shapes -- see
notes's 2026-09-08 P0 review-gate entry, finding F2). VIOLATION_SAMPLES here uses
fictional TESTLAW-style names, never CPRA -- nothing CPRA-shaped belongs anywhere under
packages/deadline_rules, even as a test string.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[3]
SCAN_ROOT = REPO_DIR / "packages" / "deadline_rules" / "src" / "deadline_rules"

PATTERNS = [
    # Call form: timedelta(days=10), relativedelta(days=+30). Signed literals included.
    re.compile(r"\b(?:timedelta|relativedelta)\(\s*(?:days|weeks)\s*=\s*[+-]?\d+", re.IGNORECASE),
    # Keyword/constant form: days=10, delay_days=14, TESTLAW_DETERMINATION_DAYS = 10.
    # A plain \b fails here because "_" is a word character, so `_DAYS` never has a
    # word/non-word transition before "DAYS" -- exclude only letters/digits instead, which
    # still lets "_" immediately precede the match.
    re.compile(r"(?<![A-Za-z0-9])days?\s*=\s*[+-]?\d+", re.IGNORECASE),
    # Prose form inline with the number: "10 calendar days", "10-day".
    re.compile(r"\b\d+\s*(calendar|business|working)?[-\s]*days?\b", re.IGNORECASE),
    # Same line, number and "day(s)" word not adjacent: `WINDOW = 10  # days`. Two
    # independent lookaheads so order/position on the line doesn't matter.
    re.compile(r"(?=.*\b\d+\b)(?=.*\bdays?\b)", re.IGNORECASE),
    # G6: a positional integer literal passed to this package's own day-arithmetic helpers, or
    # to timedelta/relativedelta without the `days=` keyword -- e.g. `add_calendar_days(start,
    # 10)`, `add_business_days(start, 12, cal)`, `timedelta(10)`. This is the realistic
    # violation shape for THIS package specifically: compute._due_date already calls exactly
    # these two helpers, so a statutory count could enter as one character's difference from
    # code that already ships (`rule.days` -> a literal). The first pattern above only catches
    # the keyword form (`days=10`); this one catches the positional form the same call could
    # just as easily use.
    re.compile(
        r"\b(?:add_calendar_days|add_business_days|timedelta|relativedelta)\(\s*[^,()]*,?\s*[+-]?\d",
        re.IGNORECASE,
    ),
    # G6: an identifier whose name contains "day"/"days" anywhere, assigned an integer literal --
    # e.g. `days_count = 10`, `delay_days_total = 14`. Broader than the keyword-form pattern
    # above, which requires the token immediately before "=" to BE "day(s)"; this one also
    # catches "day(s)" as a substring inside a longer identifier.
    re.compile(r"(?<![A-Za-z0-9_])\w*days?\w*\s*=\s*[+-]?\d+", re.IGNORECASE),
]

# Positive control: each of these is a realistic way the statutory-day-count violation would
# actually be written. If a future edit narrows PATTERNS and stops catching one of these, this
# list is what fails -- not the "zero matches on real source" assertion below, which only
# proves the scanner is quiet, never that it is watching. Deliberately fictional/generic names,
# never CPRA or any real law -- these exist to prove pattern shape coverage, not to describe any
# real deadline.
VIOLATION_SAMPLES = [
    "TESTLAW_DETERMINATION_DAYS = 10",
    "FOLLOWUP_WINDOW = 10  # days",
    "due = start + relativedelta(days=+30)",
    "schedule_followup(delay_days=14)",
    "due = start + timedelta(days=10)",
    "# this window is 10 calendar days long",
    # G6: this package's own idiom -- the day-arithmetic helper calls compute._due_date already
    # makes, keyword and positional, plus the identifier-assignment form. All six of these are
    # the exact samples the fix-batch review (G6) named as missed by the patterns above it.
    "due = add_calendar_days(clock_start, days=10)",  # was already caught (keyword form)
    "due = add_calendar_days(clock_start, 10)",
    "due = add_business_days(clock_start, 12, cal)",
    "due = start + timedelta(10)",
    "days_count = 10",
    "DETERMINATION_WINDOW = 10",
]

# G6: DETERMINATION_WINDOW = 10 is genuinely undecidable for a line-level scanner -- nothing on
# that line names "day" at all, and a scanner permissive enough to flag every bare
# `SOME_CONSTANT = <int>` would flag every page size, retry count, and batch size in any real
# codebase, not just statutory day counts. Tracked here so the gap stays visible and reviewed
# rather than silently dropped, and deliberately excluded from the "must be caught" assertion
# below -- do not contort PATTERNS trying to catch it.
_KNOWN_UNCATCHABLE_SAMPLES = frozenset({"DETERMINATION_WINDOW = 10"})


def _matches_any_pattern(line: str) -> bool:
    return any(pattern.search(line) for pattern in PATTERNS)


def test_patterns_catch_every_known_violation_shape() -> None:
    missed = [
        sample
        for sample in VIOLATION_SAMPLES
        if sample not in _KNOWN_UNCATCHABLE_SAMPLES and not _matches_any_pattern(sample)
    ]
    assert missed == [], f"PATTERNS no longer catches: {missed}"


def test_no_hardcoded_day_counts_in_deadline_rules_source() -> None:
    matches: list[str] = []
    files_scanned = 0
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        files_scanned += 1
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if _matches_any_pattern(line):
                matches.append(f"{path.relative_to(REPO_DIR)}:{lineno}: {line.strip()}")
    assert files_scanned > 0, f"no .py files found under {SCAN_ROOT} -- scan root is wrong"
    assert matches == [], "\n".join(matches)


def test_importing_deadline_rules_does_not_import_django() -> None:
    """Run in a fresh interpreter: `just test` collects this alongside backend/tests, which DOES
    import django, in the same pytest process -- an in-process `'django' in sys.modules` check
    would pass or fail depending on test collection order, not on what deadline_rules itself
    imports. A subprocess is the only way to ask the real question.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import deadline_rules, sys; print('django' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stdout + result.stderr
