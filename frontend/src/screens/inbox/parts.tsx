/**
 * The Inbox's small pieces, each drawn from the v5 screens' inline styles (106–162): the cards,
 * the split primary button, the quiet buttons, a message's toolbar, a file chip, a note row, the
 * composer pills, the toast, a check box and the loading bars. Colours, sizes and radii are
 * tokens; nothing here is a literal.
 */
import type { ReactNode } from 'react'

import { CheckIcon, ChevronDown, PlusIcon, ReturnIcon } from '../../components/icons'
import { Avatar, cx } from '../../components/ui'
import { fileTag } from '../../lib/requestView'
import { fileSize } from '../../lib/vocab'

/** A white card of the Inbox (106): #fdfdfd, 16 px round, the float shadow. */
export const CARD = 'rounded-16 bg-card shadow-float'

/** The reading pane's frame (106). */
export function PaneCard({
  className,
  children,
  label,
  paneRef,
}: {
  className?: string
  children: ReactNode
  label?: string
  /** The pane's element: it hosts the sheets that cover only the pane (145, 150). */
  paneRef?: (node: HTMLElement | null) => void
}) {
  return (
    <section
      ref={paneRef}
      aria-label={label}
      className={cx(
        CARD,
        'relative flex min-w-0 flex-col overflow-hidden',
        className?.includes('flex-[') ? className : cx('flex-1', className),
      )}
    >
      {children}
    </section>
  )
}

/** "Attach to a request ▾" (106): a 30 px outlined split button, the label and its menu.
 *  `filled` is the scrolled header's version (113): brand fill, 32 px. */
export function SplitButton({
  label,
  onClick,
  onMenu,
  menuOpen = false,
  quiet = false,
  filled = false,
  disabled = false,
}: {
  label: string
  onClick: () => void
  onMenu: () => void
  menuOpen?: boolean
  /** "Mark not reviewed ▾" (143): ink-2 on white, a grey rule. */
  quiet?: boolean
  filled?: boolean
  disabled?: boolean
}) {
  return (
    <span
      className={cx(
        'box-border inline-flex shrink-0 overflow-hidden',
        filled
          ? 'h-8 rounded-9 border-[1.5px] border-brand bg-brand'
          : quiet
            ? 'h-[30px] rounded-9 bg-card-white'
            : 'h-[30px] rounded-8 border-[1.5px] border-brand bg-card-white',
      )}
    >
      <button
        type="button"
        disabled={disabled}
        onClick={onClick}
        className={cx(
          'inline-flex items-center text-125 font-medium whitespace-nowrap',
          filled
            ? 'px-3.5 text-white'
            : quiet
              ? 'px-3.5 text-ink-2 hover:bg-hover'
              : 'px-3 text-brand hover:bg-brand-tint',
        )}
      >
        {label}
      </button>
      <button
        type="button"
        aria-label={`More next steps`}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={onMenu}
        className={cx(
          'inline-flex items-center border-l',
          filled
            ? 'border-white/30 px-[9px]'
            : quiet
              ? 'border-line px-[9px] hover:bg-hover'
              : 'border-blue-line-45 px-2 hover:bg-brand-tint',
        )}
      >
        <ChevronDown
          size={11}
          width={2.4}
          stroke={
            filled ? 'var(--color-card-white)' : quiet ? 'var(--color-ink-2)' : 'var(--color-brand)'
          }
        />
      </button>
    </span>
  )
}

/** "Follow up", "Undo", "Try again" (116, 128, 141): a 30 px text button, 9 px round. */
export function QuietButton({
  children,
  onClick,
  disabled,
  className,
  title,
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  className?: string
  title?: string
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={cx(
        'box-border inline-flex h-[30px] shrink-0 items-center rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover',
        'disabled:cursor-default disabled:text-muted-2 disabled:hover:bg-transparent',
        className,
      )}
    >
      {children}
    </button>
  )
}

/** "Open in Flagged" (140): the 32 px brand pill of a state page. */
export function PillButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="box-border inline-flex h-8 items-center rounded-pill border-[1.5px] border-brand bg-brand px-[15px] text-125 font-medium text-white hover:bg-brand-dark"
    >
      {children}
    </button>
  )
}

/** A message's toolbar (106): 32 × 30 buttons in one 9 px rounded outline. */
export function ToolGroup({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-stretch overflow-hidden rounded-9 shadow-ring-hairline">
      {children}
    </span>
  )
}

export function Tool({
  label,
  onClick,
  first = false,
  badge,
  children,
}: {
  label: string
  onClick: () => void
  first?: boolean
  /** A count in the corner (133: notes on the message). */
  badge?: number
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className={cx(
        'relative inline-flex h-[30px] w-8 items-center justify-center hover:bg-hover',
        !first && 'border-l border-[rgba(11,31,58,0.09)]',
      )}
    >
      {children}
      {badge ? (
        <span className="absolute top-px right-px box-border inline-flex h-[13px] min-w-[13px] items-center justify-center rounded-pill bg-brand px-[3px] text-tiny font-semibold text-white">
          {badge}
        </span>
      ) : null}
    </button>
  )
}

/** One file of a message (106): the coloured tag, the name and its size, on the brand tint. */
export function FileChip({
  name,
  bytes,
  onOpen,
  href,
}: {
  name: string
  bytes: number | null | undefined
  onOpen?: () => void
  href?: string
}) {
  const body = (
    <>
      <span className="box-border inline-flex h-[26px] w-[22px] items-end justify-center rounded-xs bg-red-tint-2 pb-[3px] text-micro font-bold text-danger">
        {fileTag(name)}
      </span>
      <span className="flex flex-col text-left leading-[1.3]">
        <span>{name}</span>
        <span className="text-105 text-muted">{fileSize(bytes)}</span>
      </span>
    </>
  )
  const className =
    'inline-flex items-center gap-[9px] rounded-10 bg-brand-tint px-[11px] py-[7px] text-115 text-ink hover:text-ink'
  return onOpen ? (
    <button type="button" onClick={onOpen} className={className}>
      {body}
    </button>
  ) : (
    <a href={href} className={className}>
      {body}
    </a>
  )
}

/** "+3 more ▾" after the first two files (106). */
export function MoreFiles({ count, onClick }: { count: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="box-border inline-flex h-[42px] items-center gap-1.5 rounded-9 border border-dashed border-line-2 px-3 text-12 font-medium text-ink-2 hover:bg-page"
    >
      +{count} more
      <ChevronDown size={10} width={2.4} stroke="var(--color-muted)" />
    </button>
  )
}

/** An internal note (109): initials, the name, the time, the words. */
export function NoteRow({
  initials,
  name,
  when,
  children,
  className,
}: {
  initials: string
  name: string
  when: string
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cx(
        'flex items-start gap-[11px] border-t border-hover bg-surface-row px-[22px] py-[13px]',
        className,
      )}
    >
      <Avatar initials={initials} size={22} />
      <span className="min-w-0 flex-1 text-12 leading-[1.6] text-ink-2">
        <span className="font-semibold text-ink">{name}</span>{' '}
        <span className="text-muted">· {when}</span>
        <br />
        <span className="whitespace-pre-wrap">{children}</span>
      </span>
    </div>
  )
}

/** "Reply…" and "Write a note…" (106, 133): the 18 px round white pill with + and ↩. */
export function ComposerPill({
  placeholder,
  onClick,
}: {
  placeholder: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-18 bg-card-white px-5 py-3.5 text-left shadow-float"
    >
      <span className="inline-flex size-[22px] shrink-0 items-center justify-center">
        <PlusIcon size={15} width={1.8} stroke="var(--color-ink-2)" />
      </span>
      <span className="flex-1 text-135 leading-[1.3] text-text-placeholder">{placeholder}</span>
      <span className="inline-flex size-[22px] shrink-0 items-center justify-center">
        <ReturnIcon size={16} width={1.8} stroke="var(--color-muted-2)" />
      </span>
    </button>
  )
}

/** The toast at the foot of the pane (127, 128, 132): words, an action, a progress line. */
export function Toast({
  width,
  done = false,
  children,
  action,
  onAction,
  progress,
  bottom = 28,
  bordered = true,
}: {
  width: number
  done?: boolean
  children: ReactNode
  action?: string
  onAction?: () => void
  /** 0–1: how much of its time has run. */
  progress?: number
  bottom?: number
  /** 127–132 draw a 1 px line and the 10 px menu radius; 151 draws neither. */
  bordered?: boolean
}) {
  return (
    <div
      role="status"
      style={{ width, bottom }}
      className={cx(
        'absolute left-1/2 z-[4] -translate-x-1/2 overflow-hidden shadow-popover',
        bordered ? 'rounded-10 border border-line bg-card-white' : 'rounded-16 bg-card',
      )}
    >
      <div
        className={cx(
          'flex items-center',
          bordered ? 'px-3.5 py-[7px] text-125 text-ink' : 'gap-[11px] py-[11px] pr-3 pl-3.5',
        )}
      >
        {done ? (
          <span
            className={cx(
              'inline-flex size-[22px] shrink-0 items-center justify-center rounded-pill bg-ok-tint',
              bordered && 'mr-[11px]',
            )}
          >
            <CheckIcon size={12} width={3} stroke="var(--color-ok)" />
          </span>
        ) : null}
        <span className="min-w-0 flex-1 truncate text-125 text-ink">{children}</span>
        {action ? (
          <button
            type="button"
            onClick={onAction}
            className="rounded-chip px-2 py-1 text-12 font-medium text-brand hover:bg-brand-tint"
          >
            {action}
          </button>
        ) : null}
      </div>
      {progress !== undefined ? (
        <div className={cx(bordered ? 'bg-hover px-3.5 py-[7px]' : 'h-[3px] bg-hover')}>
          <div
            className="h-[3px] bg-brand"
            style={{ width: `${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%` }}
          />
        </div>
      ) : null}
    </div>
  )
}

/** A 15 px check box (161): brand with a white tick, or a grey outline. */
export function Box({ checked, size = 15 }: { checked: boolean; size?: number }) {
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      className={cx(
        'box-border inline-flex shrink-0 items-center justify-center rounded-4',
        checked ? 'bg-brand' : 'border-[1.5px] border-line-control',
      )}
    >
      {checked ? <CheckIcon size={10} width={3.2} stroke="var(--color-card-white)" /> : null}
    </span>
  )
}

/** A grey loading bar (115). */
export function Bar({ width, height = 11 }: { width: string; height?: number }) {
  return <span aria-hidden style={{ width, height }} className="block rounded-4 bg-fill-skeleton" />
}

/** "·" between two words of a row (106). The screens give it no size, so it is the page's
 *  16 px, which is what makes a flagged row's first line 21 px tall. */
export function Dot() {
  return <span className="text-16 leading-[normal] text-line-2">·</span>
}
