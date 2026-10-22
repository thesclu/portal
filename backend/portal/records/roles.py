"""The six roles, grouped by what they decide (P20h; DECISIONS 37a, 42, 43; ARCHITECTURE §6).

Every place in the backend that asks "may this person …" by role asks one of these groups, so a
role nobody thought of fails closed: a group lists the roles it admits, never the ones it refuses.

  FULL_STAFF      admin, staff, staff attorney. See every request that is not privileged, work
                  the whole organisation's tables (the directory, templates, saved replies), read
                  unplaced mail, file and send without approval.
  TEAM            FULL_STAFF plus the limited-scope attorney and the intern: the people who work
                  requests at all. What a TEAM member may see is the visibility rule
                  (`records/services/access.py`), which is narrower for the last two.
  GRANT_SCOPED    limited-scope attorney and intern: an ordinary request reaches them only through
                  a grant (an intern also sees the requests interns made).
  NEEDS_APPROVAL  the intern: nothing they write leaves the portal without an approver's yes.
  APPROVERS       admin and staff attorney approve any intern submission they can see; a
                  limited-scope attorney approves one on a request they hold a grant for
                  (`core/services/review.py::may_approve`). Staff do not approve (42b).
  ADMIN           grants and revokes access, the Users screen, the settings; sees every request,
                  privileged ones included (37a: "privileged requests stay grant-only except for
                  admins").
  NON_ATTORNEY    staff, intern, requester: asked to agree to "Working as an intern" in the
                  new-user setup (P20i); administrators count as attorneys there.

`core.User.is_staff` still means "this person is an administrator" (DECISIONS 22, `set_role`).
"""

from uuid import UUID

from portal.records.models.choices import RecordsRole

ADMIN = RecordsRole.ADMIN.value
STAFF = RecordsRole.STAFF.value
STAFF_ATTORNEY = RecordsRole.STAFF_ATTORNEY.value
LIMITED_ATTORNEY = RecordsRole.LIMITED_ATTORNEY.value
INTERN = RecordsRole.INTERN.value
REQUESTER = RecordsRole.REQUESTER.value

FULL_STAFF: tuple[str, ...] = (ADMIN, STAFF, STAFF_ATTORNEY)
GRANT_SCOPED: tuple[str, ...] = (LIMITED_ATTORNEY, INTERN)
TEAM: tuple[str, ...] = FULL_STAFF + GRANT_SCOPED
NEEDS_APPROVAL: tuple[str, ...] = (INTERN,)
# Approve any intern submission they can see. The limited-scope attorney approves only with a grant
# on the request, which is a fact about the request, so it is decided in `may_approve`.
APPROVERS: tuple[str, ...] = (ADMIN, STAFF_ATTORNEY)
# P20i: who is not an attorney, for the new-user setup's "Working as an intern" agreement (v6 011,
# "non-attorney roles only"). Administrators count as attorneys and skip it, as staff attorneys
# and limited-scope attorneys do (QUESTIONS 4's default).
NON_ATTORNEY: tuple[str, ...] = (STAFF, INTERN, REQUESTER)

ALL: tuple[str, ...] = tuple(RecordsRole.values)


def role_of(user_id: UUID | None) -> str | None:
    """A person's records role, read from `records.users`; None for nobody or no profile."""
    if user_id is None:
        return None
    from portal.records.models import RecordsUser

    return RecordsUser.objects.filter(pk=user_id).values_list("role", flat=True).first()


def needs_approval(user_id: UUID | None) -> bool:
    """Whether what this person writes waits for an approver before it leaves the portal."""
    return role_of(user_id) in NEEDS_APPROVAL
