import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { instantOfParts, unwrap } from '../../components/ActionDialog'
import { CheckIcon, CloseIcon, PlusIcon, ReturnIcon } from '../../components/icons'
import { Avatar, cx } from '../../components/ui'
import { civilDayOf, formatDayTime, formatInstant, formatShortDate } from '../../lib/dates'
import { initialsOf } from '../../lib/presence'
import { civilDate, dueCell, historyLines, taskTitle, type HistoryLine } from '../../lib/tasks'
import { useNameOf } from '../../lib/vocab'

type Task = Schemas['TaskOut']

/** What a task's primary button does (177): the work it asks for, where the portal can take you
 *  to it; anything else is marked done here. A rules task is never marked done from a list:
 *  rules live in the rules file, under citation. */
export function primaryOf(task: Task): { label: string; to?: string } | null {
  switch (task.task_type) {
    case 'retry_delivery':
      // P23b: the bounced message itself when the task names it (its Fix and resend).
      return {
        label: 'Retry send',
        to: task.source_id
          ? `/inbox?bucket=flagged&message=${task.source_id}`
          : '/inbox?bucket=flagged',
      }
    case 'confirm_filed':
      return { label: 'Confirm', to: `/requests/${task.request_id}` }
    case 'enter_deadline_rules':
      return { label: 'Enter rules', to: '/settings/rules' }
    default:
      return null
  }
}

const MONTH = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' })

/** `YYYY-MM-DD` plus some days, on the calendar. */
function addDays(civil: string, n: number): string {
  const at = new Date(`${civil}T00:00:00Z`)
  at.setUTCDate(at.getUTCDate() + n)
  return at.toISOString().slice(0, 10)
}

/**
 * The due date's picker (178): a month of days from Sunday, the days of the months around it
 * greyed, the date it was due in red, the chosen one lit; Tomorrow, Next week and No date
 * under it.
 */
function DatePicker({
  value,
  was,
  today,
  onPick,
}: {
  value: string | null
  was: string | null
  today: string
  onPick: (civil: string | null) => void
}) {
  const [month, setMonth] = useState(() => (value ?? today).slice(0, 7))
  const first = new Date(`${month}-01T00:00:00Z`)
  const start = addDays(`${month}-01`, -first.getUTCDay())
  const last = new Date(first)
  last.setUTCMonth(last.getUTCMonth() + 1, 0)
  const weeks = Math.ceil((first.getUTCDay() + last.getUTCDate()) / 7)
  const days = Array.from({ length: weeks * 7 }, (_, i) => addDays(start, i))
  const shift = (n: number) => {
    const at = new Date(`${month}-01T00:00:00Z`)
    at.setUTCMonth(at.getUTCMonth() + n)
    setMonth(at.toISOString().slice(0, 7))
  }
  const quick =
    'flex-1 rounded-6 border border-line py-[5px] text-center text-115 text-ink-2 hover:bg-hover'
  return (
    <div className="absolute top-10 left-24 z-[6] w-[236px] rounded-10 border border-line bg-card-white p-3 shadow-menu">
      <div className="mb-2 flex items-center">
        <span className="flex-1 text-125 font-semibold text-ink">{MONTH.format(first)}</span>
        <span className="text-13 text-ink-3">
          <button type="button" aria-label="Earlier month" onClick={() => shift(-1)}>
            ‹
          </button>
          &nbsp;&nbsp;
          <button type="button" aria-label="Later month" onClick={() => shift(1)}>
            ›
          </button>
        </span>
      </div>
      <div className="mb-1 grid grid-cols-7 gap-0.5 text-center text-105 text-muted">
        {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => (
          <span key={i}>{d}</span>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {days.map((day) => {
          const chosen = day === value
          return (
            <button
              key={day}
              type="button"
              aria-label={formatShortDate(day)}
              aria-pressed={chosen}
              onClick={() => onPick(day)}
              className={cx(
                'inline-flex h-7 w-[30px] items-center justify-center rounded-chip text-12',
                chosen
                  ? 'bg-brand font-semibold text-white'
                  : !day.startsWith(month)
                    ? 'text-line-control hover:bg-hover'
                    : day === was && was < today
                      ? 'text-danger-2 hover:bg-hover'
                      : 'text-ink hover:bg-hover',
              )}
            >
              {Number(day.slice(8))}
            </button>
          )
        })}
      </div>
      <div className="mt-2.5 flex gap-1.5 border-t border-line-row-3 pt-2.5">
        <button type="button" className={quick} onClick={() => onPick(addDays(today, 1))}>
          Tomorrow
        </button>
        <button type="button" className={quick} onClick={() => onPick(addDays(today, 7))}>
          Next week
        </button>
        <button type="button" className={quick} onClick={() => onPick(null)}>
          No date
        </button>
      </div>
    </div>
  )
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="relative grid grid-cols-[86px_1fr] gap-2.5 py-1.5 text-125 leading-[normal]">
      <span className="text-muted">{label}</span>
      <span className="min-w-0 text-ink">{children}</span>
    </div>
  )
}

/**
 * A task open beside the list (177–179): its box and name, who made it; the request, agency, due
 * date and assignee; what needs doing; the one next step; and its history, from the task and its
 * audit lines. The due date is changed in place (178), with the note every edit carries. Notes on
 * the task (P23b, "Write a note…") are written at the foot and sit in the history in their place; a
 * mistaken one is voided with a reason, never deleted.
 */
export function TaskPanel({
  task,
  now,
  onClose,
  onComplete,
  onAction,
  onChanged,
}: {
  task: Task
  now: Date
  onClose: () => void
  onComplete: (task: Task) => void
  /** Reassign, edit or reopen: a dialog the page draws. */
  onAction: (spec: 'reassign' | 'edit' | 'reopen') => void
  onChanged: () => Promise<unknown>
}) {
  const me = useMe()
  const nameOf = useNameOf()
  const navigate = useNavigate()
  const history = $api.useQuery('get', '/api/records/tasks/{task_id}/history', {
    params: { path: { task_id: task.id } },
  })
  const notes = $api.useQuery('get', '/api/records/tasks/{task_id}/notes', {
    params: { path: { task_id: task.id } },
  })
  const [picking, setPicking] = useState(false)
  const [picked, setPicked] = useState<string | null | undefined>(undefined)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    function key(event: KeyboardEvent) {
      // A dialog over the panel takes its own Esc.
      if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return
      if (picking) setPicking(false)
      else onClose()
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [picking, onClose])

  const done = Boolean(task.completed_at)
  const today = civilDate(now, me.timezone)
  const was = task.due_at ? civilDate(new Date(task.due_at), me.timezone) : null
  const due = dueCell(task.due_at, now, me.timezone)
  const assignee = nameOf(task.assigned_to_user_id)
  const primary = primaryOf(task)
  const lines = historyLines(task, history.data ?? [], nameOf, me.timezone, notes.data ?? [])
  const lastDue = (history.data ?? []).filter((e) => e.field === 'due_at').at(-1)
  const editedByMe = lastDue && lastDue.actor_id === me.id
  const short = (iso: string | null) =>
    iso ? formatShortDate(civilDayOf(iso, me.timezone)) : 'no date'

  async function saveDue() {
    if (picked === undefined) return
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.PATCH('/api/records/tasks/{task_id}', {
          params: { path: { task_id: task.id } },
          body: { note: note.trim(), due_at: picked ? instantOfParts(picked, '17:00') : null },
        }),
      )
      setPicked(undefined)
      setNote('')
      await Promise.all([onChanged(), history.refetch()])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The date was not changed.')
    } finally {
      setBusy(false)
    }
  }

  const quiet =
    'box-border inline-flex h-8 items-center rounded-9 px-[15px] text-125 font-medium whitespace-nowrap hover:bg-hover'
  return (
    <aside
      role="dialog"
      aria-label={taskTitle(task)}
      // 440 px inside a 1 px line all round, content-box as the design draws it (442 in all).
      className="fixed inset-y-0 right-0 z-20 box-content flex w-[440px] flex-col border border-line bg-card-white leading-[normal] shadow-menu"
    >
      <div className="flex items-start gap-3 border-b border-hairline px-[22px] pt-[18px] pb-3.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              role="checkbox"
              aria-checked={done}
              aria-label={done ? 'Reopen this task' : 'Mark this task done'}
              disabled={!done && task.task_type === 'enter_deadline_rules'}
              onClick={() => (done ? onAction('reopen') : onComplete(task))}
              className={cx(
                'box-border inline-flex size-4 shrink-0 items-center justify-center rounded-4 border-[1.5px] text-10',
                done
                  ? 'border-green-line bg-ok-tint text-ok'
                  : 'border-line-control hover:border-brand',
              )}
            >
              {done ? '✓' : null}
            </button>
            <span
              className={cx(
                'font-display text-16 font-extrabold',
                done ? 'text-muted line-through' : 'text-ink',
              )}
            >
              {taskTitle(task)}
            </span>
          </div>
          <div className="mt-1.5 pl-[26px] text-12 text-muted">
            {done
              ? `Completed ${formatInstant(task.completed_at, me.timezone)}${
                  task.completed_by_id ? ` by ${nameOf(task.completed_by_id) ?? 'someone'}` : ''
                }`
              : `Created by ${
                  task.created_by_user_id
                    ? (nameOf(task.created_by_user_id) ?? 'someone')
                    : 'the system'
                } · ${short(task.created_at)}`}
          </div>
          {editedByMe && lastDue ? (
            <div className="mt-1 pl-[26px] text-12 text-muted">
              Edited by you · Due date {short(lastDue.old_value)} → {short(lastDue.new_value)}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={onClose}
          className="inline-flex size-7 shrink-0 items-center justify-center rounded-chip hover:bg-hover"
        >
          <CloseIcon size={14} width={2} stroke="var(--color-ink-3)" />
        </button>
      </div>

      <div className="border-b border-hairline px-[22px] py-3.5">
        <Line label="Request">
          <Link
            to={`/requests/${task.request_id}`}
            className="text-ink underline decoration-line-control underline-offset-[3px] hover:text-ink"
          >
            {task.tracking_number}
          </Link>
          {task.request_subject ? ` · ${task.request_subject}` : ''}
        </Line>
        <Line label="Agency">{task.agency_name}</Line>
        <Line label="Due">
          {done ? (
            <span className="text-muted">
              {task.due_at ? `Was due ${short(task.due_at)}` : 'No date'}
            </span>
          ) : picking || picked !== undefined ? (
            <button
              type="button"
              onClick={() => setPicking((open) => !open)}
              className="box-border inline-flex h-[30px] items-center gap-2 rounded-8 border-[1.5px] border-brand px-2.5 text-ink shadow-ring-focus-strong"
            >
              {picked === undefined
                ? was
                  ? formatShortDate(was)
                  : 'No date'
                : picked
                  ? formatShortDate(picked)
                  : 'No date'}
              <CalendarGlyph />
            </button>
          ) : (
            <button
              type="button"
              aria-label="Change the due date"
              onClick={() => setPicking(true)}
              className="text-left"
            >
              {due.group === 'overdue' ? (
                <span className="-ml-2 rounded-6 bg-danger-tint px-2 py-0.5 font-semibold text-danger-2">
                  {due.text}
                </span>
              ) : (
                <span className={due.text ? 'text-ink' : 'text-muted'}>
                  {due.text || 'No date'}
                </span>
              )}
            </button>
          )}
          {picking ? (
            <DatePicker
              value={picked === undefined ? was : picked}
              was={was}
              today={today}
              onPick={(civil) => {
                setPicked(civil)
                setPicking(false)
              }}
            />
          ) : null}
        </Line>
        {picked !== undefined && !picking ? (
          <div className="flex items-center gap-2 py-1.5 pl-24">
            <input
              aria-label="Why the date is changing"
              placeholder="Why (required)"
              value={note}
              autoFocus
              onChange={(event) => setNote(event.target.value)}
              className="box-border h-[30px] min-w-0 flex-1 rounded-8 border-[1.5px] border-line px-2.5 text-125 text-ink outline-none focus:border-brand"
            />
            <button
              type="button"
              disabled={busy || !note.trim()}
              onClick={() => void saveDue()}
              className="inline-flex h-[30px] items-center rounded-8 bg-brand px-3 text-125 font-medium text-white disabled:opacity-55"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setPicked(undefined)
                setNote('')
              }}
              className="inline-flex h-[30px] items-center rounded-8 px-2 text-125 text-ink-2 hover:bg-hover"
            >
              Cancel
            </button>
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="pl-24 text-12 text-danger">
            {error}
          </p>
        ) : null}
        <Line label="Assigned to">
          <span className="inline-flex items-center gap-[7px]">
            {assignee ? <Avatar initials={initialsOf(assignee)} size={20} /> : null}
            {assignee ?? <span className="text-muted">Nobody</span>}
          </span>
          {!done ? (
            <button
              type="button"
              onClick={() => onAction('reassign')}
              className="ml-2.5 text-12 text-brand hover:text-brand-dark"
            >
              Change
            </button>
          ) : null}
        </Line>
      </div>

      {task.description ? (
        <div className="border-b border-hairline px-[22px] py-3.5 text-125 leading-prose whitespace-pre-wrap text-ink-2">
          {task.description}
        </div>
      ) : null}

      <div className="flex gap-2 px-[22px] py-3.5">
        {done ? (
          <button
            type="button"
            onClick={() => onAction('reopen')}
            className={cx(quiet, 'text-ink')}
          >
            Reopen task
          </button>
        ) : (
          <>
            <button
              type="button"
              onClick={() => (primary?.to ? void navigate(primary.to) : onComplete(task))}
              className="box-border inline-flex h-8 items-center gap-1.5 rounded-pill border-[1.5px] border-brand bg-brand px-[15px] text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark"
            >
              {primary ? (
                primary.label
              ) : (
                <>
                  <CheckIcon size={13} width={2.4} stroke="var(--color-card-white)" />
                  Mark done
                </>
              )}
            </button>
            {primary && task.task_type !== 'enter_deadline_rules' ? (
              <button
                type="button"
                onClick={() => onComplete(task)}
                className={cx(quiet, 'text-ink-2')}
              >
                Mark done
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => onAction('reassign')}
              className={cx(quiet, 'text-ink-2')}
            >
              Reassign
            </button>
            {primary ? null : (
              <button
                type="button"
                onClick={() => onAction('edit')}
                className={cx(quiet, 'text-ink-2')}
              >
                Edit
              </button>
            )}
          </>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-[22px] pt-1 pb-3.5">
        <div className="mb-1 text-105 font-semibold tracking-group text-muted uppercase">
          History
        </div>
        {lines.map((line, index) => (
          <HistoryRow
            key={`${line.at}-${index}`}
            line={line}
            when={formatDayTime(line.at, me.timezone)}
            taskId={task.id}
            onVoided={() => void notes.refetch()}
          />
        ))}
      </div>
      <NoteComposer taskId={task.id} onAdded={() => void notes.refetch()} />
    </aside>
  )
}

/** One line of the history (177). A note can be voided, with a reason (P23b): "Void" shows on
 *  hover, the reason is asked in place, and the note stays, struck through, with who and why. */
function HistoryRow({
  line,
  when,
  taskId,
  onVoided,
}: {
  line: HistoryLine
  when: string
  taskId: string
  onVoided: () => void
}) {
  const [voiding, setVoiding] = useState(false)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const note = line.note
  async function voidNote() {
    if (!note) return
    setError(null)
    try {
      unwrap(
        await api.POST('/api/records/tasks/{task_id}/notes/{note_id}/void', {
          params: { path: { task_id: taskId, note_id: note.id } },
          body: { reason: reason.trim() },
        }),
      )
      setVoiding(false)
      onVoided()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The note was not voided.')
    }
  }
  return (
    <div className="group border-t border-line-row-3 py-2 text-12 leading-[normal]">
      <div className="flex gap-2.5">
        <span className={cx('min-w-0 flex-1 text-ink-2', note?.voided && 'line-through')}>
          <span className="text-ink">{line.who}</span> {line.what}
        </span>
        {note && !note.voided && !voiding ? (
          <button
            type="button"
            onClick={() => setVoiding(true)}
            className="hidden text-brand group-focus-within:inline group-hover:inline hover:text-brand-dark"
          >
            Void
          </button>
        ) : null}
        <span className="whitespace-nowrap text-muted">{when}</span>
      </div>
      {note?.voidedWords ? <div className="mt-0.5 text-muted">{note.voidedWords}</div> : null}
      {voiding ? (
        <div className="mt-1.5 flex items-center gap-2">
          <input
            aria-label="Why the note is voided"
            placeholder="Why (required)"
            value={reason}
            autoFocus
            onChange={(event) => setReason(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && reason.trim()) void voidNote()
            }}
            className="box-border h-[28px] min-w-0 flex-1 rounded-8 border-[1.5px] border-line px-2.5 text-12 text-ink outline-none focus:border-brand"
          />
          <button
            type="button"
            disabled={!reason.trim()}
            onClick={() => void voidNote()}
            className="inline-flex h-[28px] items-center rounded-8 bg-brand px-2.5 text-12 font-medium text-white disabled:opacity-55"
          >
            Void
          </button>
          <button
            type="button"
            onClick={() => {
              setVoiding(false)
              setReason('')
            }}
            className="inline-flex h-[28px] items-center rounded-8 px-2 text-12 text-ink-2 hover:bg-hover"
          >
            Cancel
          </button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 text-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}

/** "Write a note…" (177): the 18 px round white field at the panel's foot, + and ↩; Return adds
 *  the note (P23b). */
function NoteComposer({ taskId, onAdded }: { taskId: string; onAdded: () => void }) {
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function add() {
    const text = body.trim()
    if (!text || busy) return
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.POST('/api/records/tasks/{task_id}/notes', {
          params: { path: { task_id: taskId } },
          body: { body: text },
        }),
      )
      setBody('')
      onAdded()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The note was not added.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="px-[22px] py-3">
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void add()
        }}
        className="flex items-center gap-3 rounded-18 bg-card-white px-5 py-3.5 shadow-float"
      >
        <span className="inline-flex size-[22px] shrink-0 items-center justify-center">
          <PlusIcon size={15} width={1.8} stroke="var(--color-ink-2)" />
        </span>
        <input
          aria-label="A note on this task"
          placeholder="Write a note…"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-135 leading-[1.3] text-ink outline-none placeholder:text-text-placeholder"
        />
        <button
          type="submit"
          aria-label="Add the note"
          disabled={busy || !body.trim()}
          className="inline-flex size-[22px] shrink-0 items-center justify-center"
        >
          <ReturnIcon
            size={16}
            width={1.8}
            stroke={body.trim() ? 'var(--color-brand)' : 'var(--color-muted-2)'}
          />
        </button>
      </form>
      {error ? (
        <p role="alert" className="mt-1.5 text-12 text-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}

/** The calendar glyph in the due date's field (178). */
function CalendarGlyph() {
  return (
    <svg
      aria-hidden
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--color-ink-3)"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M16 3v4M8 3v4M3 10h18" />
    </svg>
  )
}
