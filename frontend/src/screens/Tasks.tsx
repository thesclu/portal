import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import { ActionDialog, unwrap, type ActionSpec, type Values } from '../components/ActionDialog'
import {
  AssignIcon,
  CheckIcon,
  ChevronDown,
  FilterIcon,
  RefreshIcon,
  SearchIcon,
  SelectIcon,
  SortIcon,
} from '../components/icons'
import { ListCard, ListHead } from '../components/ListTable'
import { Menu } from '../components/Menu'
import { LoadFailed } from '../components/States'
import { cx } from '../components/ui'
import { formatInstant, formatShortInstant } from '../lib/dates'
import { DUE_GROUPS, byDue, dueCell, dueCounts, dueOf, taskTitle, useNow } from '../lib/tasks'
import { humanize, useDefinitions, useNameOf, usePeople } from '../lib/vocab'
import { BulkButton, ToolButton, ToolFace } from './docket/parts'
import { Approvals, usePendingApprovals } from './tasks/Approvals'
import { NewTaskPopover } from './tasks/NewTask'
import { Suggestions, usePendingSuggestions } from './tasks/Suggestions'
import { TaskTabs } from './tasks/Tabs'
import { TaskPanel, primaryOf } from './tasks/TaskPanel'

type Task = Schemas['TaskOut']
type Person = Schemas['UserOut']

const COLUMNS = '34px 176px minmax(0,1.3fr) 150px 128px 128px'
const TASKS = '/api/records/tasks' as const
const text = (values: Values, name: string) => String(values[name] ?? '').trim()

const staffOptions = (people: Person[]) => [
  { value: '', label: 'Nobody' },
  ...people
    .filter((p) => p.is_active && p.records_role !== 'requester')
    .map((p) => ({ value: p.id, label: p.name })),
]

function reassign(tasks: Task[], people: Person[], current: string | null): ActionSpec {
  return {
    key: `assign-${tasks.map((t) => t.id).join('-')}`,
    title: tasks.length === 1 ? 'Reassign' : `Reassign ${tasks.length} tasks`,
    subtitle: tasks.length === 1 ? taskTitle(tasks[0]) : undefined,
    submit: 'Reassign',
    compact: true,
    fields: [
      {
        name: 'user',
        label: 'Who does this',
        type: 'select',
        initial: current ?? '',
        options: staffOptions(people),
      },
    ],
    run: async (values) => {
      for (const task of tasks) {
        unwrap(
          await api.POST('/api/records/tasks/{task_id}/assign', {
            params: { path: { task_id: task.id } },
            body: { user_id: text(values, 'user') || null },
          }),
        )
      }
    },
  }
}

function edit(task: Task): ActionSpec {
  return {
    key: `edit-${task.id}`,
    title: 'Edit task',
    subtitle: taskTitle(task),
    submit: 'Save',
    compact: true,
    fields: [
      {
        name: 'description',
        label: 'What needs doing',
        type: 'textarea',
        required: true,
        minHeight: 70,
        initial: task.description,
      },
      { name: 'note', label: 'Why', type: 'textarea', required: true, minHeight: 46 },
    ],
    run: async (values) => {
      if (text(values, 'description') === task.description) throw new Error('Nothing was changed.')
      unwrap(
        await api.PATCH('/api/records/tasks/{task_id}', {
          params: { path: { task_id: task.id } },
          body: { note: text(values, 'note'), description: text(values, 'description') },
        }),
      )
    },
  }
}

function reopen(task: Task): ActionSpec {
  return {
    key: `reopen-${task.id}`,
    title: 'Reopen task',
    subtitle: taskTitle(task),
    submit: 'Reopen',
    compact: true,
    fields: [{ name: 'note', label: 'Why', type: 'textarea', required: true, minHeight: 46 }],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/tasks/{task_id}/reopen', {
          params: { path: { task_id: task.id } },
          body: { note: text(values, 'note') },
        }),
      )
    },
  }
}

/** One box of the strip (173): the count, its label under it; lit on the brand tint. */
function StatBox({
  count,
  label,
  tone = 'ink',
  active,
  onClick,
}: {
  count: number | undefined
  label: string
  tone?: 'ink' | 'danger' | 'gold'
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cx(
        'flex min-w-0 flex-auto flex-col gap-1 rounded-12 px-[11px] pt-[9px] pb-2 text-left shadow-card',
        active ? 'bg-brand-tint' : 'bg-card-white hover:bg-surface-row',
      )}
    >
      <span
        className={cx(
          'text-18 leading-none font-medium tabular-nums',
          tone === 'danger' ? 'text-danger' : tone === 'gold' ? 'text-amber-text' : 'text-ink',
        )}
      >
        {count ?? '–'}
      </span>
      <span
        className={cx(
          'text-11 leading-[normal] whitespace-nowrap',
          active ? 'text-brand' : 'text-muted',
        )}
      >
        {label}
      </span>
    </button>
  )
}

/** The leading box of a row: empty to complete, ticked when done (173, 176). */
function TaskBox({
  state,
  busy,
  disabled,
  label,
  onClick,
}: {
  state: 'open' | 'kept' | 'done' | 'selected' | 'unselected'
  busy?: boolean
  disabled?: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={state === 'kept' || state === 'done' || state === 'selected' || busy}
      aria-label={label}
      disabled={disabled || busy}
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
      className={cx(
        'box-border inline-flex size-[15px] shrink-0 items-center justify-center rounded-4',
        state === 'kept'
          ? 'border-[1.5px] border-green-line bg-ok-tint text-10 text-ok'
          : state === 'done'
            ? 'bg-ok-tint'
            : state === 'selected'
              ? 'bg-brand'
              : cx(
                  'border-[1.5px] border-line-control',
                  busy
                    ? 'bg-line-control'
                    : 'hover:border-brand disabled:hover:border-line-control',
                ),
      )}
    >
      {state === 'kept' ? '✓' : null}
      {state === 'done' ? <CheckIcon size={10} width={3} stroke="var(--color-ok)" /> : null}
      {state === 'selected' ? (
        <CheckIcon size={9} width={3.4} stroke="var(--color-card-white)" />
      ) : null}
    </button>
  )
}

/** "Completed Sep 7" with who and when above it on hover (182). */
function CompletedOn({ task, timeZone }: { task: Task; timeZone: string }) {
  const nameOf = useNameOf()
  const who = nameOf(task.completed_by_id) ?? 'Someone'
  return (
    <span
      title={`Completed ${formatInstant(task.completed_at, timeZone)} by ${who}`}
      className="group/done relative"
    >
      Completed {formatShortInstant(task.completed_at, timeZone)}
      <span className="pointer-events-none absolute bottom-[calc(100%+6px)] left-1/2 z-[5] hidden -translate-x-1/2 rounded-10 border border-line bg-card-white px-2.5 py-1.5 text-12 leading-[1.3] font-normal whitespace-nowrap text-ink-2 no-underline shadow-menu group-hover/done:block">
        {who} <span className="text-muted-2">·</span>{' '}
        {formatShortInstant(task.completed_at, timeZone)},{' '}
        {new Intl.DateTimeFormat('en-US', { timeZone, hour: 'numeric', minute: '2-digit' }).format(
          new Date(task.completed_at ?? ''),
        )}
      </span>
    </span>
  )
}

/** A row's quiet action (173 "Retry send", "Confirm"): the work, where the portal can take you. */
function RowAction({ task }: { task: Task }) {
  const navigate = useNavigate()
  const primary = primaryOf(task)
  if (!primary?.to) return null
  const icon =
    task.task_type === 'retry_delivery' ? (
      <RefreshIcon size={13} width={2} stroke="var(--color-ink-2)" />
    ) : task.task_type === 'confirm_filed' ? (
      <CheckIcon size={13} width={2.4} stroke="var(--color-ink-2)" />
    ) : null
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation()
        void navigate(primary.to!)
      }}
      className="box-border inline-flex h-7 items-center gap-1.5 rounded-9 px-[11px] text-12 font-medium whitespace-nowrap text-ink hover:bg-hover"
    >
      {icon}
      {task.task_type === 'enter_deadline_rules' ? 'Enter' : primary.label}
    </button>
  )
}

/** "Retry delivery", and "by hand" after a task a person added (173). */
function TitleCell({ task, done }: { task: Task; done: 'kept' | 'done' | null }) {
  return (
    <span
      className={cx(
        'min-w-0',
        done === 'kept' && 'line-through',
        done === 'done' && 'font-medium text-muted line-through',
        !done && 'font-medium',
      )}
    >
      {taskTitle(task)}
      {task.task_type === 'manual' && task.created_by_user_id && !done ? (
        <span className="text-105 font-normal text-muted"> by hand</span>
      ) : null}
    </span>
  )
}

type Order = 'due' | 'grouped' | 'newest'
const ORDERS: Array<{ value: Order; label: string }> = [
  { value: 'due', label: 'Due date' },
  { value: 'grouped', label: 'Grouped by when due' },
  { value: 'newest', label: 'Newest' },
]

/**
 * Tasks (173–185): one flat list of what people have to do across requests, soonest due first,
 * completed in place. The strip counts what is overdue, due today and due this week, yours and
 * everyone's, and what is done, and each box filters the list. Search, Filter (kind, agency),
 * the order, Select (reassign or complete several) and New task are the toolbar. A task opens
 * beside the list (177). The approvals and suggestions views (186, 192) are `?view=`, with tabs.
 */
export function Tasks() {
  const me = useMe()
  const people = usePeople()
  const definitions = useDefinitions()
  const now = useNow()
  const nameOf = useNameOf()
  const nameOfOrSomeone = (id: string) => nameOf(id) ?? 'Someone'
  const [params, setParams] = useSearchParams()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [kept, setKept] = useState<Task[]>([])
  const [filtering, setFiltering] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [adding, setAdding] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const search = useRef<HTMLInputElement>(null)

  const get = (key: string) => params.get(key) ?? ''
  const set = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setParams(next, { replace: true })
  }
  // The pre-v5 addresses (?view=everyone, ?view=completed) still open what they named.
  const legacy = get('view')
  const view = legacy === 'approvals' || legacy === 'suggestions' ? legacy : null
  const scope = get('scope') === 'everyone' || legacy === 'everyone' ? 'everyone' : 'mine'
  const done = get('state') === 'done' || legacy === 'completed'
  const due = (['overdue', 'today', 'week'] as const).find((d) => d === get('due')) ?? null
  const order = (ORDERS.find((o) => o.value === get('order'))?.value ?? 'due') as Order
  const requestId = get('request') || null
  const openId = get('task') || null
  const q = get('q').toLowerCase()
  const category = get('category')
  const agency = get('agency')
  // The top bar's panel asks for the popover with ?new=1 (its "New task").
  const showAdd = adding || get('new') === '1'
  const closeAdd = () => {
    setAdding(false)
    if (get('new')) set({ new: null })
  }

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

  const mine = $api.useQuery('get', TASKS, { params: { query: { assignee: 'me', state: 'open' } } })
  const everyone = $api.useQuery('get', TASKS, { params: { query: { state: 'open' } } })
  const completed = $api.useQuery('get', TASKS, {
    params: { query: { state: 'completed', assignee: scope === 'mine' ? 'me' : undefined } },
  })
  const forRequest = $api.useQuery(
    'get',
    TASKS,
    { params: { query: { state: 'all', request_id: requestId ?? undefined } } },
    { enabled: Boolean(requestId) },
  )
  const approvals = usePendingApprovals()
  const suggestions = usePendingSuggestions()

  const open = (scope === 'mine' ? mine : everyone).data ?? []
  const counts = dueCounts(open, now, me.timezone)
  const source = requestId ? forRequest : done ? completed : scope === 'mine' ? mine : everyone
  let rows: Task[] = source.data ?? []
  if (requestId) {
    rows = [
      ...byDue(rows.filter((t) => !t.completed_at)),
      ...rows
        .filter((t) => t.completed_at)
        .toSorted((a, b) => (b.completed_at ?? '').localeCompare(a.completed_at ?? '')),
    ]
  } else if (done) {
    rows = rows.toSorted((a, b) => (b.completed_at ?? '').localeCompare(a.completed_at ?? ''))
  } else {
    if (due) rows = rows.filter((t) => dueOf(t.due_at, now, me.timezone).group === due)
    rows =
      order === 'newest'
        ? rows.toSorted((a, b) => b.created_at.localeCompare(a.created_at))
        : byDue(rows)
    // Completed here: they stay on screen, struck, until the page is left (completion in place).
    rows = [...rows, ...kept.filter((k) => !rows.some((t) => t.id === k.id))]
  }
  rows = rows.filter(
    (task) =>
      (!q ||
        [
          taskTitle(task),
          task.description,
          task.tracking_number,
          task.agency_name,
          task.request_subject,
        ]
          .join(' ')
          .toLowerCase()
          .includes(q)) &&
      (!category || task.category === category) &&
      (!agency || task.agency_name === agency),
  )
  const openTask =
    (openId &&
      [...(source.data ?? []), ...kept, ...(mine.data ?? []), ...(everyone.data ?? [])].find(
        (t) => t.id === openId,
      )) ||
    null
  const chosen = rows.filter((t) => selected.has(t.id))
  const agencies = [
    ...new Set([...(everyone.data ?? []), ...(completed.data ?? [])].map((t) => t.agency_name)),
  ].sort()

  const refresh = async () => {
    await Promise.all([
      mine.refetch(),
      everyone.refetch(),
      completed.refetch(),
      requestId ? forRequest.refetch() : null,
    ])
  }

  async function complete(task: Task) {
    setBusy(task.id)
    setFailure(null)
    try {
      const finished = unwrap(
        await api.POST('/api/records/tasks/{task_id}/complete', {
          params: { path: { task_id: task.id } },
        }),
      )
      setKept((was) => [...was.filter((t) => t.id !== task.id), finished])
      await refresh()
    } catch (caught) {
      setFailure(caught instanceof Error ? caught.message : 'It was not completed.')
    } finally {
      setBusy(null)
    }
  }

  async function completeChosen() {
    for (const task of chosen.filter((t) => !t.completed_at)) await complete(task)
    setSelected(new Set())
  }

  const lit = (box: 'overdue' | 'today' | 'week' | 'mine' | 'everyone' | 'done') =>
    !view &&
    !requestId &&
    (box === due || (!due && box === 'done' && done) || (!due && !done && box === scope))

  const strip = (
    <div className="flex shrink-0 gap-2">
      <StatBox
        count={counts.overdue}
        label="Overdue"
        tone="danger"
        active={lit('overdue')}
        onClick={() => set({ due: 'overdue', state: null, view: null, request: null })}
      />
      <StatBox
        count={counts.today}
        label="Due today"
        tone="gold"
        active={lit('today')}
        onClick={() => set({ due: 'today', state: null, view: null, request: null })}
      />
      <StatBox
        count={counts.week}
        label="This week"
        active={lit('week')}
        onClick={() => set({ due: 'week', state: null, view: null, request: null })}
      />
      <StatBox
        count={mine.data?.length}
        label="Mine"
        active={lit('mine')}
        onClick={() => set({ scope: null, due: null, state: null, view: null, request: null })}
      />
      <StatBox
        count={everyone.data?.length}
        label="Everyone"
        active={lit('everyone')}
        onClick={() =>
          set({ scope: 'everyone', due: null, state: null, view: null, request: null })
        }
      />
      <StatBox
        count={completed.data?.length}
        label="Done"
        active={lit('done')}
        onClick={() => set({ state: 'done', due: null, view: null, request: null })}
      />
    </div>
  )

  // P20f's two views, kept reachable: shown while something waits or while one is open.
  const doors = (
    <>
      {approvals.count || view === 'approvals' ? (
        <ToolButton
          active={view === 'approvals'}
          onClick={() => set({ view: view === 'approvals' ? null : 'approvals' })}
        >
          Approvals <span className="text-muted">{approvals.count ?? ''}</span>
        </ToolButton>
      ) : null}
      {suggestions.data?.length || view === 'suggestions' ? (
        <ToolButton
          active={view === 'suggestions'}
          onClick={() => set({ view: view === 'suggestions' ? null : 'suggestions' })}
        >
          Suggestions <span className="text-muted">{suggestions.data?.length ?? ''}</span>
        </ToolButton>
      ) : null}
    </>
  )

  const newButton = (
    <span className="relative ml-2">
      <button
        type="button"
        aria-expanded={showAdd}
        onClick={() => (showAdd ? closeAdd() : setAdding(true))}
        className={cx(
          'inline-flex h-8 items-center rounded-9 bg-brand px-3.5 text-125 font-semibold whitespace-nowrap text-white hover:bg-brand-dark',
          showAdd && 'shadow-ring-focus-strongest',
        )}
      >
        ＋ New task
      </button>
      {showAdd ? (
        <NewTaskPopover
          requestId={requestId}
          onClose={closeAdd}
          onAdded={() => {
            closeAdd()
            void refresh()
          }}
        />
      ) : null}
    </span>
  )

  const toolbar =
    selected.size > 0 ? (
      <div
        role="toolbar"
        aria-label="Actions for the selected tasks"
        className="flex shrink-0 items-center gap-1.5 pt-1"
      >
        <span className="text-13 font-semibold whitespace-nowrap text-ink">
          {selected.size} selected
        </span>
        <button
          type="button"
          onClick={() => setSelected(new Set())}
          className="px-1.5 text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark"
        >
          Clear
        </button>
        <span className="ml-auto flex items-center gap-0.5">
          {people.data ? (
            <BulkButton
              icon={<AssignIcon size={14} width={1.9} stroke="var(--color-ink-3)" />}
              onClick={() => setSpec(reassign(chosen, people.data, null))}
            >
              Assign to…
            </BulkButton>
          ) : null}
          <span aria-hidden className="mx-2 h-[18px] w-px bg-line" />
          <button
            type="button"
            onClick={() => void completeChosen()}
            className="inline-flex h-8 items-center rounded-9 bg-brand px-3.5 text-125 font-semibold whitespace-nowrap text-white hover:bg-brand-dark"
          >
            Mark {chosen.length} done
          </button>
        </span>
      </div>
    ) : (
      <div className="flex shrink-0 items-center gap-2 pt-1">
        <label className="box-border inline-flex h-[34px] w-[300px] items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1 text-13 text-ink-3">
          <SearchIcon size={15} width={1.9} stroke="var(--color-muted)" />
          <input
            ref={search}
            type="search"
            aria-label="Search tasks"
            placeholder="Search tasks"
            defaultValue={get('q')}
            onChange={(event) => set({ q: event.target.value.trim() })}
            onKeyDown={(event) => event.key === 'Escape' && event.currentTarget.blur()}
            className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-ink-3 [&::-webkit-search-cancel-button]:hidden"
          />
          <span className="ml-auto rounded-kbd border border-line px-[5px] py-px text-105 text-muted-2">
            /
          </span>
        </label>
        <ToolButton
          active={filtering || Boolean(category || agency)}
          onClick={() => setFiltering((was) => !was)}
        >
          <FilterIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
          Filter
        </ToolButton>
        {filtering || category || agency ? (
          <>
            <Menu
              label="Kind"
              align="left"
              width={220}
              items={[
                { label: 'Every kind', current: !category, onPick: () => set({ category: null }) },
                ...(definitions.data?.task_categories ?? []).map((value) => ({
                  label: value === 'person' ? 'Added by a person' : humanize(value),
                  current: value === category,
                  onPick: () => set({ category: value }),
                })),
              ]}
            >
              <ToolFace active={Boolean(category)}>
                {category
                  ? category === 'person'
                    ? 'Added by a person'
                    : humanize(category)
                  : 'Kind'}
                <ChevronDown size={12} width={1.9} stroke="var(--color-muted)" />
              </ToolFace>
            </Menu>
            <Menu
              label="Agency"
              align="left"
              width={230}
              panelClassName="max-h-[320px] overflow-y-auto"
              items={[
                { label: 'Every agency', current: !agency, onPick: () => set({ agency: null }) },
                ...agencies.map((name) => ({
                  label: name,
                  current: name === agency,
                  onPick: () => set({ agency: name }),
                })),
              ]}
            >
              <ToolFace active={Boolean(agency)}>
                <span className="max-w-[160px] truncate">{agency || 'Agency'}</span>
                <ChevronDown size={12} width={1.9} stroke="var(--color-muted)" />
              </ToolFace>
            </Menu>
          </>
        ) : null}
        <Menu
          label="Order"
          align="left"
          width={210}
          items={ORDERS.map((o) => ({
            label: o.label,
            current: o.value === order,
            onPick: () => set({ order: o.value === 'due' ? null : o.value }),
          }))}
        >
          <ToolFace>
            <SortIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
            {order === 'newest' ? 'Newest' : 'Due date'}
            <ChevronDown size={12} width={1.9} stroke="var(--color-muted)" />
          </ToolFace>
        </Menu>
        <span className="ml-auto flex gap-2">
          {doors}
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
          {newButton}
        </span>
      </div>
    )

  const rowOf = (task: Task, index: number) => {
    const state: 'open' | 'kept' | 'done' = task.completed_at
      ? done || requestId
        ? 'done'
        : 'kept'
      : 'open'
    const cell = dueCell(task.due_at, now, me.timezone)
    const picked = selected.has(task.id)
    return (
      <div
        key={task.id}
        role="row"
        aria-selected={task.id === openId || picked}
        onClick={() => set({ task: task.id })}
        style={{ gridTemplateColumns: COLUMNS }}
        className={cx(
          'grid cursor-pointer items-center gap-x-3.5 border-b border-transparent px-9 py-[11px] text-125 leading-[normal] last:border-b-0',
          state === 'kept' ? 'text-muted' : 'text-ink',
          task.id === openId
            ? 'bg-brand-tint'
            : picked
              ? 'bg-blue-tint-4'
              : index % 2 === 1
                ? 'bg-row-alt hover:bg-surface-row'
                : 'hover:bg-surface-row',
        )}
      >
        <span className="flex">
          {selecting || selected.size > 0 ? (
            <TaskBox
              state={picked ? 'selected' : 'unselected'}
              label={`Select ${taskTitle(task)} on ${task.tracking_number}`}
              onClick={() =>
                setSelected((was) => {
                  const next = new Set(was)
                  if (next.has(task.id)) next.delete(task.id)
                  else next.add(task.id)
                  return next
                })
              }
            />
          ) : (
            <TaskBox
              state={state}
              busy={busy === task.id}
              disabled={state === 'open' && task.task_type === 'enter_deadline_rules'}
              label={
                state === 'open'
                  ? `Mark ${taskTitle(task)} on ${task.tracking_number} done`
                  : `Reopen ${taskTitle(task)} on ${task.tracking_number}`
              }
              onClick={() => (state === 'open' ? void complete(task) : setSpec(reopen(task)))}
            />
          )}
        </span>
        <TitleCell task={task} done={state === 'open' ? null : state} />
        <span className={cx('min-w-0 truncate', state === 'kept' && 'line-through')}>
          <span className={state === 'kept' ? undefined : 'text-ink-2'}>
            {task.tracking_number}
          </span>
          {task.request_subject ? ` · ${task.request_subject}` : ''}
        </span>
        <span className={cx('min-w-0 truncate', state !== 'kept' && 'text-ink-2')}>
          {task.agency_name}
        </span>
        {state === 'kept' ? (
          <CompletedOn task={task} timeZone={me.timezone} />
        ) : state === 'done' ? (
          <span className="truncate text-12 whitespace-nowrap text-muted">
            {formatShortInstant(task.completed_at, me.timezone)}
            {task.completed_by_id ? ` · ${nameOfOrSomeone(task.completed_by_id)}` : ''}
          </span>
        ) : cell.group === 'overdue' ? (
          <span className="-ml-2 justify-self-start rounded-6 bg-danger-tint px-2 py-0.5 font-semibold text-danger-2">
            {cell.text}
          </span>
        ) : (
          <span className="text-ink-2">{cell.text}</span>
        )}
        <span className="justify-self-end">
          {state === 'open' ? <RowAction task={task} /> : null}
        </span>
      </div>
    )
  }
  const loading = source.isPending
  const body: ReactNode = loading ? (
    Array.from({ length: 4 }, (_, i) => (
      <div
        key={i}
        aria-hidden
        style={{ gridTemplateColumns: COLUMNS, opacity: 1 - i * 0.18 }}
        className="grid items-center gap-x-3.5 border-b border-transparent px-9 py-[15px]"
      >
        <span />
        {['82%', '92%', '76%', '70px', '4px'].map((width, j) => (
          <span key={j} className="block h-[11px] rounded-4 bg-fill-skeleton" style={{ width }} />
        ))}
      </div>
    ))
  ) : source.isError ? (
    <LoadFailed what="the tasks" error={source.error} onRetry={() => void source.refetch()} />
  ) : rows.length === 0 ? (
    <div className="flex flex-col items-center gap-[9px] px-5 py-[52px] leading-[normal]">
      <div className="font-display text-lg font-extrabold text-ink">
        {q || category || agency || due
          ? 'No tasks match'
          : done
            ? 'Nothing done yet'
            : 'Nothing outstanding'}
      </div>
      <div className="max-w-[44ch] text-center text-125 leading-[1.6] text-muted">
        {q || category || agency || due
          ? 'Try a different search or filter.'
          : done
            ? 'Completed tasks appear here.'
            : scope === 'mine'
              ? 'No open task is assigned to you.'
              : 'No open tasks.'}
      </div>
      {q || category || agency || due || done ? null : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="rounded-9 px-[13px] py-1.5 text-115 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
        >
          ＋ New task
        </button>
      )}
    </div>
  ) : order === 'grouped' && !done && !requestId ? (
    DUE_GROUPS.flatMap((group) => {
      const inGroup = rows.filter(
        (t) => !t.completed_at && dueOf(t.due_at, now, me.timezone).group === group.key,
      )
      if (inGroup.length === 0) return []
      return [
        <div
          key={`group-${group.key}`}
          className={cx(
            'flex items-center gap-2 border-b border-fill-skeleton bg-surface-row px-[18px] py-2.5 text-105 leading-[normal] font-semibold tracking-group uppercase',
            group.key === 'overdue' ? 'text-danger-2' : 'text-muted',
          )}
        >
          {group.label}
          <span className="font-medium tracking-normal text-muted-2">{inGroup.length}</span>
        </div>,
        ...inGroup.map((task, index) => rowOf(task, index)),
      ]
    })
  ) : (
    rows.map((task, index) => rowOf(task, index))
  )

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Tasks" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        {view ? (
          <>
            <TaskTabs
              current={view}
              all={everyone.data?.length}
              mine={mine.data?.length}
              approvals={approvals.count}
              suggestions={suggestions.data?.length}
            />
            {view === 'approvals' ? <Approvals /> : <Suggestions />}
          </>
        ) : (
          <>
            {strip}
            {toolbar}
            {failure ? (
              <p role="alert" className="text-12 text-danger">
                {failure}
              </p>
            ) : null}
            <ListCard aria-label="Tasks">
              <ListHead columns={COLUMNS}>
                <span />
                <span>Task</span>
                <span>Request</span>
                <span>Agency</span>
                <span>Due</span>
                <span />
              </ListHead>
              {body}
            </ListCard>
          </>
        )}
      </div>
      {openTask ? (
        <TaskPanel
          key={openTask.id}
          task={openTask}
          now={now}
          onClose={() => set({ task: null })}
          onComplete={(task) => void complete(task)}
          onChanged={refresh}
          onAction={(kind) =>
            setSpec(
              kind === 'reopen'
                ? reopen(openTask)
                : kind === 'edit'
                  ? edit(openTask)
                  : reassign([openTask], people.data ?? [], openTask.assigned_to_user_id),
            )
          }
        />
      ) : null}
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          setSelected(new Set())
          void refresh()
        }}
      />
    </div>
  )
}
