"""Non-negotiable #2: ledger writes only through the service layer, inside writable().

records/models/_base.py:42-52 is the guard: LedgerModel.save() refuses unless the caller is
inside writable(), and LedgerModel.delete() refuses unconditionally. Nothing else in the
suite exercised this Python-level guard directly (writable() only ever appeared inside the
`agency` fixture, always already inside the context), so deleting the guard left the whole
suite green. These tests exercise save()/delete() directly, in and out of writable().
"""

import pytest
from portal.records.models import Agency, UseServicesInstead, writable


def test_save_outside_writable_raises(db) -> None:
    agency = Agency(name="Test Agency", jurisdiction="California")
    with pytest.raises(UseServicesInstead, match="written only by"):
        agency.save()


def test_save_inside_writable_succeeds(db) -> None:
    with writable():
        agency = Agency.objects.create(name="Test Agency", jurisdiction="California")
    assert agency.pk is not None


def test_delete_always_raises_outside_writable(db) -> None:
    agency = Agency(name="Test Agency", jurisdiction="California")
    with pytest.raises(UseServicesInstead, match="never deleted"):
        agency.delete()


def test_delete_always_raises_inside_writable(db) -> None:
    # The ban is unconditional -- writable() only ever lifts the save() guard, never delete().
    with writable():
        agency = Agency.objects.create(name="Test Agency", jurisdiction="California")
        with pytest.raises(UseServicesInstead, match="never deleted"):
            agency.delete()
