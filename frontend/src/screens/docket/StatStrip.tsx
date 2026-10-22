import type { ReactNode } from 'react'

import { cx } from '../../components/ui'
import { barHeight, sparkline } from '../../lib/docket'
import type { DocketStats, Stat } from './stats'

const DAY_LETTERS = ['S', 'M', 'T', 'W', 'T', 'F', 'S']

/** A number while it loads (031): a 34 × 22 block. */
function Skeleton({ className }: { className?: string }) {
  return <span className={cx('block h-[22px] w-[34px] rounded-kbd bg-fill-skeleton', className)} />
}

function Box({
  label,
  active,
  onPick,
  flex,
  gap = 'gap-1.5',
  children,
}: {
  label: string
  active: boolean
  /** Absent when the box does not filter the list. */
  onPick?: () => void
  flex: string
  gap?: string
  children: ReactNode
}) {
  const className = cx(
    'box-border flex min-h-0 min-w-0 flex-col overflow-hidden rounded-16 px-[18px] py-3.5 text-left shadow-card',
    flex,
    gap,
    active ? 'bg-brand-tint' : 'bg-card-white',
    onPick && !active && 'hover:bg-page',
  )
  const caption = (
    <span
      className={cx(
        'truncate text-125',
        label === 'All open' && active ? 'font-semibold text-brand' : 'text-ink-3',
      )}
    >
      {label}
    </span>
  )
  return onPick ? (
    <button type="button" aria-pressed={active} onClick={onPick} className={className}>
      {caption}
      {children}
    </button>
  ) : (
    <div className={className}>
      {caption}
      {children}
    </div>
  )
}

/** 025's sparkline: a 1.6 px line in a 120 × 30 box stretched to the size given. */
function Sparkline({
  values,
  stroke,
  width,
  height,
}: {
  values: number[]
  stroke: string
  width: string
  height: number
}) {
  const points = sparkline(values)
  if (!points) return null
  return (
    <svg
      aria-hidden
      width={width}
      height={height}
      viewBox="0 0 120 30"
      preserveAspectRatio="none"
      className="block min-w-0 shrink"
    >
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={1.6}
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

/**
 * The stat strip (025): Overdue and Producing stacked, Due this week with its bars, Waiting on
 * agency and Assigned to you stacked, All open. Borderless white boxes with the card shadow; the
 * active one on the brand tint. Every box filters the list to what it counted; All open is the
 * resting state. The sparklines under Overdue and All open are the history route's last dates.
 */
export function StatStrip({
  stats,
  today,
  stat,
  onStat,
}: {
  stats: DocketStats | undefined
  today: string
  stat: Stat | null
  onStat: (stat: Stat | null) => void
}) {
  const busiest = Math.max(0, ...(stats?.week ?? []).map((d) => d.count))
  const pick = (box: Stat) => () => onStat(stat === box ? null : box)
  return (
    <div className="flex h-[196px] shrink-0 gap-2.5">
      <div className="flex min-w-0 flex-[1.05] flex-col gap-2.5">
        <Box label="Overdue" flex="flex-1" active={stat === 'overdue'} onPick={pick('overdue')}>
          <div className="mt-auto flex min-w-0 items-end gap-3">
            {stats ? (
              <>
                <span className="text-3xl leading-none font-semibold whitespace-nowrap text-danger">
                  {stats.overdue}
                </span>
                {stats.overdueTrend.length > 1 ? (
                  <span className="ml-auto flex min-w-0">
                    <Sparkline
                      values={stats.overdueTrend}
                      stroke="var(--color-danger)"
                      width="90"
                      height={24}
                    />
                  </span>
                ) : null}
              </>
            ) : (
              <Skeleton />
            )}
          </div>
        </Box>
        <Box
          label="Producing"
          flex="flex-1"
          active={stat === 'producing'}
          onPick={pick('producing')}
        >
          <div className="mt-auto flex min-w-0 items-end gap-3">
            {stats ? (
              <>
                <span className="text-2xl leading-none font-semibold whitespace-nowrap text-ink">
                  {stats.producing}
                </span>
                {stats.needsReview > 0 ? (
                  <span className="ml-auto flex min-w-0">
                    <span
                      className="inline-flex items-center gap-1.5 text-13 leading-none font-semibold text-amber-text"
                      title="Waiting on a person's review"
                    >
                      <span className="size-[7px] rounded-pill bg-amber-dot" />
                      {stats.needsReview}
                    </span>
                  </span>
                ) : null}
              </>
            ) : (
              <>
                <Skeleton />
                <Skeleton className="ml-auto" />
              </>
            )}
          </div>
        </Box>
      </div>
      <Box
        label="Due this week"
        flex="flex-[1.7]"
        gap="gap-2"
        active={stat === 'week'}
        onPick={pick('week')}
      >
        {stats ? (
          <span className="text-32 leading-none font-semibold whitespace-nowrap text-ink">
            {stats.dueThisWeek}
          </span>
        ) : (
          <Skeleton />
        )}
        <div className="mt-auto">
          <div className="flex items-end gap-3.5">
            {(stats?.week ?? []).map(({ day, count }) => {
              const isToday = day === today
              return (
                <div key={day} className="flex flex-col items-center gap-[5px]">
                  <span className={cx('text-105 text-muted', count === 0 && 'invisible')}>
                    {count}
                  </span>
                  <span
                    className={cx(
                      'w-[22px] rounded-4',
                      isToday ? 'bg-brand' : 'bg-blue-fill-strong',
                    )}
                    style={{ height: barHeight(count, busiest) }}
                  />
                  <span
                    className={cx(
                      'text-105',
                      isToday ? 'font-semibold text-brand' : 'font-normal text-muted',
                    )}
                  >
                    {DAY_LETTERS[new Date(`${day}T00:00:00Z`).getUTCDay()]}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      </Box>
      <div className="flex min-w-0 flex-[0.95] flex-col gap-2.5">
        <Box
          label="Waiting on agency"
          flex="flex-1"
          active={stat === 'waiting'}
          onPick={pick('waiting')}
        >
          <div className="mt-auto flex min-w-0 items-end gap-3">
            {stats ? (
              <span className="text-2xl leading-none font-semibold whitespace-nowrap text-ink">
                {stats.waiting}
              </span>
            ) : (
              <Skeleton />
            )}
          </div>
        </Box>
        <Box label="Assigned to you" flex="flex-1" active={stat === 'mine'} onPick={pick('mine')}>
          <div className="mt-auto flex min-w-0 items-end gap-3">
            {stats ? (
              <span className="text-2xl leading-none font-semibold whitespace-nowrap text-ink">
                {stats.mine}
              </span>
            ) : (
              <Skeleton />
            )}
          </div>
        </Box>
      </div>
      <Box label="All open" flex="flex-[1.15]" active={stat === null} onPick={() => onStat(null)}>
        {stats ? (
          <span
            className={cx(
              'text-stat leading-none font-semibold whitespace-nowrap',
              stat === null ? 'text-brand' : 'text-ink',
            )}
          >
            {stats.allOpen}
          </span>
        ) : (
          <Skeleton />
        )}
        {stats && stats.openTrend.length > 1 ? (
          <div className="mt-auto">
            <Sparkline
              values={stats.openTrend}
              stroke="var(--color-brand)"
              width="100%"
              height={36}
            />
          </div>
        ) : null}
      </Box>
    </div>
  )
}

/** The split view's strip (032): six small boxes in one row, the same figures. */
export function CompactStrip({
  stats,
  stat,
  onStat,
}: {
  stats: DocketStats | undefined
  stat: Stat | null
  onStat: (stat: Stat | null) => void
}) {
  const boxes: Array<{
    label: string
    value: number | undefined
    tone: string
    pick?: Stat | null
  }> = [
    { label: 'Overdue', value: stats?.overdue, tone: 'text-danger', pick: 'overdue' },
    { label: 'Due this week', value: stats?.dueThisWeek, tone: 'text-amber-text', pick: 'week' },
    { label: 'Producing', value: stats?.producing, tone: 'text-ink', pick: 'producing' },
    { label: 'Waiting', value: stats?.waiting, tone: 'text-ink', pick: 'waiting' },
    { label: 'Assigned to you', value: stats?.mine, tone: 'text-ink', pick: 'mine' },
    { label: 'All open', value: stats?.allOpen, tone: 'text-ink', pick: null },
  ]
  return (
    <div className="flex shrink-0 gap-2">
      {boxes.map((box) => {
        const active = box.pick !== undefined && box.pick === stat
        const className = cx(
          'flex min-w-0 flex-auto flex-col gap-1 rounded-12 px-[11px] pt-[9px] pb-2 text-left shadow-card',
          active ? 'bg-brand-tint' : 'bg-card-white',
        )
        const body = (
          <>
            {box.value === undefined ? (
              <Skeleton className="h-[18px]" />
            ) : (
              <span className={cx('text-18 leading-none font-medium tabular-nums', box.tone)}>
                {box.value}
              </span>
            )}
            <span className={cx('truncate text-11', active ? 'text-brand' : 'text-muted')}>
              {box.label}
            </span>
          </>
        )
        return box.pick === undefined ? (
          <div key={box.label} className={className}>
            {body}
          </div>
        ) : (
          <button
            key={box.label}
            type="button"
            aria-pressed={active}
            onClick={() => onStat(active && box.pick !== null ? null : (box.pick ?? null))}
            className={className}
          >
            {body}
          </button>
        )
      })}
    </div>
  )
}
