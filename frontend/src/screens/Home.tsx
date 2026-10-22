import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { isBuilt } from '../app/nav'
import { useMe } from '../app/session'
import { unwrap } from '../components/ActionDialog'
import { useRefreshTasks } from '../components/TasksPanel'
import { LoadFailed, Loading } from '../components/States'
import { cx } from '../components/ui'
import { daysFigure } from '../lib/vocab'
import { roleShort } from '../lib/roles'
import { statusText, statusWords } from '../lib/status'
import { civilDate, daysBetween, taskTitle, useNow } from '../lib/tasks'

type HomeTask = Schemas['HomeTaskOut']

/** How many rows a card previews before its link to the full list (013 draws four on the left,
 *  three on the right). */
const LEFT_ROWS = 4
const RIGHT_ROWS = 3

const OVERDUE_GRID = 'grid grid-cols-[150px_1.2fr_1.3fr_70px] gap-x-3.5'

const dayLabel = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})
const monthDay = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})
/** "Thu Sep 10" from a civil date. */
const civilLabel = (civil: string, format: Intl.DateTimeFormat) =>
  format.format(new Date(`${civil.slice(0, 10)}T00:00:00Z`)).replace(',', '')

/** "Wednesday, September 9": the day, in the person's own zone, as the title row's subtitle. */
function today(timeZone: string): string {
  const options = { weekday: 'long', month: 'long', day: 'numeric' } as const
  try {
    return new Intl.DateTimeFormat('en-US', { ...options, timeZone }).format(new Date())
  } catch {
    return new Intl.DateTimeFormat('en-US', options).format(new Date())
  }
}

function HomeCard({
  title,
  count,
  children,
}: {
  title: string
  count: ReactNode
  children: ReactNode
}) {
  return (
    <section className="overflow-hidden rounded-16 bg-card shadow-float">
      <div className="flex items-center gap-2.5 px-5 pt-3.5 pb-3">
        <h2 className="text-14 font-semibold text-ink">{title}</h2>
        <span className="text-125 text-muted">{count}</span>
      </div>
      {children}
    </section>
  )
}

/** One of the rows under a card's hairline (Due this week, Assigned to you, Flagged). */
const ROW = 'flex border-t border-line-row px-5 py-2.5 text-125'

/**
 * Home (design 013): what the law requires today. Overdue (from the Docket's own query, so the
 * status reads as on the Docket), due this week, the tasks assigned to you (a tick completes one
 * in place), and flagged mail.
 */
export function Home() {
  const me = useMe()
  // P20h (240): a limited-scope attorney's Home is what waits for their approval and the requests
  // they were given; nothing about what is hidden from them.
  return me.records_role === 'limited_attorney' ? <GivenHome /> : <TeamHome />
}

/** A row of 240's two cards: the line, its muted second line, and what sits at the right. */
function GivenRow({
  to,
  line,
  sub,
  end,
}: {
  to: string
  line: ReactNode
  sub: ReactNode
  end: ReactNode
}) {
  return (
    <Link
      to={to}
      className="flex items-center gap-3 border-t border-line-row py-2.5 text-13 text-ink hover:text-ink"
    >
      <span className="min-w-0 flex-1">
        {line}
        <br />
        <span className="text-12 text-muted">{sub}</span>
      </span>
      {end}
    </Link>
  )
}

function GivenCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-1 flex-col gap-2.5 rounded-16 bg-card-white px-[22px] py-[18px] leading-[normal] shadow-card">
      <h2 className="font-display text-md-lg font-extrabold text-ink">{title}</h2>
      {children}
    </section>
  )
}

/**
 * Home for a limited-scope attorney (240): "Waiting for your approval", the interns' letters on
 * the requests they were given (Review opens 235), and "Your requests", each with its days figure
 * (overdue in red with the ring) or, with no deadline, its status. Only what they may see.
 */
function GivenHome() {
  const pending = $api.useQuery('get', '/api/core/reviews/pending')
  const requests = $api.useQuery('get', '/api/records/requests', {
    params: { query: { sort: 'deadline', open: true, limit: 50 } },
  })
  const approvals = (pending.data ?? []).filter((r) => r.kind === 'approval')
  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Home" />
      <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
        {pending.isError || requests.isError ? (
          <LoadFailed
            what="Home"
            error={pending.error ?? requests.error}
            onRetry={() => void Promise.all([pending.refetch(), requests.refetch()])}
          />
        ) : pending.isPending || requests.isPending ? (
          <Loading what="Loading what is yours" />
        ) : (
          <div className="flex items-start gap-4">
            <GivenCard title="Waiting for your approval">
              {approvals.length === 0 ? (
                <p className="border-t border-line-row py-2.5 text-13 text-muted">
                  Nothing is waiting for you.
                </p>
              ) : (
                approvals.map((review) => (
                  <GivenRow
                    key={review.id}
                    to={`/approvals/${review.id}`}
                    line={review.title}
                    sub={[
                      review.tracking_number,
                      review.agency_short_name ?? review.agency_name,
                      `from ${review.submitted_by_name}${
                        review.submitted_by_role ? `, ${roleShort(review.submitted_by_role)}` : ''
                      }`,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                    end={<span className="font-medium text-brand">Review</span>}
                  />
                ))
              )}
            </GivenCard>
            <GivenCard title="Your requests">
              {(requests.data?.items ?? []).length === 0 ? (
                <p className="border-t border-line-row py-2.5 text-13 text-muted">
                  No request has been given to you yet.
                </p>
              ) : (
                (requests.data?.items ?? []).map((row) => {
                  const days = daysFigure(row.days_to_next_deadline)
                  return (
                    <GivenRow
                      key={row.id}
                      to={`/requests/${row.id}`}
                      line={row.subject}
                      sub={`${row.tracking_number} · ${row.agency_name}`}
                      end={
                        row.days_to_next_deadline === null ? (
                          <span className="text-muted">{statusText(row.current_status)}</span>
                        ) : days.overdue ? (
                          <span className="font-semibold text-danger-2">
                            <span
                              aria-hidden
                              className="mr-1.5 box-border inline-block size-2 rounded-pill border-[1.5px] border-danger-2"
                            />
                            {days.text}
                          </span>
                        ) : (
                          <span className="text-muted">{days.text}</span>
                        )
                      }
                    />
                  )
                })
              )}
            </GivenCard>
          </div>
        )}
      </div>
    </div>
  )
}

function TeamHome() {
  const me = useMe()
  const home = $api.useQuery('get', '/api/records/home')
  const todayCivil = civilDate(useNow(), me.timezone)
  const overdue = $api.useQuery('get', '/api/records/requests', {
    params: { query: { due_before: todayCivil, sort: 'deadline', limit: LEFT_ROWS } },
  })

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Home" subtitle={today(me.timezone)} />
      <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
        {home.isPending ? (
          <Loading what="Loading what is due" />
        ) : home.isError ? (
          <LoadFailed what="Home" error={home.error} onRetry={() => void home.refetch()} />
        ) : (
          <div className="flex items-start gap-[18px]">
            <div className="flex min-w-0 flex-1 flex-col gap-3.5">
              <HomeCard title="Overdue" count={overdue.data?.total ?? ''}>
                <div
                  className={cx(
                    OVERDUE_GRID,
                    'border-y border-hairline px-9 py-[9px] text-105 font-semibold tracking-label text-muted uppercase',
                  )}
                >
                  <span>Tracking no.</span>
                  <span>Agency</span>
                  <span>Status</span>
                  <span className="text-right">Days</span>
                </div>
                {(overdue.data?.items ?? []).map((row, index, rows) => {
                  const words = statusWords(row.current_status)
                  return (
                    <Link
                      key={row.id}
                      to={`/requests/${row.id}`}
                      className={cx(
                        OVERDUE_GRID,
                        'items-center px-9 py-[11px] text-125 text-ink hover:text-ink',
                        index < rows.length - 1 && 'border-b border-transparent',
                        index % 2 === 1 && 'bg-row-alt',
                      )}
                    >
                      <span className="min-w-0 truncate">{row.tracking_number}</span>
                      <span className="min-w-0 truncate">{row.agency_name}</span>
                      <span className="min-w-0 truncate">
                        <span className="text-muted">
                          {words.family}
                          {words.qualifier ? ' · ' : ''}
                        </span>
                        {words.qualifier ? (
                          <span className="font-medium text-ink">{words.qualifier}</span>
                        ) : null}
                      </span>
                      <span className="min-w-0 text-right whitespace-nowrap">
                        <span className="-my-0.5 -mr-2 inline-block rounded-6 bg-danger-tint px-2 py-0.5 leading-ui font-semibold text-danger-2">
                          {daysFigure(row.days_to_next_deadline).text}
                        </span>
                      </span>
                    </Link>
                  )
                })}
                {overdue.data && overdue.data.total === 0 ? (
                  <p className="px-9 py-[11px] text-125 text-muted">Nothing is overdue.</p>
                ) : null}
                {/* No font size of its own in 013: the 16 px line box sets the link's baseline. */}
                <div className="border-t border-line-row px-5 py-2.5 text-16">
                  <Link
                    to="/docket?stat=overdue"
                    className="text-125 font-medium whitespace-nowrap text-brand"
                  >
                    All {overdue.data?.total ?? ''} in Docket →
                  </Link>
                </div>
              </HomeCard>

              <HomeCard title="Due this week" count={home.data.due_soon.length}>
                {home.data.due_soon.slice(0, LEFT_ROWS).map((row) => (
                  <Link
                    key={row.deadline.id}
                    to={`/requests/${row.request_id}`}
                    className={cx(ROW, 'gap-3.5 text-ink hover:text-ink')}
                  >
                    <span className="flex-[0_0_64px] text-muted">
                      {civilLabel(row.deadline.due_at, dayLabel)}
                    </span>
                    <span className="text-ink">
                      {row.tracking_number} · {row.agency_name} · {row.kind_label.toLowerCase()}
                    </span>
                  </Link>
                ))}
                {home.data.due_soon.length === 0 ? (
                  <p className={cx(ROW, 'text-muted')}>Nothing is due this week.</p>
                ) : null}
              </HomeCard>
            </div>

            {/* 340 px as 013's style says. (Its rendering is 362: a nowrap subject's width is the
                column's min-content there. A long subject must not widen the column here.) */}
            <div className="flex w-[340px] min-w-0 shrink-0 flex-col gap-3.5">
              <HomeCard title="Assigned to you" count={home.data.assigned_tasks.length}>
                {home.data.assigned_tasks.slice(0, RIGHT_ROWS).map((entry) => (
                  <AssignedRow
                    key={entry.task.id}
                    entry={entry}
                    todayCivil={todayCivil}
                    timeZone={me.timezone}
                  />
                ))}
                {home.data.assigned_tasks.length === 0 ? (
                  <p className={cx(ROW, 'text-muted')}>Nothing is assigned to you.</p>
                ) : null}
              </HomeCard>

              <HomeCard title="Flagged in Inbox" count={home.data.flagged.total}>
                {home.data.flagged.items.slice(0, RIGHT_ROWS).map((row) => {
                  const meta = (row.source.raw_metadata ?? {}) as {
                    subject?: string
                    from?: string
                  }
                  const from = row.events[0]?.agency_name ?? meta.from ?? ''
                  const body = (
                    <>
                      <span className="min-w-0 flex-1 truncate text-ink">
                        {meta.subject ?? 'A message'}
                      </span>
                      <span className="whitespace-nowrap text-muted">{from}</span>
                    </>
                  )
                  return isBuilt('inbox') ? (
                    <Link
                      key={row.source.id}
                      to={`/inbox?bucket=flagged&message=${row.source.id}`}
                      className={cx(ROW, 'items-center gap-3 hover:text-ink')}
                    >
                      {body}
                    </Link>
                  ) : (
                    <div key={row.source.id} className={cx(ROW, 'items-center gap-3')}>
                      {body}
                    </div>
                  )
                })}
                {home.data.flagged.total === 0 ? (
                  <p className={cx(ROW, 'text-muted')}>No flagged mail.</p>
                ) : null}
              </HomeCard>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** A task assigned to me: the box completes it in place; the words open its request. */
function AssignedRow({
  entry,
  todayCivil,
  timeZone,
}: {
  entry: HomeTask
  todayCivil: string
  timeZone: string
}) {
  const refresh = useRefreshTasks()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const due = entry.task.due_at ? civilDate(new Date(entry.task.due_at), timeZone) : null
  const late = due !== null && daysBetween(todayCivil, due) <= 0
  async function complete() {
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.POST('/api/records/tasks/{task_id}/complete', {
          params: { path: { task_id: entry.task.id } },
        }),
      )
      await refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'It was not completed.')
      setBusy(false)
    }
  }
  return (
    <div className={cx(ROW, 'items-center gap-2.5')}>
      <button
        type="button"
        role="checkbox"
        aria-checked={busy}
        aria-label={`Complete ${taskTitle(entry.task)} on ${entry.tracking_number}`}
        disabled={busy}
        onClick={() => void complete()}
        className={cx(
          'box-border inline-flex size-[15px] shrink-0 rounded-4 border-[1.5px] border-line-control',
          busy ? 'bg-line-control' : 'bg-card-white hover:border-brand',
        )}
      />
      <Link
        to={`/requests/${entry.task.request_id}`}
        className="min-w-0 flex-1 truncate text-ink hover:text-ink"
        title={error ?? undefined}
      >
        {taskTitle(entry.task)} · {entry.agency_name}
      </Link>
      {error ? (
        <span role="alert" className="text-danger">
          Not done
        </span>
      ) : due ? (
        <span className={late ? 'text-danger' : 'text-muted'}>
          {due === todayCivil ? 'Today' : civilLabel(due, monthDay)}
        </span>
      ) : null}
    </div>
  )
}
