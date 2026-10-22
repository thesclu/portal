import type { ReactNode } from 'react'

import { CheckIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { statusGlyph, type GlyphTone } from '../../lib/status'

const TONE: Record<GlyphTone, string> = {
  danger: 'var(--color-danger)',
  brand: 'var(--color-brand)',
  muted: 'var(--color-muted)',
}

// The pie slices 025 draws inside the ring, all on a 12 × 12 box (radius 2.945).
const SLICE = {
  quarter: 'M6 6L6 3.055A2.945 2.945 0 0 1 8.945 6Z',
  half: 'M6 6L6 3.055A2.945 2.945 0 0 1 6 8.945Z',
  'three-quarters': 'M6 6L6 3.055A2.945 2.945 0 1 1 3.055 6Z',
} as const

/** The 12 px ring before a status (025): how far along the request is, and whether it is late. */
export function StatusGlyph({ code }: { code: string }) {
  const { fill, tone } = statusGlyph(code)
  const colour = TONE[tone]
  return (
    <svg aria-hidden width="12" height="12" viewBox="0 0 12 12" className="shrink-0">
      <circle cx="6" cy="6" r="4.75" fill="none" stroke={colour} strokeWidth="1.5" />
      {fill === 'dot' ? <circle cx="6" cy="6" r="2.14" fill={colour} /> : null}
      {fill === 'small-dot' ? <circle cx="6" cy="6" r="1.3" fill={colour} /> : null}
      {fill in SLICE ? <path d={SLICE[fill as keyof typeof SLICE]} fill={colour} /> : null}
    </svg>
  )
}

/** The 15 px box of a selectable row (028): empty, ticked, or the header's "some" dash. */
export function CheckBox({
  checked,
  some = false,
  label,
  onChange,
}: {
  checked: boolean
  some?: boolean
  label: string
  onChange: () => void
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={some ? 'mixed' : checked}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation()
        onChange()
      }}
      className={cx(
        'box-border inline-flex size-[15px] shrink-0 items-center justify-center rounded-4',
        checked || some ? 'bg-brand' : 'border-[1.5px] border-line-control bg-card-white',
      )}
    >
      {some ? (
        <svg
          aria-hidden
          width="9"
          height="9"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--color-card-white)"
          strokeWidth="3.4"
          strokeLinecap="round"
        >
          <path d="M6 12h12" />
        </svg>
      ) : checked ? (
        <CheckIcon size={9} width={3.4} stroke="var(--color-card-white)" />
      ) : null}
    </button>
  )
}

/** A toolbar control's face (025): 34 px, 9 px round, 12.5 px ink-2; pressed, it is lit. */
export function ToolFace({ active = false, children }: { active?: boolean; children: ReactNode }) {
  return (
    <span
      className={cx(
        'box-border inline-flex h-[34px] shrink-0 items-center gap-[7px] rounded-9 px-2.5 text-125 whitespace-nowrap',
        active ? 'bg-hover font-medium text-ink' : 'text-ink-2 hover:bg-hover',
      )}
    >
      {children}
    </span>
  )
}

export function ToolButton({
  active = false,
  onClick,
  children,
}: {
  active?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} className="rounded-9">
      <ToolFace active={active}>{children}</ToolFace>
    </button>
  )
}

/** A quiet action in a selection bar (028, 040). */
export function BulkButton({
  icon,
  onClick,
  children,
}: {
  icon?: ReactNode
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex h-8 items-center gap-[7px] rounded-9 px-2.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
    >
      {icon}
      {children}
    </button>
  )
}
