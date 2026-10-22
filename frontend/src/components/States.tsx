/** What a list or a page shows when it has nothing to show yet, or cannot (design 211–213). */
import type { ReactNode } from 'react'

import { messageOf } from '../api/query'
import { Button, Callout, cx } from './ui'

export function Loading({ what = 'Loading' }: { what?: string }) {
  return (
    <div role="status" aria-live="polite" className="px-4 py-10 text-center text-13 text-muted">
      {what}…
    </div>
  )
}

/**
 * The centred state: an 18 px 600 title, a 13 px muted line under it (at most 420 px wide),
 * and the actions in a row 8 px below. `full` makes it fill the page (min 420 px), as the
 * error pages do; a list's empty state is shorter.
 */
export function Empty({
  title,
  actions,
  full = false,
  role,
  children,
}: {
  title: string
  actions?: ReactNode
  full?: boolean
  role?: string
  children?: ReactNode
}) {
  return (
    <div
      role={role}
      className={cx(
        'flex flex-col items-center justify-center gap-2.5 px-4',
        full ? 'min-h-[420px] flex-1' : 'py-12',
      )}
    >
      <p className="text-18 font-semibold text-ink">{title}</p>
      {children ? (
        <p className="max-w-[420px] text-center text-13 leading-[1.6] text-muted">{children}</p>
      ) : null}
      {actions ? <div className="mt-2 flex gap-2">{actions}</div> : null}
    </div>
  )
}

/** "Couldn't load … Try again": a failed read, with the way to repeat it. */
export function LoadFailed({
  what,
  error,
  onRetry,
}: {
  what: string
  error?: unknown
  onRetry: () => void
}) {
  return (
    <Empty
      role="alert"
      title={`Couldn’t load ${what}`}
      actions={
        <Button kind="pill" onClick={onRetry}>
          Try again
        </Button>
      }
    >
      {messageOf(error, 'The server did not answer.')}
    </Empty>
  )
}

/** "Couldn't save … Refresh": a write refused because the thing changed underneath (a 409). */
export function SaveConflict({ message, onRefresh }: { message: string; onRefresh: () => void }) {
  return (
    <Callout tone="amber" role="alert" className="flex items-center justify-between gap-3">
      <span>Couldn’t save. {message}</span>
      <Button kind="text" className="h-auto py-1 text-amber" onClick={onRefresh}>
        Refresh
      </Button>
    </Callout>
  )
}
