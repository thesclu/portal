"""Non-negotiable #5: no statutory day count may live in code. Numbers live in
packages/deadline_rules/data/rules.toml, entered by owner with citations.

Scans every .py file under backend/portal, migrations included -- schema.sql itself is never
embedded as text in a .py file (records_db.0001 reads it from disk at import time), so this scan
cannot be tripped by the schema.
"""

import re
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_DIR / "backend" / "portal"

PATTERNS = [
    # Call form: timedelta(days=10), relativedelta(days=+30). Signed literals included.
    re.compile(r"\b(?:timedelta|relativedelta)\(\s*(?:days|weeks)\s*=\s*[+-]?\d+", re.IGNORECASE),
    # Keyword/constant form: days=10, delay_days=14, CPRA_DETERMINATION_DAYS = 10.
    # A plain \b fails here because "_" is a word character, so `_DAYS` never has a
    # word/non-word transition before "DAYS" -- exclude only letters/digits instead, which
    # still lets "_" immediately precede the match.
    re.compile(r"(?<![A-Za-z0-9])days?\s*=\s*[+-]?\d+", re.IGNORECASE),
    # Prose form inline with the number: "10 calendar days", "10-day".
    re.compile(r"\b\d+\s*(calendar|business|working)?[-\s]*days?\b", re.IGNORECASE),
    # Same line, number and "day(s)" word not adjacent: `WINDOW = 10  # days`. Two
    # independent lookaheads so order/position on the line doesn't matter.
    re.compile(r"(?=.*\b\d+\b)(?=.*\bdays?\b)", re.IGNORECASE),
]

# Positive control (F2): each of these is a realistic way the statutory-day-count violation
# would actually be written. If a future edit narrows PATTERNS and stops catching one of
# these, this list is what fails -- not the "zero matches on real code" assertion below,
# which only proves the scanner is quiet, never that it is watching.
VIOLATION_SAMPLES = [
    "CPRA_DETERMINATION_DAYS = 10",
    "AGENCY_RESPONSE_WINDOW = 10  # days",
    "due = start + relativedelta(days=+30)",
    "schedule_followup(delay_days=14)",
    "due = start + timedelta(days=10)",
    "# the agency has 10 calendar days to respond",
]


def _matches_any_pattern(line: str) -> bool:
    return any(pattern.search(line) for pattern in PATTERNS)


def test_patterns_catch_every_known_violation_shape() -> None:
    missed = [sample for sample in VIOLATION_SAMPLES if not _matches_any_pattern(sample)]
    assert missed == [], f"PATTERNS no longer catches: {missed}"


def test_no_hardcoded_day_counts_in_backend() -> None:
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
