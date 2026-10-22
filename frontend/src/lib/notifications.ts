/**
 * The pure pieces behind the bell and the Notifications and Activity settings tabs (P15b): the
 * words for each notification kind, and the query the activity log and its export take.
 */

/** The short label beside a notification in the bell. */
const KIND_LABEL: Record<string, string> = {
  mention: 'Mention',
  assigned: 'Assigned',
  bounce: 'Bounce',
  deadline: 'Deadline',
  review: 'Review',
  flagged: 'Flagged',
  arrived: 'New mail',
}

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

/** The sentence beside each "Email me when" switch (201's words where it draws the kind). */
const EMAIL_WHEN: Record<string, string> = {
  mention: 'Someone mentions me in a note',
  assigned: 'Something is assigned to me',
  bounce: 'A message I sent bounces',
  deadline: 'A deadline on my request is near',
  review: 'Something needs my sign-off, or mine was decided',
  flagged: 'A message is flagged',
  arrived: 'Anything new arrives',
}

/** The order 201 draws the kinds in; a kind it does not draw follows, in the API's order. */
const EMAIL_ORDER = ['deadline', 'flagged', 'assigned', 'mention', 'arrived', 'bounce', 'review']

/** A deadline's line names how far ahead the person is told, when that is known ("A deadline is
 *  3 days out", the setting `notifications.deadline_warning_days`). */
export function emailWhenLabel(kind: string, warningDays?: number): string {
  if (kind === 'deadline' && warningDays !== undefined && Number.isInteger(warningDays)) {
    if (warningDays === 0) return 'A deadline is today'
    return `A deadline is ${warningDays} ${warningDays === 1 ? 'day' : 'days'} out`
  }
  return EMAIL_WHEN[kind] ?? kind
}

export function emailOrder(kinds: readonly string[]): string[] {
  const rank = (kind: string) => {
    const at = EMAIL_ORDER.indexOf(kind)
    return at === -1 ? EMAIL_ORDER.length + kinds.indexOf(kind) : at
  }
  return [...kinds].sort((a, b) => rank(a) - rank(b))
}

/** "Daily summary at 7:00 AM" (201), from the summary hour (0–23). */
export function summaryHourLabel(hour: number): string {
  const h = ((Math.trunc(hour) % 24) + 24) % 24
  const twelve = h % 12 === 0 ? 12 : h % 12
  return `${twelve}:00 ${h < 12 ? 'AM' : 'PM'}`
}

/** The value of the person filter that means "the system alone, no person". */
export const SYSTEM = 'system'

/** The activity log's filters as the screen holds them. `who` is '' for anyone, `SYSTEM` for
 *  the system alone, or a user's id. An empty string anywhere means "not set". */
export interface ActivityFilters {
  who: string
  action: string
  since: string
  until: string
}

export interface ActivityQuery {
  actor_id?: string
  system?: boolean
  action?: string
  since?: string
  until?: string
}

/** The query the API takes, with nothing sent for a filter left empty. */
export function activityQuery(filters: ActivityFilters): ActivityQuery {
  const query: ActivityQuery = {}
  if (filters.who === SYSTEM) query.system = true
  else if (filters.who) query.actor_id = filters.who
  if (filters.action) query.action = filters.action
  if (filters.since) query.since = filters.since
  if (filters.until) query.until = filters.until
  return query
}

/** The CSV download for the same filters: a plain link, so the session cookie goes with it. */
export function activityExportHref(filters: ActivityFilters): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(activityQuery(filters))) params.set(key, String(value))
  const search = params.toString()
  return search ? `/api/core/activity/export?${search}` : '/api/core/activity/export'
}

/** The local calendar date `days` days before `now`, as YYYY-MM-DD for a date input. */
export function daysAgo(days: number, now: Date = new Date()): string {
  const when = new Date(now)
  when.setDate(when.getDate() - days)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
}

/** The activity log's time spans (190 "Last 7 days ▾"): how many days back `since` goes, or
 *  null for all time. */
export const ACTIVITY_SPANS: Array<{ key: string; label: string; days: number | null }> = [
  { key: 'today', label: 'Today', days: 0 },
  { key: '7', label: 'Last 7 days', days: 7 },
  { key: '30', label: 'Last 30 days', days: 30 },
  { key: '365', label: 'Last 12 months', days: 365 },
  { key: 'all', label: 'All time', days: null },
]

/** "Today 9:12 AM", "Yesterday 4:18 PM", else "Sep 6, 2:40 PM" (190's When), in a zone. */
export function activityWhen(iso: string, timeZone: string, now: Date = new Date()): string {
  const day = (when: Date) =>
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(when)
  const at = new Date(iso)
  const time = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour: 'numeric',
    minute: '2-digit',
  }).format(at)
  const today = day(now)
  const yesterday = day(new Date(now.getTime() - 86_400_000))
  if (day(at) === today) return `Today ${time}`
  if (day(at) === yesterday) return `Yesterday ${time}`
  const date = new Intl.DateTimeFormat('en-US', {
    timeZone,
    month: 'short',
    day: 'numeric',
  }).format(at)
  return `${date}, ${time}`
}

const SUBJECT: Record<string, string> = {
  request: 'Request',
  user: 'Users',
  setting: 'Settings',
  rule: 'Rules',
  calendar: 'Calendars',
  law: 'Rules',
  draft: 'Letter',
  review: 'Approval',
  suggestion: 'Suggestion',
  access_request: 'Access',
  agency: 'Agency',
  message: 'Message',
  source: 'Mail',
  closure: 'Closure days',
  template: 'Template',
  task: 'Task',
}

/** What an activity line is on (190's On): the kind of thing it names, in a word. */
export function activitySubject(subjectType: string): string {
  if (!subjectType) return '—'
  return (
    SUBJECT[subjectType] ?? subjectType.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase())
  )
}
