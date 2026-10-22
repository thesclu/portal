"""Every records model is an unmanaged view over a table schema.sql defines.

Two guards live here. `save()` refuses unless the caller is inside
`writable()`, which only the service layer enters, so an API handler, an admin
screen or a stray script cannot write a ledger row by accident. `delete()`
always refuses: rows in this schema are voided or superseded, never removed,
and the database role the app runs as has no DELETE privilege either.
"""

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

from django.db import models

_WRITABLE: ContextVar[bool] = ContextVar("records_writable", default=False)


class UseServicesInstead(RuntimeError):
    """Raised when something outside portal.records.services tries to write the ledger."""


@contextlib.contextmanager
def writable() -> Iterator[None]:
    token = _WRITABLE.set(True)
    try:
        yield
    finally:
        _WRITABLE.reset(token)


def is_writable() -> bool:
    return _WRITABLE.get()


class LedgerModel(models.Model):
    class Meta:
        abstract = True
        managed = False

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not _WRITABLE.get():
            raise UseServicesInstead(
                f"{type(self).__name__} rows are written only by portal.records.services."
            )
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise UseServicesInstead(
            f"{type(self).__name__} rows are never deleted; void or supersede them instead."
        )
