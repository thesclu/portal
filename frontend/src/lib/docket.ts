/**
 * The Docket's arithmetic (design 025): how tall each "Due this week" bar is drawn, the two
 * sparklines' points, and which list filters each stat box stands for. Pure, so it is tested.
 * The counts themselves come from the API's summary and history routes.
 */

const at = (civil: string) => new Date(`${civil}T00:00:00Z`)
const iso = (date: Date) => date.toISOString().slice(0, 10)

/** The civil date after `civil`: the exclusive end of a span the list's `due_before` takes. */
export function dateAfter(civil: string): string {
  return iso(new Date(at(civil).getTime() + 86_400_000))
}

/** A bar's height in px: 48 for the busiest day (four or more), 12 per request below that, and a
 *  3 px stub for an empty day, as 025 draws them. */
export function barHeight(count: number, busiest: number): number {
  if (count <= 0) return 3
  const unit = 48 / Math.max(busiest, 4)
  return Math.max(3, Math.round(count * unit))
}

/** 025's sparklines are drawn in a 120 × 30 box, the line between y 26 (the lowest value) and
 *  y 9 (the highest), points spread evenly across. */
const SPARK_WIDTH = 120
const SPARK_LOW = 26
const SPARK_HIGH = 9

/** The `points` of a sparkline's polyline for these values, oldest first. A flat series is drawn
 *  along the bottom; fewer than two values draw nothing. */
export function sparkline(values: number[]): string {
  if (values.length < 2) return ''
  const low = Math.min(...values)
  const high = Math.max(...values)
  const step = SPARK_WIDTH / (values.length - 1)
  const round = (n: number) => Math.round(n * 10) / 10
  return values
    .map((value, i) => {
      const share = high === low ? 0 : (value - low) / (high - low)
      return `${round(i * step)},${round(SPARK_LOW - share * (SPARK_LOW - SPARK_HIGH))}`
    })
    .join(' ')
}

/** Which stat box filters the list. `null` is "All open", the strip's resting state. */
export type Stat = 'overdue' | 'producing' | 'week' | 'waiting' | 'mine'
export const STATS: readonly Stat[] = ['overdue', 'producing', 'week', 'waiting', 'mine']

export interface StatSource {
  today: string
  due_by_date: Array<{ date: string }>
  producing_statuses: string[]
  waiting_statuses: string[]
}

export interface StatFilter {
  status?: string
  due_before?: string
  due_after?: string
  assignee?: string
  open?: boolean
}

/**
 * The list filters a stat box stands for, so the list shows exactly what the box counted (the
 * summary route's own definitions). Null while a box that needs the summary's lists or dates is
 * waiting for it.
 */
export function statFilter(stat: Stat | null, source: StatSource | undefined): StatFilter | null {
  switch (stat) {
    case 'mine':
      return { assignee: 'me' }
    case null:
      return { open: true }
    default:
      break
  }
  if (!source) return null
  if (stat === 'overdue') return { due_before: source.today }
  if (stat === 'producing') return { status: source.producing_statuses.join(',') }
  if (stat === 'waiting') return { status: source.waiting_statuses.join(',') }
  const dates = source.due_by_date.map((d) => d.date)
  if (dates.length === 0) return null
  return { due_after: dates[0], due_before: dateAfter(dates[dates.length - 1]) }
}
