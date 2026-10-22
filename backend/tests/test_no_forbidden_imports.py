"""Non-negotiable #6b: never import from `panel/`, `portal_build_context/`, or any CSV.

Those are the old, unverified data sources this rebuild deliberately does not trust (see
notes and the notes ruling that ground truth is the two schema files only).
Scans every .py file under backend/portal and every packages/*/src tree for an import of
either module or a path/string reference to either directory or to a .csv file. Mirrors
test_no_day_counts.py's file-scanning shape: PATTERNS list, a positive-control test that
pins the patterns against realistic violation samples, and a scan test that also asserts it
examined a non-zero number of files (an empty scan root would otherwise pass vacuously).
"""

import re
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]

SCAN_ROOTS = [REPO_DIR / "backend" / "portal"]
SCAN_ROOTS.extend(sorted((REPO_DIR / "packages").glob("*/src")))

PATTERNS = [
    # import panel / from panel import ... / from panel.x import y
    re.compile(r"^\s*(?:import|from)\s+panel(?:\s|\.|$)"),
    # import portal_build_context / from portal_build_context import ...
    re.compile(r"^\s*(?:import|from)\s+portal_build_context(?:\s|\.|$)"),
    # a path or string literal that points into either directory
    re.compile(r"""['"](?:\.\./)*(?:panel|portal_build_context)/"""),
    # a path or string literal naming a .csv file
    re.compile(r"""\.csv['"]"""),
]

VIOLATION_SAMPLES = [
    "import panel",
    "from panel import charts",
    "from portal_build_context import brief",
    "import portal_build_context.rulebook as rb",
    "TRACKER_PATH = Path('../../portal_build_context/tracker.csv')",
    "df = pd.read_csv('panel/data/export.csv')",
]


def _matches_any_pattern(line: str) -> bool:
    return any(pattern.search(line) for pattern in PATTERNS)


def test_patterns_catch_every_known_violation_shape() -> None:
    missed = [sample for sample in VIOLATION_SAMPLES if not _matches_any_pattern(sample)]
    assert missed == [], f"PATTERNS no longer catches: {missed}"


def test_no_panel_or_portal_build_context_or_csv_references() -> None:
    matches: list[str] = []
    files_scanned = 0
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            files_scanned += 1
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, start=1):
                if _matches_any_pattern(line):
                    matches.append(f"{path.relative_to(REPO_DIR)}:{lineno}: {line.strip()}")
    assert files_scanned > 0, f"no .py files found under {SCAN_ROOTS} -- scan roots are wrong"
    assert matches == [], "\n".join(matches)
