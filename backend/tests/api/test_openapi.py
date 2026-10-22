"""The exported schema names every path BUILD_PLAN's P3 section lists, and the committed
`frontend/openapi.json` -- what P4a's TypeScript client is actually generated from -- matches a
fresh export exactly. Nothing here builds the live schema and calls it a day: a schema that is
correct in memory but stale on disk is invisible to every test that only checks the live API."""

import json
from pathlib import Path

from ninja.responses import NinjaJSONEncoder

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMMITTED_SCHEMA_PATH = _REPO_ROOT / "frontend" / "openapi.json"

EXPECTED = {
    ("GET", "/api/records/agencies"),
    ("POST", "/api/records/agencies"),
    ("PATCH", "/api/records/agencies/{agency_id}"),
    ("GET", "/api/records/agencies/{agency_id}/contacts"),
    ("POST", "/api/records/agencies/{agency_id}/contacts"),
    ("GET", "/api/records/requests"),
    ("POST", "/api/records/requests"),
    ("GET", "/api/records/requests/{request_id}"),
    ("POST", "/api/records/requests/{request_id}/file"),
    ("POST", "/api/records/requests/{request_id}/status"),
    ("POST", "/api/records/requests/{request_id}/edits"),
    ("POST", "/api/records/requests/{request_id}/events"),
    ("POST", "/api/records/events/{event_id}/void"),
    ("POST", "/api/records/requests/{request_id}/deadlines"),
    ("POST", "/api/records/deadlines/{deadline_id}/satisfy"),
    ("POST", "/api/records/deadlines/{deadline_id}/waive"),
    ("POST", "/api/records/deadlines/{deadline_id}/supersede"),
    ("GET", "/api/records/sources"),
    ("GET", "/api/records/sources/{source_id}"),
    ("POST", "/api/records/sources/{source_id}/assign"),
    ("POST", "/api/records/sources/{source_id}/reviewed"),
    ("GET", "/api/records/tasks"),
    ("POST", "/api/records/tasks"),
    ("POST", "/api/records/tasks/{task_id}/complete"),
    ("POST", "/api/records/requests/{request_id}/fees"),
    ("PATCH", "/api/records/fees/{fee_id}"),
    ("GET", "/api/records/definitions"),
    # P8a: the design-v2 ledger-side doors.
    ("GET", "/api/records/agencies/{agency_id}"),
    ("POST", "/api/records/requests/{request_id}/assign"),
    ("POST", "/api/records/requests/{request_id}/narrow"),
    ("POST", "/api/records/requests/{request_id}/items"),
    ("PATCH", "/api/records/tasks/{task_id}"),
    ("POST", "/api/records/tasks/{task_id}/assign"),
    ("POST", "/api/records/tasks/{task_id}/reopen"),
    ("POST", "/api/records/deadlines/{deadline_id}/resolve"),
    ("PATCH", "/api/core/users/{user_id}"),
    # P8b: the Inbox, "Record as...", and the read views. `POST /sources/{id}/assign` still means
    # "attach to a request"; the person-assignment door is `/assignee`.
    ("GET", "/api/records/inbox"),
    ("POST", "/api/records/sources/{source_id}/flag"),
    ("POST", "/api/records/sources/{source_id}/assignee"),
    ("POST", "/api/records/sources/{source_id}/unreview"),
    ("POST", "/api/records/sources/{source_id}/resolve"),
    ("GET", "/api/records/sources/{source_id}/notes"),
    ("POST", "/api/records/sources/{source_id}/notes"),
    ("POST", "/api/records/source-notes/{note_id}/void"),
    ("POST", "/api/records/sources/{source_id}/records"),
    ("POST", "/api/records/requests/{request_id}/documents"),
    ("GET", "/api/records/events/{event_id}/classify"),
    ("POST", "/api/records/events/{event_id}/classify"),
    ("GET", "/api/records/attachments/{attachment_id}/download"),
    ("GET", "/api/records/sources/{source_id}/raw"),
    ("GET", "/api/records/home"),
    ("GET", "/api/records/calendar"),
    ("GET", "/api/records/rules"),
}


def test_every_p3_path_is_registered():
    from portal.api import api

    schema = api.get_openapi_schema()
    found = {
        (method.upper(), path) for path, methods in schema["paths"].items() for method in methods
    }
    missing = EXPECTED - found
    assert not missing, f"missing from the live API: {missing}"


def test_committed_openapi_json_matches_a_fresh_export():
    """`export_openapi_schema` writes `frontend/openapi.json` with
    `json.dumps(schema, cls=NinjaJSONEncoder, indent=2)`; comparing decoded dicts (not raw text)
    means this test cares about content drift, not incidental whitespace, and encodes UUIDs/dates
    through the same `NinjaJSONEncoder` the real export command uses, so nothing here has its own,
    possibly-different idea of how those serialise."""
    from portal.api import api

    fresh = json.loads(json.dumps(api.get_openapi_schema(), cls=NinjaJSONEncoder))
    committed = json.loads(_COMMITTED_SCHEMA_PATH.read_text())
    assert committed == fresh, (
        "frontend/openapi.json is stale -- regenerate it with "
        "`uv run backend/manage.py export_openapi_schema --api portal.api.api "
        "--output frontend/openapi.json --indent 2`"
    )


def test_no_two_schema_classes_share_a_name() -> None:
    """ninja keys components by class name: two `ReviewOut`s in different apps are silently
    merged into one shape in openapi.json and the TypeScript client, and nothing fails until a
    screen reads a field that is not there. It bit P6 (`CalendarOut`), P11 (`RecipientOut`,
    `MessageOut`) and P13 (`ReviewOut`). So: every Schema class defined anywhere under
    `portal.*.api` has a name of its own. The modules are walked, not listed, so a new router
    cannot be left out."""
    import importlib
    import inspect
    import pkgutil

    import portal
    from ninja import Schema

    modules = []
    for app in pkgutil.iter_modules(portal.__path__):
        try:
            api_pkg = importlib.import_module(f"portal.{app.name}.api")
        except ModuleNotFoundError:
            continue
        modules.append(api_pkg)
        if hasattr(api_pkg, "__path__"):
            for sub in pkgutil.iter_modules(api_pkg.__path__):
                modules.append(importlib.import_module(f"portal.{app.name}.api.{sub.name}"))
    assert len(modules) >= 8, [m.__name__ for m in modules]
    seen: dict[str, str] = {}
    clashes: dict[str, list[str]] = {}
    for module in modules:
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, Schema) or obj is Schema or obj.__module__ != module.__name__:
                continue
            if name in seen and seen[name] != module.__name__:
                clashes.setdefault(name, [seen[name]]).append(module.__name__)
            seen[name] = module.__name__
    assert clashes == {}, clashes
