import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api, messageOf } from '../api/query'
import { useMe } from '../app/session'
import { dueNowCount, dueOf, taskTitle, type DueGroup } from '../lib/tasks'
import { unwrap } from './ActionDialog'
import { IconButton } from './IconButton'
import { CheckSquareIcon, PlusIcon } from './icons'
import { cx } from './ui'

type Task = Schemas['TaskOut']

const GROUPS: Array<{ key: DueGroup; label: string }> = [
  { key: 'overdue', label: 'Overdue' },
  { key: 'today', label: 'Today' },
  { key: 'week', label: 'This week' },
  // Not drawn in 016: tasks due after this week, or with no date, so none is hidden.
  { key: 'later', label: 'Later' },
]

/** Every open-task list the portal keeps in its cache, so a tick here updates them all. */
export function useRefreshTasks() {
  const client = useQueryClient()
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ['get', '/api/records/tasks'] }),
      client.invalidateQueries({ queryKey: ['get', '/api/records/home'] }),
    ])
}

/**
 * The top bar's check-square (DECISIONS 32; design 016, 017): a 380 px panel under the icon of
^ * open tasks, Mine or the Team's, grouped Overdue · Today · This week, a tick completing one in
 * place. The icon's dot means something is due today or overdue; the count is in its name.
 */
export function TasksPanel() {
  const me = useMe()
  const panelId = useId()
  const wrapper = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [scope, setScope] = useState<'mine' | 'team'>('mine')
  const navigate = useNavigate()
  const refresh = useRefreshTasks()
  // A new task is added from the Tasks page's popover (183), the one form for it.
  const addTask = () => {
    setOpen(false)
    void navigate('/tasks?new=1')
  }
  const mine = $api.useQuery(
    'get',
    '/api/records/tasks',
    { params: { query: { assignee: 'me', state: 'open' } } },
    { refetchInterval: 60_000 },
  )
  const team = $api.useQuery(
    'get',
    '/api/records/tasks',
    { params: { query: { state: 'open' } } },
    { enabled: open && scope === 'team' },
  )

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (wrapper.current && !wrapper.current.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  // The panel lives as long as the session: "now" is when the list was last read (each minute).
  const now = new Date(Math.max(mine.dataUpdatedAt, team.dataUpdatedAt))
  const dueNow = dueNowCount(mine.data ?? [], now, me.timezone)
  const list = scope === 'mine' ? mine : team
  const tasks = (list.data ?? [])
    .map((task) => ({ task, due: dueOf(task.due_at, now, me.timezone) }))
    .toSorted((a, b) => (a.task.due_at ?? '￿').localeCompare(b.task.due_at ?? '￿'))

  return (
    <div ref={wrapper} className="contents">
      <IconButton
        label={dueNow > 0 ? `Tasks, ${dueNow} due today or overdue` : 'Tasks'}
        open={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
        className={open ? 'bg-fill-subtle-3 hover:bg-fill-subtle-3' : undefined}
      >
        <CheckSquareIcon size={16} stroke={open ? 'var(--color-ink)' : 'var(--color-ink-2)'} />
        {dueNow > 0 ? (
          <span
            aria-hidden
            className="absolute top-[5px] right-[5px] size-[7px] rounded-pill bg-brand shadow-[0_0_0_2px_var(--color-card-white)]"
          />
        ) : null}
      </IconButton>
      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label="Tasks"
          className="fixed top-[52px] right-[108px] z-20 flex w-[382px] flex-col overflow-hidden rounded-14 border border-line bg-card-white leading-[normal] shadow-panel"
        >
          <div className="flex items-center gap-2.5 px-[18px] pt-4 pb-3">
            <span className="font-display text-16 font-extrabold tracking-dialog text-ink">
              Tasks
            </span>
            {tasks.length > 0 ? (
              <span className="text-12 text-muted">{tasks.length} open</span>
            ) : null}
            <Link
              to="/tasks"
              onClick={() => setOpen(false)}
              className="ml-auto text-12 font-medium text-brand hover:text-brand-dark"
            >
              View all
            </Link>
          </div>
          <div
            role="group"
            aria-label="Whose tasks"
            className="mx-[18px] mb-1 inline-flex gap-0.5 self-start rounded-pill bg-page p-[3px]"
          >
            {(['mine', 'team'] as const).map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={scope === key}
                onClick={() => setScope(key)}
                className={cx(
                  'rounded-pill px-3 py-[5px] text-12',
                  scope === key
                    ? 'bg-card-white font-semibold text-ink shadow-xs'
                    : 'font-medium text-ink-2',
                )}
              >
                {key === 'mine' ? 'Mine' : 'Team'}
              </button>
            ))}
          </div>
          {list.isPending ? (
            <p className="px-[18px] py-9 text-center text-125 text-muted">Loading…</p>
          ) : list.isError ? (
            <p role="alert" className="px-[18px] py-9 text-center text-125 text-danger">
              {messageOf(list.error, 'Couldn’t load your tasks.')}
            </p>
          ) : tasks.length === 0 ? (
            <div className="flex flex-col items-center gap-3 px-[18px] pt-9 pb-10">
              <span className="text-14 font-semibold text-ink">No open tasks</span>
              <NewTaskButton onClick={addTask} />
            </div>
          ) : (
            <>
              <div className="max-h-[560px] overflow-y-auto">
                {GROUPS.map((group) => {
                  const rows = tasks.filter((row) => row.due.group === group.key)
                  if (rows.length === 0) return null
                  return (
                    <div key={group.key}>
                      <div className="px-[18px] pt-3 pb-1 text-105 font-semibold tracking-label text-muted uppercase">
                        {group.label}
                      </div>
                      {rows.map(({ task, due }) => (
                        <TaskRow
                          key={task.id}
                          task={task}
                          due={due.label}
                          overdue={due.group === 'overdue'}
                          onOpen={() => setOpen(false)}
                          onDone={refresh}
                        />
                      ))}
                    </div>
                  )
                })}
              </div>
              <div className="px-2 pt-2 pb-2.5">
                <NewTaskButton onClick={addTask} />
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

function NewTaskButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-9 px-2.5 py-2 text-125 font-medium text-ink-2 hover:bg-hover"
    >
      <PlusIcon size={14} width={1.9} stroke="var(--color-ink-2)" />
      New task
    </button>
  )
}

function TaskRow({
  task,
  due,
  overdue,
  onOpen,
  onDone,
}: {
  task: Task
  due: string
  overdue: boolean
  onOpen: () => void
  onDone: () => Promise<unknown>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function complete() {
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.POST('/api/records/tasks/{task_id}/complete', {
          params: { path: { task_id: task.id } },
        }),
      )
      await onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'It was not completed.')
      setBusy(false)
    }
  }
  return (
    <div className="flex items-start gap-[11px] px-[18px] py-[9px]">
      <button
        type="button"
        role="checkbox"
        aria-checked={busy}
        aria-label={`Complete ${taskTitle(task)} on ${task.tracking_number}`}
        disabled={busy}
        onClick={() => void complete()}
        className={cx(
          'mt-px box-border size-4 shrink-0 rounded-pill border-[1.5px] border-line-control',
          busy ? 'bg-line-control' : 'hover:border-brand',
        )}
      />
      <Link to={`/requests/${task.request_id}`} onClick={onOpen} className="min-w-0 flex-1">
        <div className="text-13 leading-ui text-ink">{taskTitle(task)}</div>
        <div className="mt-0.5 truncate text-115 text-muted">
          {task.tracking_number} · {task.agency_name}
        </div>
        {error ? (
          <div role="alert" className="mt-0.5 text-115 text-danger">
            {error}
          </div>
        ) : null}
      </Link>
      {due ? (
        <span
          className={cx(
            'text-115 whitespace-nowrap',
            overdue ? 'font-semibold text-danger' : 'text-muted',
          )}
        >
          {due}
        </span>
      ) : null}
    </div>
  )
}
