import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { $api } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { NEW_REQUEST_BUILT } from '../../app/nav'
import { useMe } from '../../app/session'
import { LoadFailed } from '../../components/States'
import { cx } from '../../components/ui'
import { statusWords } from '../../lib/status'
import { daysFigure } from '../../lib/vocab'

/** Rows read at a time, as the full Docket reads them. */
const PAGE = 100

const COLUMNS = '150px minmax(0,1.05fr) minmax(0,1.5fr) 210px 96px 70px'

const MONTH_DAY = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})
const shortDate = (civil: string | null) =>
  civil ? MONTH_DAY.format(new Date(`${civil.slice(0, 10)}T00:00:00Z`)) : '—'

/**
 * The Docket for someone who sees only some requests (P20h): an intern (239: requests made by
 * interns and the ones they were given) and a limited-scope attorney (241: only what an admin gave
 * them). As drawn, it is the table alone: no stat strip, no search or filter bar, no banner and no
 * count of what is hidden, nothing that says other requests exist. The open requests, soonest
 * deadline first; the status in two tones (the family muted, the qualifier in ink) without the
 * ring; the days figure muted, or red with its ring once overdue; no assignee column.
 *
 * The server decides what is listed (the same visibility rule as every other read); this only
 * draws it.
 */
export function ScopedDocket() {
  const me = useMe()
  const navigate = useNavigate()
  const [limit, setLimit] = useState(PAGE)
  const docket = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { open: true, sort: 'deadline', limit } } },
    { placeholderData: (previous) => previous },
  )
  const rows = docket.data?.items ?? []
  const total = docket.data?.total ?? 0
  const grid = 'grid items-center gap-x-3.5 px-9'

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2.5 leading-[normal]">
      <PageHeader
        title="Docket"
        actions={
          NEW_REQUEST_BUILT ? (
            <Link
              to="/new"
              className="box-border inline-flex h-8 shrink-0 items-center gap-1.5 rounded-pill bg-brand px-[13px] text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark hover:text-white"
            >
              ＋ New request
            </Link>
          ) : null
        }
      />
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        <div
          role="table"
          aria-label="Docket"
          className={cx(
            'flex min-h-0 flex-col overflow-hidden rounded-16 bg-card-white pb-1.5 shadow-card',
            docket.isFetching && !docket.isPending && 'opacity-80',
          )}
        >
          <div className="min-h-0 overflow-y-auto">
            <div
              role="row"
              className={cx(
                grid,
                'h-[34px] text-105 font-semibold tracking-label text-muted uppercase',
              )}
              style={{ gridTemplateColumns: COLUMNS }}
            >
              <span role="columnheader">Tracking no.</span>
              <span role="columnheader">Agency</span>
              <span role="columnheader">Subject</span>
              <span role="columnheader">Status</span>
              <span role="columnheader">Due</span>
              <span role="columnheader" className="text-right">
                Days
              </span>
            </div>
            {docket.isPending ? (
              Array.from({ length: 6 }, (_, i) => (
                <div
                  key={i}
                  aria-hidden
                  className={cx(grid, 'h-12', i % 2 === 1 && 'bg-row-alt')}
                  style={{ gridTemplateColumns: COLUMNS, opacity: 1 - i * 0.12 }}
                >
                  {['96px', '80%', '88%', '70%', '44px', '26px'].map((width, j) => (
                    <span key={j} className={cx('min-w-0', j === 5 && 'flex justify-end')}>
                      <span
                        className="block h-[11px] rounded-4 bg-fill-skeleton"
                        style={{ width }}
                      />
                    </span>
                  ))}
                </div>
              ))
            ) : docket.isError ? (
              <LoadFailed
                what="the docket"
                error={docket.error}
                onRetry={() => void docket.refetch()}
              />
            ) : total === 0 ? (
              <div className="flex flex-col items-center gap-1 px-6 py-12 text-center">
                <div className="text-14 font-semibold text-ink">Nothing on the docket</div>
                <div className="text-125 text-muted">
                  {me.records_role === 'intern'
                    ? 'Requests made by interns, and requests you’re given, appear here.'
                    : 'Requests appear here once an admin gives them to you.'}
                </div>
              </div>
            ) : (
              rows.map((row, i) => {
                const days = daysFigure(row.days_to_next_deadline)
                const words = statusWords(row.current_status)
                return (
                  <div
                    key={row.id}
                    role="row"
                    onClick={() => void navigate(`/requests/${row.id}`)}
                    className={cx(
                      grid,
                      'h-12 cursor-pointer text-125 text-ink',
                      i % 2 === 1 ? 'bg-row-alt hover:bg-hover' : 'hover:bg-page',
                    )}
                    style={{ gridTemplateColumns: COLUMNS }}
                  >
                    <Link
                      role="cell"
                      to={`/requests/${row.id}`}
                      onClick={(event) => event.stopPropagation()}
                      className="text-ink-2 hover:text-ink-2"
                    >
                      {row.tracking_number}
                    </Link>
                    <span role="cell" className="min-w-0 truncate">
                      {row.agency_name}
                    </span>
                    <span role="cell" className="min-w-0 truncate">
                      {row.subject}
                    </span>
                    <span role="cell" className="min-w-0 truncate">
                      <span className="text-muted">{words.family}</span>
                      {words.qualifier ? ` · ${words.qualifier}` : null}
                    </span>
                    <span role="cell" className="text-ink-2">
                      {shortDate(row.next_deadline_at)}
                    </span>
                    <span
                      role="cell"
                      className={cx(
                        'text-right',
                        days.overdue ? 'font-semibold text-danger-2' : 'text-muted',
                      )}
                    >
                      {days.overdue ? (
                        <span
                          aria-hidden
                          className="mr-1.5 box-border inline-block size-2 rounded-pill border-[1.5px] border-danger-2"
                        />
                      ) : null}
                      {days.text}
                    </span>
                  </div>
                )
              })
            )}
            {rows.length < total && !docket.isPending ? (
              <button
                type="button"
                onClick={() => setLimit((n) => n + PAGE)}
                className="block w-full py-3 text-center text-125 font-medium text-brand hover:text-brand-dark"
              >
                Show more ({rows.length} of {total})
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
