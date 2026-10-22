/**
 * The Inbox's list column (106, 116, 133, 160): the search line, the count with the sort and
 * Refresh, and the list card. Each kind of row is drawn as its screen draws it: a flagged row
 * says its reason (106), a New row the sender and the agency (133), a sent row whom it went to
 * (116), a draft row "Draft" in red (118), an All row where it stands (160), a search result the
 * words around the hit (153). A row with a check box is a selection (161).
 */
import { useMe } from '../../app/session'
import { ChevronDown, RefreshIcon, SearchIcon, SlidersIcon } from '../../components/icons'
import { MenuPanel } from '../../components/Menu'
import { cx } from '../../components/ui'
import { formatInstant, formatListTime } from '../../lib/dates'
import { useNameOf } from '../../lib/vocab'
import {
  SORTS,
  flagWords,
  senderLine,
  senderOf,
  sentLine,
  snippetOf,
  sortLabel,
  statusLine,
  subjectOf,
  type Message,
  type Row,
  type Sort,
} from './model'
import { Bar, Box, CARD, Dot } from './parts'

/* ── The search line and the count row ─────────────────────────────────── */

/** The underlined search line (106): the glass, the words, Filters at the end. */
export function SearchLine({
  placeholder,
  value,
  onSearch,
  onFilters,
  filtersOn = false,
  inputRef,
}: {
  placeholder: string
  value: string
  onSearch: (q: string) => void
  onFilters: () => void
  filtersOn?: boolean
  inputRef?: React.Ref<HTMLInputElement>
}) {
  return (
    <div className="relative flex items-center gap-2">
      <label className="box-border flex h-[34px] min-w-0 flex-1 items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1">
        <SearchIcon size={15} width={2} stroke="var(--color-muted)" />
        <input
          ref={inputRef}
          key={value}
          type="search"
          aria-label={placeholder}
          placeholder={placeholder}
          defaultValue={value}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onSearch(event.currentTarget.value.trim())
            if (event.key === 'Escape') event.currentTarget.blur()
          }}
          className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-muted-2 [&::-webkit-search-cancel-button]:hidden"
        />
        <FiltersButton on={filtersOn} onClick={onFilters} />
      </label>
    </div>
  )
}

export function FiltersButton({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      title="Filters"
      aria-label="Filters"
      aria-pressed={on}
      onClick={(event) => {
        event.preventDefault()
        onClick()
      }}
      className={cx(
        '-mr-[7px] inline-flex size-7 shrink-0 items-center justify-center rounded-chip hover:bg-hover',
        on && 'bg-hover',
      )}
    >
      <SlidersIcon size={15} width={1.9} stroke="var(--color-ink-2)" />
    </button>
  )
}

/** "Newest first ▾" and its menu (156). */
export function SortControl({
  sort,
  open,
  onToggle,
  onPick,
  searching = false,
}: {
  sort: Sort
  open: boolean
  onToggle: () => void
  onPick: (sort: Sort) => void
  /** The search layout's version (153): 13 px in, ink-2; open, it turns ink 500 (156). */
  searching?: boolean
}) {
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={onToggle}
        className={cx(
          'inline-flex items-center gap-1.5 text-12 whitespace-nowrap',
          searching ? 'h-6 pr-1' : '',
          searching && !open ? 'pl-[13px] text-ink-2' : '',
          open ? 'pl-0.5 font-medium text-ink' : 'text-ink-2',
        )}
      >
        {sortLabel(sort)}
        <ChevronDown size={9} width={2.6} stroke="var(--color-muted)" />
      </button>
      {open ? (
        <MenuPanel
          aria-label="Sort"
          width={190}
          className="absolute top-7 left-0 z-[4]"
          items={SORTS.map((option) => ({
            label: option.label,
            separator: 'separator' in option ? option.separator : false,
            current: option.value === sort,
            onPick: () => onPick(option.value),
          }))}
        />
      ) : null}
    </span>
  )
}

export function RefreshButton({ onClick, busy }: { onClick: () => void; busy?: boolean }) {
  return (
    <button
      type="button"
      title="Refresh"
      aria-label="Refresh"
      aria-busy={busy || undefined}
      onClick={onClick}
      className="inline-flex size-6 shrink-0 items-center justify-center rounded-6 hover:bg-hover"
    >
      <RefreshIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
    </button>
  )
}

/** "4 flagged · Newest first ▾ · ⟳" (106); All mail sits it 13 px in (160); a selection shows
 *  "☑ 2 of 3" in place of the count (161). */
export function CountRow({
  words,
  inset = false,
  selection,
  sort,
}: {
  words: string
  inset?: boolean
  selection?: { chosen: number; total: number; onClear: () => void }
  sort: React.ReactNode
}) {
  return (
    <div
      className={cx(
        'flex h-7 items-center gap-2.5',
        inset || selection ? '-mt-0.5 -mb-1 pr-2 pl-[13px]' : 'justify-between pl-1',
      )}
    >
      {selection ? (
        <button
          type="button"
          onClick={selection.onClear}
          className="ml-0.5 inline-flex items-center gap-2 text-12 whitespace-nowrap text-ink-2"
        >
          <Box checked />
          {selection.chosen} of {selection.total}
        </button>
      ) : (
        <span className="text-12 whitespace-nowrap text-muted">{words}</span>
      )}
      {sort}
    </div>
  )
}

/* ── The rows ───────────────────────────────────────────────────────────── */

const RULE = 'border-line-list'

/** A flagged message (106): the sender, its reason with the orange dot, the time; the subject. */
export function FlaggedRow({
  row,
  first,
  active,
  onOpen,
}: {
  row: Row
  first: boolean
  active: boolean
  onOpen: () => void
}) {
  const me = useMe()
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={active || undefined}
      className={cx(
        'block w-full px-4 py-[13px] text-left',
        !first && `border-t ${RULE}`,
        active ? 'bg-page' : 'hover:bg-page',
      )}
    >
      <span className="flex items-center gap-2">
        <span className="min-w-0 flex-[0_1_auto] truncate text-115 text-muted">
          {senderOf(row)}
        </span>
        <Dot />
        <span className="inline-flex items-center gap-[5px] text-115 whitespace-nowrap text-warn">
          <span className="size-[5px] shrink-0 rounded-pill bg-attention" />
          {flagWords(row.source.flag_reason).row}
        </span>
        <span
          title={formatInstant(row.source.captured_at, me.timezone)}
          className="ml-auto text-11 whitespace-nowrap text-muted"
        >
          {formatListTime(row.source.captured_at, me.timezone)}
        </span>
      </span>
      <span className="mt-1 block min-w-0 truncate text-13 leading-[1.35] font-semibold text-ink">
        {subjectOf(row)}
      </span>
    </button>
  )
}

/** A New message (133), or one of All mail (160) with its unread dot and where it stands, or a
 *  search result (153) with the words around the hit. */
export function MessageRow({
  row,
  active,
  onOpen,
  variant,
  query,
  selecting,
  chosen,
  onToggle,
}: {
  row: Row
  active: boolean
  onOpen: () => void
  variant: 'new' | 'all' | 'search'
  query?: string
  selecting?: boolean
  chosen?: boolean
  onToggle?: (range: boolean) => void
}) {
  const me = useMe()
  const unreviewed = !row.source.reviewed_at
  const landed = row.events[0]
  const status = statusLine(row)
  const snippet = variant === 'search' && query ? snippetOf(landed?.display_body, query) : null
  const roomy = variant === 'new' && !selecting
  return (
    <div
      className={cx(
        'flex items-start gap-2.5 border-b px-4',
        RULE,
        roomy ? 'py-[15px]' : 'py-3.5',
        active || chosen ? 'bg-brand-tint' : 'hover:bg-page',
      )}
    >
      {selecting ? (
        <button
          type="button"
          aria-label={chosen ? 'Deselect' : 'Select'}
          aria-pressed={chosen}
          onClick={(event) => onToggle?.(event.shiftKey)}
          className="mt-0.5 inline-flex"
        >
          <Box checked={Boolean(chosen)} />
        </button>
      ) : null}
      <button
        type="button"
        onClick={(event) => {
          if ((event.shiftKey || event.metaKey || event.ctrlKey) && onToggle) {
            onToggle(event.shiftKey)
            return
          }
          onOpen()
        }}
        aria-current={active || undefined}
        className="block min-w-0 flex-1 text-left"
      >
        <span className={cx('flex gap-2', roomy ? 'items-baseline' : 'items-center')}>
          <span className="min-w-0 flex-1 truncate text-115 text-muted">{senderLine(row)}</span>
          {variant !== 'new' && variant !== 'search' && unreviewed ? (
            <span className="size-[7px] shrink-0 rounded-pill bg-brand" />
          ) : null}
          {selecting && unreviewed ? (
            <span className="size-[7px] shrink-0 rounded-pill bg-brand" />
          ) : null}
          <span
            title={formatInstant(row.source.captured_at, me.timezone)}
            className="text-11 whitespace-nowrap text-muted"
          >
            {formatListTime(row.source.captured_at, me.timezone)}
          </span>
        </span>
        <span
          className={cx(
            'mt-[3px] block min-w-0 truncate text-13 leading-[1.4] text-ink',
            unreviewed || variant === 'search' ? 'font-semibold' : 'font-medium',
          )}
        >
          {subjectOf(row)}
        </span>
        {snippet ? (
          <span className="mt-1 block text-115 leading-[1.45] text-muted">
            {snippet.before}
            <mark className="rounded-xs bg-amber-highlight px-px text-inherit">{snippet.hit}</mark>
            {snippet.after}
          </span>
        ) : null}
        {variant === 'all' && status.text ? (
          <span className={cx('mt-1 block text-11', status.flagged ? 'text-warn' : 'text-muted-2')}>
            {status.text}
          </span>
        ) : null}
        {variant === 'search' && landed ? (
          <span className="mt-[5px] block text-11 text-muted-2">{landed.tracking_number}</span>
        ) : null}
      </button>
    </div>
  )
}

/** A message the portal sent or is writing (116, 118), or one in All mail (160). */
export function SentRow({
  message,
  active,
  onOpen,
  variant,
  query,
}: {
  message: Message
  active: boolean
  onOpen: () => void
  variant: 'sent' | 'drafts' | 'all' | 'search'
  query?: string
}) {
  const me = useMe()
  const nameOf = useNameOf()
  const draft = message.status === 'draft' || message.status === 'queued'
  const at = message.sent_at ?? message.updated_at
  const snippet = variant === 'search' && query ? snippetOf(message.body_text, query) : null
  const failed = message.status === 'failed'
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={active || undefined}
      className={cx(
        'flex w-full items-start gap-2.5 border-b px-4 text-left',
        RULE,
        variant === 'sent' || variant === 'drafts' ? 'py-[15px]' : 'py-3.5',
        active ? 'bg-brand-tint' : 'hover:bg-page',
      )}
    >
      <span className="block min-w-0 flex-1">
        <span
          className={cx(
            'flex gap-2',
            variant === 'sent' || variant === 'drafts' ? 'items-baseline' : 'items-center',
          )}
        >
          <span className="min-w-0 flex-1 truncate text-115 text-muted">
            {draft && variant === 'drafts' ? (
              <>
                <span className="font-medium text-danger">Draft</span> ·{' '}
              </>
            ) : null}
            {variant === 'all' || variant === 'search'
              ? (nameOf(message.created_by_user_id) ?? 'Your team')
              : sentLine(message)}
          </span>
          <span
            title={formatInstant(at, me.timezone)}
            className="text-11 whitespace-nowrap text-muted"
          >
            {formatListTime(at, me.timezone)}
          </span>
        </span>
        <span
          className={cx(
            'mt-[3px] block min-w-0 truncate text-13 leading-[1.4] text-ink',
            variant === 'search' ? 'font-semibold' : 'font-medium',
          )}
        >
          {message.subject || '(no subject)'}
        </span>
        {snippet ? (
          <span className="mt-1 block text-115 leading-[1.45] text-muted">
            {snippet.before}
            <mark className="rounded-xs bg-amber-highlight px-px text-inherit">{snippet.hit}</mark>
            {snippet.after}
          </span>
        ) : null}
        {variant === 'all' || variant === 'search' ? (
          <span
            className={cx(
              variant === 'search' ? 'mt-[5px]' : 'mt-1',
              'block text-11',
              failed ? 'text-warn' : 'text-muted-2',
            )}
          >
            {failed ? 'Not sent' : draft ? 'Draft' : 'Sent'} · {message.tracking_number}
          </span>
        ) : null}
      </span>
    </button>
  )
}

/* ── The card's own states ─────────────────────────────────────────────── */

export function ListCard({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cx(CARD, 'flex flex-1 flex-col overflow-hidden', className)}>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
    </div>
  )
}

/** "Nothing flagged" (114), "Nothing sent yet" (117), "No drafts" (119). */
export function ListEmpty({ title, words }: { title: string; words?: string }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-1.5 px-5 py-10 text-center">
      <div className="text-135 font-semibold text-ink">{title}</div>
      {words ? <div className="mt-1.5 text-12 leading-[1.6] text-muted">{words}</div> : null}
    </div>
  )
}

/** "Couldn't load New" (141). */
export function ListFailed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-1 flex-col items-center justify-center gap-2.5 p-[30px] text-center"
    >
      <div className="text-135 font-semibold text-ink">Couldn’t load {what}</div>
      <div className="text-12 leading-[1.6] text-muted">The mail server didn’t answer.</div>
      <button
        type="button"
        onClick={onRetry}
        className="mt-1 inline-flex h-[30px] items-center rounded-9 px-[15px] text-125 font-medium text-ink-2 hover:bg-hover"
      >
        Try again
      </button>
    </div>
  )
}

/** The list's loading bars: four lines a row in Flagged (115), two in New (138). */
export function ListLoading({ flagged }: { flagged: boolean }) {
  const rows = flagged ? [1, 0.82, 0.64, 0.46] : [1, 0.8, 0.6]
  return rows.map((opacity) => (
    <div
      key={opacity}
      style={{ opacity }}
      className={cx(
        'flex flex-col gap-[7px] border-b',
        flagged ? 'border-line-row-2 px-[15px] py-3.5' : 'border-line-list px-4 py-[15px]',
      )}
    >
      {(flagged ? ['72%', '90%', '62%', '40%'] : ['70%', '88%']).map((width) => (
        <Bar key={width} width={width} />
      ))}
    </div>
  ))
}
