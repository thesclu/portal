"""The only place the records ledger is written.

Every module here enters `writable()` around its model writes; nothing outside this package can.
Import the modules, not the functions -- `services.status.override_status(...)` reads as the
chokepoint it is, where a bare `override_status(...)` does not.
"""
