/** Outbound mail (P11): the small shared pieces of the Inbox's Sent and Drafts, the composer and
 *  the message pane. Pure functions first, hooks after. */
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import type { Schemas } from '../../api/client'
import { $api } from '../../api/query'
import type { Tone } from '../../lib/vocab'

export type Message = Schemas['MailMessageOut']
export type Recipient = Schemas['MailRecipientOut']
export type Address = Schemas['AddressIn']
export type SavedReply = Schemas['SavedReplyOut']

export const OUTBOUND_BUCKETS = ['sent', 'drafts'] as const
export type OutboundBucket = (typeof OUTBOUND_BUCKETS)[number]

/** Sent holds what went and what failed to go; Drafts holds what is being written and what is
 *  waiting out its undo window. Each is two of the API's buckets. */
export const API_BUCKETS: Record<OutboundBucket, ['sent', 'failed'] | ['drafts', 'queued']> = {
  sent: ['sent', 'failed'],
  drafts: ['drafts', 'queued'],
}

export const KIND: Record<string, string> = {
  new: 'New message',
  reply: 'Reply',
  forward: 'Forward',
  filing: 'Filing',
  followup: 'Follow-up',
}

export const DELIVERY: Record<string, { label: string; tone: Tone }> = {
  pending: { label: 'Pending', tone: 'neutral' },
  delivered: { label: 'Delivered', tone: 'ok' },
  bounced: { label: 'Bounced', tone: 'danger' },
  complained: { label: 'Marked as spam', tone: 'danger' },
}

export function statusOf(message: Message): { label: string; tone: Tone } {
  switch (message.status) {
    case 'draft':
      return { label: 'Draft', tone: 'neutral' }
    case 'queued':
      return { label: 'Waiting to send', tone: 'accent' }
    case 'sending':
      return { label: 'Sending', tone: 'accent' }
    case 'sent':
      return { label: 'Sent', tone: 'ok' }
    case 'failed':
      return { label: 'Failed', tone: 'danger' }
    default:
      return { label: message.status, tone: 'neutral' }
  }
}

export const recipientsOf = (message: Message, kind: 'to' | 'cc' | 'bcc'): Recipient[] =>
  message.recipients.filter((r) => r.kind === kind)

export const toAddress = (r: Recipient): Address => ({ address: r.address, name: r.name })

/** `Jane Doe <jane@agency.gov>` -> `Jane Doe`; a bare address stays as it is. */
export function shownAddress(entry: { address: string; name?: string | null }): string {
  return entry.name ? `${entry.name} <${entry.address}>` : entry.address
}

/** The To line of a list row: names where there are any, addresses otherwise. */
export function summarizeTo(message: Message): string {
  const to = recipientsOf(message, 'to')
  if (to.length === 0) return 'Nobody yet'
  return to.map((r) => r.name || r.address).join(', ')
}

const ADDRESS = /^[^\s@<>"]+@[^\s@<>"]+\.[^\s@<>"]+$/

/**
 * Addresses typed or pasted into To, Cc or Bcc: `a@x.gov, "Jane Doe" <jane@x.gov>; bob@x.gov`.
 * Pieces are split on commas, semicolons and line breaks that are not inside quotes or angle
 * brackets. Anything that is not an address comes back in `rejected`, verbatim, so the person
 * can see what was not taken.
 */
export function parseAddresses(text: string): { addresses: Address[]; rejected: string[] } {
  const pieces: string[] = []
  let current = ''
  let quoted = false
  let bracketed = false
  for (const char of text) {
    if (char === '"') quoted = !quoted
    else if (char === '<' && !quoted) bracketed = true
    else if (char === '>' && !quoted) bracketed = false
    if ((char === ',' || char === ';' || char === '\n') && !quoted && !bracketed) {
      pieces.push(current)
      current = ''
      continue
    }
    current += char
  }
  pieces.push(current)

  const addresses: Address[] = []
  const rejected: string[] = []
  const seen = new Set<string>()
  for (const raw of pieces) {
    const piece = raw.trim()
    if (!piece) continue
    const match = /^(.*?)\s*<([^<>]+)>$/.exec(piece)
    const address = (match ? match[2] : piece).trim()
    const name = match
      ? match[1]
          .trim()
          .replace(/^"(.*)"$/, '$1')
          .trim()
      : ''
    if (!ADDRESS.test(address)) {
      rejected.push(piece)
      continue
    }
    const key = address.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    addresses.push(name ? { address, name } : { address })
  }
  return { addresses, rejected }
}

/** Whole seconds until an instant, never negative; `null` when there is no instant. */
export function secondsUntil(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return null
  return Math.max(0, Math.ceil((at - now) / 1000))
}

/** A failed message cannot be edited; "Fix and resend" is a new draft made of the same words. */
export function copyForResend(message: Message): Schemas['MailMessageIn'] {
  const kind = ['new', 'reply', 'forward'].includes(message.kind) ? message.kind : 'new'
  return {
    request_id: message.request_id,
    kind,
    subject: message.subject,
    body_text: message.body_text,
    to: recipientsOf(message, 'to').map(toAddress),
    cc: recipientsOf(message, 'cc').map(toAddress),
    bcc: recipientsOf(message, 'bcc').map(toAddress),
    attachment_ids: message.attachment_ids,
    in_reply_to_source_id: message.in_reply_to_source_id,
    attach_letter: message.attach_letter,
  }
}

// ---- hooks ---------------------------------------------------------------------------------------

/** Whether mail really goes out, and how long Send can be taken back. */
export function useSending() {
  return $api.useQuery('get', '/api/mail/sending', {}, { staleTime: 5 * 60_000 })
}

/** Re-reads every mail list and count, and the Inbox and Home, which show sent copies. */
export function useRefreshMail(): () => Promise<void> {
  const client = useQueryClient()
  return () =>
    client.invalidateQueries({
      predicate: (query) => {
        const path = query.queryKey[1]
        return (
          typeof path === 'string' &&
          (path.startsWith('/api/mail/') ||
            path === '/api/records/inbox' ||
            path === '/api/records/home')
        )
      },
    })
}

/** The time, ticking once a second while `active`; still while not. Read as a number so the
 *  countdown and "Sending…" are worked out from one clock. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  return now
}

/** The rows of Sent or Drafts: the API's two buckets behind each, read together and merged
 *  newest first. */
export function useOutboundRows(
  bucket: OutboundBucket,
  q: string,
  enabled: boolean,
  span: { since?: string; until?: string } = {},
) {
  const [first, second] = API_BUCKETS[bucket]
  // P23b: "Any time" (154) narrows what was sent as it narrows what arrived.
  const when = { since: span.since, until: span.until }
  const a = $api.useQuery(
    'get',
    '/api/mail/messages',
    { params: { query: { bucket: first, q: q || undefined, limit: 100, ...when } } },
    { placeholderData: (previous) => previous, enabled },
  )
  const b = $api.useQuery(
    'get',
    '/api/mail/messages',
    { params: { query: { bucket: second, q: q || undefined, limit: 100, ...when } } },
    { placeholderData: (previous) => previous, enabled },
  )
  const rows = [...(a.data?.items ?? []), ...(b.data?.items ?? [])].sort((x, y) =>
    y.updated_at.localeCompare(x.updated_at),
  )
  return {
    rows,
    total: (a.data?.total ?? 0) + (b.data?.total ?? 0),
    isPending: a.isPending || b.isPending,
    isError: a.isError || b.isError,
    error: a.error ?? b.error,
    refetch: () => Promise.all([a.refetch(), b.refetch()]),
  }
}
