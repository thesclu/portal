import type { Schemas } from '../../api/client'
import { $api } from '../../api/query'

export type { Stat } from '../../lib/docket'

export type DocketSummary = Schemas['DocketSummaryOut']

export interface DocketStats {
  overdue: number
  producing: number
  needsReview: number
  dueThisWeek: number
  /** Monday to Friday: the civil date and how many requests' next deadline falls on it. */
  week: Array<{ day: string; count: number }>
  waiting: number
  mine: number
  allOpen: number
  /** The two sparklines, oldest first: overdue and open at the end of each date. Empty until
   *  the history has loaded. */
  overdueTrend: number[]
  openTrend: number[]
  /** The summary as the API sent it: the date it counted from and the statuses each box counts,
   *  which the list is filtered by. */
  summary: DocketSummary
}

/** The cache keys every docket write invalidates along with the list (`useRefreshDocket`). */
export const SUMMARY_KEYS = [
  ['get', '/api/records/requests/summary'],
  ['get', '/api/records/requests/history'],
  ['get', '/api/records/requests/assignee-counts'],
] as const

/**
 * The stat strip's figures, one read of the summary route (counted under the list's own
 * visibility rule), and the sparklines from the history route.
 */
export function useDocketStats(): DocketStats | undefined {
  const summary = $api.useQuery('get', '/api/records/requests/summary')
  const history = $api.useQuery('get', '/api/records/requests/history')
  const data = summary.data
  if (!data) return undefined
  const week = data.due_by_date.map((d) => ({ day: d.date, count: d.count }))
  const points = history.data?.points ?? []
  return {
    overdue: data.overdue,
    producing: data.producing,
    needsReview: data.producing_unreviewed,
    dueThisWeek: week.reduce((n, d) => n + d.count, 0),
    week,
    waiting: data.waiting_on_agency,
    mine: data.assigned_to_me,
    allOpen: data.all_open,
    overdueTrend: points.map((p) => p.overdue),
    openTrend: points.map((p) => p.open),
    summary: data,
  }
}
