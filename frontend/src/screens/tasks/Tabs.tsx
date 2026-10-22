import { Link } from 'react-router'

import { cx } from '../../components/ui'

type Current = 'approvals' | 'suggestions'

/**
 * The tabs over Tasks' approvals (234): All · Assigned to me · Approvals, each with its count,
 * 4 px apart; the open one on white with the segment shadow, ink and 600, the others ink-2 and
 * 500, the count 12 px muted. All and Assigned to me go back to the list (173) with its strip.
 * Suggestions (192), drawn nowhere in v6, follows while one waits or while it is open.
 */
export function TaskTabs({
  current,
  all,
  mine,
  approvals,
  suggestions,
}: {
  current: Current
  all?: number
  mine?: number
  approvals?: number
  suggestions?: number
}) {
  const tab = (to: string, label: string, count: number | undefined, on: boolean) => (
    <Link
      key={label}
      to={to}
      replace
      role="tab"
      aria-selected={on}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-9 px-3 py-[7px] text-13 leading-[normal]',
        on
          ? 'bg-card-white font-semibold text-ink shadow-xs hover:text-ink'
          : 'font-medium text-ink-2 hover:bg-hover hover:text-ink',
      )}
    >
      {label}
      {count !== undefined ? <span className="text-12 font-medium text-muted">{count}</span> : null}
    </Link>
  )
  return (
    <div role="tablist" aria-label="Tasks" className="flex gap-1">
      {tab('/tasks?scope=everyone', 'All', all, false)}
      {tab('/tasks', 'Assigned to me', mine, false)}
      {tab('/tasks?view=approvals', 'Approvals', approvals, current === 'approvals')}
      {suggestions || current === 'suggestions'
        ? tab('/tasks?view=suggestions', 'Suggestions', suggestions, current === 'suggestions')
        : null}
    </div>
  )
}
