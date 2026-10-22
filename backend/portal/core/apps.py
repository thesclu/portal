from django.apps import AppConfig
from django.db.models.signals import post_migrate, pre_migrate


class CoreConfig(AppConfig):
    name = "portal.core"
    label = "core"
    verbose_name = "Portal core"

    def ready(self) -> None:
        from portal.core.chat import checks as chat_checks  # noqa: F401 (registers the checks)
        from portal.core.keycloak import checks as keycloak_checks  # noqa: F401 (registers them)
        from portal.core.services import signins
        from portal.db.hooks import ensure_schemas, grant_core_to_app_role

        pre_migrate.connect(ensure_schemas, dispatch_uid="portal.db.ensure_schemas")
        post_migrate.connect(grant_core_to_app_role, dispatch_uid="portal.db.grant_core")
        signins.connect()
        _admin_signs_in_through_keycloak()


def _admin_signs_in_through_keycloak() -> None:
    """P25a: Django's admin had its own password form, a second door. Nobody holds a password
    here any more, so its sign-in page sends people through Keycloak and back (the admin itself
    still asks for `is_staff`)."""
    from urllib.parse import urlencode

    from django.contrib import admin
    from django.http import HttpResponseRedirect

    def login(request, extra_context=None):  # type: ignore[no-untyped-def]
        back = request.GET.get("next") or "/admin/"
        return HttpResponseRedirect(f"/api/core/auth/oidc/login?{urlencode({'next': back})}")

    admin.site.login = login  # type: ignore[method-assign]
