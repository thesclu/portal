/**
 * How a task reads in the short lists (the top bar's Tasks panel, 016; Home's "Assigned to you",
 * 013): its name, and where its due date falls for the reader. Pure, so it is tested.
 */
import { useState } from 'react'

import { humanize } from './vocab'

export interface TaskLike {
  task_type: string
  description: string
  due_at: string | null
}

/** The task types whose words are not their code made legible (016 draws these two). */
const TASK_LABELS: Record<string, string> = {
  manual_followup: 'Manual follow-up',
  review_no_records_claim: 'Review no-records claim',
}

/** "Review no-records claim": a system task by its type; a person's own task by its first line. */
export function taskTitle(task: TaskLike): string {
  if (task.task_type === 'manual') return task.description.split('\n')[0] || humanize('manual')
  return TASK_LABELS[task.task_type] ?? humanize(task.task_type)
}

/** The civil date an instant falls on in a zone, as YYYY-MM-DD. */
export function civilDate(at: Date, timeZone: string): string {
  const make = (zone: string) =>
    new Intl.DateTimeFormat('en-CA', {
      timeZone: zone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(at)
  try {
    return make(timeZone)
  } catch {
    return make('UTC')
  }
}

const day = (civil: string) => Date.parse(`${civil}T00:00:00Z`) / 86_400_000

/** Whole days from one civil date to another (positive when `to` is later). */
export function daysBetween(from: string, to: string): number {
  return Math.round(day(to) - day(from))
}

export type DueGroup = 'overdue' | 'today' | 'week' | 'later'

export interface Due {
  group: DueGroup
  /** "+3d", "Today", "Thu", "Oct 30", or "" for no date. */
  label: string
}

const weekday = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: 'UTC' })
const monthDay = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})

/**
 * Where a due instant falls for a reader in `timeZone` on `now`: overdue (by whole days), today,
 * later this week (to Saturday), or later. A task with no due date is "later" with no label.
 */
export function dueOf(dueAt: string | null, now: Date, timeZone: string): Due {
  if (!dueAt) return { group: 'later', label: '' }
  const at = new Date(dueAt)
  if (Number.isNaN(at.getTime())) return { group: 'later', label: '' }
  const today = civilDate(now, timeZone)
  const due = civilDate(at, timeZone)
  const days = daysBetween(today, due)
  const asDate = new Date(`${due}T00:00:00Z`)
  if (days < 0) return { group: 'overdue', label: `+${-days}d` }
  if (days === 0) {
    // Due earlier today is already late; it still reads as today's.
    return { group: 'today', label: 'Today' }
  }
  const toSaturday = 6 - new Date(`${today}T00:00:00Z`).getUTCDay()
  if (days <= toSaturday) return { group: 'week', label: weekday.format(asDate) }
  return { group: 'later', label: monthDay.format(asDate) }
}

/** The badge on the top bar's Tasks icon: how many are due today or overdue (DECISIONS 32). */
export function dueNowCount(tasks: TaskLike[], now: Date, timeZone: string): number {
  return tasks.filter((t) => {
    const group = dueOf(t.due_at, now, timeZone).group
    return group === 'overdue' || group === 'today'
  }).length
}

/** The moment the screen first rendered: "today" for a page stays the same while it is open. */
export function useNow(): Date {
  const [now] = useState(() => new Date())
  return now
}

/* ── The Tasks page (P20e, 173–185) ─────────────────────────────────────────── */

/** The full task the page reads: what the short lists need, and the rest of TaskOut. */
export interface TaskRowLike extends TaskLike {
  id: string
  completed_at: string | null
  created_at: string
}

/**
 * The Due cell (173): "Sep 5 · +4d" overdue (the only colour in the row), "Today", "Sep 9", or
 * nothing when the task has no date.
 */
export function dueCell(dueAt: string | null, now: Date, timeZone: string): Due & { text: string } {
  const due = dueOf(dueAt, now, timeZone)
  if (!dueAt || !due.label) return { ...due, text: '' }
  if (due.group === 'today') return { ...due, text: 'Today' }
  const asDate = new Date(`${civilDate(new Date(dueAt), timeZone)}T00:00:00Z`)
  const date = monthDay.format(asDate)
  return { ...due, text: due.group === 'overdue' ? `${date} · ${due.label}` : date }
}

/** Soonest first; no date last; the older task first between equals. */
export function byDue<T extends TaskRowLike>(tasks: T[]): T[] {
  return tasks.toSorted(
    (a, b) =>
      (a.due_at ?? '￿').localeCompare(b.due_at ?? '￿') || a.created_at.localeCompare(b.created_at),
  )
}

export type DueFilter = 'overdue' | 'today' | 'week'

/** The strip's three due boxes (173): open tasks overdue, due today, due later this week. */
export function dueCounts(
  tasks: TaskLike[],
  now: Date,
  timeZone: string,
): Record<DueFilter, number> {
  const counts = { overdue: 0, today: 0, week: 0 }
  for (const task of tasks) {
    const group = dueOf(task.due_at, now, timeZone).group
    if (group !== 'later') counts[group] += 1
  }
  return counts
}

/** The headings of the grouped list (174). */
export const DUE_GROUPS: Array<{ key: DueGroup; label: string }> = [
  { key: 'overdue', label: 'Overdue' },
  { key: 'today', label: 'Due today' },
  { key: 'week', label: 'This week' },
  { key: 'later', label: 'Later' },
]

export interface HistoryEntry {
  at: string
  field: string
  old_value: string | null
  new_value: string | null
  note: string
  actor_name: string | null
}

export interface HistoryLine {
  at: string
  who: string
  what: string
  /** P23b: a note on the task ("noted: “…”"), which can be voided, never deleted. */
  note?: { id: string; voided: boolean; voidedWords: string | null }
}

/** A note on a task as the API gives it (`TaskNoteOut`). */
export interface TaskNoteLike {
  id: string
  author_name: string
  body: string
  created_at: string
  voided_at: string | null
  voided_by_name: string | null
  void_reason: string | null
}

/**
 * A task's history (177) in plain words, oldest first: who made it, each audited change, and who
 * completed it. A note a person wrote is quoted; the portal's own notes ("Assigned to …") are
 * not, since the line already says it.
 */
export function historyLines(
  task: TaskRowLike & {
    created_by_system?: string | null
    created_by_user_id?: string | null
    completed_by_id?: string | null
  },
  entries: HistoryEntry[],
  nameOf: (id: string | null | undefined) => string | null,
  timeZone: string,
  notes: TaskNoteLike[] = [],
): HistoryLine[] {
  const lines: HistoryLine[] = [
    {
      at: task.created_at,
      who: task.created_by_user_id ? (nameOf(task.created_by_user_id) ?? 'Someone') : 'System',
      what: 'created this task',
    },
  ]
  for (const entry of entries) {
    const who = entry.actor_name ?? 'Someone'
    const quoted = entry.note ? ` · “${entry.note}”` : ''
    if (entry.field === 'assigned_to_user_id') {
      const to = nameOf(entry.new_value)
      lines.push({ at: entry.at, who, what: to ? `reassigned it to ${to}` : 'unassigned it' })
    } else if (entry.field === 'due_at') {
      const date = entry.new_value
        ? monthDay.format(new Date(`${civilDate(new Date(entry.new_value), timeZone)}T00:00:00Z`))
        : null
      lines.push({
        at: entry.at,
        who,
        what: (date ? `moved the due date to ${date}` : 'removed the due date') + quoted,
      })
    } else if (entry.field === 'description') {
      lines.push({ at: entry.at, who, what: `edited what needs doing${quoted}` })
    } else if (entry.field === 'completed_at') {
      lines.push({ at: entry.at, who, what: `reopened it${quoted}` })
    }
  }
  if (task.completed_at) {
    lines.push({
      at: task.completed_at,
      who: nameOf(task.completed_by_id) ?? 'Someone',
      what: 'completed it',
    })
  }
  // P23b: the notes people wrote on it (177 "Dana Nolan noted: “…”"), a voided one kept and
  // said to be voided, by whom and why.
  for (const note of notes) {
    lines.push({
      at: note.created_at,
      who: note.author_name,
      what: `noted: “${note.body}”`,
      note: {
        id: note.id,
        voided: Boolean(note.voided_at),
        voidedWords: note.voided_at
          ? `Voided by ${note.voided_by_name ?? 'someone'}${note.void_reason ? ` · ${note.void_reason}` : ''}`
          : null,
      },
    })
  }
  return lines.toSorted((a, b) => a.at.localeCompare(b.at))
}
