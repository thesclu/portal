import pytest
from django.db import DatabaseError, connection


@pytest.fixture
def app_role(db):
    """Run the test body as portal_app. SET LOCAL reverts when pytest-django rolls back.

    SET LOCAL ROLE outside a transaction block is only a WARNING, not an error -- it silently
    no-ops and current_user stays whatever it already was. pytest-django's `db` fixture (the
    default, transaction=False) always wraps a test in a transaction, so this normally holds,
    but a test written `django_db(transaction=True)` plus this marker would run entirely as
    the owner while the marker claims otherwise. Assert the switch actually took effect
    instead of trusting it silently.
    """
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ROLE portal_app")
        # P17: the policies read app.context; a service-level test is the portal's own work.
        # A test about the policies themselves sets `web` on top (tests/test_rls.py).
        cur.execute("SELECT set_config('app.context', 'system', true)")
        cur.execute("select current_user")
        current = cur.fetchone()[0]
    assert current == "portal_app", (
        f"SET LOCAL ROLE did not take effect (current_user is {current!r} instead of "
        "'portal_app'). SET LOCAL only works inside a transaction block -- this fixture "
        "requires pytest-django's default django_db(transaction=False), not "
        "django_db(transaction=True)."
    )
    yield
    # RESET ROLE is redundant with the rollback pytest-django's `db` fixture performs next
    # (SET LOCAL reverts on rollback too), and it runs BEFORE that rollback. If the test left
    # the transaction aborted (a raw, un-atomic()-wrapped statement failed), issuing RESET ROLE
    # here would itself raise "current transaction is aborted" -- a confusing second teardown
    # error on top of the real one. Tolerate that; the rollback resets the role regardless.
    with connection.cursor() as cur:
        try:
            cur.execute("RESET ROLE")
        except DatabaseError:
            pass


@pytest.fixture(autouse=True)
def _apply_app_role_marker(request):
    if request.node.get_closest_marker("app_role"):
        request.getfixturevalue("app_role")


# ---- P23a: test people have finished the new-user setup, as the seed's do -------------------
#
# Since P23a the server refuses every route but the setup's own to a person whose setup is
# unfinished or who has not accepted an agreement version in force (portal.core.rls). The
# development seed marks its people finished (`seed_synthetic --setup-only`); the tests do the
# same for every account they make, so a test about something else is not stopped at the door:
# when a user row is created, it is marked finished and given an acceptance of each agreement in
# force, both kinds, whatever the role (so a later role change does not bring a pending one).
# The acceptance rows are synthetic: no signature file, no PDF.
#
# A test about the setup itself is marked `unfinished_setup` and gets people exactly as
# production makes them: unfinished, with nothing accepted. Publishing a new version in any test
# still leaves everyone it applies to with a pending acceptance, as in production.


def _finish_new_people(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return
    from django.utils import timezone
    from portal.core.models import Agreement, AgreementAcceptance, User

    now = timezone.now()
    for kind in Agreement.KINDS:
        agreement = Agreement.objects.filter(kind=kind).order_by("-version").first()
        if agreement is None:
            continue
        AgreementAcceptance.objects.create(
            agreement=agreement,
            user=instance,
            accepted_at=now,
            signed_name=instance.name or "Synthetic Person",
            signature_storage_name="test://no-signature-file",
            signature_content_type="image/png",
            signature_sha256="0" * 64,
            ip_address="192.0.2.1",
            user_agent="tests/conftest.py",
            pdf_storage_name="test://no-pdf",
            pdf_sha256="0" * 64,
        )
    User.objects.filter(pk=instance.pk).update(setup_completed_at=now)
    instance.setup_completed_at = now


@pytest.fixture(autouse=True)
def _people_have_finished_the_setup(request):
    if request.node.get_closest_marker("unfinished_setup"):
        yield
        return
    from django.conf import settings
    from django.db.models.signals import post_save

    uid = "tests.finish_new_people"
    post_save.connect(_finish_new_people, sender=settings.AUTH_USER_MODEL, dispatch_uid=uid)
    try:
        yield
    finally:
        post_save.disconnect(sender=settings.AUTH_USER_MODEL, dispatch_uid=uid)


# ---- P25a: no test reaches a real Keycloak by accident ---------------------------------------
#
# The development Keycloak may well be running on this machine; a test that happens to walk the
# sign-in routes (the census, the setup's route walk) must not touch it. Every test gets a
# Keycloak address that cannot resolve (`.invalid`) unless it fakes Keycloak itself
# (tests/core/keycloak_fake.py) or is one of the integration tests (tests/keycloak/, which opt in
# with the `keycloak_integration` marker).


@pytest.fixture(autouse=True)
def _no_real_keycloak(request, settings):
    if request.node.get_closest_marker("keycloak_integration"):
        yield
        return
    from portal.core.keycloak import admin as kc_admin
    from portal.core.keycloak import oidc, sessions

    settings.KEYCLOAK = {
        **settings.KEYCLOAK,
        "ISSUER": "https://keycloak.invalid/auth/realms/sclu",
        "INTERNAL_URL": "http://keycloak.invalid/auth",
        "REALM": "sclu",
        "CLIENT_SECRET": "no-real-keycloak-in-tests",
        "ADMIN_CLIENT_SECRET": "no-real-keycloak-in-tests",
        "TIMEOUT_SECONDS": 2.0,
    }
    oidc.reset_client()
    kc_admin.reset_client()
    sessions.forget_policy()
    yield
    oidc.reset_client()
    kc_admin.reset_client()
    sessions.forget_policy()


@pytest.fixture
def staff_user(db):
    from portal.core.services.identity import create_user

    return create_user(
        email="staff@example.org",
        name="Staff Person",
        records_role="staff",
    )


@pytest.fixture
def colleague_user(db):
    """A second person who can be assigned work: staff, active."""
    from portal.core.services.identity import create_user

    return create_user(
        email="colleague@example.org",
        name="Colleague Person",
        records_role="staff",
    )


@pytest.fixture
def admin_user(db):
    from portal.core.services.identity import create_user

    return create_user(
        email="admin@example.org",
        name="Admin Person",
        records_role="admin",
        is_staff=True,
    )


@pytest.fixture
def agency(db, staff_user):
    """Through the service, not the model: P2 made directory.create_agency the only way in."""
    from portal.records.actors import Actor
    from portal.records.services import directory

    return directory.create_agency(
        Actor.human(staff_user),
        name="Test Agency",
        jurisdiction="Testland",
        records_law="TESTLAW",
    )


@pytest.fixture(params=["owner", "app_role"])
def as_each_role(request, db):
    """Run the test twice: once as the migration owner, once as portal_app.

    The owner can do anything, so a service that works for it proves nothing about the grants the
    running application actually has. Use as `pytestmark = pytest.mark.usefixtures("as_each_role")`
    in a module whose tests are all plain `db` tests. It cannot be combined with
    `django_db(transaction=True)` (SET LOCAL ROLE needs the surrounding transaction), so threaded
    tests live in their own module and run as the owner. Because it is applied before the other
    fixtures, `staff_user`, `agency` and the rest are created as portal_app too.
    """
    if request.param == "app_role":
        request.getfixturevalue("app_role")
    return request.param


@pytest.fixture
def media(tmp_path, settings):
    """Point file storage at a temporary directory, so a test that stores a file leaves nothing in
    the repository's media folder. Returns the directory."""
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path)},
        },
    }
    return tmp_path
