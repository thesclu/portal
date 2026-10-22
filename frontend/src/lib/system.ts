/**
 * Pure pieces of Settings → System (199): how far off a calendar's end is, in words, and what a
 * scheduled check is called.
 */

/** The jobs the portal schedules, in the words System uses for them. */
export const JOB_LABEL: Record<string, string> = {
  'records.sweep_overdue_deadlines': 'Overdue check',
  'records.send_auto_followups': 'Automatic follow-ups',
  'records.production_review_timeout': 'Unreviewed productions',
  'records.requeue_stalled_mail': 'Mail waiting on its files',
  'mail.requeue_stuck_messages': 'Messages waiting to go',
  'core.deadline_reminders': 'Deadline reminders',
  'core.daily_summaries': 'Daily summaries',
  // P25a: the changes owed to Keycloak taken up again, and the sign-in rules written, hourly.
  'core.keycloak_sweep': 'Sign-in sync',
}

/** The order System lists them in: the three 199 draws first, the rest after by name. */
const JOB_ORDER = [
  'records.sweep_overdue_deadlines',
  'records.send_auto_followups',
  'records.production_review_timeout',
]

export function jobLabel(name: string): string {
  return JOB_LABEL[name] ?? name
}

export function jobOrder<T extends { name: string }>(jobs: readonly T[]): T[] {
  const rank = (name: string) => {
    const at = JOB_ORDER.indexOf(name)
    return at === -1 ? JOB_ORDER.length : at
  }
  return [...jobs].sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name))
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`

/**
 * "Runs out in 4 years" (199): from today's civil date to the calendar's last entered date, in
 * whole years, else months, else days; "Ran out" once it has passed. Both dates `YYYY-MM-DD`.
 */
export function runsOut(last: string | null | undefined, today: string): string {
  if (!last) return 'No dates entered'
  if (last < today) return 'Ran out'
  const [ly, lm, ld] = last.split('-').map(Number)
  const [ty, tm, td] = today.split('-').map(Number)
  let months = (ly - ty) * 12 + (lm - tm)
  if (ld < td) months -= 1
  if (months >= 12) return `Runs out in ${plural(Math.floor(months / 12), 'year')}`
  if (months >= 1) return `Runs out in ${plural(months, 'month')}`
  const span = Math.round((Date.UTC(ly, lm - 1, ld) - Date.UTC(ty, tm - 1, td)) / 86_400_000)
  return span === 0 ? 'Runs out today' : `Runs out in ${plural(span, 'day')}`
}

/**
 * How often a schedule runs, in minutes, read from its cron line well enough to tell a job that
 * stopped: `*\/15 * * * *` every 15, an hour field of `*` hourly, a day field of `*` daily, else
 * monthly. Null for a line it cannot read.
 */
export function cronPeriodMinutes(cron: string): number | null {
  const fields = cron.trim().split(/\s+/)
  if (fields.length !== 5) return null
  const [minute, hour, day] = fields
  const every = /^\*\/(\d+)$/.exec(minute)
  if (every) return Number(every[1])
  if (minute === '*') return 1
  if (hour === '*') return 60
  const hourly = /^\*\/(\d+)$/.exec(hour)
  if (hourly) return Number(hourly[1]) * 60
  if (day === '*') return 1440
  return 1440 * 31
}

/** A job is late when it has not run for more than two of its periods (and a little). */
export function isLate(
  lastRun: string | null | undefined,
  cron: string,
  now: Date = new Date(),
): boolean {
  const period = cronPeriodMinutes(cron)
  if (!lastRun || period === null) return false
  return now.getTime() - new Date(lastRun).getTime() > (period * 2 + 5) * 60_000
}
