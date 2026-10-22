/**
 * The request page's arithmetic (062–078): the status steps of the right column, how a request
 * item and a task read, how the thread folds, and the timeline's rows. Pure, so it is tested;
 * every word a status is shown in comes from `status.ts`.
 */
import { STEP_WORDS, isPreFiling } from './status'

export interface StatusChangeLike {
  status: string
  effective_at: string
  voided_at: string | null
}

export interface DeadlineLike {
  id: string
  deadline_type: string
  request_item_id: string | null
  due_at: string
  status: string
  basis: string
  legal_basis: string | null
  provision: string | null
}

export interface RequestLike {
  current_status: string
  filed_at: string | null
  agency_reference_number?: string | null
  status_history: StatusChangeLike[]
  deadlines: DeadlineLike[]
}

export type StepState = 'done' | 'current' | 'future'

export interface StepDeadline {
  deadline: DeadlineLike
  /** A second date on file for the same thing (062 "Oct 3"), or one that has passed. */
  tone: 'ink' | 'warn' | 'danger'
}

export interface Step {
  key: string
  label: string
  state: StepState
  /** The muted line under the current step (076 "Pending"). */
  sub?: string
  /** When the step was reached, for its hover (064). */
  at?: string
  /** What else the hover says (064 "Agency ref …"). */
  detail?: string
  deadlines?: StepDeadline[]
  /** Two dates on file for one thing: the pair "Resolve due date" chooses between. */
  conflict?: DeadlineLike[]
}

const DETERMINED = new Set([
  'determination_issued',
  'denied',
  'no_records_claimed',
  'new_production',
  'production_unreviewed',
  'partial_production',
  'production_received',
])
const PRODUCING = new Set(['new_production', 'production_unreviewed', 'partial_production'])
const TERMINAL = new Set(['closed', 'withdrawn', 'denied'])

/** Which deadline kinds belong to which step on now. Fee and other dates go with any. */
const STAGE_KINDS: Record<string, string[]> = {
  determination: ['statutory_determination'],
  production: ['production'],
  appeal: ['challenge_response'],
}
const ANY_STAGE = ['fee_response', 'other']

/** Active and passed deadlines of these kinds, by date; a repeat of a kind and item is a second
 *  date on file and reads in warn; a passed one reads in danger. */
export function stepDeadlines(deadlines: DeadlineLike[], kinds: string[]): StepDeadline[] {
  const rows = deadlines
    .filter(
      (d) => (d.status === 'active' || d.status === 'missed') && kinds.includes(d.deadline_type),
    )
    .toSorted((a, b) => a.due_at.localeCompare(b.due_at))
  const seen = new Set<string>()
  return rows.map((deadline) => {
    const key = `${deadline.deadline_type}:${deadline.request_item_id ?? ''}`
    const repeat = deadline.status === 'active' && seen.has(key)
    if (deadline.status === 'active') seen.add(key)
    return {
      deadline,
      tone: deadline.status === 'missed' ? 'danger' : repeat ? 'warn' : 'ink',
    }
  })
}

/** The first two active deadlines of one kind and item, when there are two: what "Resolve due
 *  date" (073) chooses between. */
export function conflictingPair(deadlines: DeadlineLike[]): DeadlineLike[] | null {
  const groups = new Map<string, DeadlineLike[]>()
  for (const deadline of deadlines.filter((d) => d.status === 'active')) {
    const key = `${deadline.deadline_type}:${deadline.request_item_id ?? ''}`
    groups.set(key, [...(groups.get(key) ?? []), deadline])
  }
  for (const group of groups.values()) {
    if (group.length > 1)
      return group.toSorted((a, b) => a.due_at.localeCompare(b.due_at)).slice(0, 2)
  }
  return null
}

/**
 * The status steps (062, 076, 077, 078): the milestones reached, each with when; the step the
 * request is on now with the dates it is waiting on; the milestones still ahead, ending in
 * Closed. Acknowledged is shown only once it has happened, or ahead of a request not yet filed
 * (077 leaves it out, 076 draws it).
 */
export function statusSteps(request: RequestLike, extra: { filedBy?: string | null } = {}): Step[] {
  const history = request.status_history
    .filter((change) => !change.voided_at)
    .toSorted((a, b) => a.effective_at.localeCompare(b.effective_at))
  const first = (codes: Set<string> | string[]) => {
    const set = codes instanceof Set ? codes : new Set(codes)
    return history.find((change) => set.has(change.status))
  }
  const current = request.current_status
  const pending = isPreFiling(current)
  const terminal = TERMINAL.has(current)
  const steps: Step[] = []

  // Filing.
  if (pending) {
    steps.push({ key: 'filing', label: STEP_WORDS.filing, state: 'current', sub: 'Pending' })
  } else {
    steps.push({
      key: 'filing',
      label: STEP_WORDS.filed,
      state: 'done',
      at: request.filed_at ?? first(['filed'])?.effective_at,
      detail: extra.filedBy ? `By ${extra.filedBy}` : undefined,
    })
  }

  // Acknowledged.
  const acknowledged = first(['acknowledged'])
  if (acknowledged) {
    steps.push({
      key: 'acknowledged',
      label: STEP_WORDS.acknowledged,
      state: 'done',
      at: acknowledged.effective_at,
      detail: request.agency_reference_number
        ? `Agency ref ${request.agency_reference_number}`
        : undefined,
    })
  } else if (pending) {
    steps.push({ key: 'acknowledged', label: STEP_WORDS.acknowledged, state: 'future' })
  }

  const due = (key: string, milestone: string): Step => {
    const rows = stepDeadlines(request.deadlines, [...STAGE_KINDS[key], ...ANY_STAGE])
    const pair = conflictingPair(
      request.deadlines.filter((d) => rows.some((r) => r.deadline.id === d.id)),
    )
    return {
      key,
      label: STEP_WORDS.due(milestone),
      state: 'current',
      deadlines: rows,
      conflict: pair ?? undefined,
    }
  }

  // Determination.
  const determined = first(DETERMINED)
  const deniedOrNone =
    determined?.status === 'denied' || determined?.status === 'no_records_claimed'
  if (determined) {
    steps.push({
      key: 'determination',
      label:
        determined.status === 'denied'
          ? STEP_WORDS.denied
          : determined.status === 'no_records_claimed'
            ? STEP_WORDS.noRecords
            : STEP_WORDS.determination,
      state: 'done',
      at: determined.effective_at,
    })
  } else if (pending) {
    steps.push({ key: 'determination', label: STEP_WORDS.determination, state: 'future' })
  } else if (!terminal) {
    steps.push(due('determination', STEP_WORDS.determination))
  }

  // Appeal: shown once one has been filed, after the determination it answers.
  const appealed = first(['appealed'])
  if (appealed) {
    steps.push({
      key: 'appeal',
      label: STEP_WORDS.appealed,
      state: 'done',
      at: appealed.effective_at,
    })
  }

  // Production.
  const received = first(['production_received'])
  const partial = first(PRODUCING)
  const appealedNow = current === 'appealed'
  if (received) {
    steps.push({
      key: 'production',
      label: STEP_WORDS.production,
      state: 'done',
      at: received.effective_at,
    })
  } else {
    if (partial) {
      steps.push({
        key: 'partial',
        label: STEP_WORDS.partialProduction,
        state: 'done',
        at: partial.effective_at,
      })
    }
    if (pending || (!determined && !terminal)) {
      // Production dates already on file (an agency's commitment) wait under the step ahead.
      const ahead = stepDeadlines(request.deadlines, STAGE_KINDS.production).filter(
        (row) => row.deadline.status === 'active',
      )
      const pair = conflictingPair(ahead.map((row) => row.deadline))
      steps.push({
        key: 'production',
        label: STEP_WORDS.production,
        state: 'future',
        deadlines: ahead.length > 0 ? ahead : undefined,
        conflict: pair ?? undefined,
      })
    } else if (determined && !deniedOrNone && !terminal && !appealedNow) {
      steps.push(due('production', STEP_WORDS.production))
    }
  }

  if (appealedNow) {
    const step = due('appeal', 'Response')
    steps.push(step)
  }

  // Closed.
  if (current === 'closed' || current === 'withdrawn') {
    const closed = first([current])
    steps.push({
      key: 'closed',
      label: current === 'withdrawn' ? STEP_WORDS.withdrawn : STEP_WORDS.closed,
      state: 'done',
      at: closed?.effective_at,
    })
  } else {
    steps.push({ key: 'closed', label: STEP_WORDS.closed, state: 'future' })
  }
  return steps
}

/** How a request item reads (062): its icon, and whether it can be answered. */
export function itemState(
  disposition: string | null | undefined,
): 'produced' | 'withheld' | 'open' {
  if (disposition === 'produced') return 'produced'
  if (disposition === 'withheld' || disposition === 'partly') return 'withheld'
  return 'open'
}

/** A task's due date (062): "Today" and anything past in red, otherwise the date, muted. */
export function taskDue(
  dueDay: string | null,
  today: string,
): { text: string | null; late: boolean; today: boolean } {
  if (!dueDay) return { text: null, late: false, today: false }
  if (dueDay === today) return { text: 'Today', late: true, today: true }
  return { text: null, late: dueDay < today, today: false }
}

/** How the thread folds (062): the newest message open, the one before it and the first one as
 *  collapsed rows, and everything between them behind "N more". */
export type ThreadPiece = { kind: 'message'; index: number } | { kind: 'more'; indexes: number[] }

export function foldThread(count: number, expanded: boolean): ThreadPiece[] {
  if (expanded || count <= 3)
    return Array.from({ length: count }, (_, index) => ({ kind: 'message', index }))
  const middle = Array.from({ length: count - 3 }, (_, i) => i + 1)
  return [
    { kind: 'message', index: 0 },
    { kind: 'more', indexes: middle },
    { kind: 'message', index: count - 2 },
    { kind: 'message', index: count - 1 },
  ]
}

/** A file's tag (062, 067): its extension in capitals, "FILE" when it has none. */
export function fileTag(filename: string): string {
  const match = /\.([a-z0-9]{1,5})$/i.exec(filename)
  return match ? match[1].toUpperCase() : 'FILE'
}

/** "34 pages · 1.2 MB" (067). */
export function fileMeta(pages: number | null | undefined, size: string): string {
  const parts = [pages ? `${pages} ${pages === 1 ? 'page' : 'pages'}` : '', size].filter(Boolean)
  return parts.join(' · ')
}

/** The sender's display name from an address line: `L. Alvarez <l@x.org>` -> `L. Alvarez`. */
export function senderName(address: string): { name: string; email: string } {
  const match = /^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/.exec(address)
  if (match) return { name: match[1].trim() || match[2], email: match[2] }
  return { name: address.trim(), email: '' }
}
