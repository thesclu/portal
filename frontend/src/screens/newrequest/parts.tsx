/**
 * The small pieces the new-request screens (044–061) share, drawn from their inline styles:
 * the underlined search, the outlined "Use", the quiet brand actions with an icon, the section
 * titles of stage 4's card, the 13 px text button beside a primary one, and the composer of the
 * assistant's panel. Colours and type come from tokens.css.
 */
import {
  useEffect,
  useState,
  type ButtonHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from 'react'

import { CheckIcon, ReturnIcon, SearchIcon } from '../../components/icons'
import { cx } from '../../components/ui'

/** The underlined search (044 "Search master letters…", 048 "Add an agency"): 34 px high, a
 *  1.5 px rule under it, the 15 px glass, 13 px words in ink-3 until something is typed. */
export function FlowSearch({
  placeholder,
  value,
  onChange,
  onKeyDown,
  inputRef,
  className,
  autoFocus,
  label,
}: {
  placeholder: string
  value: string
  onChange: (value: string) => void
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void
  inputRef?: Ref<HTMLInputElement>
  className?: string
  autoFocus?: boolean
  label?: string
}) {
  return (
    <label
      className={cx(
        'box-border flex h-[34px] items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1',
        className,
      )}
    >
      <SearchIcon size={15} width={2} stroke="var(--color-ink-3)" />
      <input
        ref={inputRef}
        type="search"
        aria-label={label ?? placeholder}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        className="min-w-0 flex-1 bg-transparent text-13 leading-[normal] text-ink outline-none placeholder:text-ink-3 [&::-webkit-search-cancel-button]:hidden"
      />
    </label>
  )
}

/** "Use" (044): 54 px wide, a 1.5 px brand outline on white, 11.5 px. */
export function UseButton({
  busy,
  className,
  children = 'Use',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
      className={cx(
        'box-content inline-block w-[54px] shrink-0 rounded-8 border-[1.5px] border-brand bg-card-white py-1.5 text-center text-115 leading-[normal] font-medium text-brand hover:bg-brand-tint disabled:opacity-55',
        className,
      )}
    >
      {children}
    </button>
  )
}

/**
 * Copy and download as v6 draws them (049–051): a 28 px square, 8 px round, one 15 px icon, its
 * old label as the tooltip; blue where the person files by hand (049, 051), grey where the letter
 * goes by email (050). A copy shows a tick for a moment once it is on the clipboard.
 */
export function IconTool({
  label,
  tone,
  icon,
  href,
  download,
  copyText,
  disabled,
}: {
  label: string
  tone: 'brand' | 'muted'
  icon: ReactNode
  href?: string
  download?: string
  /** What to put on the clipboard (a copy button), read when it is pressed. */
  copyText?: () => string | undefined
  disabled?: boolean
}) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const id = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(id)
  }, [copied])
  const classes = cx(
    'inline-flex size-7 shrink-0 items-center justify-center rounded-8 align-middle hover:bg-hover disabled:opacity-55',
    tone === 'brand' ? 'text-brand' : 'text-muted',
  )
  if (href)
    return (
      <a className={classes} href={href} download={download} title={label} aria-label={label}>
        {icon}
      </a>
    )
  return (
    <button
      type="button"
      title={copied ? 'Copied' : label}
      aria-label={copied ? 'Copied' : label}
      disabled={disabled}
      className={classes}
      onClick={() => {
        const value = copyText?.()
        if (value) void navigator.clipboard.writeText(value).then(() => setCopied(true))
      }}
    >
      {copied ? <CheckIcon size={15} width={2.2} /> : icon}
    </button>
  )
}

/** A 28 px square holding one 14 px icon (049 "Edit this one", "Full screen"). */
export function IconAction({
  title,
  onClick,
  children,
}: {
  title: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className="inline-flex size-7 items-center justify-center rounded-6 hover:bg-hover"
    >
      {children}
    </button>
  )
}

/** Stage 4's section titles (049 "Cover Message", "Letter"): Libre Franklin 16 px 800. */
export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="font-display text-16 leading-[normal] font-extrabold text-ink">{children}</h2>
  )
}

/** The 13 px text button beside a primary one (049 "Next — leave pending filing"). */
export function FlowTextButton({
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...rest}
      className={cx(
        'rounded-9 px-3 py-[9px] text-13 leading-[normal] font-medium whitespace-nowrap text-ink-2 hover:bg-hover disabled:opacity-55',
        className,
      )}
    >
      {children}
    </button>
  )
}

/** The primary button of a flow (049 "I submitted it — mark filed"): 13 px 600, 10px 18px. */
export function FlowPrimaryButton({
  busy,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
      className={cx(
        'rounded-9 bg-brand px-[18px] py-2.5 text-13 leading-[normal] font-semibold whitespace-nowrap text-white hover:bg-brand-dark disabled:opacity-55',
        className,
      )}
    >
      {children}
    </button>
  )
}

/** The assistant's composer (058 "Ask a follow-up…"): a 16 px round white box with a 1.5 px
 *  line, the words at 13 px, the return key at the right. */
export function AskBox({
  placeholder,
  value,
  onChange,
  onSubmit,
  disabled,
  busy,
  autoFocus,
  multiline = false,
  className,
}: {
  placeholder: string
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  disabled?: boolean
  busy?: boolean
  autoFocus?: boolean
  /** Lines grow with the words; Return sends and Shift-Return starts a new line (a note). */
  multiline?: boolean
  className?: string
}) {
  const field =
    'min-w-0 flex-1 bg-transparent text-13 leading-[normal] text-ink outline-none placeholder:text-muted-2'
  return (
    <form
      className={cx(
        'box-border flex items-center gap-3 rounded-16 border-[1.5px] border-line-2 bg-card-white px-3.5 py-[11px] focus-within:shadow-ring-focus',
        className,
      )}
      onSubmit={(event) => {
        event.preventDefault()
        if (value.trim() && !busy) onSubmit()
      }}
    >
      {multiline ? (
        <textarea
          aria-label={placeholder}
          placeholder={placeholder}
          value={value}
          rows={1}
          autoFocus={autoFocus}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              if (value.trim() && !busy) onSubmit()
            }
          }}
          className={cx(field, 'max-h-40 resize-none field-sizing-content')}
        />
      ) : (
        <input
          aria-label={placeholder}
          placeholder={placeholder}
          value={value}
          autoFocus={autoFocus}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className={field}
        />
      )}
      <button
        type="submit"
        aria-label="Send"
        aria-busy={busy || undefined}
        disabled={disabled || busy || !value.trim()}
        className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-6 enabled:hover:bg-hover"
      >
        <ReturnIcon
          size={16}
          stroke={value.trim() ? 'var(--color-ink-2)' : 'var(--color-muted-2)'}
        />
      </button>
    </form>
  )
}
