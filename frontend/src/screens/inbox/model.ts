/**
 * The Inbox's words and rules (design 106–162), as pure functions so they can be tested: which
 * list a URL names, what a row says, which next step a flagged message offers, the order of a
 * merged list, a search hit's snippet, a thread's earlier messages.
 *
 * Three tabs live in the sidebar under Inbox: New · Flagged · All (as 106 draws them). Sent and
 * Drafts are filters of All (DECISIONS 38): the URL keeps the old `bucket=sent|drafts` so every
 * link already sent out still opens the right list.
 */
import type { Schemas } from '../../api/client'

export type Row = Schemas['InboxRowOut']
export type Message = Schemas['MailMessageOut']
export type RequestEvent = Schemas['EventOut']

export const BUCKETS = ['flagged', 'new', 'all', 'sent', 'drafts'] as const
export type Bucket = (typeof BUCKETS)[number]
export type Tab = 'new' | 'flagged' | 'all'

/** The sidebar's order (106). */
export const TABS: Tab[] = ['new', 'flagged', 'all']

export function parseBucket(value: string | null | undefined): Bucket {
  return (BUCKETS as readonly string[]).includes(value ?? '') ? (value as Bucket) : 'flagged'
}

export const isOutbound = (bucket: Bucket): bucket is 'sent' | 'drafts' =>
  bucket === 'sent' || bucket === 'drafts'

/** The tab a list belongs to: Sent and Drafts are All's. */
export function tabOf(bucket: Bucket): Tab {
  return bucket === 'flagged' || bucket === 'new' ? bucket : 'all'
}

const TITLE: Record<Bucket, string> = {
  flagged: 'Flagged',
  new: 'New',
  all: 'All mail',
  sent: 'Sent',
  drafts: 'Drafts',
}

export function titleOf(bucket: Bucket): string {
  return TITLE[bucket]
}

/** The words under the search line: "4 flagged", "3 new", "48 sent", "1 draft", "126 messages". */
export function countWords(bucket: Bucket, n: number): string {
  if (bucket === 'drafts') return `${n} ${n === 1 ? 'draft' : 'drafts'}`
  if (bucket === 'all') return `${n} ${n === 1 ? 'message' : 'messages'}`
  return `${n} ${bucket}`
}

export function searchPlaceholder(bucket: Bucket): string {
  return bucket === 'all' ? 'Search all mail' : `Search ${TITLE[bucket]}`
}

/* ── Flag reasons (DECISIONS 30i) ───────────────────────────────────────── */

/** Each reason in plain words: `row` in the list (106), `long` in the message's header (129). */
export const FLAG_WORDS: Record<string, { row: string; long: string }> = {
  no_matching_request: { row: 'No matching request', long: 'No matching request' },
  may_cover_others: { row: 'May cover others', long: 'May cover other requests' },
  delivery_failed: { row: 'Delivery failed', long: 'Delivery failed' },
  reply_to_ignored: { row: 'Reply-to ignored', long: 'Reply-to ignored' },
  attachment_too_large: { row: 'Attachment too large', long: 'Attachment too large' },
  assistant: {
    row: 'The assistant thinks this is misfiled',
    long: 'The assistant thinks this is misfiled',
  },
  other: { row: 'Other reason', long: 'Other reason' },
}

export function flagWords(reason: string | null | undefined): { row: string; long: string } {
  return FLAG_WORDS[reason ?? 'other'] ?? FLAG_WORDS.other
}

/** What the primary button of a flagged message does (106, 120, 124, 129). */
export type NextStep = 'attach' | 'attach_others' | 'fix_resend' | 'handled'

export const NEXT_WORDS: Record<NextStep, string> = {
  attach: 'Attach to a request',
  attach_others: 'Attach to other requests',
  fix_resend: 'Fix and resend',
  handled: 'Mark handled',
}

export function nextStep(row: Row): NextStep {
  const reason = row.source.flag_reason
  if (reason === 'delivery_failed') return 'fix_resend'
  if (row.events.length === 0) return 'attach'
  if (reason === 'may_cover_others') return 'attach_others'
  return 'handled'
}

/* ── What a row says ────────────────────────────────────────────────────── */

interface Metadata {
  from?: string
  from_name?: string
  subject?: string
  to?: string
}

const metadataOf = (row: Row): Metadata => (row.source.raw_metadata ?? {}) as Metadata

export function subjectOf(row: Row): string {
  return metadataOf(row).subject?.trim() || '(no subject)'
}

const SOURCE_WORDS: Record<string, string> = {
  scanned_mail: 'Scanned letter',
  portal_capture: 'Portal download',
  imported_eml: 'Imported email',
  postmark_email: 'Email',
  sent_email: 'Sent email',
}

/** Who a message is from: the name it gives, else its address, else what kind of thing it is. */
export function senderOf(row: Row): string {
  const meta = metadataOf(row)
  // A copy of mail we sent (a bounce, 124): whom it went to.
  if (row.source.source_type === 'sent_email' && meta.to) return `To ${meta.to}`
  return (
    meta.from_name?.trim() ||
    meta.from?.trim() ||
    SOURCE_WORDS[row.source.source_type] ||
    'Unknown sender'
  )
}

/** "K. Okafor · UC Irvine PD" (133): the sender, and the agency it landed on. */
export function senderLine(row: Row): string {
  const agency = row.events[0]?.agency_name
  return agency ? `${senderOf(row)} · ${agency}` : senderOf(row)
}

/** The third line of an All row (160): where it stands. */
export function statusLine(row: Row): { text: string; flagged: boolean } {
  const landed = row.events[0]?.tracking_number
  if (row.source.flagged_for_review)
    return { text: `Flagged · ${flagWords(row.source.flag_reason).row}`, flagged: true }
  if (!row.source.reviewed_at && landed) return { text: `New · ${landed}`, flagged: false }
  return { text: landed ?? '', flagged: false }
}

/** A sent message's recipients for its row: "To K. Okafor", names where given. */
export function sentLine(message: Message): string {
  const to = message.recipients.filter((r) => r.kind === 'to')
  if (to.length === 0) return 'To nobody yet'
  return `To ${to.map((r) => r.name || r.address).join(', ')}`
}

/* ── All mail: what arrived and what was sent, in one list ────────────── */

export type AllItem =
  | { kind: 'in'; id: string; at: string; row: Row }
  | { kind: 'out'; id: string; at: string; message: Message }

export function mergeAll(rows: Row[], messages: Message[], oldestFirst = false): AllItem[] {
  const items: AllItem[] = [
    ...rows.map((row) => ({
      kind: 'in' as const,
      id: row.source.id,
      at: row.source.captured_at,
      row,
    })),
    ...messages.map((message) => ({
      kind: 'out' as const,
      id: message.id,
      at: message.sent_at ?? message.updated_at,
      message,
    })),
  ]
  return items.toSorted((a, b) =>
    oldestFirst ? a.at.localeCompare(b.at) : b.at.localeCompare(a.at),
  )
}

/** The message to open after this one leaves the list: the next, else the one before. */
export function neighbour(ids: string[], current: string): string | null {
  const at = ids.indexOf(current)
  if (at === -1) return ids[0] ?? null
  return ids[at + 1] ?? ids[at - 1] ?? null
}

/* ── Search ─────────────────────────────────────────────────────────────── */

/** "…the Flock Safety agreement…" (153): the words around the first hit, cut at word ends. */
export function snippetOf(
  text: string | null | undefined,
  query: string,
  radius = 36,
): { before: string; hit: string; after: string } | null {
  const needle = query.trim()
  if (!text || !needle) return null
  const flat = text.replace(/\s+/g, ' ')
  const at = flat.toLowerCase().indexOf(needle.toLowerCase())
  if (at === -1) return null
  let start = Math.max(0, at - radius)
  let end = Math.min(flat.length, at + needle.length + radius)
  if (start > 0 && flat[start - 1] !== ' ') {
    const cut = flat.indexOf(' ', start)
    if (cut !== -1 && cut < at) start = cut + 1
  }
  if (end < flat.length) {
    const cut = flat.lastIndexOf(' ', end)
    if (cut > at + needle.length) end = cut
  }
  return {
    before: (start > 0 ? '…' : '') + flat.slice(start, at),
    hit: flat.slice(at, at + needle.length),
    after: flat.slice(at + needle.length, end) + (end < flat.length ? '…' : ''),
  }
}

/* ── A thread's earlier messages (110, 136, 142) ────────────────────────── */

/** What is not a message: a note is the team's, not the thread's. */
const NOT_MESSAGES = new Set(['note'])

/** The messages on the same request before this one, oldest first: what a chain row shows. */
export function chainOf(
  events: RequestEvent[],
  sourceId: string,
  occurredAt: string | null | undefined,
): RequestEvent[] {
  return events
    .filter(
      (event) =>
        !NOT_MESSAGES.has(event.event_type) &&
        !event.voided_at &&
        event.source_id !== sourceId &&
        (event.channel === 'email' || event.source_id) &&
        (!occurredAt || event.occurred_at < occurredAt),
    )
    .toSorted((a, b) => a.occurred_at.localeCompare(b.occurred_at))
}

export type ChainPiece = { kind: 'row'; index: number } | { kind: 'more'; indexes: number[] }

/** Three or fewer are all shown (142); more fold to the first, "N more", and the last (136). */
export function foldChain(count: number, expanded: boolean): ChainPiece[] {
  if (expanded || count <= 3)
    return Array.from({ length: count }, (_, index) => ({ kind: 'row', index }))
  return [
    { kind: 'row', index: 0 },
    { kind: 'more', indexes: Array.from({ length: count - 2 }, (_, i) => i + 1) },
    { kind: 'row', index: count - 1 },
  ]
}

/* ── Sort (156) ─────────────────────────────────────────────────────────── */

export const SORTS = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'agency', label: 'By agency' },
  { value: 'request', label: 'By request' },
  { value: 'unreviewed', label: 'New first', separator: true },
] as const
export type Sort = (typeof SORTS)[number]['value']

export function parseSort(value: string | null | undefined): Sort {
  return (SORTS.find((s) => s.value === value)?.value ?? 'newest') as Sort
}

export function sortLabel(sort: Sort): string {
  return SORTS.find((s) => s.value === sort)?.label ?? 'Newest first'
}

/* ── P23b: "Any time" (154) ─────────────────────────────────────────────── */

/** The search's time chip: the spans the Activity page offers (190), or the dates chosen. */
export const TIME_SPANS: ReadonlyArray<{ key: string; label: string; days: number }> = [
  { key: 'today', label: 'Today', days: 0 },
  { key: '7', label: 'Last 7 days', days: 7 },
  { key: '30', label: 'Last 30 days', days: 30 },
  { key: '365', label: 'Last 12 months', days: 365 },
]

/** `YYYY-MM-DD`, `days` before `now`'s own date. */
function civilBefore(days: number, now: Date): string {
  const when = new Date(now)
  when.setDate(when.getDate() - days)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
}

function shortCivil(civil: string): string {
  const [year, month, day] = civil.split('-').map(Number)
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, day)))
}

/**
 * What the chip reads and the dates the reads ask for. `span` is one of `TIME_SPANS`' keys, or
 * `custom` with `since` and `until` (either may be empty); anything else is any time.
 */
export function timeSpan(
  span: string | null,
  since: string | null,
  until: string | null,
  now: Date = new Date(),
): { label: string; since?: string; until?: string } {
  const preset = TIME_SPANS.find((s) => s.key === span)
  if (preset) return { label: preset.label, since: civilBefore(preset.days, now) }
  if (span === 'custom' && (since || until)) {
    const label =
      since && until
        ? since === until
          ? shortCivil(since)
          : `${shortCivil(since)} – ${shortCivil(until)}`
        : since
          ? `Since ${shortCivil(since)}`
          : `Until ${shortCivil(until as string)}`
    return { label, since: since || undefined, until: until || undefined }
  }
  return { label: 'Any time' }
}

/* ── P23b: who moved it, and when (140) ─────────────────────────────────── */

/** "20 seconds ago", "4 min ago", "at 9:12 AM", "on Sep 3": how long since a message moved. */
export function movedWhen(iso: string, timeZone: string, now: Date = new Date()): string {
  const at = new Date(iso)
  const seconds = Math.max(0, Math.round((now.getTime() - at.getTime()) / 1000))
  if (seconds < 60) return `${seconds} ${seconds === 1 ? 'second' : 'seconds'} ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const day = (when: Date) =>
    new Intl.DateTimeFormat('en-CA', { timeZone, dateStyle: 'short' }).format(when)
  if (day(at) === day(now))
    return `at ${new Intl.DateTimeFormat('en-US', { timeZone, hour: 'numeric', minute: '2-digit' }).format(at)}`
  return `on ${new Intl.DateTimeFormat('en-US', { timeZone, month: 'short', day: 'numeric' }).format(at)}`
}

/** "Maya just moved this to Flagged" (140): a person by their first name ("just" within ten
 *  minutes), the reader as "You", a process as itself ("Incoming mail"), or nobody known. */
export function movedTitle(
  by: string | null | undefined,
  at: string | null | undefined,
  me: string,
  now: Date = new Date(),
): string {
  if (!by) return 'This was moved to Flagged'
  const recent = at ? now.getTime() - new Date(at).getTime() < 10 * 60_000 : false
  const process = /^(The |A |Incoming |Outgoing )/.test(by)
  const who = by === me ? 'You' : process ? by : (by.split(' ')[0] ?? by)
  return `${who} ${recent ? 'just ' : ''}moved this to Flagged`
}
