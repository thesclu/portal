import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router'

import type { Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import { LoadFailed, Loading } from '../components/States'
import { cx } from '../components/ui'
import { civilDate, useNow } from '../lib/tasks'

type DeadlineRow = Schemas['DeadlineRowOut']
type View = 'month' | 'week' | 'list'

/** How far ahead the list looks (038: "Next 30 days"). A view range, not a legal period. */
const LIST_DAYS = 30

// Civil dates only, handled in UTC so that no reader's own zone can move a day.
const iso = (date: Date) => date.toISOString().slice(0, 10)
const at = (civil: string) => new Date(`${civil}T00:00:00Z`)
const plus = (date: Date, days: number) => new Date(date.getTime() + days * 86_400_000)
const startOfWeek = (date: Date) => plus(date, -date.getUTCDay())
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const format = (options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat('en-US', { ...options, timeZone: 'UTC' })
const MONTH_YEAR = format({ month: 'long', year: 'numeric' })
const MONTH_DAY = format({ month: 'short', day: 'numeric' })
const LIST_DAY = format({ weekday: 'short', month: 'short', day: 'numeric' })

function range(view: View, anchor: Date, today: Date): { start: Date; end: Date } {
  if (view === 'list') return { start: today, end: plus(today, LIST_DAYS) }
  if (view === 'week') {
    const start = startOfWeek(anchor)
    return { start, end: plus(start, 6) }
  }
  const first = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), 1))
  const last = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + 1, 0))
  return { start: startOfWeek(first), end: plus(startOfWeek(last), 6) }
}

/** "Sep 7 – 11, 2026", or "Sep 28 – Oct 2, 2026" across a month. */
function weekTitle(first: Date, last: Date): string {
  const sameMonth = first.getUTCMonth() === last.getUTCMonth()
  const end = sameMonth ? String(last.getUTCDate()) : MONTH_DAY.format(last)
  return `${MONTH_DAY.format(first)} – ${end}, ${last.getUTCFullYear()}`
}

/** A deadline's state on the calendar: done (satisfied or waived, struck as 015 draws it),
 *  overdue (missed, or active and past), or still to come. */
function tone(row: DeadlineRow): 'done' | 'overdue' | 'upcoming' {
  if (row.status === 'satisfied' || row.status === 'waived') return 'done'
  if (row.status === 'missed') return 'overdue'
  return row.days_from_today < 0 ? 'overdue' : 'upcoming'
}

const CHIP = {
  done: 'bg-page text-muted-2 line-through',
  overdue: 'bg-danger-tint text-danger-2',
  upcoming: 'bg-brand-tint text-brand',
  followup: 'bg-page text-ink-2',
} as const

interface Entry {
  key: string
  to: string
  label: string
  title: string
  tone: keyof typeof CHIP
}

const viewOf = (value: string | null): View =>
  value === 'week' || value === 'list' ? value : 'month'

/**
 * Calendar (design 015 month, 037 week, 038 list): every deadline on the date it falls due (the
 * finished ones struck), each holiday with its name where an administrator gave it one, and in
 * the week the follow-ups due that week. There is no calendar table; the three views are the
 * same read over a different range.
 */
export function Calendar() {
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const view = viewOf(params.get('view'))
  const todayCivil = civilDate(useNow(), me.timezone)
  const today = at(todayCivil)
  const anchor = at(params.get('on') ?? todayCivil)
  const { start, end } = range(view, anchor, today)
  const calendar = $api.useQuery('get', '/api/records/calendar', {
    params: { query: { start: iso(start), end: iso(end) } },
  })
  // The week also shows the follow-ups due in it (open follow-up tasks).
  const tasks = $api.useQuery(
    'get',
    '/api/records/tasks',
    { params: { query: { state: 'open' } } },
    { enabled: view === 'week' },
  )

  const go = (next: { view?: View; on?: string }) =>
    setParams({ view: next.view ?? view, on: next.on ?? iso(anchor) }, { replace: true })
  const step = (direction: number) => {
    if (view === 'week') return go({ on: iso(plus(anchor, 7 * direction)) })
    go({
      on: iso(new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + direction, 1))),
    })
  }

  const byDay = useMemo(() => {
    const map = new Map<string, Entry[]>()
    const add = (day: string, entry: Entry) => map.set(day, [...(map.get(day) ?? []), entry])
    for (const row of calendar.data?.deadlines ?? []) {
      add(row.deadline.due_at, {
        key: row.deadline.id,
        to: `/requests/${row.request_id}`,
        label: row.agency_name,
        title: `${row.agency_name} · ${row.kind_label} · ${row.tracking_number}`,
        tone: tone(row),
      })
    }
    for (const task of tasks.data ?? []) {
      if (task.task_type !== 'manual_followup' || !task.due_at) continue
      add(civilDate(new Date(task.due_at), me.timezone), {
        key: task.id,
        to: `/requests/${task.request_id}`,
        label: task.agency_name,
        title: `${task.agency_name} · follow-up · ${task.tracking_number}`,
        tone: 'followup',
      })
    }
    return map
  }, [calendar.data, tasks.data, me.timezone])
  const kinds = useMemo(() => {
    const map = new Map<string, string>()
    for (const row of calendar.data?.deadlines ?? []) map.set(row.deadline.id, row.kind_label)
    return map
  }, [calendar.data])
  const holidays = useMemo(() => {
    const days = new Map<string, string | null>()
    for (const cal of calendar.data?.calendars ?? [])
      for (const day of cal.holidays) days.set(day.date, day.name ?? days.get(day.date) ?? null)
    return days
  }, [calendar.data])
  // Which weekdays are closed comes from the jurisdiction calendars. They number the week from
  // Monday (0); a JS Date numbers it from Sunday.
  const weekend = useMemo(() => {
    const closed = new Set<number>()
    for (const cal of calendar.data?.calendars ?? [])
      for (const weekday of cal.weekend) closed.add((weekday + 1) % 7)
    return closed
  }, [calendar.data])

  const days: Date[] = []
  for (let day = start; day <= end; day = plus(day, 1)) days.push(day)
  const workdays = days.filter((day) => !weekend.has(day.getUTCDay()))
  const title =
    view === 'month'
      ? MONTH_YEAR.format(anchor)
      : view === 'week'
        ? weekTitle(workdays[0] ?? start, workdays.at(-1) ?? end)
        : `Next ${LIST_DAYS} days`

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Calendar" />
      <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
        <div className="flex items-center gap-2.5">
          <h2 className="text-md-lg font-semibold text-ink" aria-live="polite">
            {title}
          </h2>
          {view === 'list' ? null : (
            <span className="ml-2 flex gap-1">
              <NavButton label="Earlier" onClick={() => step(-1)}>
                ‹
              </NavButton>
              <NavButton onClick={() => go({ on: todayCivil })}>Today</NavButton>
              <NavButton label="Later" onClick={() => step(1)}>
                ›
              </NavButton>
            </span>
          )}
          <span className="ml-auto">
            <div
              role="group"
              aria-label="View"
              className="inline-flex h-10 items-center gap-0.5 rounded-pill bg-fill-subtle-4 p-[3px]"
            >
              {(['month', 'week', 'list'] as View[]).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={view === option}
                  onClick={() => go({ view: option })}
                  className={cx(
                    'inline-flex h-7 items-center rounded-pill px-3.5 text-125 font-medium capitalize',
                    view === option ? 'bg-card-white text-ink shadow-xs-2' : 'text-ink-2',
                  )}
                >
                  {option}
                </button>
              ))}
            </div>
          </span>
        </div>

        {calendar.isPending ? (
          <Loading what="Loading deadlines" />
        ) : calendar.isError ? (
          <LoadFailed
            what="the calendar"
            error={calendar.error}
            onRetry={() => void calendar.refetch()}
          />
        ) : (
          <div className="overflow-hidden rounded-16 bg-card shadow-float">
            {view === 'month' ? (
              <>
                <div className="grid grid-cols-7 border-b border-hairline bg-row-alt">
                  {WEEKDAYS.map((name) => (
                    <span
                      key={name}
                      className="p-2 text-105 font-semibold tracking-label text-muted uppercase"
                    >
                      {name}
                    </span>
                  ))}
                </div>
                <div className="grid grid-cols-7">
                  {days.map((day) => {
                    const key = iso(day)
                    const off = day.getUTCMonth() !== anchor.getUTCMonth()
                    const holiday = holidays.get(key)
                    return (
                      <div
                        key={key}
                        className={cx(
                          'flex min-h-[106px] flex-col gap-1 border-r border-b border-line-row px-2 py-[7px]',
                          weekend.has(day.getUTCDay()) ? 'bg-surface-row' : 'bg-card-white',
                        )}
                      >
                        <span
                          className={cx(
                            'text-12',
                            key === todayCivil
                              ? 'self-start rounded-pill bg-brand px-[7px] py-px font-semibold text-white'
                              : off
                                ? 'text-line-control'
                                : 'text-ink-2',
                          )}
                        >
                          {day.getUTCDate()}
                        </span>
                        {holiday ? <span className="text-11 text-muted">{holiday}</span> : null}
                        {(byDay.get(key) ?? [])
                          .filter((entry) => entry.tone !== 'followup')
                          .map((entry) => (
                            <Link
                              key={entry.key}
                              to={entry.to}
                              title={entry.title}
                              className={cx(
                                'truncate rounded-kbd px-1.5 py-0.5 text-11',
                                CHIP[entry.tone],
                              )}
                            >
                              {entry.label}
                            </Link>
                          ))}
                      </div>
                    )
                  })}
                </div>
              </>
            ) : view === 'week' ? (
              <div
                className="grid"
                style={{ gridTemplateColumns: `repeat(${workdays.length}, minmax(0, 1fr))` }}
              >
                {workdays.map((day, index) => {
                  const key = iso(day)
                  const holiday = holidays.get(key)
                  return (
                    <div
                      key={key}
                      className={cx(
                        'flex min-h-[320px] flex-col',
                        index < workdays.length - 1 && 'border-r border-line-row',
                      )}
                    >
                      <div
                        className={cx(
                          'border-b border-hairline bg-surface-row px-3 py-2.5 text-12',
                          key === todayCivil ? 'font-semibold text-brand' : 'text-ink-2',
                        )}
                      >
                        {WEEKDAYS[day.getUTCDay()]} {MONTH_DAY.format(day)}
                        {holiday ? (
                          <span className="font-normal text-muted"> · {holiday}</span>
                        ) : null}
                      </div>
                      <div className="flex flex-col gap-1.5 p-2.5">
                        {(byDay.get(key) ?? []).map((entry) => (
                          <Link
                            key={entry.key}
                            to={entry.to}
                            title={entry.title}
                            className={cx(
                              'rounded-chip px-[9px] py-[7px] text-12 leading-ui',
                              CHIP[entry.tone],
                            )}
                          >
                            {entry.label} ·{' '}
                            {entry.tone === 'followup'
                              ? 'follow-up'
                              : (kinds.get(entry.key) ?? '').toLowerCase()}
                          </Link>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : calendar.data.deadlines.length === 0 ? (
              <p className="px-5 py-10 text-center text-125 text-muted">
                Nothing falls due in the next {LIST_DAYS} days.
              </p>
            ) : (
              <ListView rows={calendar.data.deadlines} />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function NavButton({
  label,
  onClick,
  children,
}: {
  label?: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="box-border inline-flex h-8 items-center gap-1.5 rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
    >
      {children}
    </button>
  )
}

/** 038: the deadlines grouped under their date, soonest first. */
function ListView({ rows }: { rows: DeadlineRow[] }) {
  const groups: Array<{ day: string; rows: DeadlineRow[] }> = []
  for (const row of rows) {
    const last = groups.at(-1)
    if (last && last.day === row.deadline.due_at) last.rows.push(row)
    else groups.push({ day: row.deadline.due_at, rows: [row] })
  }
  return (
    <>
      {groups.map((group) => (
        <div key={group.day}>
          <div className="border-t border-hairline bg-surface-row px-5 pt-2.5 pb-1 text-105 font-semibold tracking-label text-muted uppercase">
            {LIST_DAY.format(at(group.day))}
          </div>
          {group.rows.map((row) => (
            <Link
              key={row.deadline.id}
              to={`/requests/${row.request_id}`}
              className={cx(
                'flex gap-3.5 border-t border-line-row-2 px-5 py-2.5 text-125 hover:text-ink',
                tone(row) === 'done' && 'line-through',
              )}
            >
              <span className="flex-[0_0_180px] truncate text-ink">{row.agency_name}</span>
              <span className="flex-1 text-ink-2">{row.kind_label}</span>
              <span className="text-muted">{row.tracking_number}</span>
            </Link>
          ))}
        </div>
      ))}
    </>
  )
}
