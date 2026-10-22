"""Pin the two schema files byte-for-byte. Non-negotiable #1: neither is ever edited.

If a sha256 in this test ever needs to change, that is a signal something touched a schema file,
not a signal to update the constant.

Both files exist twice on purpose -- once as the reference copy owner edits and once inside the
repo, where the migration reads it -- and the second test of each pair is what keeps the two
copies moving together. It skips rather than fails when the reference tree is absent, because a
CI checkout of the app alone is a legitimate way to run this suite.
"""

import hashlib
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_DIR / "db" / "records" / "schema.sql"
RECORDS_SHA256 = "4b0401da7a003dc0420db04b97474edb63079af4e7b74d80d06bf0d24f9b296a"

DRAFTING_PATH = REPO_DIR / "db" / "drafting" / "drafting-schema.sql"
DRAFTING_SHA256 = "40b68bc5859c3b90103d5697b74bfb9d404fd21d14601ea095aa83b2ce551511"
MAIL_PATH = REPO_DIR / "db" / "mail" / "mail-schema.sql"
MAIL_SHA256 = "87d59ddfeef8ea8e96144ed04cd340b65a1195fda1a3cf11aad85930cd069dd3"

# The two ground-truth schema files are kept in the repo (reference/portal-build-schema) so any
# machine has them; the original folder outside the repo is the fallback.
REFERENCE_DIR = next(
    (
        d
        for d in (
            REPO_DIR / "reference" / "portal-build-schema",
            (REPO_DIR / ".." / ".." / "portal build schema").resolve(),
        )
        if d.is_dir()
    ),
    REPO_DIR / "reference" / "portal-build-schema",
)


def test_schema_sql_sha256_pinned() -> None:
    digest = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    assert digest == RECORDS_SHA256


def test_schema_sql_matches_reference_copy() -> None:
    reference_path = REFERENCE_DIR / "schema.sql"
    if not reference_path.exists():
        pytest.skip(f"reference copy not present at {reference_path}")
    assert SCHEMA_PATH.read_bytes() == reference_path.read_bytes()


def test_drafting_schema_sql_sha256_pinned() -> None:
    digest = hashlib.sha256(DRAFTING_PATH.read_bytes()).hexdigest()
    assert digest == DRAFTING_SHA256


def test_drafting_schema_sql_matches_reference_copy() -> None:
    reference_path = REFERENCE_DIR / "drafting-schema.sql"
    if not reference_path.exists():
        pytest.skip(f"reference copy not present at {reference_path}")
    assert DRAFTING_PATH.read_bytes() == reference_path.read_bytes()


def test_mail_schema_sql_sha256_pinned() -> None:
    """The third schema file (BUILD_PLAN P11). It has no reference copy outside the repo: it was
    written here, under the same rule, and the pin is the whole of its protection."""
    digest = hashlib.sha256(MAIL_PATH.read_bytes()).hexdigest()
    assert digest == MAIL_SHA256
