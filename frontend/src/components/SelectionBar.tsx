import type { ReactNode } from 'react'

import { Button } from './ui'

/**
 * The table's header row while rows are selected: the column names give way to "N selected",
 * the actions, and Clear, on the brand tint. Every list puts this in its <thead> in place of
 * its own header row, so nothing else knows what it looks like.
 */
export function SelectionBar({
  count,
  columns,
  onClear,
  leading,
  children,
}: {
  count: number
  /** How many columns the table has, so the one cell spans them all. */
  columns: number
  onClear: () => void
  /** Anything that belongs before the count, such as the select-all box. */
  leading?: ReactNode
  children: ReactNode
}) {
  if (count === 0) return null
  return (
    <tr>
      <th colSpan={columns} className="rounded-10 bg-brand-tint px-3 py-1.5 text-left font-normal">
        <div
          role="toolbar"
          aria-label="Actions for the selected rows"
          className="flex items-center gap-2"
        >
          {leading}
          <span className="text-125 font-semibold text-brand">{count} selected</span>
          <span className="flex flex-1 flex-wrap items-center gap-1.5">{children}</span>
          <Button kind="text" onClick={onClear}>
            Clear
          </Button>
        </div>
      </th>
    </tr>
  )
}
