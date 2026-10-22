import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { NEW_REQUEST_BUILT } from '../app/nav'
import { useMe } from '../app/session'
import { ActionDialog, type ActionSpec } from '../components/ActionDialog'
import {
  AssignIcon,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CloseIcon,
  ColumnsIcon,
  ExportIcon,
  FilterIcon,
  MoreIcon,
  NoteIcon,
  SearchIcon,
  SelectIcon,
  SortIcon,
} from '../components/icons'
import { Menu, MenuPanel, type MenuItem } from '../components/Menu'
import { LoadFailed } from '../components/States'
import { Avatar, cx } from '../components/ui'
import { STATS, statFilter } from '../lib/docket'
import { initialsOf } from '../lib/presence'
import { isGrantScoped } from '../lib/roles'
import { statusText } from '../lib/status'
import { civilDate, useNow } from '../lib/tasks'
import { daysFigure, useDefinitions, useNameOf, usePeople } from '../lib/vocab'
import { markDuplicate, recordEvent, withdraw, type Context } from './request/actions'
import {
  AssignDialog,
  ChangeStatusDialog,
  FollowUpDialog,
  noteSpec,
  useActionContext,
  useRefreshDocket,
} from './docket/BulkDialogs'
import { DetailPanel } from './docket/DetailPanel'
import { exportRegistry, type ExportChoice } from './docket/ExportDialog'
import { ScopedDocket } from './docket/ScopedDocket'
import { BulkButton, CheckBox, StatusGlyph, ToolButton, ToolFace } from './docket/parts'
import { CompactStrip, StatStrip } from './docket/StatStrip'
import { useDocketStats, type Stat } from './docket/stats'

type Row = Schemas['DocketRowOut']

/** Rows read at a time; the table scrolls inside its card and "Show more" reads the next. */
const PAGE = 100

const SORTS: Array<{ value: string; label: string }> = [
  { value: 'deadline', label: 'Due date' },
  { value: 'newest', label: 'Newest' },
  { value: 'status_changed', label: 'Latest change' },
  { value: 'agency', label: 'Agency' },
]

type Column = 'agency' | 'subject' | 'status' | 'due' | 'days' | 'assignee'
const COLUMNS: Array<{ key: Column; label: string; width: string }> = [
  { key: 'agency', label: 'Agency', width: '190px' },
  { key: 'subject', label: 'Subject', width: '1fr' },
  { key: 'status', label: 'Status', width: '200px' },
  { key: 'due', label: 'Due', width: '80px' },
  { key: 'days', label: 'Days', width: '56px' },
  { key: 'assignee', label: 'Assigned to', width: '30px' },
]
const COLUMNS_KEY = 'sclu.docket.columns'

function readHidden(): Column[] {
  try {
    const stored = JSON.parse(localStorage.getItem(COLUMNS_KEY) ?? '[]')
    return Array.isArray(stored) ? stored : []
  } catch {
    return []
  }
}

const MONTH_DAY = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})
const shortDate = (civil: string | null) =>
  civil ? MONTH_DAY.format(new Date(`${civil.slice(0, 10)}T00:00:00Z`)) : '—'
const isOverdue = (row: Row) => (row.days_to_next_deadline ?? 0) < 0

/** The address holds every filter and view, so a docket state can be bookmarked and sent. */
function useDocketParams() {
  const [params, setParams] = useSearchParams()
  const get = (key: string) => params.get(key) ?? ''
  const set = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setParams(next, { replace: true })
  }
  return { get, set }
}

/**
 * Docket (design 025–033): every request you may see, soonest deadline first. The stat strip
 * (every box filters the list to what it counted), the toolbar (search, Filter, Sort, Columns,
 * Select), the table with the days cell as the only colour, rows selected with the bulk bar, the
 * row menu, and
 * `?view=split` with a request open beside the list (`&open=ID`, and `&expanded=1` to fill the
 * page).
 *
 * P20h: an intern or a limited-scope attorney gets the table alone (239, 241; `ScopedDocket`).
 */
export function Docket() {
  const me = useMe()
  return isGrantScoped(me.records_role) ? <ScopedDocket /> : <TeamDocket />
}

function TeamDocket() {
  const me = useMe()
  const navigate = useNavigate()
  const url = useDocketParams()
  const today = civilDate(useNow(), me.timezone)
  const stats = useDocketStats()
  const nameOf = useNameOf()
  const refresh = useRefreshDocket()
  const loadContext = useActionContext()

  const stat = (
    (STATS as readonly string[]).includes(url.get('stat')) ? url.get('stat') : null
  ) as Stat | null
  const split = url.get('view') === 'split'
  const openId = split ? url.get('open') || null : null
  const expanded = Boolean(openId) && url.get('expanded') === '1'

  const [limit, setLimit] = useState(PAGE)
  const [filtering, setFiltering] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [hidden, setHidden] = useState<Column[]>(readHidden)
  const [menu, setMenu] = useState<{ row: Row; top: number } | null>(null)
  const [bulk, setBulk] = useState<'assign' | 'status' | 'followup' | null>(null)
  const [bulkRows, setBulkRows] = useState<Row[]>([])
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [specError, setSpecError] = useState<string | null>(null)

  // Each stat box filters the list to what it counted (the summary's own definitions); a status
  // picked in the filter chips stands in for All open's "open only".
  const boxFilter = statFilter(stat, stats?.summary)
  const query = {
    q: url.get('q') || undefined,
    status: boxFilter?.status ?? (url.get('status') || undefined),
    agency_id: url.get('agency') || undefined,
    assignee: boxFilter?.assignee ?? (url.get('assignee') || undefined),
    due_before: boxFilter?.due_before,
    due_after: boxFilter?.due_after,
    open: boxFilter?.open && !url.get('status') ? true : undefined,
    sort: url.get('sort') || 'deadline',
  }
  const docket = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { ...query, limit } } },
    { placeholderData: (previous) => previous, enabled: boxFilter !== null },
  )
  const rows = useMemo(() => docket.data?.items ?? [], [docket.data])
  const total = docket.data?.total ?? 0
  const anyFilter = Boolean(query.q || url.get('status') || query.agency_id || url.get('assignee'))
  const showFilters =
    filtering || Boolean(url.get('status') || query.agency_id || url.get('assignee'))

  const chosen = rows.filter((row) => selected.has(row.id))
  const openRow = rows.find((row) => row.id === openId)

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function startBulk(kind: 'assign' | 'status' | 'followup', target: Row[]) {
    setMenu(null)
    setBulkRows(target)
    setBulk(kind)
  }

  async function startAction(row: Row, make: (ctx: Context) => ActionSpec) {
    setMenu(null)
    setSpecError(null)
    try {
      setSpec(make(await loadContext(row.id)))
    } catch (caught) {
      setSpecError(caught instanceof Error ? caught.message : 'Couldn’t open that.')
    }
  }

  /** The selected rows, exactly, through the registry export (`ids`), as the server writes it. */
  async function exportSelection(rows: Row[], choice: ExportChoice) {
    const { data, error } = await api.GET('/api/records/requests/export', {
      params: {
        query: {
          ids: rows.map((row) => row.id).join(','),
          format: choice.format,
          since: choice.since,
          until: choice.until,
        },
      },
      parseAs: 'blob',
    })
    if (error || !data) throw new Error('Couldn’t export those requests.')
    const link = document.createElement('a')
    link.href = URL.createObjectURL(data)
    link.download = `docket-${today}.${choice.format}`
    link.click()
    URL.revokeObjectURL(link.href)
  }

  const rowMenu = (row: Row): MenuItem[] => [
    { label: 'Open', onPick: () => void navigate(`/requests/${row.id}`) },
    {
      label: 'Open in new tab',
      onPick: () => window.open(`/requests/${row.id}`, '_blank', 'noopener'),
    },
    { label: 'Record event…', separator: true, onPick: () => void startAction(row, recordEvent) },
    { label: 'Add note…', onPick: () => setSpec(noteSpec([row], refresh)) },
    { label: 'Assign to…', onPick: () => startBulk('assign', [row]) },
    { label: 'Draft follow-up…', onPick: () => startBulk('followup', [row]) },
    // 029 draws "Copy tracking number" here; it is left out: nothing offers to copy the tracking
    // number (ARCHITECTURE §7.1, §4.4).
    {
      label: 'Mark as duplicate…',
      separator: true,
      onPick: () => void startAction(row, markDuplicate),
    },
    {
      label: 'Withdraw…',
      danger: true,
      separator: true,
      onPick: () => void startAction(row, withdraw),
    },
  ]

  // "/" puts the cursor in the search (ARCHITECTURE §2.5).
  const search = useRef<HTMLInputElement>(null)
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement
      if (event.key !== '/' || /input|textarea|select/i.test(target.tagName)) return
      if (target.isContentEditable) return
      event.preventDefault()
      search.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const visible = COLUMNS.filter((c) => !hidden.includes(c.key))
  const setColumns = (next: Column[]) => {
    setHidden(next)
    try {
      localStorage.setItem(COLUMNS_KEY, JSON.stringify(next))
    } catch {
      /* the choice lasts the session */
    }
  }

  const closeOpen = () => url.set({ open: null, expanded: null })

  const toolbar =
    selected.size > 0 ? (
      <div
        role="toolbar"
        aria-label="Actions for the selected requests"
        className="flex shrink-0 items-center gap-1.5 pt-4 pr-9 pb-1"
      >
        <span className="text-13 font-semibold whitespace-nowrap text-ink">
          {selected.size} selected
        </span>
        {chosen.length > 0 && chosen.every(isOverdue) ? (
          <span className="text-125 whitespace-nowrap text-muted">· all overdue</span>
        ) : null}
        <button
          type="button"
          onClick={() => setSelected(new Set())}
          className="px-1.5 text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark"
        >
          Clear
        </button>
        <span className="ml-auto flex items-center gap-0.5">
          <BulkButton
            icon={<AssignIcon size={14} width={1.9} stroke="var(--color-ink-3)" />}
            onClick={() => startBulk('assign', chosen)}
          >
            Assign to…
          </BulkButton>
          {/* Not drawn on 028; kept so a status change on several stays reachable (open question). */}
          <BulkButton onClick={() => startBulk('status', chosen)}>Change status…</BulkButton>
          <BulkButton
            icon={<NoteIcon size={14} width={1.9} stroke="var(--color-ink-3)" />}
            onClick={() => setSpec(noteSpec(chosen, refresh))}
          >
            Add note
          </BulkButton>
          <BulkButton
            icon={<ExportIcon size={14} width={1.9} stroke="var(--color-ink-3)" />}
            onClick={() => setSpec(exportRegistry(chosen, exportSelection))}
          >
            Export
          </BulkButton>
          <span aria-hidden className="mx-2 h-[18px] w-px bg-line" />
          <button
            type="button"
            onClick={() => startBulk('followup', chosen)}
            className="inline-flex h-8 items-center rounded-9 bg-brand px-3.5 text-125 font-semibold whitespace-nowrap text-white hover:bg-brand-dark"
          >
            Draft {chosen.length} follow-up{chosen.length === 1 ? '' : 's'}
          </button>
        </span>
      </div>
    ) : (
      <div className="flex shrink-0 items-center gap-2 pt-4 pr-9 pb-1">
        <label className="box-border inline-flex h-[34px] w-[300px] items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1 text-13 text-ink-3">
          <SearchIcon size={15} width={1.9} stroke="var(--color-muted)" />
          <input
            ref={search}
            type="search"
            aria-label="Search the docket"
            placeholder="Search Docket"
            defaultValue={url.get('q')}
            onKeyDown={(event) => {
              if (event.key === 'Enter') url.set({ q: event.currentTarget.value.trim() })
              if (event.key === 'Escape') event.currentTarget.blur()
            }}
            onBlur={(event) => url.set({ q: event.currentTarget.value.trim() })}
            className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-ink-3 [&::-webkit-search-cancel-button]:hidden"
          />
          <span className="ml-auto rounded-kbd border border-line px-[5px] py-px text-105 text-muted-2">
            /
          </span>
        </label>
        <ToolButton active={showFilters} onClick={() => setFiltering((was) => !was)}>
          <FilterIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
          Filter
        </ToolButton>
        {showFilters ? <FilterChips url={url} /> : null}
        <Menu
          label="Sort"
          align="left"
          width={190}
          items={SORTS.map((sort) => ({
            label: sort.label,
            onPick: () => url.set({ sort: sort.value === 'deadline' ? null : sort.value }),
          }))}
        >
          <ToolFace>
            <SortIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
            {SORTS.find((s) => s.value === query.sort)?.label ?? 'Due date'}
            <ChevronDown size={12} width={1.9} stroke="var(--color-muted)" />
          </ToolFace>
        </Menu>
        {split ? null : (
          <span className="ml-auto flex gap-2">
            <ColumnsMenu hidden={hidden} onChange={setColumns} />
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
        )}
      </div>
    )

  const table = (
    <DocketTable
      rows={rows}
      total={total}
      pending={docket.isPending}
      error={docket.isError ? docket.error : null}
      onRetry={() => void docket.refetch()}
      fetching={docket.isFetching}
      compact={split}
      columns={split ? null : visible}
      selecting={selecting || selected.size > 0}
      selected={selected}
      onToggle={toggle}
      onToggleAll={() =>
        setSelected(selected.size > 0 ? new Set() : new Set(rows.map((row) => row.id)))
      }
      openId={openId}
      onOpen={(row) =>
        split ? url.set({ open: row.id, expanded: null }) : void navigate(`/requests/${row.id}`)
      }
      onMenu={(row, top) => setMenu({ row, top })}
      menu={
        menu ? (
          <div
            className="absolute right-[26px] z-[5]"
            style={{ top: menu.top }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <MenuPanel
              aria-label={`Actions for ${menu.row.tracking_number}`}
              items={rowMenu(menu.row)}
              onPicked={() => setMenu(null)}
            />
          </div>
        ) : null
      }
      onCloseMenu={() => setMenu(null)}
      nameOf={nameOf}
      empty={
        anyFilter || stat ? (
          <EmptyState title="No requests match" line="Nothing fits these filters.">
            <button
              type="button"
              onClick={() =>
                url.set({ q: null, status: null, agency: null, assignee: null, stat: null })
              }
              className="inline-flex h-8 items-center rounded-9 bg-brand px-[13px] text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark"
            >
              Clear filters
            </button>
          </EmptyState>
        ) : (
          <EmptyState title="Nothing on the docket" line="Requests appear here once reserved.">
            {NEW_REQUEST_BUILT ? (
              <Link
                to="/new"
                className="inline-flex h-8 items-center rounded-9 bg-brand px-[13px] text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark hover:text-white"
              >
                ＋ New request
              </Link>
            ) : null}
          </EmptyState>
        )
      }
      more={rows.length < total ? () => setLimit((n) => n + PAGE) : null}
    />
  )

  const dialogs = (
    <>
      <AssignDialog rows={bulk === 'assign' ? bulkRows : null} onClose={() => setBulk(null)} />
      <ChangeStatusDialog
        rows={bulk === 'status' ? bulkRows : null}
        onClose={() => setBulk(null)}
      />
      <FollowUpDialog rows={bulk === 'followup' ? bulkRows : null} onClose={() => setBulk(null)} />
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          void refresh()
        }}
      />
      {specError ? (
        <p
          role="alert"
          className="fixed right-6 bottom-6 z-30 rounded-12 bg-danger-tint px-4 py-3 text-12 text-danger-2"
        >
          {specError}
        </p>
      ) : null}
    </>
  )

  if (expanded && openId) {
    const index = rows.findIndex((row) => row.id === openId)
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 leading-[normal]">
        <div className="-mt-1 flex shrink-0 items-center gap-2 px-2 text-125 text-muted">
          <button
            type="button"
            onClick={() => url.set({ view: null, open: null, expanded: null })}
            className="font-medium text-brand hover:text-brand-dark"
          >
            ← Docket
          </button>
          {stats ? (
            <>
              <span>·</span>
              <span>{stats.overdue} overdue</span>
            </>
          ) : null}
          {index >= 0 ? (
            <span className="inline-flex gap-1">
              <PagerButton
                label="Previous"
                disabled={index <= 0}
                onClick={() => url.set({ open: rows[index - 1].id })}
              >
                <ChevronLeft size={15} stroke="var(--color-ink-3)" />
              </PagerButton>
              <span className="self-center px-1.5 tabular-nums">
                {index + 1} of {total}
              </span>
              <PagerButton
                label="Next"
                disabled={index >= rows.length - 1}
                onClick={() => url.set({ open: rows[index + 1].id })}
              >
                <ChevronRight size={15} stroke="var(--color-ink-3)" />
              </PagerButton>
            </span>
          ) : null}
        </div>
        <DetailPanel
          id={openId}
          row={openRow}
          expanded
          onExpand={(full) => url.set({ expanded: full ? '1' : null })}
          onClose={closeOpen}
          moreItems={openRow ? rowMenu(openRow) : []}
        />
        {dialogs}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2.5 leading-[normal]">
      <PageHeader
        title="Docket"
        actions={
          NEW_REQUEST_BUILT ? (
            <Link
              to="/new"
              className="box-border inline-flex h-8 shrink-0 items-center gap-1.5 rounded-pill bg-brand px-[13px] text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark hover:text-white"
            >
              ＋ New request
            </Link>
          ) : null
        }
      />
      <div className={cx('flex min-h-0 flex-1 gap-2.5', split ? 'flex-row gap-3' : 'flex-col')}>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2.5">
          {split ? (
            <CompactStrip stats={stats} stat={stat} onStat={(next) => url.set({ stat: next })} />
          ) : (
            <StatStrip
              stats={stats}
              today={today}
              stat={stat}
              onStat={(next) => url.set({ stat: next })}
            />
          )}
          {toolbar}
          {table}
        </div>
        {openId ? (
          <DetailPanel
            id={openId}
            row={openRow}
            expanded={false}
            onExpand={(full) => url.set({ expanded: full ? '1' : null })}
            onClose={closeOpen}
            moreItems={openRow ? rowMenu(openRow) : []}
          />
        ) : null}
      </div>
      {dialogs}
    </div>
  )
}

function PagerButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string
  disabled: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex size-[30px] items-center justify-center rounded-8 border-[1.5px] border-line bg-card-white disabled:opacity-55"
    >
      {children}
    </button>
  )
}

/** The three filters 026 draws once Filter is pressed; a chosen one turns brand with its own ×
 *  (027). */
function FilterChips({ url }: { url: ReturnType<typeof useDocketParams> }) {
  const definitions = useDefinitions()
  const people = usePeople()
  const agencies = $api.useQuery('get', '/api/records/agencies', {}, { staleTime: 60_000 })
  const staff = (people.data ?? []).filter((p) => p.is_active && p.records_role !== 'requester')
  const chips: Array<{ key: string; label: string; items: MenuItem[]; chosen: string }> = [
    {
      key: 'status',
      label: 'Status',
      chosen: url.get('status') ? statusText(url.get('status')) : '',
      items: (definitions.data?.statuses ?? []).map((s) => ({
        label: statusText(s.code),
        onPick: () => url.set({ status: s.code }),
      })),
    },
    {
      key: 'agency',
      label: 'Agency',
      chosen: (agencies.data ?? []).find((a) => a.id === url.get('agency'))?.name ?? '',
      items: (agencies.data ?? []).map((a) => ({
        label: a.name,
        onPick: () => url.set({ agency: a.id }),
      })),
    },
    {
      key: 'assignee',
      label: 'Assigned to',
      chosen:
        url.get('assignee') === 'me'
          ? 'Me'
          : (staff.find((p) => p.id === url.get('assignee'))?.name ?? ''),
      items: [
        { label: 'Me', onPick: () => url.set({ assignee: 'me', stat: null }) },
        ...staff.map((p) => ({
          label: p.name,
          onPick: () => url.set({ assignee: p.id, stat: null }),
        })),
      ],
    },
  ]
  return (
    <>
      {chips.map((chip) =>
        url.get(chip.key) ? (
          <span
            key={chip.key}
            className="box-border inline-flex h-[34px] shrink-0 items-center gap-[7px] rounded-9 px-2.5 text-125 font-medium whitespace-nowrap text-brand"
          >
            <button
              type="button"
              aria-label={`Clear ${chip.label}`}
              onClick={() => url.set({ [chip.key]: null })}
              className="inline-flex"
            >
              <CloseIcon size={12} width={2.4} stroke="var(--color-brand)" />
            </button>
            <Menu
              label={`${chip.label}: ${chip.chosen}`}
              align="left"
              width={230}
              items={chip.items}
              panelClassName="max-h-[320px] overflow-y-auto"
            >
              <span className="inline-flex items-center gap-[7px]" title={chip.chosen}>
                {chip.label}
                <ChevronDown size={12} width={1.9} stroke="var(--color-brand)" />
              </span>
            </Menu>
          </span>
        ) : (
          <Menu
            key={chip.key}
            label={chip.label}
            align="left"
            width={230}
            items={chip.items}
            panelClassName="max-h-[320px] overflow-y-auto"
          >
            <ToolFace>
              {chip.label}
              <ChevronDown size={12} width={1.9} stroke="var(--color-muted)" />
            </ToolFace>
          </Menu>
        ),
      )}
    </>
  )
}

/** Columns: which of the table's columns show (kept in this browser). */
function ColumnsMenu({
  hidden,
  onChange,
}: {
  hidden: Column[]
  onChange: (next: Column[]) => void
}) {
  return (
    <Menu
      label="Columns"
      width={200}
      items={COLUMNS.map((column) => ({
        label: `${hidden.includes(column.key) ? '　' : '✓'}  ${column.label}`,
        onPick: () =>
          onChange(
            hidden.includes(column.key)
              ? hidden.filter((key) => key !== column.key)
              : [...hidden, column.key],
          ),
      }))}
    >
      <ToolFace>
        <ColumnsIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
        Columns
      </ToolFace>
    </Menu>
  )
}

function EmptyState({
  title,
  line,
  children,
}: {
  title: string
  line: string
  children?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2.5 px-5 pt-[120px]">
      <div className="text-md-lg font-medium text-ink">{title}</div>
      <div className="text-125 text-muted">{line}</div>
      {children ? <span className="mt-2">{children}</span> : null}
    </div>
  )
}

function DocketTable({
  rows,
  total,
  pending,
  error,
  onRetry,
  fetching,
  compact,
  columns,
  selecting,
  selected,
  onToggle,
  onToggleAll,
  openId,
  onOpen,
  onMenu,
  menu,
  onCloseMenu,
  nameOf,
  empty,
  more,
}: {
  rows: Row[]
  total: number
  pending: boolean
  error: unknown
  onRetry: () => void
  fetching: boolean
  compact: boolean
  columns: typeof COLUMNS | null
  selecting: boolean
  selected: Set<string>
  onToggle: (id: string) => void
  onToggleAll: () => void
  openId: string | null
  onOpen: (row: Row) => void
  onMenu: (row: Row, top: number) => void
  menu: ReactNode
  onCloseMenu: () => void
  nameOf: (id: string | null | undefined) => string | null
  empty: ReactNode
  more: (() => void) | null
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const show = (key: Column) => columns?.some((c) => c.key === key) ?? false
  const template = compact
    ? '124px minmax(0,1fr) 188px 52px'
    : [
        selecting ? '18px' : null,
        '140px',
        ...(columns ?? []).map((c) => c.width.replace('1fr', 'minmax(0,1fr)')),
      ]
        .filter(Boolean)
        .join(' ')
  const grid = cx('grid items-center gap-x-3.5', compact ? 'px-6' : 'px-9')

  useEffect(() => {
    if (!menu) return
    const away = () => onCloseMenu()
    const key = (event: KeyboardEvent) => event.key === 'Escape' && onCloseMenu()
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', key)
    }
  }, [menu, onCloseMenu])

  const openMenu = (row: Row, element: HTMLElement) => {
    const box = scroller.current
    const top = box ? element.offsetTop - box.scrollTop + 20 : 0
    onMenu(row, top)
  }

  return (
    <div
      className={cx(
        'relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-16 bg-card-white shadow-card',
        fetching && !pending && 'opacity-80',
      )}
    >
      <div ref={scroller} className={cx('min-h-0 flex-1 overflow-y-auto', compact && '-mx-1.5')}>
        <div
          role="row"
          className={cx(grid, 'h-[34px] text-12 text-muted')}
          style={{ gridTemplateColumns: template }}
        >
          {selecting && !compact ? (
            <span className="flex">
              <CheckBox
                checked={rows.length > 0 && selected.size === rows.length}
                some={selected.size > 0 && selected.size < rows.length}
                label="Select every request shown"
                onChange={onToggleAll}
              />
            </span>
          ) : null}
          <span>Tracking no.</span>
          {compact ? (
            <>
              <span>Subject</span>
              <span>Status</span>
              <span className="text-right">Days</span>
            </>
          ) : (
            (columns ?? []).map((c) => (
              <span key={c.key} className={c.key === 'days' ? 'text-right' : undefined}>
                {c.key === 'assignee' ? '' : c.label}
              </span>
            ))
          )}
        </div>
        {pending ? (
          Array.from({ length: 10 }, (_, i) => (
            <div
              key={i}
              aria-hidden
              className={cx(grid, 'h-12 text-125', i % 2 === 1 && 'bg-row-alt')}
              style={{ gridTemplateColumns: template, opacity: 1 - i * 0.09 }}
            >
              {selecting && !compact ? <span /> : null}
              <span className="block h-[11px] w-24 rounded-4 bg-fill-skeleton" />
              {(compact ? ['88%', '70%', '26px'] : (columns ?? []).map((c) => SKELETON[c.key])).map(
                (width, j) => (
                  <span key={j} className="min-w-0">
                    {width ? (
                      <span
                        className="block h-[11px] rounded-4 bg-fill-skeleton"
                        style={{ width }}
                      />
                    ) : null}
                  </span>
                ),
              )}
            </div>
          ))
        ) : error ? (
          <LoadFailed what="the docket" error={error} onRetry={onRetry} />
        ) : total === 0 ? (
          empty
        ) : (
          rows.map((row, i) => {
            const days = daysFigure(row.days_to_next_deadline)
            const late = days.overdue
            const picked = selected.has(row.id)
            const assignee = nameOf(row.assigned_to_user_id)
            return (
              <div
                key={row.id}
                role="row"
                aria-selected={picked || row.id === openId}
                onClick={() => onOpen(row)}
                onContextMenu={(event) => {
                  event.preventDefault()
                  openMenu(row, event.currentTarget)
                }}
                className={cx(
                  grid,
                  'group h-12 cursor-pointer text-125',
                  row.id === openId
                    ? 'bg-brand-tint'
                    : picked
                      ? 'bg-blue-tint-4'
                      : i % 2 === 1
                        ? 'bg-row-alt hover:bg-hover'
                        : 'hover:bg-page',
                )}
                style={{ gridTemplateColumns: template }}
              >
                {selecting && !compact ? (
                  <span className="flex">
                    <CheckBox
                      checked={picked}
                      label={`Select ${row.tracking_number}`}
                      onChange={() => onToggle(row.id)}
                    />
                  </span>
                ) : null}
                <Link
                  to={`/requests/${row.id}`}
                  onClick={(event) => {
                    if (compact) event.preventDefault()
                    else event.stopPropagation()
                  }}
                  className="text-ink-2 hover:text-ink-2"
                >
                  {row.tracking_number}
                </Link>
                {!compact && show('agency') ? (
                  <span className="min-w-0 truncate">{row.agency_name}</span>
                ) : null}
                {compact || show('subject') ? (
                  <span className="min-w-0 truncate text-ink-2">{row.subject}</span>
                ) : null}
                {compact || show('status') ? (
                  <span className="flex min-w-0 items-center gap-2">
                    <StatusGlyph code={row.current_status} />
                    <span className="truncate">{statusText(row.current_status)}</span>
                  </span>
                ) : null}
                {!compact && show('due') ? (
                  <span className={late ? 'text-danger' : 'text-ink-2'}>
                    {shortDate(row.next_deadline_at)}
                  </span>
                ) : null}
                {compact || show('days') ? (
                  <span className="text-right">
                    <span className={late ? 'font-semibold text-danger' : 'text-muted'}>
                      {days.text}
                    </span>
                  </span>
                ) : null}
                {!compact && show('assignee') ? (
                  <span className="relative flex justify-end">
                    {assignee ? (
                      <span
                        title={assignee}
                        className="group-hover:invisible group-focus-within:invisible"
                      >
                        <Avatar initials={initialsOf(assignee)} size={22} />
                      </span>
                    ) : null}
                    <button
                      type="button"
                      aria-label={`Actions for ${row.tracking_number}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        openMenu(row, event.currentTarget.closest('[role=row]') as HTMLElement)
                      }}
                      className="absolute top-1/2 right-0 inline-flex size-[22px] -translate-y-1/2 items-center justify-center rounded-6 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                    >
                      <MoreIcon size={15} stroke="var(--color-ink-3)" />
                    </button>
                  </span>
                ) : null}
              </div>
            )
          })
        )}
        {more && !pending ? (
          <button
            type="button"
            onClick={more}
            className="block w-full py-3 text-center text-125 font-medium text-brand hover:text-brand-dark"
          >
            Show more ({rows.length} of {total})
          </button>
        ) : null}
      </div>
      {menu}
    </div>
  )
}

/** The loading rows' placeholder widths per column (031). */
const SKELETON: Record<Column, string | null> = {
  agency: '80%',
  subject: '88%',
  status: '70%',
  due: '44px',
  days: '26px',
  assignee: null,
}
