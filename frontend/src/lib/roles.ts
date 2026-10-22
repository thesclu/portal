/**
 * The six roles (P20h; DECISIONS 37a, 42, 43; v6 ARCHITECTURE §6), as the screens need them: the
 * words, the one-line descriptions Invite and Change role draw (206, 207), and the groups that
 * decide what a screen offers. The server decides; these only choose what to draw, and they
 * mirror `backend/portal/records/roles.py` group for group.
 */

export type Role =
  'admin' | 'staff' | 'staff_attorney' | 'limited_attorney' | 'intern' | 'requester'

/** In the order Invite and Change role list them (206, 207). */
export const ROLES: Role[] = [
  'admin',
  'staff',
  'staff_attorney',
  'limited_attorney',
  'intern',
  'requester',
]

export const ROLE_LABEL: Record<Role, string> = {
  admin: 'Admin',
  staff: 'Staff',
  staff_attorney: 'Staff attorney',
  limited_attorney: 'Attorney, limited scope',
  intern: 'Intern',
  requester: 'Requester',
}

/** The short form a list row or a byline uses (230 "Attorney · given Oct 12", 234 "· Intern"). */
export const ROLE_SHORT: Record<Role, string> = {
  admin: 'Admin',
  staff: 'Staff',
  staff_attorney: 'Staff attorney',
  limited_attorney: 'Attorney',
  intern: 'Intern',
  requester: 'Requester',
}

export const ROLE_DESCRIPTION: Record<Role, string> = {
  admin: 'Manages users, settings and templates. Sees everything.',
  staff: 'Sees and works on every request.',
  staff_attorney: 'Sees and works on everything, can file, and approves intern letters.',
  limited_attorney:
    'Sees only the requests an admin gives them, and can do everything on those, including approving intern letters.',
  intern:
    'Sees requests made by interns and requests they’re given. Their letters need an attorney’s approval before anything is filed or sent.',
  requester: 'Suggests requests and follows their own.',
}

export function isRole(value: string | null | undefined): value is Role {
  return Boolean(value) && (ROLES as string[]).includes(value as string)
}

export function roleLabel(value: string | null | undefined): string {
  return isRole(value) ? ROLE_LABEL[value] : 'No role'
}

export function roleShort(value: string | null | undefined): string {
  return isRole(value) ? ROLE_SHORT[value] : ''
}

/** Admin, staff, staff attorney: every request that is not privileged, unplaced mail, and the
 *  organisation's tables (the directory, templates, saved replies). */
export function isFullStaff(role: string | null | undefined): boolean {
  return role === 'admin' || role === 'staff' || role === 'staff_attorney'
}

/** Anyone who works requests at all. */
export function isTeam(role: string | null | undefined): boolean {
  return isFullStaff(role) || role === 'limited_attorney' || role === 'intern'
}

/** Nothing an intern writes leaves the portal without an approver's yes (42b). */
export function needsApproval(role: string | null | undefined): boolean {
  return role === 'intern'
}

/** Who may be given a request in Change access (230 "Add an attorney or intern"). */
export function isGrantScoped(role: string | null | undefined): boolean {
  return role === 'limited_attorney' || role === 'intern'
}
