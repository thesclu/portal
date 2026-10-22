/**
 * The list cards of the v5 Agencies, Tasks, Records and Reports screens (039–043, 163–185),
 * drawn from their inline styles: a #fdfdfd card, 16 px round, the float shadow; a head row of
 * 10.5 px small capitals on a hairline, 9px 36px; rows of 12.5 px, 11px 36px, every other one on
 * the row grey. The columns are a CSS grid template the caller gives, 14 px apart.
 *
 * And the detail cards (164): a 14 px 600 title with a quiet link at its right, and label · value
 * lines under hairlines, the label 118 px wide.
 */
import type { CSSProperties, ReactNode } from 'react'
import { Link } from 'react-router'

import { cx } from './ui'

export function ListCard({
  className,
  children,
  'aria-label': label,
}: {
  className?: string
  children: ReactNode
  'aria-label'?: string
}) {
  return (
    <section
      aria-label={label}
      className={cx('overflow-hidden rounded-16 bg-card text-125 shadow-float', className)}
    >
      {children}
    </section>
  )
}

const GRID = 'grid gap-x-3.5 px-9'

/** The small-capitals head row. `ruled="both"` adds the hairline above (a head under a title). */
export function ListHead({
  columns,
  ruled = 'bottom',
  className,
  children,
}: {
  columns: string
  ruled?: 'bottom' | 'both'
  className?: string
  children: ReactNode
}) {
  return (
    <div
      role="row"
      style={{ gridTemplateColumns: columns }}
      className={cx(
        GRID,
        'py-[9px] text-105 leading-[normal] font-semibold tracking-label text-muted uppercase',
        ruled === 'both' ? 'border-y border-hairline' : 'border-b border-hairline',
        className,
      )}
    >
      {children}
    </div>
  )
}

/** A row: a link when `to` is given, else a plain row (its cells may hold buttons). */
export function ListRow({
  columns,
  index,
  to,
  onClick,
  selected = false,
  tone = 'brand',
  zebra = true,
  pad = 'py-[11px]',
  className,
  style,
  children,
  'aria-label': label,
}: {
  columns: string
  /** Its place in the list: every other row is on the row grey. */
  index: number
  /** The row's top and bottom padding (11 px; 163 draws 12, 040 draws 10). */
  pad?: string
  to?: string
  onClick?: () => void
  /** Lit as the open or chosen row (177's #eef3fa). */
  selected?: boolean
  /** The lit row's ground: the brand tint (177), or the softer one of a ticked row (040). */
  tone?: 'brand' | 'soft'
  /** Every other row on the row grey (163, 173); 040 draws none. */
  zebra?: boolean
  className?: string
  style?: CSSProperties
  children: ReactNode
  'aria-label'?: string
}) {
  const classes = cx(
    GRID,
    // Each row but the last carries a transparent hairline, as the screens draw them.
    'items-center border-b border-transparent text-125 leading-[normal] text-ink last:border-b-0',
    pad,
    selected
      ? tone === 'soft'
        ? 'bg-blue-tint-3'
        : 'bg-brand-tint'
      : zebra && index % 2 === 1
        ? 'bg-row-alt hover:bg-surface-row'
        : 'hover:bg-surface-row',
    (to || onClick) && 'cursor-pointer',
    className,
  )
  const merged = { gridTemplateColumns: columns, ...style }
  if (to) {
    return (
      <Link to={to} aria-label={label} style={merged} className={cx(classes, 'hover:text-ink')}>
        {children}
      </Link>
    )
  }
  return (
    <div role="row" aria-label={label} style={merged} className={classes} onClick={onClick}>
      {children}
    </div>
  )
}

/** A card's title row (164 "Filing · Edit"): 14 px 600, a 12.5 px brand action at the right. */
export function CardHead({
  title,
  count,
  action,
  className,
}: {
  title: string
  count?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cx('flex items-center gap-2.5 px-5 pt-3.5 pb-3 leading-[normal]', className)}>
      <span className="text-14 font-semibold text-ink">{title}</span>
      {count !== undefined && count !== null ? (
        <span className="text-125 text-muted">{count}</span>
      ) : null}
      {action ? <span className="ml-auto flex items-center gap-3">{action}</span> : null}
    </div>
  )
}

/** The quiet link at a card's right ("Edit", "Add", "See all 6"). */
export function CardAction({
  onClick,
  to,
  children,
}: {
  onClick?: () => void
  to?: string
  children: ReactNode
}) {
  const classes =
    'text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark focus-visible:outline-brand'
  if (to) {
    return (
      <Link to={to} className={classes}>
        {children}
      </Link>
    )
  }
  return (
    <button type="button" onClick={onClick} className={classes}>
      {children}
    </button>
  )
}

/** One label · value line of a detail card (164): 11px 20px under a hairline. */
export function InfoRow({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: ReactNode
}) {
  return (
    <div
      className={cx(
        'flex gap-4 border-t border-line-row px-5 py-[11px] text-125 leading-[normal]',
        className,
      )}
    >
      <span className="w-[118px] shrink-0 text-muted">{label}</span>
      {children}
    </div>
  )
}
