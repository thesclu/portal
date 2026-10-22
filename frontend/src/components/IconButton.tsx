import type { ReactNode } from 'react'

import { cx } from './ui'

/** One of the top bar's 38 px buttons: 10 px radius, no border, the icon centred. */
export function IconButton({
  label,
  inert = false,
  open = false,
  className,
  children,
  ...rest
}: {
  label: string
  /** Drawn, not yet wired: no screen says what it opens. */
  inert?: boolean
  open?: boolean
  className?: string
  children: ReactNode
  onClick?: () => void
  'aria-controls'?: string
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-disabled={inert || undefined}
      aria-expanded={inert ? undefined : open}
      {...rest}
      className={cx(
        'relative box-border inline-flex size-[38px] shrink-0 items-center justify-center rounded-10 outline-none',
        inert
          ? 'cursor-default'
          : 'hover:bg-hover focus-visible:outline-2 focus-visible:outline-brand',
        open && 'bg-hover',
        className,
      )}
    >
      {children}
    </button>
  )
}
