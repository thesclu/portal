/**
 * Pure helpers for Settings → Rules & calendars (P18b): weekday names, the pasted list of
 * holiday dates, and whether a rule has been retired.
 */

/** Weekday names in the calendar's own numbering (`date.weekday()`: Monday is zero). */
export const WEEKDAYS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const

/** Saturday and Sunday, in that numbering. */
export const DEFAULT_WEEKEND = [5, 6]

/** `[5, 6]` -> `Saturday, Sunday`; an empty weekend reads `none`. */
export function weekendNames(weekend: number[]): string {
  const names = [...weekend]
    .sort((a, b) => a - b)
    .map((day) => WEEKDAYS[day])
    .filter(Boolean)
  return names.length > 0 ? names.join(', ') : 'none'
}

/** True when `text` is a real date written YYYY-MM-DD. */
export function isCivilDate(text: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (!match) return false
  const [year, month, day] = match.slice(1).map(Number)
  const when = new Date(Date.UTC(year, month - 1, day))
  return (
    when.getUTCFullYear() === year && when.getUTCMonth() === month - 1 && when.getUTCDate() === day
  )
}

export interface HolidayLine {
  date: string
  name: string
}

/**
 * One holiday per line: the date as YYYY-MM-DD, then, after a space, a name if there is one.
 * Blank lines are skipped. Every bad line is named in `errors`, so the person can fix them all
 * at once; the good lines are still returned.
 */
export function parseHolidayLines(text: string): { holidays: HolidayLine[]; errors: string[] } {
  const holidays: HolidayLine[] = []
  const errors: string[] = []
  const seen = new Map<string, number>()
  text.split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim()
    if (!line) return
    const number = index + 1
    const match = /^(\S+)(?:\s+(.*))?$/.exec(line)
    const date = match?.[1] ?? ''
    if (!isCivilDate(date)) {
      errors.push(`Line ${number} does not start with a date written YYYY-MM-DD.`)
      return
    }
    const earlier = seen.get(date)
    if (earlier !== undefined) {
      errors.push(`Line ${number} repeats line ${earlier}.`)
      return
    }
    seen.set(date, number)
    holidays.push({ date, name: (match?.[2] ?? '').trim() })
  })
  return { holidays, errors }
}

/** Today's civil date in a zone, as YYYY-MM-DD. */
export function civilToday(timeZone: string, now: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(now)
  } catch {
    return now.toISOString().slice(0, 10)
  }
}

/** A rule whose end date has passed. */
export function isRetired(rule: { effective_to: string | null }, today: string): boolean {
  return rule.effective_to !== null && rule.effective_to < today
}
