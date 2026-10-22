import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import {
  ChevronDown,
  CloseIcon,
  FilterIcon,
  SearchIcon,
  SelectIcon,
  SortIcon,
} from '../components/icons'
import { ListCard, ListHead, ListRow } from '../components/ListTable'
import { Menu } from '../components/Menu'
import { Empty, LoadFailed } from '../components/States'
import { cx } from '../components/ui'
import { formatShortInstant } from '../lib/dates'
import { fileSize } from '../lib/vocab'
import { BulkButton, CheckBox, ToolButton, ToolFace } from './docket/parts'

type Row = Schemas['ProducedRecordOut']

const PAGE = 100
const COLUMNS = '24px minmax(0,1.5fr) 140px minmax(0,1fr) minmax(0,1fr) 80px'
const ORDERS = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
] as const

/** The link that copies a file's download address (040 "Copy links"). */
function LinkIcon() {
  return (
    <svg
      aria-hidden
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--color-ink-3)"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
    </svg>
  )
}

/**
 * Records (039, 040): every produced file across the requests the reader may see, newest first.
 * Search over file names, requests and items (the words inside a file are not searched: there is
 * no extracted text, DECISIONS "Design v2" 9), an agency filter, the date order, and Select to
 * copy links to several or download them as one set. "Add to request" is not drawn: nothing
 * attaches a produced file to another request.
 */
export function Records() {
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const agency = params.get('agency') ?? ''
  const order = params.get('order') === 'oldest' ? 'oldest' : 'newest'
  const [limit, setLimit] = useState(PAGE)
  const [filtering, setFiltering] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [copied, setCopied] = useState(false)
  const search = useRef<HTMLInputElement>(null)
  const agencies = $api.useQuery('get', '/api/records/agencies', {}, { staleTime: 60_000 })
  const listing = $api.useQuery(
    'get',
    '/api/records/produced',
    {
      params: {
        query: { q: q || undefined, agency_id: agency || undefined, limit, order },
      },
    },
    { placeholderData: (previous) => previous },
  )
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement
      if (event.key !== '/' || /input|textarea|select/i.test(target.tagName)) return
      event.preventDefault()
      search.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (!copied) return
    const id = window.setTimeout(() => setCopied(false), 2_000)
    return () => window.clearTimeout(id)
  }, [copied])

  const rows = listing.data?.items ?? []
  const total = listing.data?.total ?? 0
  const ids = [...selected]
  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const agencyName = (agencies.data ?? []).find((a) => a.id === agency)?.name
  const showChecks = selecting || selected.size > 0

  const toolbar =
    selected.size > 0 ? (
      <div
        role="toolbar"
        aria-label="Actions for the selected files"
        className="flex shrink-0 items-center gap-1.5 pt-4 pr-9 pb-1"
      >
        <span className="text-13 font-semibold whitespace-nowrap text-ink">
          {selected.size} selected
        </span>
        <span className="text-125 whitespace-nowrap text-muted">
          · of {total} {total === 1 ? 'file' : 'files'}
        </span>
        <button
          type="button"
          onClick={() => setSelected(new Set())}
          className="px-1.5 text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark"
        >
          Clear
        </button>
        <span className="ml-auto flex items-center gap-0.5">
          <BulkButton
            icon={<LinkIcon />}
            onClick={() => {
              const links = ids
                .map((id) => `${location.origin}/api/records/attachments/${id}/download`)
                .join('\n')
              void navigator.clipboard.writeText(links).then(() => setCopied(true))
            }}
          >
            {copied ? 'Links copied' : 'Copy links'}
          </BulkButton>
          <span aria-hidden className="mx-2 h-[18px] w-px bg-line" />
          <a
            href={`/api/records/produced/set?ids=${ids.join(',')}`}
            className="inline-flex h-8 items-center rounded-9 bg-brand px-3.5 text-125 font-semibold whitespace-nowrap text-white hover:bg-brand-dark hover:text-white"
          >
            Download {ids.length}
          </a>
        </span>
      </div>
    ) : (
      <div className="flex shrink-0 items-center gap-2">
        <label className="box-border inline-flex h-[34px] w-[300px] items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1 text-13 text-ink-3">
          <SearchIcon size={15} width={1.9} stroke="var(--color-muted)" />
          <input
            ref={search}
            type="search"
            aria-label="Search records"
            placeholder="Search file names and text"
            defaultValue={q}
            onKeyDown={(event) => {
              if (event.key === 'Enter') set('q', event.currentTarget.value.trim())
              if (event.key === 'Escape') event.currentTarget.blur()
            }}
            onBlur={(event) => set('q', event.currentTarget.value.trim())}
            className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-ink-3 [&::-webkit-search-cancel-button]:hidden"
          />
          <span className="ml-auto rounded-kbd border border-line px-[5px] py-px text-105 text-muted-2">
            /
          </span>
        </label>
        <ToolButton
          active={filtering || Boolean(agency)}
          onClick={() => setFiltering((was) => !was)}
        >
          <FilterIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
          Filter
        </ToolButton>
        {filtering || agency ? (
          agency ? (
            <span className="box-border inline-flex h-[34px] shrink-0 items-center gap-[7px] rounded-9 px-2.5 text-125 font-medium whitespace-nowrap text-brand">
              <button
                type="button"
                aria-label="Clear Agency"
                onClick={() => set('agency', '')}
                className="inline-flex"
              >
                <CloseIcon size={12} width={2.4} stroke="var(--color-brand)" />
              </button>
              <span className="max-w-[220px] truncate">{agencyName ?? 'Agency'}</span>
            </span>
          ) : (
            <Menu
              label="Agency"
              align="left"
              width={230}
              panelClassName="max-h-[320px] overflow-y-auto"
              items={(agencies.data ?? []).map((a) => ({
                label: a.name,
                onPick: () => set('agency', a.id),
              }))}
            >
              <ToolFace>
                Agency
                <ChevronDown size={12} width={1.9} stroke="var(--color-muted)" />
              </ToolFace>
            </Menu>
          )
        ) : null}
        <Menu
          label="Order by date"
          align="left"
          width={180}
          items={ORDERS.map((o) => ({
            label: o.label,
            current: o.value === order,
            onPick: () => set('order', o.value === 'newest' ? '' : o.value),
          }))}
        >
          <ToolFace>
            <SortIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
            Date
            <ChevronDown size={12} width={1.9} stroke="var(--color-muted)" />
          </ToolFace>
        </Menu>
        <span className="ml-auto flex gap-2">
          <ToolButton
            active={selecting}
            onClick={() => {
              setSelecting((was) => !was)
              setSelected(new Set())
            }}
          >
            <SelectIcon size={15} width={1.9} stroke="var(--color-ink-3)" />
            Select
          </ToolButton>
        </span>
      </div>
    )

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Records" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        {toolbar}
        {listing.isError ? (
          <LoadFailed
            what="the records"
            error={listing.error}
            onRetry={() => void listing.refetch()}
          />
        ) : listing.isPending ? null : rows.length === 0 ? (
          q || agency ? (
            <Empty title="No records match">Try a different search or agency.</Empty>
          ) : (
            <Empty title="No records yet">
              A file becomes a record when a production is reviewed, or when it is marked as one.
            </Empty>
          )
        ) : (
          <ListCard
            aria-label="Records"
            className={cx(listing.isFetching && !listing.isPending && 'opacity-80')}
          >
            <ListHead columns={COLUMNS}>
              <span className="flex">
                {showChecks ? (
                  <CheckBox
                    checked={rows.length > 0 && selected.size === rows.length}
                    some={selected.size > 0 && selected.size < rows.length}
                    label="Select every file shown"
                    onChange={() =>
                      setSelected(
                        selected.size > 0 ? new Set() : new Set(rows.map((r) => r.attachment.id)),
                      )
                    }
                  />
                ) : null}
              </span>
              <span>File</span>
              <span>Request</span>
              <span>Agency</span>
              <span>Item</span>
              <span>Received</span>
            </ListHead>
            {rows.map((row: Row, index) => {
              const picked = selected.has(row.attachment.id)
              const size = row.attachment.page_count
                ? `${row.attachment.page_count} pp`
                : fileSize(row.attachment.size_bytes)
              return (
                <ListRow
                  key={`${row.attachment.id}-${row.request_id}-${row.item_id ?? ''}`}
                  columns={COLUMNS}
                  index={index}
                  zebra={false}
                  selected={picked}
                  tone="soft"
                  pad="py-2.5"
                  onClick={showChecks ? () => toggle(row.attachment.id) : undefined}
                >
                  <span className="flex">
                    {showChecks ? (
                      <CheckBox
                        checked={picked}
                        label={`Select ${row.attachment.filename}`}
                        onChange={() => toggle(row.attachment.id)}
                      />
                    ) : null}
                  </span>
                  <span className="min-w-0 truncate text-ink">
                    <a
                      href={`/api/records/attachments/${row.attachment.id}/download`}
                      onClick={(event) => event.stopPropagation()}
                      className="text-ink hover:text-brand"
                    >
                      {row.attachment.filename}
                    </a>
                    {size ? <span className="text-muted"> · {size}</span> : null}
                  </span>
                  <Link
                    to={`/requests/${row.request_id}`}
                    title={row.subject}
                    onClick={(event) => event.stopPropagation()}
                    className="text-ink-2 hover:text-ink"
                  >
                    {row.tracking_number}
                  </Link>
                  <span className="min-w-0 truncate">{row.agency_name}</span>
                  <span className="min-w-0 truncate text-ink-2">
                    {row.item_number
                      ? `${row.item_number} · ${row.item_description}`
                      : 'Whole request'}
                  </span>
                  <span className="text-muted">
                    {formatShortInstant(row.attachment.created_at, me.timezone)}
                  </span>
                </ListRow>
              )
            })}
            {rows.length < total ? (
              <button
                type="button"
                onClick={() => setLimit((n) => n + PAGE)}
                className="block w-full py-3 text-center text-125 font-medium text-brand hover:text-brand-dark"
              >
                Show more ({rows.length} of {total})
              </button>
            ) : null}
          </ListCard>
        )}
      </div>
    </div>
  )
}
