import { keepPreviousData } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'

import { $api, messageOf } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import { ChooseDates } from '../components/ChooseDates'
import { ChevronDown } from '../components/icons'
import { ListCard, ListHead, ListRow } from '../components/ListTable'
import { Menu } from '../components/Menu'
import { LoadFailed } from '../components/States'
import { Button, cx } from '../components/ui'
import { formatDate } from '../lib/dates'
import {
  ACTIVITY_SPANS,
  SYSTEM,
  activityExportHref,
  activityQuery,
  activitySubject,
  activityWhen,
  daysAgo,
  type ActivityFilters,
} from '../lib/notifications'

const LIMIT = 100
const COLUMNS = '150px 1.1fr 1.6fr 1.2fr'

/** A filter's face (190 "Anyone ▾"): 32 px, 12.5 px ink-2, the 10 px chevron. */
function FilterFace({ children, active }: { children: ReactNode; active?: boolean }) {
  return (
    <span
      className={cx(
        'inline-flex h-8 items-center gap-1.5 rounded-9 px-2.5 text-125 whitespace-nowrap hover:bg-hover',
        active ? 'font-medium text-ink' : 'text-ink-2',
      )}
    >
      {children}
      <ChevronDown size={10} width={2.4} stroke="var(--color-ink-3)" />
    </span>
  )
}

/**
 * Activity (190), administrators: who did what, when, newest first, filtered by person, action
 * and time span, a page at a time; Export downloads the same slice as a CSV. Opened from Users
 * ("View activity" asks for one person with `?who=`), so the sidebar lights Users.
 */
export function Activity() {
  const me = useMe()
  const [params] = useSearchParams()
  const [filters, setFilters] = useState<ActivityFilters>(() => ({
    who: params.get('who') ?? '',
    action: '',
    since: daysAgo(7),
    until: '',
  }))
  const [span, setSpan] = useState('7')
  const [choosing, setChoosing] = useState(false)
  const users = $api.useQuery('get', '/api/core/users')
  const log = $api.useInfiniteQuery(
    'get',
    '/api/core/activity',
    { params: { query: { ...activityQuery(filters), limit: LIMIT } } },
    {
      pageParamName: 'offset',
      initialPageParam: 0,
      getNextPageParam: (last, _pages, lastOffset) => {
        const loaded = Number(lastOffset) + last.items.length
        return last.items.length > 0 && loaded < last.total ? loaded : undefined
      },
      placeholderData: keepPreviousData,
    },
  )

  const set = (patch: Partial<ActivityFilters>) => setFilters((was) => ({ ...was, ...patch }))
  const pages = log.data?.pages ?? []
  const rows = pages.flatMap((page) => page.items)
  const total = pages[0]?.total ?? 0
  const actions = pages[0]?.actions ?? []
  const actionOptions =
    filters.action && !actions.includes(filters.action) ? [filters.action, ...actions] : actions
  const people = [...(users.data ?? [])].sort((a, b) => a.name.localeCompare(b.name))
  const whoLabel =
    filters.who === SYSTEM
      ? 'System only'
      : (people.find((p) => p.id === filters.who)?.name ?? 'Anyone')
  const spanLabel =
    span === 'custom'
      ? [
          filters.since && `From ${formatDate(filters.since)}`,
          filters.until && `to ${formatDate(filters.until)}`,
        ]
          .filter(Boolean)
          .join(' ') || 'All time'
      : (ACTIVITY_SPANS.find((s) => s.key === span)?.label ?? 'All time')

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Activity" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        <div className="flex items-center gap-2">
          <Menu
            label="Person"
            align="left"
            width={220}
            panelClassName="max-h-[320px] overflow-y-auto"
            items={[
              { label: 'Anyone', current: !filters.who, onPick: () => set({ who: '' }) },
              {
                label: 'System only',
                current: filters.who === SYSTEM,
                onPick: () => set({ who: SYSTEM }),
              },
              ...people.map((person) => ({
                label: person.name,
                current: person.id === filters.who,
                onPick: () => set({ who: person.id }),
              })),
            ]}
          >
            <FilterFace active={Boolean(filters.who)}>{whoLabel}</FilterFace>
          </Menu>
          <Menu
            label="Action"
            align="left"
            width={240}
            panelClassName="max-h-[320px] overflow-y-auto"
            items={[
              { label: 'Any action', current: !filters.action, onPick: () => set({ action: '' }) },
              ...actionOptions.map((action) => ({
                label: action,
                current: action === filters.action,
                onPick: () => set({ action }),
              })),
            ]}
          >
            <FilterFace active={Boolean(filters.action)}>
              {filters.action || 'Any action'}
            </FilterFace>
          </Menu>
          <Menu
            label="When"
            align="left"
            width={200}
            items={[
              ...ACTIVITY_SPANS.map((option) => ({
                label: option.label,
                current: option.key === span,
                onPick: () => {
                  setSpan(option.key)
                  set({
                    since: option.days === null ? '' : daysAgo(option.days),
                    until: '',
                  })
                },
              })),
              {
                label: 'Choose dates…',
                separator: true,
                current: span === 'custom',
                onPick: () => setChoosing(true),
              },
            ]}
          >
            <FilterFace>{spanLabel}</FilterFace>
          </Menu>
          <span className="ml-auto">
            <a
              href={activityExportHref(filters)}
              download
              className="inline-flex h-8 items-center rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover hover:text-ink"
            >
              Export
            </a>
          </span>
        </div>
        <ListCard aria-label="Activity" className={log.isFetching ? 'opacity-90' : undefined}>
          <ListHead columns={COLUMNS}>
            <span>When</span>
            <span>Who</span>
            <span>What</span>
            <span>On</span>
          </ListHead>
          {log.isPending ? (
            Array.from({ length: 4 }, (_, i) => (
              <div
                key={i}
                aria-hidden
                style={{ gridTemplateColumns: COLUMNS, opacity: 1 - i * 0.18 }}
                className="grid items-center gap-x-3.5 px-9 py-[15px]"
              >
                {['70%', '60%', '85%', '50%'].map((width, j) => (
                  <span
                    key={j}
                    className="block h-[11px] rounded-4 bg-fill-skeleton"
                    style={{ width }}
                  />
                ))}
              </div>
            ))
          ) : log.isError && rows.length === 0 ? (
            <LoadFailed
              what="the activity log"
              error={log.error}
              onRetry={() => void log.refetch()}
            />
          ) : rows.length === 0 ? (
            <p className="px-9 py-10 text-center text-125 text-muted">Nothing in this range.</p>
          ) : (
            rows.map((row, index) => (
              <ListRow key={row.id} columns={COLUMNS} index={index}>
                <span className="min-w-0 truncate" title={row.at}>
                  {activityWhen(row.at, me.timezone)}
                </span>
                <span className="min-w-0 truncate">{row.who}</span>
                <span className="min-w-0 truncate" title={`${row.summary} · ${row.action}`}>
                  {row.summary}
                </span>
                <span className="min-w-0 truncate" title={row.on || undefined}>
                  {/* P23b: the thing by name (a tracking number, an agency, a letter, "CSV · all
                      time"), as far as the reader may know; its kind when nothing more. */}
                  {row.on_link ? (
                    <Link to={row.on_link} className="text-ink hover:text-brand">
                      {row.on || activitySubject(row.subject_type || 'request')}
                    </Link>
                  ) : (
                    row.on || activitySubject(row.subject_type)
                  )}
                </span>
              </ListRow>
            ))
          )}
        </ListCard>
        {log.isError && rows.length > 0 ? (
          <p role="alert" className="text-12 text-danger">
            {messageOf(log.error)}
          </p>
        ) : null}
        {log.hasNextPage ? (
          <div className="flex items-center gap-3">
            <Button
              kind="text"
              busy={log.isFetchingNextPage}
              onClick={() => void log.fetchNextPage()}
            >
              Show more
            </Button>
            <span className="text-12 text-muted">
              Showing {rows.length} of {total}
            </span>
          </div>
        ) : null}
      </div>
      {choosing ? (
        <ChooseDates
          since={filters.since}
          until={filters.until}
          onClose={() => setChoosing(false)}
          onChoose={(since, until) => {
            setSpan('custom')
            set({ since, until })
            setChoosing(false)
          }}
        />
      ) : null}
    </div>
  )
}
