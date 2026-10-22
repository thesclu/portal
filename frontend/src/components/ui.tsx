/**
 * The small pieces every screen is made of, drawn as design v3 draws them (tokens.css recipes and
 * the screens' inline styles). Colour, size and radius come from tokens.css; nothing here is a
 * literal. The pre-v3 kinds (`secondary`, `quiet`) and names (`Notice`) are kept as aliases so an
 * unported screen still compiles; a ported screen uses the v3 names.
 */
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

import { ChevronDown } from './icons'

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */

/**
 * primary — brand fill, square 9 px (`pill` rounds it: dialog footers).
 * pill    — the 32 px brand pill of page actions and state pages ("＋ New request", "Go home").
 * text    — no fill, ink-2 (`pill`: the dialog footer's Cancel).
 * outline — 32 px, 1 px line border.
 * icon    — a 28 px square for one icon.
 * danger  — primary in the danger colour.
 * secondary → outline and quiet → text are the pre-v3 names.
 */
export type ButtonKind =
  'primary' | 'pill' | 'text' | 'outline' | 'icon' | 'danger' | 'secondary' | 'quiet'

const KIND: Record<Exclude<ButtonKind, 'secondary' | 'quiet'>, string> = {
  primary:
    'rounded-9 bg-brand px-[18px] py-[10px] text-13 font-semibold text-white hover:bg-brand-dark',
  danger:
    'rounded-9 bg-danger px-[18px] py-[10px] text-13 font-semibold text-white hover:bg-danger-2',
  pill: 'h-8 rounded-pill bg-brand px-[14px] text-125 font-medium text-white hover:bg-brand-dark',
  text: 'h-8 rounded-9 px-[14px] text-125 font-medium text-ink-2 hover:bg-hover',
  outline:
    'h-8 rounded-9 border border-line bg-card-white px-[13px] text-125 font-medium text-ink-2 hover:bg-page',
  icon: 'size-7 rounded-8 text-ink-3 hover:bg-hover hover:text-ink',
}

/** The pill variants in full (dialog footers): each replaces its kind's classes rather than adding
 *  to them, so two font sizes never meet on one element. Every dialog footer draws its buttons at
 *  the default line height (35 px tall with the 9 px padding: 097 and the other 61 footers), not
 *  the 1 of the page buttons. */
const PILLED: Partial<Record<ButtonKind, string>> = {
  primary:
    'rounded-pill bg-brand px-[18px] py-[9px] text-13 leading-[normal] font-semibold text-white hover:bg-brand-dark',
  danger:
    'rounded-pill bg-danger px-[18px] py-[9px] text-13 leading-[normal] font-semibold text-white hover:bg-danger-2',
  text: 'rounded-pill px-3 py-[9px] text-13 leading-[normal] font-medium text-ink-2 hover:bg-hover',
}

export function Button({
  kind = 'outline',
  pill = false,
  busy = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  kind?: ButtonKind
  pill?: boolean
  busy?: boolean
}) {
  const resolved = kind === 'secondary' ? 'outline' : kind === 'quiet' ? 'text' : kind
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cx(
        'inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap',
        'transition-colors duration-[120ms] disabled:cursor-not-allowed disabled:opacity-55',
        (pill && PILLED[resolved]) || cx('leading-none', KIND[resolved]),
        className,
      )}
    >
      {children}
    </button>
  )
}

/* ── Chips, badges, pills ────────────────────────────────────────────────── */

export function Chip({
  primary = false,
  className,
  children,
}: {
  primary?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <span
      className={cx(
        'inline-flex h-7 items-center rounded-pill px-[11px] text-115 leading-none',
        primary
          ? 'bg-brand font-semibold text-white'
          : 'border border-line-2 font-medium text-ink-2',
        className,
      )}
    >
      {children}
    </span>
  )
}

export type Tone = 'neutral' | 'accent' | 'danger' | 'warn' | 'ok'

const TONE: Record<Tone, string> = {
  neutral: 'bg-page text-ink-2',
  accent: 'bg-brand-tint text-brand',
  danger: 'bg-danger-tint text-danger-2',
  warn: 'bg-warn-tint text-warn',
  ok: 'bg-ok-tint text-ok',
}

/** The flag badge and its siblings: 11 px 600 on a tint, pill-shaped. */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-pill px-[9px] py-0.5 text-11 leading-none font-semibold',
        TONE[tone],
      )}
    >
      {children}
    </span>
  )
}

/** "+15d" in a table cell: the only colour in a row. */
export function OverduePill({ children }: { children: ReactNode }) {
  return (
    <span className="-my-0.5 -mr-2 inline-block rounded-6 bg-danger-tint px-2 py-0.5 leading-[1.35] font-semibold text-danger-2">
      {children}
    </span>
  )
}

/** "Determination · overdue": the family in muted, the qualifier in 500 ink. */
export function StatusText({ family, qualifier }: { family: string; qualifier?: string | null }) {
  return (
    <span className="min-w-0 truncate">
      <span className="text-muted">
        {family}
        {qualifier ? ' · ' : ''}
      </span>
      {qualifier ? <span className="font-medium text-ink">{qualifier}</span> : null}
    </span>
  )
}

/* ── Surfaces ────────────────────────────────────────────────────────────── */

export function Card({
  className,
  white = false,
  children,
}: {
  className?: string
  /** The white variant: tables, sidebar, dialogs. */
  white?: boolean
  children: ReactNode
}) {
  return (
    <section
      className={cx(
        'overflow-hidden rounded-16',
        white ? 'bg-card-white shadow-card' : 'bg-card shadow-float',
        className,
      )}
    >
      {children}
    </section>
  )
}

/** A card's first row: a 14 px 600 title with an optional muted count beside it. */
export function CardTitle({ count, children }: { count?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 px-5 pt-3.5 pb-3">
      <span className="text-14 font-semibold text-ink">{children}</span>
      {count !== undefined && count !== null ? (
        <span className="text-125 text-muted">{count}</span>
      ) : null}
    </div>
  )
}

export type CalloutTone = 'amber' | 'danger' | 'grey'

const CALLOUT: Record<CalloutTone, string> = {
  amber: 'bg-amber-tint text-amber',
  danger: 'bg-danger-tint text-danger-2',
  grey: 'bg-page text-ink-2',
}

/** State by fill only: never a coloured left border. */
export function Callout({
  tone = 'grey',
  role,
  className,
  children,
}: {
  tone?: CalloutTone
  role?: string
  className?: string
  children: ReactNode
}) {
  return (
    <div
      role={role}
      className={cx(
        'rounded-12 px-[15px] py-[13px] text-12 leading-body',
        CALLOUT[tone],
        className,
      )}
    >
      {children}
    </div>
  )
}

const NOTICE_TONE: Record<Tone, CalloutTone> = {
  neutral: 'grey',
  accent: 'grey',
  ok: 'grey',
  warn: 'amber',
  danger: 'danger',
}

/** Pre-v3 name for a callout; the tone maps onto the three callouts the design has. */
export function Notice({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <Callout tone={NOTICE_TONE[tone]} role="status">
      {children}
    </Callout>
  )
}

/** A heading for a block of rows, in the design's small capitals (10.5 px, .07em, muted). */
export function GroupLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <h3
      className={cx(
        'mb-2 text-105 leading-none font-semibold tracking-label text-muted uppercase',
        className,
      )}
    >
      {children}
    </h3>
  )
}

/* ── Fields ──────────────────────────────────────────────────────────────── */

export function Field({
  label,
  hint,
  error,
  required,
  children,
}: {
  label: string
  hint?: ReactNode
  error?: ReactNode
  /** Draws the design's "required" marker beside the label. */
  required?: boolean
  children: ReactNode
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-125 leading-[normal] text-ink-2">
        {label}
        {required ? <span className="ml-1 text-danger">required</span> : null}
      </span>
      {children}
      {error ? (
        <span role="alert" className="text-125 text-danger">
          {error}
        </span>
      ) : hint ? (
        <span className="text-12 text-muted">{hint}</span>
      ) : null}
    </label>
  )
}

const CONTROL =
  'box-border rounded-10 border-[1.5px] border-line-2 bg-card-white px-3 text-13 text-ink ' +
  'outline-none placeholder:text-muted focus:border-brand focus:shadow-ring-focus-strong focus-visible:outline-none ' +
  'aria-invalid:border-red-line disabled:bg-page disabled:text-ink-2'

/** Full width unless the caller names a width: two width classes on one element do not resolve
 *  by the order they are written in, so only one may be present. */
const width = (className?: string) => (/(^|\s)w-/.test(className ?? '') ? null : 'w-full')

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cx(CONTROL, 'h-10', width(className), className)} />
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={cx('relative inline-flex', width(className))}>
      <select
        {...rest}
        className={cx(CONTROL, 'h-10 appearance-none pr-8', width(className), className)}
      >
        {children}
      </select>
      <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-ink-3">
        <ChevronDown size={11} width={2.4} />
      </span>
    </span>
  )
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...rest}
      className={cx(CONTROL, 'min-h-[76px] py-2.5 leading-body', width(className), className)}
    />
  )
}

/* ── People ──────────────────────────────────────────────────────────────── */

/** A person's initials in a circle (the portal has no photos): brand fill, white letters. */
export function Avatar({
  initials,
  size = 28,
  className,
}: {
  initials: string
  size?: number
  className?: string
}) {
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, fontSize: size >= 30 ? 11 : size <= 22 ? 8 : 9.5 }}
      className={cx(
        'inline-flex shrink-0 items-center justify-center rounded-pill bg-brand leading-none font-semibold text-white',
        className,
      )}
    >
      {initials}
    </span>
  )
}

/** A key hint: "esc", "↵", "/". */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-kbd border border-line px-1.5 py-px text-11 leading-[normal] text-muted-2">
      {children}
    </span>
  )
}
