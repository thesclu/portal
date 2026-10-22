import type { ReactNode } from 'react'
import { Link } from 'react-router'

import type { Schemas } from '../../api/client'
import { ChevronLeft, DotsIcon, PaperclipIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { avatarColour, fileSize } from '../../lib/chat'

type Part = Schemas['ChatPartOut']
type File = Schemas['ChatFileOut']

/** A person's initials on their colour (018: 24 px at 9 px, 28 px at 10 px), with the presence
 *  dot at the corner when they are online or away. */
export function ChatAvatar({
  id,
  initials,
  size = 28,
  state,
}: {
  id: string
  initials: string
  size?: 24 | 28
  state?: string | null
}) {
  return (
    <span className="relative inline-flex shrink-0">
      <span
        aria-hidden
        style={{ width: size, height: size, background: avatarColour(id) }}
        className={cx(
          'inline-flex items-center justify-center rounded-pill font-semibold text-white',
          size === 24 ? 'text-9' : 'text-10',
        )}
      >
        {initials}
      </span>
      {state === 'online' || state === 'away' ? (
        <span
          aria-hidden
          className={cx(
            'absolute -right-px -bottom-px size-[9px] rounded-pill shadow-ring-avatar',
            state === 'online' ? 'bg-ok-dot' : 'bg-amber-dot-2',
          )}
        />
      ) : null}
    </span>
  )
}

/** "Channels", "Direct messages", "Online", "Away" (018, 021). */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-[18px] pt-3 pb-1 text-105 font-semibold tracking-label text-muted uppercase">
      {children}
    </div>
  )
}

/** A 30 px square button in the panel's header rows (018–020). */
export function HeaderButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick?: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="inline-flex size-[30px] shrink-0 items-center justify-center rounded-8 hover:bg-hover"
    >
      {children}
    </button>
  )
}

/** The header of an open view (019, 020): back, what it is, and the ⋯ menu when given. */
export function ViewHeader({
  onBack,
  children,
  menu,
}: {
  onBack: () => void
  children: ReactNode
  menu?: ReactNode
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-hairline pt-3 pr-3 pb-2.5 pl-2.5">
      <HeaderButton label="Back" onClick={onBack}>
        <ChevronLeft size={16} width={1.9} stroke="var(--color-ink-3)" />
      </HeaderButton>
      {children}
      {menu ? <span className="ml-auto">{menu}</span> : null}
    </div>
  )
}

export function MoreDots() {
  return <DotsIcon size={16} fill="var(--color-ink-3)" stroke="none" />
}

const LINK = 'text-ink underline decoration-line-control underline-offset-[3px] hover:text-brand'

/** A message's words as the reader may see them: a request they may not open is a neutral chip
 *  (the server sent nothing else about it), every other link is drawn as 019 draws one. */
export function MessageParts({ parts }: { parts: Part[] }) {
  return parts.map((part, index) => {
    const key = `${index}-${part.kind}`
    if (part.kind === 'mention') {
      return (
        <span key={key} className="font-medium text-brand">
          {part.text}
        </span>
      )
    }
    if (part.kind === 'request') {
      if (!part.to) {
        return (
          <span
            key={key}
            title="A request you cannot open"
            className="rounded-kbd bg-page px-1.5 py-px text-12 text-muted"
          >
            {part.text}
          </span>
        )
      }
      return (
        <Link key={key} to={part.to} title={part.title ?? undefined} className={LINK}>
          {part.text}
        </Link>
      )
    }
    if (part.kind === 'page' && part.to) {
      return (
        <Link key={key} to={part.to} className={LINK}>
          {part.text}
        </Link>
      )
    }
    if (part.kind === 'url' && part.href) {
      return (
        <a key={key} href={part.href} target="_blank" rel="noopener noreferrer" className={LINK}>
          {part.text}
        </a>
      )
    }
    return <span key={key}>{part.text}</span>
  })
}

/** A file sent with a message: its name and size, downloaded by a click. */
export function FileChip({ file, href }: { file: File; href: string }) {
  return (
    <a
      href={href}
      className="inline-flex max-w-full items-center gap-1.5 rounded-8 border border-line px-2 py-1 text-12 text-ink no-underline hover:bg-page hover:text-ink"
    >
      <PaperclipIcon size={13} width={1.9} stroke="var(--color-ink-3)" />
      <span className="truncate">{file.file_name}</span>
      <span className="shrink-0 text-muted">{fileSize(file.size)}</span>
    </a>
  )
}

/** The panel's brand button (022 "New message"). */
export function PanelButton({
  children,
  onClick,
  disabled = false,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center rounded-9 bg-brand px-3.5 py-2 text-125 font-semibold text-white hover:bg-brand-dark disabled:opacity-60"
    >
      {children}
    </button>
  )
}
