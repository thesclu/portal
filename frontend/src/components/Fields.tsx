/**
 * The dialog fields of the v4 screens (070–104), each drawn from the screens' inline styles.
 *
 * A dialog field is not outlined: it is white, 12 px round and raised by a shadow (070 "New
 * status", "Reason"); with the focus it becomes the 1.5 px brand outline, 10 px round (071
 * "New"). A read-only value sits on the page grey (070 "Now"). The 1.5 px border is always
 * there, transparent until the focus, so nothing moves when it arrives.
 */
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

import { CheckIcon, ChevronDown, SearchIcon } from './icons'
import { cx } from './ui'

const RAISED =
  'box-border rounded-12 border-[1.5px] border-transparent bg-card-white px-[10.5px] text-13 ' +
  'text-ink shadow-field outline-none placeholder:text-muted focus:rounded-10 ' +
  'focus:border-brand focus:shadow-none focus-visible:outline-none disabled:bg-page ' +
  'disabled:text-ink-2 aria-invalid:border-red-line'

/** 40 px in 070–072, 38 px in the later dialogs (079–104). */
const height = (compact?: boolean) => (compact ? 'h-[38px]' : 'h-10')

export function RaisedInput({
  compact,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { compact?: boolean }) {
  return <input {...rest} className={cx(RAISED, height(compact), 'w-full', className)} />
}

export function RaisedSelect({
  compact,
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { compact?: boolean }) {
  return (
    <span className="relative inline-flex w-full">
      <select
        {...rest}
        className={cx(RAISED, height(compact), 'w-full appearance-none pr-8', className)}
      >
        {children}
      </select>
      <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2">
        <ChevronDown size={compact ? 10 : 11} width={2.4} stroke="var(--color-ink-3)" />
      </span>
    </span>
  )
}

export function RaisedTextarea({
  minHeight = 76,
  className,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { minHeight?: number }) {
  return (
    <textarea
      {...rest}
      style={{ minHeight }}
      // It grows with what is in it, as the screens' boxes do (170 "Letter goes to").
      className={cx(
        RAISED,
        'w-full resize-none field-sizing-content py-[8.5px] leading-body',
        className,
      )}
    />
  )
}

/** A value shown, not edited (070 "Now"). */
export function ReadonlyBox({ compact, children }: { compact?: boolean; children: ReactNode }) {
  return (
    <div
      className={cx(
        'box-border flex items-center gap-2 rounded-12 bg-page px-3 text-13 text-ink-2',
        height(compact),
      )}
    >
      <span className="min-w-0 flex-1 truncate">{children}</span>
    </div>
  )
}

/** One choice of several in a row (072 "Who called", 079 an item's disposition, 085 "Type"). */
export function Segmented({
  label,
  options,
  value,
  onChange,
  size = 'sm',
}: {
  label: string
  options: Array<{ value: string; label: string }>
  value: string
  onChange: (value: string) => void
  /** sm: 32 px, 12.5 px (079, 085, 089); md: 38 px, 13 px (072). */
  size?: 'sm' | 'md'
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx(
        'box-border inline-flex shrink-0 overflow-hidden border-[1.5px] border-line-2',
        size === 'md' ? 'h-[38px] rounded-10' : 'h-8 rounded-9',
      )}
    >
      {options.map((option, index) => {
        const chosen = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={chosen}
            onClick={() => onChange(option.value)}
            className={cx(
              'inline-flex items-center whitespace-nowrap',
              size === 'md' ? 'px-3.5 text-13' : 'px-[11px] text-125',
              index > 0 && 'border-l border-line',
              chosen ? 'bg-hover font-medium text-ink' : 'text-ink-2 hover:bg-page',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

/** The 15 px square box of the dialogs (072 "Also change status", 079 a file). */
export function Box({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden
      className={cx(
        'box-border inline-flex size-[15px] shrink-0 items-center justify-center rounded-4',
        checked ? 'bg-brand' : 'border-[1.5px] border-line-control bg-card-white',
      )}
    >
      {checked ? <CheckIcon size={9} width={3.4} stroke="var(--color-card-white)" /> : null}
    </span>
  )
}

export function CheckRow({
  checked,
  onChange,
  children,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  children: ReactNode
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2.5 text-13 text-ink">
      <input
        type="checkbox"
        className="sr-only"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <Box checked={checked} />
      <span className="min-w-0">{children}</span>
    </label>
  )
}

/** A choice drawn as a card with its own words (073, 075, 087, 099). `tight`: the 11 px padding
 *  087 and 172 draw, where 073 and 075 draw 12. */
export function RadioCard({
  name,
  checked,
  onChange,
  title,
  hint,
  tight = false,
}: {
  name: string
  checked: boolean
  onChange: () => void
  title: ReactNode
  hint?: ReactNode
  tight?: boolean
}) {
  return (
    <label
      className={cx(
        'flex cursor-pointer items-start gap-3 px-3.5',
        tight ? 'py-[11px]' : 'py-3',
        checked
          ? 'rounded-10 border-[1.5px] border-brand bg-blue-tint-3'
          : 'rounded-12 bg-card-white shadow-field',
      )}
    >
      <input type="radio" className="sr-only" name={name} checked={checked} onChange={onChange} />
      <span
        aria-hidden
        className={cx(
          'mt-px box-border size-4 shrink-0 rounded-pill',
          checked ? 'border-[5px] border-brand' : 'border-[1.5px] border-line-control',
        )}
      />
      <span className="min-w-0">
        <span className="block text-13 font-medium text-ink">{title}</span>
        {hint ? (
          <span className="mt-0.5 block text-12 leading-[1.45] text-muted">{hint}</span>
        ) : null}
      </span>
    </label>
  )
}

/** The underlined search above a picker list (089, 096). */
export function SearchLine({
  placeholder,
  value,
  onChange,
}: {
  placeholder: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="box-border flex h-[34px] items-center gap-2 border-b-[1.5px] border-line-underline pr-1">
      <SearchIcon size={15} width={2} stroke="var(--color-ink-3)" />
      <input
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-muted-2"
      />
    </label>
  )
}

/** A raised list of ticked rows (089, 096, 099): a chosen row on the brand tint. */
export function Checklist({
  rows,
  chosen,
  onToggle,
  empty = 'Nothing to choose from.',
}: {
  rows: Array<{
    value: string
    label: ReactNode
    lead?: ReactNode
    meta?: ReactNode
    disabled?: boolean
  }>
  chosen: string[]
  onToggle: (value: string) => void
  empty?: string
}) {
  if (rows.length === 0) return <p className="text-125 text-muted">{empty}</p>
  return (
    <div className="overflow-hidden rounded-12 bg-card-white shadow-field">
      {rows.map((row, index) => {
        const on = chosen.includes(row.value)
        return (
          <label
            key={row.value}
            className={cx(
              'flex items-center gap-2.5 px-3.5 py-2.5 text-125',
              index > 0 && 'border-t border-line-row-2',
              on && 'bg-blue-tint-3',
              row.disabled ? 'cursor-not-allowed' : 'cursor-pointer',
            )}
          >
            <input
              type="checkbox"
              className="sr-only"
              checked={on}
              disabled={row.disabled}
              onChange={() => onToggle(row.value)}
            />
            <Box checked={on} />
            {row.lead ? <span className="text-ink-2">{row.lead}</span> : null}
            <span className="min-w-0 flex-1 truncate text-ink">{row.label}</span>
            {row.meta ? <span className="whitespace-nowrap text-muted">{row.meta}</span> : null}
          </label>
        )
      })}
    </div>
  )
}

/** A field's label over its control (070): 12.5 px ink-2, "required" in red beside it. */
export function DialogField({
  label,
  required,
  hint,
  hideLabel = false,
  children,
}: {
  label: string
  required?: boolean
  hint?: ReactNode
  /** The label is read out but not drawn (102: the drop zone says what it is). */
  hideLabel?: boolean
  children: ReactNode
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className={cx('text-125 leading-[normal] text-ink-2', hideLabel && 'sr-only')}>
        {label}
        {required ? <span className="ml-1 text-danger">required</span> : null}
      </span>
      {children}
      {hint ? <span className="text-12 text-muted">{hint}</span> : null}
    </label>
  )
}

/** One choice of a few on a grey pill track (165, 166 "Files by"): the chosen one white and
 *  raised, every option the same width. */
export function PillChoice({
  label,
  options,
  value,
  onChange,
  size = 'sm',
}: {
  label: string
  options: Array<{ value: string; label: string }>
  value: string
  onChange: (value: string) => void
  /** sm: 12 px, every option the same width (165, 213); md: 12.5 px, each its own width (008). */
  size?: 'sm' | 'md'
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx(
        'inline-flex gap-0.5 rounded-pill bg-page p-[3px]',
        size === 'md' && 'self-start',
      )}
    >
      {options.map((option) => {
        const chosen = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={chosen}
            onClick={() => onChange(option.value)}
            className={cx(
              'rounded-pill text-center leading-[normal] whitespace-nowrap',
              size === 'md' ? 'px-3.5 py-1.5 text-125' : 'flex-1 px-3 py-[5px] text-12',
              chosen
                ? 'bg-card-white font-semibold text-ink shadow-xs'
                : 'font-medium text-ink-2 hover:text-ink',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

/** The on/off switch of Settings (201 "Email me when", 217 "Use in"): a 32 × 18 pill (30 wide
 *  in 217), brand when on and the line grey when off, a 14 px white knob 2 px in from the edge. */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
  narrow = false,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  disabled?: boolean
  /** 30 px wide, as 217 draws it, rather than 32. */
  narrow?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative inline-block h-[18px] shrink-0 rounded-pill disabled:cursor-not-allowed disabled:opacity-55',
        narrow ? 'w-[30px]' : 'w-8',
        checked ? 'bg-brand' : 'bg-line-2',
      )}
    >
      <span
        aria-hidden
        className={cx(
          'absolute top-0.5 size-3.5 rounded-pill bg-card-white',
          checked ? 'right-0.5' : 'left-0.5',
        )}
      />
    </button>
  )
}
