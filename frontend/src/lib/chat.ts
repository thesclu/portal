/**
 * Team chat's pure helpers (P22): the times and day lines the panel draws (018–020), who's online
 * in words (021, DECISIONS 33c: the server has already cut a request down to "A request" for
 * anyone but an administrator), @mentions in the composer, merging live messages into a page, and
 * what a browser notification may say (the sender and the conversation, never the words).
 */

export interface ChatPerson {
  id: string
  name: string
  initials: string
}

export interface ChatMessageLike {
  id: string
  created_at: string
  changed_at: string
}

export interface ChatChannelLike {
  id: string
  kind: string
  name: string
  people: ChatPerson[]
}

export interface SeenLike {
  state: string
  where: string
  on_request: boolean
  request_label?: string | null
  minutes_since: number
}

const dayKeyFormats = new Map<string, Intl.DateTimeFormat>()

/** The reader's calendar date of an instant, as `2026-10-17`. An unknown zone reads as UTC. */
export function dayKey(iso: string, timeZone: string): string {
  let format = dayKeyFormats.get(timeZone)
  if (!format) {
    try {
      format = new Intl.DateTimeFormat('en-CA', {
        timeZone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      })
    } catch {
      format = new Intl.DateTimeFormat('en-CA', { timeZone: 'UTC' })
    }
    dayKeyFormats.set(timeZone, format)
  }
  return format.format(new Date(iso))
}

function daysBetween(earlier: string, later: string): number {
  const [a, b] = [earlier, later].map((key) => {
    const [y, m, d] = key.split('-').map(Number)
    return Date.UTC(y!, (m ?? 1) - 1, d ?? 1)
  })
  return Math.round((b! - a!) / 86_400_000)
}

function formatIn(iso: string, timeZone: string, options: Intl.DateTimeFormatOptions): string {
  try {
    return new Intl.DateTimeFormat('en-US', { timeZone, ...options }).format(new Date(iso))
  } catch {
    return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', ...options }).format(new Date(iso))
  }
}

/** A message's time: "9:12 AM". */
export function clockTime(iso: string, timeZone: string): string {
  return formatIn(iso, timeZone, { hour: 'numeric', minute: '2-digit' })
}

/** The conversation list's time (018): today's clock, "Yesterday", the weekday within the week,
 *  then the date. */
export function listTime(iso: string, timeZone: string, now: Date = new Date()): string {
  const gap = daysBetween(dayKey(iso, timeZone), dayKey(now.toISOString(), timeZone))
  if (gap <= 0) return clockTime(iso, timeZone)
  if (gap === 1) return 'Yesterday'
  if (gap < 7) return formatIn(iso, timeZone, { weekday: 'short' })
  return formatIn(iso, timeZone, { month: 'short', day: 'numeric' })
}

/** The line between days in a conversation (019): "Today", "Yesterday", else "Mon, Oct 12"
 *  (with the year when it is not this year's). */
export function dayLabel(iso: string, timeZone: string, now: Date = new Date()): string {
  const key = dayKey(iso, timeZone)
  const today = dayKey(now.toISOString(), timeZone)
  const gap = daysBetween(key, today)
  if (gap <= 0) return 'Today'
  if (gap === 1) return 'Yesterday'
  const sameYear = key.slice(0, 4) === today.slice(0, 4)
  return formatIn(iso, timeZone, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}

/** Messages in days, oldest first, each day under its label. */
export function byDay<T extends ChatMessageLike>(
  messages: readonly T[],
  timeZone: string,
  now: Date = new Date(),
): Array<{ key: string; label: string; messages: T[] }> {
  const out: Array<{ key: string; label: string; messages: T[] }> = []
  for (const message of messages) {
    const key = dayKey(message.created_at, timeZone)
    const last = out[out.length - 1]
    if (last && last.key === key) last.messages.push(message)
    else out.push({ key, label: dayLabel(message.created_at, timeZone, now), messages: [message] })
  }
  return out
}

/** A page with live messages folded in: a newer copy of a message replaces the one held (an edit,
 *  a deletion), a new one is added, and the page stays oldest first. */
export function mergeMessages<T extends ChatMessageLike>(
  held: readonly T[],
  incoming: readonly T[],
) {
  const byId = new Map(held.map((message) => [message.id, message]))
  for (const message of incoming) {
    const known = byId.get(message.id)
    if (!known || known.changed_at <= message.changed_at) byId.set(message.id, message)
  }
  return [...byId.values()].toSorted((a, b) =>
    a.created_at === b.created_at
      ? a.id.localeCompare(b.id)
      : a.created_at.localeCompare(b.created_at),
  )
}

/** "2 online · 1 away" (018, 021); "No one else online" when nobody (022). */
export function onlineSummary(online: number, away: number): string {
  if (online === 0 && away === 0) return 'No one else online'
  const parts = []
  if (online > 0) parts.push(`${online} online`)
  if (away > 0) parts.push(`${away} away`)
  return parts.join(' · ')
}

/** The line under a person in who's online (021): where they are, or how long they have been
 *  away. */
export function seenLine(person: SeenLike): string {
  if (person.state === 'away') return `Away ${Math.max(1, person.minutes_since)} min`
  return person.where
}

/** The line under a direct message's name (020): "Viewing REQ-…" for an administrator, "Viewing
 *  a request" for anyone else, the destination otherwise, or "Away 14 min". */
export function viewingLine(person: SeenLike | undefined): { lead: string; label: string } | null {
  if (!person) return null
  if (person.state === 'away') return { lead: seenLine(person), label: '' }
  if (person.request_label) return { lead: 'Viewing ', label: person.request_label }
  if (person.on_request) return { lead: 'Viewing a request', label: '' }
  return person.where ? { lead: person.where, label: '' } : null
}

export function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] ?? name
}

/** What a conversation is called: "# name" for a channel, its people for a direct message. */
export function conversationName(channel: ChatChannelLike): string {
  if (channel.kind !== 'dm') return channel.name
  if (channel.people.length === 0) return 'Just you'
  return channel.people.map((person) => person.name).join(', ')
}

/** The composer's placeholder (019, 020): "Message #name", or "Message <first name>". */
export function placeholderFor(channel: ChatChannelLike): string {
  if (channel.kind !== 'dm') return `Message #${channel.name}`
  if (channel.people.length === 1) return `Message ${firstName(channel.people[0]!.name)}`
  return 'Message the group'
}

/** "6 members", "1 member". */
export function memberCount(count: number): string {
  return count === 1 ? '1 member' : `${count} members`
}

/** A browser notification (owner, 2026-10-17): the sender and the conversation, never the
 *  message's words. */
export function notificationFor(
  sender: string,
  channel: ChatChannelLike | undefined,
): { title: string; body: string } {
  if (!channel || channel.kind === 'dm') return { title: sender, body: 'Direct message' }
  return { title: sender, body: `in #${channel.name}` }
}

/** The conversation a notification's link opens (`/?chat=<id>`), or null for any other link. */
export function chatLinkTarget(link: string): string | null {
  try {
    const url = new URL(link, 'http://portal.invalid')
    return url.searchParams.get('chat')
  } catch {
    return null
  }
}

/** The name being typed after an "@" just before the caret, or null when the caret is not in one.
 *  Up to two words, so "@Seed St" still finds "Seed Staff". */
export function mentionQuery(text: string, caret: number): string | null {
  const before = text.slice(0, caret)
  const match = /(?:^|[\s(])@([\p{L}][\p{L}.'-]*(?: [\p{L}][\p{L}.'-]*)?)?$/u.exec(before)
  if (!match) return null
  return match[1] ?? ''
}

/** The text with the "@partial" before the caret replaced by "@Full Name ", and where the caret
 *  goes after it. */
export function insertMention(
  text: string,
  caret: number,
  name: string,
): { text: string; caret: number } {
  const before = text.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at === -1) return { text, caret }
  const inserted = `@${name} `
  return {
    text: text.slice(0, at) + inserted + text.slice(caret),
    caret: at + inserted.length,
  }
}

/** The people whose name matches what is typed after "@", first names first. */
export function matchPeople<T extends { name: string }>(people: readonly T[], query: string): T[] {
  const q = query.trim().toLowerCase()
  if (!q) return people.slice(0, 6)
  const starts = people.filter((p) =>
    p.name
      .toLowerCase()
      .split(/\s+/)
      .some((word) => word.startsWith(q)),
  )
  const whole = people.filter((p) => p.name.toLowerCase().startsWith(q))
  return [...new Set([...whole, ...starts])].slice(0, 6)
}

/** The colours the design gives people's initials (018: green, orange, purple; the brand blue
 *  besides), one per person, the same everywhere. */
export const AVATAR_COLOURS = [
  'var(--color-avatar-green)',
  'var(--color-avatar-orange)',
  'var(--color-avatar-purple)',
  'var(--color-brand)',
] as const

export function avatarColour(id: string): string {
  let hash = 0
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return AVATAR_COLOURS[hash % AVATAR_COLOURS.length]!
}

/** "12 KB", "3.4 MB". */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  const mb = bytes / (1024 * 1024)
  return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} MB`
}
