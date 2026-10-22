/**
 * The Inbox (design 106–162): one list beside one reading pane. Its three lists sit under Inbox in
 * the sidebar, New · Flagged · All, and Sent and Drafts are filters of All. Flagged holds what the
 * system could not finish, each row saying why in plain words; New holds mail that landed on a
 * request and nobody has looked at; All holds everything that arrived and everything sent.
 *
 * The URL holds what is on screen: `bucket` (flagged · new · all · sent · drafts), `message`, the
 * search (`q`, `scope=all`), the filters (`agency`, `request`, `assignee`), `sort`, and an open
 * attachment (`file`, `page`).
 */
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import { ActionDialog, type ActionSpec } from '../components/ActionDialog'
import { AddDocument } from '../components/AddDocument'
import { ChooseDates } from '../components/ChooseDates'
import { ChevronDown, ChevronRight, CloseIcon, FilePlusIcon, SearchIcon } from '../components/icons'
import { MenuPanel } from '../components/Menu'
import { cx } from '../components/ui'
import { formatTime } from '../lib/dates'
import { isFullStaff } from '../lib/roles'
import { useDefinitions, usePeople } from '../lib/vocab'
import * as inboxActions from './inbox/actions'
import {
  CountRow,
  FiltersButton,
  FlaggedRow,
  ListCard,
  ListEmpty,
  ListFailed,
  ListLoading,
  MessageRow,
  RefreshButton,
  SearchLine,
  SentRow,
  SortControl,
} from './inbox/List'
import { useOutboundRows, useRefreshMail } from './inbox/mail'
import {
  countWords,
  flagWords,
  isOutbound,
  mergeAll,
  movedTitle,
  movedWhen,
  neighbour,
  parseBucket,
  parseSort,
  searchPlaceholder,
  senderOf,
  subjectOf,
  TIME_SPANS,
  tabOf,
  timeSpan,
  titleOf,
  type AllItem,
  type Bucket,
  type Message,
  type Row,
} from './inbox/model'
import { OutboundPane } from './inbox/OutboundPane'
import { Bar, CARD, PaneCard, PillButton } from './inbox/parts'
import { NotesLane, Reader, type Afterwards } from './inbox/Reader'
import { ShortcutSheet, useInboxKeys } from './inbox/Shortcuts'
import { Viewer } from './inbox/Viewer'

type Source = Schemas['SourceOut']

export function Inbox() {
  const me = useMe()
  const navigate = useNavigate()
  const client = useQueryClient()
  const refreshMail = useRefreshMail()
  const definitions = useDefinitions()
  const people = usePeople()
  const [params, setParams] = useSearchParams()
  const bucket = parseBucket(params.get('bucket'))
  const outbound = isOutbound(bucket)
  const selectedId = params.get('message')
  const q = params.get('q') ?? ''
  const scopeAll = params.get('scope') === 'all'
  const agency = params.get('agency') ?? ''
  const requestFilter = params.get('request') ?? ''
  const assignee = params.get('assignee') ?? ''
  // P23b: "Any time" (154): a span of the Activity page's, or dates chosen.
  const time = timeSpan(params.get('span'), params.get('since'), params.get('until'))
  const timed = Boolean(time.since || time.until)
  const sort = parseSort(params.get('sort'))
  const fileId = params.get('file')
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1)
  const searching =
    q !== '' ||
    params.get('filters') === '1' ||
    Boolean(agency || requestFilter || assignee) ||
    timed
  const filtersShown =
    params.get('filters') === '1' || Boolean(agency || requestFilter || assignee) || timed
  const [menu, setMenu] = useState<
    'sort' | 'scope' | 'agency' | 'request' | 'time' | 'assignee' | null
  >(null)
  const [help, setHelp] = useState(false)
  // "Add a document" (061): a scan, a portal download or an email file onto a request.
  const [adding, setAdding] = useState(false)
  const [chosen, setChosen] = useState<string[]>([])
  const [selecting, setSelecting] = useState(false)
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [sticky, setSticky] = useState<{ row: Row; afterwards: Afterwards } | null>(null)
  const [signals, setSignals] = useState<{
    compose: { kind: 'reply' | 'forward'; at: number } | null
    note: number
    attach: number
  }>({ compose: null, note: 0, attach: 0 })
  const [online, setOnline] = useState(() =>
    typeof navigator === 'undefined' ? true : navigator.onLine,
  )
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const up = () => setOnline(true)
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => {
      window.removeEventListener('online', up)
      window.removeEventListener('offline', down)
    }
  }, [])

  const set = (next: Record<string, string | null>) => {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value) merged.set(key, value)
      else merged.delete(key)
    }
    setParams(merged, { replace: true })
  }

  // What arrived: the list's own bucket, or All when searching all mail.
  const inboundBucket = searching && scopeAll ? 'all' : outbound ? 'flagged' : bucket
  const inbox = $api.useQuery(
    'get',
    '/api/records/inbox',
    {
      params: {
        query: {
          bucket: inboundBucket,
          q: outbound && !searching ? undefined : q || undefined,
          agency_id: agency || undefined,
          request_id: requestFilter || undefined,
          assignee: assignee || undefined,
          sort,
          limit: 100,
          since: time.since,
          until: time.until,
        },
      },
    },
    { placeholderData: (previous) => previous },
  )
  // What was sent: Sent and Drafts, and the sent half of All and of a search of all mail.
  const sentHalf = bucket === 'all' || (searching && scopeAll)
  const sentRows = useOutboundRows('sent', q, (bucket === 'sent' || sentHalf) && !assignee, time)
  const draftRows = useOutboundRows('drafts', q, bucket === 'drafts', time)
  // With an agency filter, sent mail is kept to that agency's requests.
  const agencyRequests = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { agency_id: agency, limit: 200 } } },
    { enabled: Boolean(agency) && (sentHalf || bucket === 'sent') },
  )
  const agencyIds = new Set((agencyRequests.data?.items ?? []).map((r) => r.id))
  const keepSent = (m: Message) =>
    (!agency || agencyIds.has(m.request_id)) && (!requestFilter || m.request_id === requestFilter)

  const rows = outbound ? [] : (inbox.data?.items ?? [])
  const sentItems = (bucket === 'drafts' ? draftRows.rows : sentRows.rows).filter(keepSent)
  const all: AllItem[] =
    bucket === 'all' || (searching && scopeAll && !outbound)
      ? mergeAll(rows, sentHalf ? sentItems : [], sort === 'oldest')
      : []
  const mixed = bucket === 'all' || (searching && scopeAll && !outbound)
  const ids = outbound
    ? sentItems.map((m) => m.id)
    : mixed
      ? all.map((item) => item.id)
      : rows.map((r) => r.source.id)

  const selectedRow = rows.find((r) => r.source.id === selectedId) ?? null
  const selectedSent = outbound || mixed ? sentItems.find((m) => m.id === selectedId) : undefined
  const stickyRow = sticky && sticky.row.source.id === selectedId ? sticky : null
  const pending = outbound
    ? (bucket === 'drafts' ? draftRows : sentRows).isPending
    : inbox.isPending
  // P23b: a message opened by its address that is not on the loaded page is read on its own
  // (`/inbox/{id}`), with the lists it is in now: still in this one, it is simply further down and
  // opens; gone from it, the pane says where it went and who moved it (140).
  const beyond =
    Boolean(selectedId) && !selectedRow && !stickyRow && !selectedSent && !pending && !outbound
  const single = $api.useQuery(
    'get',
    '/api/records/inbox/{source_id}',
    { params: { path: { source_id: selectedId ?? '' } } },
    { enabled: beyond, retry: false },
  )
  const singleHere = beyond && single.data?.buckets.includes(inboundBucket) ? single.data : null
  const readerRow = selectedRow ?? stickyRow?.row ?? singleHere
  const failed = outbound ? (bucket === 'drafts' ? draftRows : sentRows).isError : inbox.isError
  const total = outbound
    ? sentItems.length
    : mixed
      ? (inbox.data?.total ?? 0) + (sentHalf ? sentRows.total : 0)
      : (inbox.data?.total ?? 0)

  // A message opened by its address that is not in this list any more (140).
  const missing = beyond && !readerRow && Boolean(single.data)

  const open = (id: string | null) => {
    if (id !== selectedId) setSticky(null)
    set({ message: id, file: null, page: null })
  }
  const nextAfter = (current: string | null) =>
    current ? neighbour(ids, current) : (ids[0] ?? null)
  const refreshAll = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['get', '/api/records/inbox'] }),
      client.invalidateQueries({ queryKey: ['get', '/api/records/inbox/{source_id}'] }),
      refreshMail(),
    ])
  }

  const index = selectedId ? ids.indexOf(selectedId) : -1
  useInboxKeys(
    {
      next: () => open(ids[Math.min(ids.length - 1, index + 1)] ?? null),
      previous: () => open(ids[Math.max(0, index - 1)] ?? null),
      open: () => {
        if (!selectedId && ids[0]) open(ids[0])
      },
      close: () => {
        if (chosen.length) {
          setChosen([])
          setSelecting(false)
        } else if (fileId) set({ file: null, page: null })
      },
      select: () => {
        if (!selectedId) return
        setSelecting(true)
        setChosen((was) =>
          was.includes(selectedId) ? was.filter((x) => x !== selectedId) : [...was, selectedId],
        )
      },
      reviewed: (andNext) => {
        if (!readerRow || !readerRow.events.length) return
        void api
          .POST('/api/records/sources/{source_id}/reviewed', {
            params: { path: { source_id: readerRow.source.id } },
          })
          .then(async () => {
            const after = nextAfter(readerRow.source.id)
            await refreshAll()
            if (andNext) open(after)
          })
      },
      attach: () => setSignals((s) => ({ ...s, attach: s.attach + 1 })),
      reply: () => setSignals((s) => ({ ...s, compose: { kind: 'reply', at: Date.now() } })),
      forward: () => setSignals((s) => ({ ...s, compose: { kind: 'forward', at: Date.now() } })),
      note: () => setSignals((s) => ({ ...s, note: s.note + 1 })),
      help: () => setHelp(true),
      search: () => searchRef.current?.focus(),
      go: (where) =>
        void navigate(
          where === 'inbox'
            ? '/inbox?bucket=new'
            : where === 'flagged'
              ? '/inbox?bucket=flagged'
              : `/${where}`,
        ),
    },
    !help,
  )

  const title = titleOf(bucket)
  const sortControl = (
    <>
      <span className={cx(!searching && 'ml-auto')}>
        <SortControl
          sort={sort}
          open={menu === 'sort'}
          searching={searching}
          onToggle={() => setMenu(menu === 'sort' ? null : 'sort')}
          onPick={(next) => {
            setMenu(null)
            set({ sort: next === 'newest' ? null : next })
          }}
        />
      </span>
      <span className={cx(searching && 'mr-2')}>
        <RefreshButton onClick={() => void refreshAll()} busy={inbox.isFetching} />
      </span>
    </>
  )

  const toggleChosen = (id: string) => {
    setSelecting(true)
    setChosen((was) => (was.includes(id) ? was.filter((x) => x !== id) : [...was, id]))
  }

  /* ── The list's rows ── */
  const listBody = (): ReactNode => {
    if (failed) return <ListFailed what={title} onRetry={() => void refreshAll()} />
    if (outbound) {
      if (sentItems.length === 0)
        return (
          <ListEmpty
            title={q ? 'No mail matches' : bucket === 'drafts' ? 'No drafts' : 'Nothing sent yet'}
          />
        )
      return sentItems.map((m) => (
        <SentRow
          key={m.id}
          message={m}
          active={m.id === selectedId}
          onOpen={() => open(m.id)}
          variant={bucket === 'drafts' ? 'drafts' : 'sent'}
        />
      ))
    }
    if (mixed) {
      if (all.length === 0)
        return <ListEmpty title="No mail here yet" words="Mail appears once it arrives." />
      return all.map((item) =>
        item.kind === 'in' ? (
          <MessageRow
            key={item.id}
            row={item.row}
            active={item.id === selectedId}
            onOpen={() => open(item.id)}
            variant={q ? 'search' : 'all'}
            query={q}
            selecting={selecting}
            chosen={chosen.includes(item.id)}
            onToggle={() => toggleChosen(item.id)}
          />
        ) : (
          <SentRow
            key={item.id}
            message={item.message}
            active={item.id === selectedId}
            onOpen={() => open(item.id)}
            variant={q ? 'search' : 'all'}
            query={q}
          />
        ),
      )
    }
    if (rows.length === 0)
      return q ? (
        <ListEmpty title={`No matches in ${title}`} />
      ) : bucket === 'flagged' ? (
        <ListEmpty
          title="Nothing flagged"
          words={
            // P20h: unplaced mail is for full staff; someone who sees only some requests is told
            // nothing about mail they cannot see.
            isFullStaff(me.records_role)
              ? 'Everything that arrived found its request.'
              : 'Nothing on your requests needs a look.'
          }
        />
      ) : (
        <ListEmpty title="All caught up" words="Nothing new." />
      )
    return rows.map((row, i) =>
      bucket === 'flagged' && !q ? (
        <FlaggedRow
          key={row.source.id}
          row={row}
          first={i === 0}
          active={row.source.id === selectedId}
          onOpen={() => open(row.source.id)}
        />
      ) : (
        <MessageRow
          key={row.source.id}
          row={row}
          active={row.source.id === selectedId}
          onOpen={() => open(row.source.id)}
          variant={q ? 'search' : 'new'}
          query={q}
          selecting={selecting}
          chosen={chosen.includes(row.source.id)}
          onToggle={() => toggleChosen(row.source.id)}
        />
      ),
    )
  }

  /* ── The reading pane ── */
  const pane = (): ReactNode => {
    if (chosen.length > 0)
      return (
        <Several
          rows={rows.filter((r) => chosen.includes(r.source.id))}
          count={chosen.length}
          onAct={setSpec}
          onReviewed={async () => {
            for (const id of chosen)
              await api.POST('/api/records/sources/{source_id}/reviewed', {
                params: { path: { source_id: id } },
              })
            setChosen([])
            setSelecting(false)
            await refreshAll()
          }}
          definitions={definitions.data}
          people={people.data ?? []}
        />
      )
    if (selectedSent || (outbound && selectedId))
      return (
        <OutboundPane
          key={selectedId}
          messageId={selectedId as string}
          placeholder={selectedSent}
          bucket={outbound ? bucket : 'all'}
          onClose={() => open(null)}
          onOpen={(next, id) => set({ bucket: next, message: id })}
        />
      )
    if (readerRow)
      return (
        <Reader
          key={readerRow.source.id}
          row={readerRow}
          bucket={bucket}
          afterwards={stickyRow?.afterwards ?? null}
          onAfterwards={(next) => setSticky(next ? { row: readerRow, afterwards: next } : null)}
          onOpenFile={(id) => set({ file: id, page: null })}
          onNext={() => open(nextAfter(readerRow.source.id))}
          onClose={() => open(null)}
          composeRequest={signals.compose}
          noteRequest={signals.note}
          attachRequest={signals.attach}
        />
      )
    if (missing && single.data)
      return (
        <Moved
          item={single.data}
          bucket={bucket}
          me={me}
          onOpenIn={(next) => set({ bucket: next })}
          onNext={() => open(ids[0] ?? null)}
        />
      )
    if (bucket === 'sent' || bucket === 'drafts')
      return (
        <PaneCard className="items-center justify-center gap-1.5">
          <div className="font-display text-lg font-extrabold text-ink">Nothing to show</div>
        </PaneCard>
      )
    if (failed)
      return (
        <PaneCard>
          <div className="flex-1" />
        </PaneCard>
      )
    return (
      <PaneCard className="items-center justify-center gap-2">
        <div className="font-display text-lg font-extrabold">Nothing to read</div>
        <div className="max-w-[38ch] text-center text-125 leading-[1.6] text-muted">
          Pick a message.
        </div>
      </PaneCard>
    )
  }

  /* ── Layouts ── */
  const offline = !online ? (
    <div className="flex items-center gap-3 rounded-10 border-[1.5px] border-amber-line bg-amber-tint-3 px-3.5 py-2.5 text-125 text-warn">
      <span className="flex-1">
        Offline. Showing{' '}
        {inbox.dataUpdatedAt
          ? formatTime(new Date(inbox.dataUpdatedAt).toISOString(), me.timezone)
          : 'what was loaded'}
        . Nothing can be sent until the connection is back.
      </span>
      <button type="button" onClick={() => void refreshAll()} className="font-medium text-brand">
        Retry
      </button>
    </div>
  ) : null

  const viewerFile =
    readerRow && fileId ? readerRow.attachments.find((a) => a.id === fileId) : undefined

  let body: ReactNode
  if (pending && !outbound && bucket === 'new' && !inbox.data) {
    body = (
      <div className="flex flex-1 items-stretch gap-4">
        <div className={cx(CARD, 'flex-[0_0_340px] overflow-hidden')}>
          <div className="px-0">
            <SearchLine
              placeholder={searchPlaceholder(bucket)}
              value={q}
              onSearch={() => undefined}
              onFilters={() => undefined}
            />
          </div>
          <ListLoading flagged={false} />
        </div>
        <PaneSkeleton flagged={false} />
      </div>
    )
  } else if (pending && !inbox.data && !outbound) {
    body = (
      <div className="flex flex-1 items-stretch gap-4">
        <div className={cx(CARD, 'flex-[0_0_340px] overflow-hidden')}>
          <SearchLine
            placeholder={searchPlaceholder(bucket)}
            value={q}
            onSearch={() => undefined}
            onFilters={() => undefined}
          />
          <ListLoading flagged />
        </div>
        <PaneSkeleton flagged />
      </div>
    )
  } else if (
    bucket === 'new' &&
    !q &&
    !searching &&
    rows.length === 0 &&
    !failed &&
    !selectedId &&
    online
  ) {
    // Nothing new (137): one card says so.
    body = (
      <div className="flex flex-1 items-stretch gap-4">
        <div className={cx(CARD, 'flex flex-1 flex-col items-center justify-center gap-2 p-10')}>
          <div className="font-display text-lg font-extrabold">All caught up</div>
          <div className="text-125 text-muted">Nothing new.</div>
        </div>
      </div>
    )
  } else if (viewerFile && readerRow) {
    // An attachment open (111, 162): the list folds to a strip, the message stays as context.
    body = (
      <div className="flex min-h-0 flex-1 items-stretch gap-3.5">
        <button
          type="button"
          aria-label="Show the list"
          onClick={() => set({ file: null, page: null })}
          className={cx(CARD, 'flex flex-[0_0_36px] items-center justify-center')}
        >
          <span className="inline-flex size-[26px] items-center justify-center">
            <ChevronRight size={14} width={2} stroke="var(--color-ink-3)" />
          </span>
        </button>
        {readerRow.source.flagged_for_review ? (
          <Context
            row={readerRow}
            fileId={viewerFile.id}
            onFile={(id) => set({ file: id, page: null })}
          />
        ) : (
          <Reader
            key={readerRow.source.id}
            className="flex-[0_0_330px]"
            row={readerRow}
            bucket={bucket}
            afterwards={null}
            onAfterwards={() => undefined}
            onOpenFile={(id) => set({ file: id, page: null })}
            onNext={() => open(nextAfter(readerRow.source.id))}
            onClose={() => set({ file: null, page: null })}
          />
        )}
        <Viewer
          file={viewerFile}
          page={page}
          onPage={(next) => set({ page: String(next) })}
          onClose={() => set({ file: null, page: null })}
        />
      </div>
    )
  } else if (searching) {
    body = (
      <>
        <SearchBar
          bucket={bucket}
          q={q}
          scopeAll={scopeAll}
          filtersShown={filtersShown}
          menu={menu}
          setMenu={setMenu}
          agency={agency}
          request={requestFilter}
          assignee={assignee}
          time={time}
          span={params.get('span') ?? ''}
          since={params.get('since') ?? ''}
          until={params.get('until') ?? ''}
          people={people.data ?? []}
          set={set}
          inputRef={searchRef}
        />
        <div className="flex min-h-0 flex-1 items-stretch gap-4">
          <div className="flex min-w-0 flex-[0_0_340px] flex-col gap-0.5">
            <div className="-mt-1 flex items-center justify-between">{sortControl}</div>
            {!failed && ids.length === 0 && !pending ? (
              <div
                className={cx(
                  CARD,
                  'flex flex-1 flex-col items-center justify-center gap-2.5 px-6 py-[34px] text-center',
                )}
              >
                <div className="text-135 font-semibold text-ink">
                  No matches in {scopeAll ? 'all mail' : title}
                </div>
                {!scopeAll ? (
                  <button
                    type="button"
                    onClick={() => set({ scope: 'all' })}
                    className="box-border inline-flex h-8 items-center rounded-9 border-[1.5px] border-brand px-3.5 text-125 font-medium text-brand hover:bg-brand-tint"
                  >
                    Search all mail
                  </button>
                ) : null}
              </div>
            ) : (
              <ListCard>{listBody()}</ListCard>
            )}
          </div>
          {!failed && ids.length === 0 && !pending ? (
            <div className={cx(CARD, 'flex-1')} />
          ) : (
            <div className="mt-[22px] flex min-w-0 flex-1">{pane()}</div>
          )}
        </div>
      </>
    )
  } else {
    body = (
      <div className="flex min-h-0 flex-1 items-stretch gap-4">
        <div className="flex min-h-0 min-w-0 flex-[0_0_340px] flex-col gap-2">
          <SearchLine
            placeholder={searchPlaceholder(bucket)}
            value={q}
            inputRef={searchRef}
            onSearch={(next) => set({ q: next || null, message: null })}
            onFilters={() => set({ filters: '1' })}
          />
          <CountRow
            words={countWords(bucket, total)}
            inset={bucket === 'all'}
            selection={
              chosen.length > 0
                ? {
                    chosen: chosen.length,
                    total: ids.length,
                    onClear: () => {
                      setChosen([])
                      setSelecting(false)
                    },
                  }
                : undefined
            }
            sort={sortControl}
          />
          <ListCard>{listBody()}</ListCard>
        </div>
        {pane()}
      </div>
    )
  }

  return (
    // The screens set no line height, so the browser's "normal" is theirs; the Inbox takes it
    // rather than the app's 1.35 (as P20c did for the request page).
    <div
      className="flex min-h-0 flex-1 flex-col leading-[normal]"
      onClick={() => menu && setMenu(null)}
    >
      <PageHeader
        title={title}
        trail={undefined}
        actions={
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="inline-flex h-8 items-center gap-1.5 rounded-9 px-3 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
          >
            <FilePlusIcon size={14} width={1.8} stroke="var(--color-ink-2)" />
            Add a document
          </button>
        }
      />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
        {offline}
        {body}
      </div>
      <ShortcutSheet open={help} onClose={() => setHelp(false)} />
      <AddDocument
        open={adding}
        onClose={() => setAdding(false)}
        onAdded={() => void refreshAll()}
      />
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          setChosen([])
          setSelecting(false)
          void refreshAll()
        }}
      />
    </div>
  )
}

/* ── Search mode (153–157) ────────────────────────────────────────────── */

function Chip({
  label,
  open,
  active,
  applied,
  onClick,
}: {
  label: string
  open: boolean
  active?: boolean
  applied?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-haspopup="menu"
      aria-expanded={open}
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
      className={cx(
        'box-border inline-flex h-8 items-center gap-1.5 rounded-8 px-2.5 text-125 whitespace-nowrap',
        applied
          ? 'border-[1.5px] border-brand bg-brand-tint font-medium text-brand'
          : active
            ? 'bg-brand-tint font-medium text-brand'
            : open
              ? 'bg-hover text-ink'
              : 'text-ink-2 hover:bg-hover',
      )}
    >
      {label}
      <ChevronDown
        size={9}
        width={2.6}
        stroke={active ? 'var(--color-brand)' : 'var(--color-muted)'}
      />
    </button>
  )
}

function SearchBar({
  bucket,
  q,
  scopeAll,
  filtersShown,
  menu,
  setMenu,
  agency,
  request,
  assignee,
  time,
  span,
  since,
  until,
  people,
  set,
  inputRef,
}: {
  bucket: Bucket
  q: string
  scopeAll: boolean
  filtersShown: boolean
  menu: string | null
  setMenu: (menu: 'sort' | 'scope' | 'agency' | 'request' | 'time' | 'assignee' | null) => void
  agency: string
  request: string
  assignee: string
  time: { label: string; since?: string; until?: string }
  span: string
  since: string
  until: string
  people: Schemas['UserOut'][]
  set: (next: Record<string, string | null>) => void
  inputRef: React.Ref<HTMLInputElement>
}) {
  const [choosing, setChoosing] = useState(false)
  const agencies = $api.useQuery('get', '/api/records/agencies', {}, { enabled: filtersShown })
  const requests = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { limit: 50, sort: 'newest', agency_id: agency || undefined } } },
    { enabled: filtersShown },
  )
  const [find, setFind] = useState('')
  const tab = titleOf(tabOf(bucket))
  const agencyName = (agencies.data ?? []).find((a) => a.id === agency)?.name
  const requestName = (requests.data?.items ?? []).find((r) => r.id === request)?.tracking_number
  const personName = people.find((p) => p.id === assignee)?.name ?? (assignee === 'me' ? 'Me' : '')
  const needle = find.trim().toLowerCase()
  const dropdown = (
    items: Array<{ value: string; label: string }>,
    pick: (value: string) => void,
    placeholder: string,
    current: string,
  ) => (
    <div
      onClick={(event) => event.stopPropagation()}
      className="absolute top-11 z-[4] box-content w-[230px] overflow-hidden rounded-10 border border-line bg-card-white shadow-menu"
      style={{ left: 470 }}
    >
      <div className="px-3.5 py-[7px]">
        <input
          autoFocus
          aria-label={placeholder}
          placeholder={placeholder}
          value={find}
          onChange={(event) => setFind(event.target.value)}
          className="w-full rounded-chip border-[1.5px] border-line px-[9px] py-[5px] text-12 text-ink outline-none placeholder:text-muted-2 focus:border-brand"
        />
      </div>
      <div className="max-h-72 overflow-y-auto">
        {items
          .filter((item) => !needle || item.label.toLowerCase().includes(needle))
          .map((item) => (
            <button
              key={item.value || 'any'}
              type="button"
              onClick={() => {
                pick(item.value)
                setFind('')
                setMenu(null)
              }}
              className={cx(
                'block w-full px-3.5 py-[7px] text-left text-125 text-ink hover:bg-hover',
                item.value === current && 'font-medium',
              )}
            >
              {item.label}
            </button>
          ))}
      </div>
      <div className="h-3.5" />
    </div>
  )
  return (
    <div className="relative flex items-center gap-2">
      <label className="box-border flex h-[38px] flex-[0_0_340px] items-center gap-[9px] rounded-10 border-[1.5px] border-line-2 bg-card-white px-3">
        <SearchIcon size={15} width={2} stroke="var(--color-muted)" />
        {scopeAll ? (
          <span className="inline-flex shrink-0 items-center gap-[5px] rounded-6 bg-brand-tint py-0.5 pr-1.5 pl-2 text-115 font-medium whitespace-nowrap text-brand">
            All mail
            <button
              type="button"
              aria-label={`Search ${tab} only`}
              onClick={(event) => {
                event.preventDefault()
                set({ scope: null })
              }}
              className="text-10 text-blue-text-muted"
            >
              ✕
            </button>
          </span>
        ) : null}
        <input
          ref={inputRef}
          key={q}
          type="search"
          aria-label={scopeAll ? 'Search all mail' : `Search ${tab}`}
          defaultValue={q}
          autoFocus={!q}
          onKeyDown={(event) => {
            if (event.key === 'Enter')
              set({ q: event.currentTarget.value.trim() || null, message: null })
            if (event.key === 'Escape') set({ q: null, scope: null, filters: null, message: null })
          }}
          className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none [&::-webkit-search-cancel-button]:hidden"
        />
        <button
          type="button"
          aria-label="Clear the search"
          onClick={(event) => {
            event.preventDefault()
            set({
              q: null,
              scope: null,
              filters: null,
              agency: null,
              request: null,
              assignee: null,
              span: null,
              since: null,
              until: null,
              message: null,
            })
          }}
          className="mr-0.5 inline-flex"
        >
          <CloseIcon size={13} width={2.2} stroke="var(--color-muted-2)" />
        </button>
        <FiltersButton
          on={filtersShown}
          onClick={() => set({ filters: filtersShown ? null : '1' })}
        />
      </label>
      {filtersShown ? (
        <>
          <Chip
            label={scopeAll ? 'All mail' : tab}
            active
            open={menu === 'scope'}
            onClick={() => setMenu(menu === 'scope' ? null : 'scope')}
          />
          <Chip
            label={agencyName ?? 'Any agency'}
            applied={Boolean(agency)}
            open={menu === 'agency'}
            onClick={() => setMenu(menu === 'agency' ? null : 'agency')}
          />
          <Chip
            label={requestName ?? 'Any request'}
            applied={Boolean(request)}
            open={menu === 'request'}
            onClick={() => setMenu(menu === 'request' ? null : 'request')}
          />
          <Chip
            label={time.label}
            applied={Boolean(time.since || time.until)}
            open={menu === 'time'}
            onClick={() => setMenu(menu === 'time' ? null : 'time')}
          />
          <Chip
            label={personName || 'Anyone'}
            applied={Boolean(assignee)}
            open={menu === 'assignee'}
            onClick={() => setMenu(menu === 'assignee' ? null : 'assignee')}
          />
        </>
      ) : null}
      {menu === 'time' ? (
        <div
          onClick={(event) => event.stopPropagation()}
          className="absolute top-11 z-[4]"
          style={{ left: 700 }}
        >
          <MenuPanel
            aria-label="When it arrived"
            width={200}
            items={[
              {
                label: 'Any time',
                current: !time.since && !time.until,
                onPick: () => set({ span: null, since: null, until: null }),
              },
              ...TIME_SPANS.map((option) => ({
                label: option.label,
                current: span === option.key,
                onPick: () => set({ span: option.key, since: null, until: null }),
              })),
              {
                label: 'Choose dates…',
                separator: true,
                current: span === 'custom',
                onPick: () => setChoosing(true),
              },
            ]}
            onPicked={() => setMenu(null)}
          />
        </div>
      ) : null}
      {choosing ? (
        <ChooseDates
          since={span === 'custom' ? since : ''}
          until={span === 'custom' ? until : ''}
          onClose={() => setChoosing(false)}
          onChoose={(from, to) => {
            setChoosing(false)
            set(
              from || to
                ? { span: 'custom', since: from || null, until: to || null }
                : { span: null, since: null, until: null },
            )
          }}
        />
      ) : null}
      {menu === 'scope' ? (
        <div
          onClick={(event) => event.stopPropagation()}
          className="absolute top-11 z-[4]"
          style={{ left: 350 }}
        >
          <MenuPanel
            aria-label="Where to search"
            width={190}
            items={[
              { label: tab, current: !scopeAll, onPick: () => set({ scope: null }) },
              { label: 'All mail', current: scopeAll, onPick: () => set({ scope: 'all' }) },
            ]}
            onPicked={() => setMenu(null)}
          />
        </div>
      ) : null}
      {menu === 'agency'
        ? dropdown(
            [
              { value: '', label: 'Any agency' },
              ...(agencies.data ?? []).map((a) => ({ value: a.id, label: a.name })),
            ],
            (value) => set({ agency: value || null }),
            'Find an agency',
            agency,
          )
        : null}
      {menu === 'request'
        ? dropdown(
            [
              { value: '', label: 'Any request' },
              ...(requests.data?.items ?? []).map((r) => ({
                value: r.id,
                label: `${r.tracking_number} · ${r.subject}`,
              })),
            ],
            (value) => set({ request: value || null }),
            'Find a request',
            request,
          )
        : null}
      {menu === 'assignee'
        ? dropdown(
            [
              { value: '', label: 'Anyone' },
              { value: 'me', label: 'Assigned to me' },
              ...people
                .filter((p) => p.is_active && p.records_role !== 'requester')
                .map((p) => ({ value: p.id, label: p.name })),
            ],
            (value) => set({ assignee: value || null }),
            'Find a teammate',
            assignee,
          )
        : null}
    </div>
  )
}

/* ── The pane's other states ──────────────────────────────────────────── */

/** "Maya just moved this to Flagged" (140): the message left this list while it was open, and
 *  who moved it and when, when the activity log knows (P23b). */
function Moved({
  item,
  bucket,
  me,
  onOpenIn,
  onNext,
}: {
  item: Schemas['InboxItemOut']
  bucket: Bucket
  me: Schemas['MeOut']
  onOpenIn: (bucket: Bucket) => void
  onNext: () => void
}) {
  const source: Source = item.source
  const flagged = source.flagged_for_review
  const where: Bucket = flagged
    ? 'flagged'
    : item.buckets.includes('new')
      ? 'new'
      : item.buckets.includes('all')
        ? 'all'
        : 'sent'
  const title = flagged
    ? movedTitle(item.flagged_by, item.flagged_at, me.name)
    : source.reviewed_at
      ? 'This was marked reviewed'
      : 'This is not in this list'
  return (
    <PaneCard className="items-center justify-center gap-3 p-10 text-center">
      <div className="font-display text-16 font-extrabold text-ink">{title}</div>
      <div className="max-w-[38ch] text-125 leading-[1.6] text-muted">
        {flagged
          ? `Marked “${flagWords(source.flag_reason).long}”${
              item.flagged_at ? ` ${movedWhen(item.flagged_at, me.timezone)}` : ''
            }.`
          : source.reviewed_at
            ? 'It is under All mail now.'
            : 'It may be further down the list.'}
      </div>
      <div className="mt-1 flex gap-2">
        {where !== bucket ? (
          <PillButton onClick={() => onOpenIn(where)}>Open in {titleOf(where)}</PillButton>
        ) : null}
        <button
          type="button"
          onClick={onNext}
          className="inline-flex h-8 items-center rounded-9 px-[15px] text-125 font-medium text-ink-2 hover:bg-hover"
        >
          Next {bucket === 'flagged' ? 'flagged' : bucket === 'new' ? 'new' : 'message'}
        </button>
      </div>
    </PaneCard>
  )
}

/** "2 messages selected" (161): what can be done to all of them at once. */
function Several({
  rows,
  count,
  onAct,
  onReviewed,
  definitions,
  people,
}: {
  rows: Row[]
  count: number
  onAct: (spec: ActionSpec) => void
  onReviewed: () => Promise<void>
  definitions: Schemas['DefinitionsOut'] | undefined
  people: Schemas['UserOut'][]
}) {
  const quiet =
    'inline-flex h-8 items-center rounded-9 px-3.5 text-125 font-medium text-ink-2 hover:bg-hover'
  const landed = rows.every((r) => r.events.length > 0)
  return (
    <PaneCard className="items-center justify-center gap-[18px] p-10">
      <div className="font-display text-lg font-extrabold text-ink">
        {count} {count === 1 ? 'message' : 'messages'} selected
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {landed ? (
          <button
            type="button"
            onClick={() => void onReviewed()}
            className="box-border inline-flex h-8 items-center rounded-pill border-[1.5px] border-brand bg-brand px-3.5 text-125 font-medium text-white hover:bg-brand-dark"
          >
            Reviewed
          </button>
        ) : null}
        <button type="button" className={quiet} onClick={() => onAct(severalAssign(rows, people))}>
          Assign to…
        </button>
        {definitions && rows.every((r) => !r.source.flagged_for_review && !r.source.reviewed_at) ? (
          <button
            type="button"
            className={quiet}
            onClick={() => onAct(severalFlag(rows, definitions))}
          >
            Move to Flagged
          </button>
        ) : null}
      </div>
      <div className="text-12 text-muted">Shift-click to select a range · Esc to clear</div>
    </PaneCard>
  )
}

function severalAssign(rows: Row[], people: Schemas['UserOut'][]): ActionSpec {
  const one = inboxActions.assignTo(rows[0], people)
  return {
    ...one,
    key: `assign-several-${rows.map((r) => r.source.id).join('-')}`,
    subtitle: `${rows.length} messages`,
    run: async (values) => {
      for (const row of rows) await inboxActions.assignTo(row, people).run(values)
    },
  }
}

function severalFlag(rows: Row[], definitions: Schemas['DefinitionsOut']): ActionSpec {
  const one = inboxActions.flag(rows[0], definitions)
  return {
    ...one,
    key: `flag-several-${rows.map((r) => r.source.id).join('-')}`,
    subtitle: `${rows.length} messages`,
    run: async (values) => {
      for (const row of rows) await inboxActions.flag(row, definitions).run(values)
    },
  }
}

/** The message beside an open attachment (111): its words, its files, the team's notes. */
function Context({
  row,
  fileId,
  onFile,
}: {
  row: Row
  fileId: string
  onFile: (id: string) => void
}) {
  const me = useMe()
  const [writing, setWriting] = useState(false)
  const noteRef = useRef<HTMLTextAreaElement>(null)
  const notes = $api.useQuery('get', '/api/records/sources/{source_id}/notes', {
    params: { path: { source_id: row.source.id } },
  })
  const message = $api.useQuery(
    'get',
    '/api/records/sources/{source_id}/message',
    { params: { path: { source_id: row.source.id } } },
    { staleTime: 5 * 60_000 },
  )
  const live = (notes.data ?? []).filter((n) => !n.voided_at)
  const body = message.data?.readable ? message.data.body : (row.events[0]?.display_body ?? '')
  return (
    <section className={cx(CARD, 'flex flex-[0_0_330px] flex-col overflow-hidden')}>
      <div className="border-b border-hairline px-[18px] py-[13px]">
        <div className="font-display text-135 leading-[1.35] font-extrabold">{subjectOf(row)}</div>
        <div className="mt-1 text-115 text-muted">
          {senderOf(row)} · {formatTime(row.source.captured_at, me.timezone)}
        </div>
      </div>
      <div className="max-h-56 overflow-y-auto border-b border-line-row-2 px-[18px] py-3.5 text-12 leading-[1.75] whitespace-pre-wrap text-ink-2">
        {body}
      </div>
      <div className="flex flex-wrap gap-1.5 px-[18px] py-3">
        {row.attachments.map((file) => (
          <button
            key={file.id}
            type="button"
            onClick={() => onFile(file.id)}
            className={cx(
              'box-border inline-flex max-w-[calc(50%-3px)] min-w-0 items-center gap-[7px] overflow-hidden px-2.5 py-1.5 text-115 whitespace-nowrap',
              file.id === fileId
                ? 'rounded-8 bg-brand-tint font-semibold text-brand'
                : 'rounded-9 border border-line-chip-2 bg-surface-soft text-ink-2',
            )}
          >
            <span className="block min-w-0 truncate">{file.filename}</span>
          </button>
        ))}
      </div>
      <div className="flex flex-1 flex-col bg-row-alt">
        <NotesLane
          sourceId={row.source.id}
          notes={live}
          writing={writing}
          onWrite={() => setWriting(true)}
          onDone={() => {
            setWriting(false)
            void notes.refetch()
          }}
          noteRef={noteRef}
          tight
        />
      </div>
    </section>
  )
}

/** The pane's loading bars (115, 138). */
function PaneSkeleton({ flagged }: { flagged: boolean }) {
  const lines = flagged
    ? ['94%', '86%', '78%', '70%', '62%', '54%']
    : ['92%', '83%', '74%', '65%', '56%']
  return (
    <div
      className={cx(CARD, 'flex flex-1 flex-col gap-3', flagged ? 'px-6 py-5' : 'px-[22px] py-5')}
    >
      <span aria-hidden className="block" />
      <Bar width={flagged ? '34%' : '30%'} />
      <div className={flagged ? 'h-2.5' : 'h-2'} />
      {lines.map((width) => (
        <Bar key={width} width={width} />
      ))}
    </div>
  )
}
