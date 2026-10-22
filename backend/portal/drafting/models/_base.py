"""The base every drafting model uses, and the one thing it deliberately is not.

`DraftingModel` is NOT `records.models.LedgerModel`, and that is the point rather than an
oversight. Deliberate departure #1 in drafting-schema.md: DELETE is granted on these tables
because an unfiled draft is a scratch document, not a record of anything. `LedgerModel.delete()`
raises unconditionally, so inheriting it would make the grant unreachable from Python and turn
the carve-out into dead SQL. The two BEFORE DELETE triggers in the schema are what bounds the
grant, and they bound it for every caller -- psql included -- rather than only for the ORM.

There is no `writable()` guard here either. That context manager exists to keep everything
outside `records.services` away from the ledger; drafting rows are ordinary application rows
whose worst-case loss is a letter nobody sent, and copying the idiom would claim a protection
this schema does not need.

What is inherited is only the unmanaged-view part: these tables are created by
drafting_db.0001 from the pinned SQL, never by Django.
"""

from django.db import models


class DraftingModel(models.Model):
    class Meta:
        abstract = True
        managed = False
