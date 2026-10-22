/**
 * The destinations, their groups, and which of them exist yet.
 *
 * BUILD_PLAN, design v2 re-plan, rule 8: a control whose part has not been built does not render.
 * Each frontend part switches its own destinations on here; nothing else decides what the sidebar
 * or the quick-jump palette offers. Campaigns is not planned and has no entry at all.
 *
 * Design v3 (ARCHITECTURE §2.2): three groups, Today · Requests · Organisation, in this order.
 * Inbox badges the flagged count and Tasks the open tasks assigned to me; nothing else badges.
 * Collapsed, the sidebar shows each item's icon, as the new-request screens draw it (044–061).
 */
export type Group = 'today' | 'requests' | 'organisation'

export const GROUPS: Array<{ key: Group; label: string }> = [
  { key: 'today', label: 'Today' },
  { key: 'requests', label: 'Requests' },
  { key: 'organisation', label: 'Organisation' },
]

export type IconName =
  | 'home'
  | 'inbox'
  | 'tasks'
  | 'calendar'
  | 'docket'
  | 'agencies'
  | 'records'
  | 'templates'
  | 'reports'
  | 'users'
  | 'settings'

export interface Destination {
  key: string
  label: string
  to: string
  group: Group
  icon: IconName
  /** Shown to portal administrators only. */
  adminOnly?: boolean
  /** Staff and admins only: a requester never sees it (P13). */
  staffOnly?: boolean
  /** A requester's own screens: staff never see them (P13). */
  requesterOnly?: boolean
  /** The frontend part that builds it; false until then. */
  built: boolean
}

export const DESTINATIONS: Destination[] = [
  {
    key: 'home',
    label: 'Home',
    to: '/',
    group: 'today',
    icon: 'home',
    staffOnly: true,
    built: true,
  },
  {
    key: 'inbox',
    label: 'Inbox',
    to: '/inbox',
    group: 'today',
    icon: 'inbox',
    staffOnly: true,
    built: true,
  },
  {
    key: 'tasks',
    label: 'Tasks',
    to: '/tasks',
    group: 'today',
    icon: 'tasks',
    staffOnly: true,
    built: true,
  },
  {
    key: 'calendar',
    label: 'Calendar',
    to: '/calendar',
    group: 'today',
    icon: 'calendar',
    staffOnly: true,
    built: true,
  },
  {
    key: 'docket',
    label: 'Docket',
    to: '/docket',
    group: 'requests',
    icon: 'docket',
    staffOnly: true,
    built: true,
  },
  {
    key: 'agencies',
    label: 'Agencies',
    to: '/agencies',
    group: 'requests',
    icon: 'agencies',
    staffOnly: true,
    built: true,
  },
  {
    key: 'records',
    label: 'Records',
    to: '/records',
    group: 'requests',
    icon: 'records',
    staffOnly: true,
    built: true,
  },
  {
    key: 'templates',
    label: 'Templates',
    to: '/templates',
    group: 'requests',
    icon: 'templates',
    staffOnly: true,
    built: true,
  },
  // A requester's own destinations sit in the same groups: what is theirs today, and the request
  // they may suggest.
  {
    key: 'my-requests',
    label: 'My requests',
    to: '/my-requests',
    group: 'today',
    icon: 'docket',
    requesterOnly: true,
    built: true,
  },
  {
    key: 'suggest',
    label: 'Suggest a request',
    to: '/suggest',
    group: 'requests',
    icon: 'templates',
    requesterOnly: true,
    built: true,
  },
  {
    key: 'reports',
    label: 'Reports',
    to: '/reports',
    group: 'organisation',
    icon: 'reports',
    staffOnly: true,
    built: true,
  },
  {
    key: 'users',
    label: 'Users',
    to: '/users',
    group: 'organisation',
    icon: 'users',
    adminOnly: true,
    staffOnly: true,
    built: true,
  },
  // A requester keeps Settings for their own profile (password, time zone); the other tabs are
  // staff-only inside the screen.
  {
    key: 'settings',
    label: 'Settings',
    to: '/settings',
    group: 'organisation',
    icon: 'settings',
    built: true,
  },
]

export function isBuilt(key: string): boolean {
  return DESTINATIONS.some((destination) => destination.key === key && destination.built)
}

/** The shape of the signed-in person this module needs: only what decides where they may go. */
export interface Viewer {
  records_role?: string | null
  is_staff: boolean
}

export function isRequester(me: Viewer | null | undefined): boolean {
  return me?.records_role === 'requester'
}

/** The built destinations this person may go to, in sidebar order (group by group). */
export function destinationsFor(me: Viewer): Destination[] {
  const requester = isRequester(me)
  const allowed = DESTINATIONS.filter(
    (d) =>
      d.built &&
      (requester ? !d.staffOnly : !d.requesterOnly) &&
      (!d.adminOnly || (me.is_staff && !requester)),
  )
  const order = GROUPS.map((g) => g.key)
  return allowed.toSorted((a, b) => order.indexOf(a.group) - order.indexOf(b.group))
}

/** The new-request flow's addresses (stage 1 at /new, the rest under /drafts): the sidebar is
 *  collapsed for its duration (ARCHITECTURE §2.4, 044–061). */
export function inNewRequestFlow(pathname: string): boolean {
  return ['/new', '/drafts'].some((to) => pathname === to || pathname.startsWith(`${to}/`))
}

/** Where the sidebar is the 64 px strip whatever the person chose: the new-request flow, and
 *  reviewing an intern's letter (235–237 draw it collapsed, like the flow). */
export function drawnCollapsed(pathname: string): boolean {
  return inNewRequestFlow(pathname) || pathname.startsWith('/approvals/')
}

/** "New request" is P4d's. */
export const NEW_REQUEST_BUILT = true

/** Where a signed-in person lands: a requester on their own requests; everyone else on Home. */
export function landing(me?: Viewer | null): string {
  if (isRequester(me)) return '/my-requests'
  return isBuilt('home') ? '/' : '/settings'
}
