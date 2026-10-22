/**
 * The sums and labels of the new-request stages (design 1e and 2a), kept pure so they can be
 * tested: how a recipient files, where its letter goes, what a batch will create, and what each
 * one's result reads as afterwards. No legal content and no day counts live here.
 */
import type { Schemas } from '../../../api/client'

export type Recipient = Schemas['RecipientOut']
export type Agency = Schemas['AgencyOut']
type Template = Schemas['TemplateOut']

export const STEPS = [
  { key: 'start', label: 'Start' },
  { key: 'draft', label: 'Draft' },
  { key: 'agencies', label: 'Agencies' },
  { key: 'file', label: 'Review & file' },
] as const
export type StepKey = (typeof STEPS)[number]['key']

export const CHANNELS = [
  { value: 'email', label: 'Email' },
  { value: 'portal', label: 'Portal' },
  { value: 'mail', label: 'Postal mail' },
] as const

/** "Email", "GovQA portal", "Web form", "Postal mail": the stage-3 "Files by" column. */
export function filesBy(channel: string, agency?: Pick<Agency, 'portal_kind'> | null): string {
  if (channel === 'email') return 'Email'
  if (channel === 'mail') return 'Postal mail'
  const kind = agency?.portal_kind
  if (!kind) return 'Portal'
  return /portal|form/i.test(kind) ? kind : `${kind} portal`
}

/** The short form beside an agency in a list: "Email", "GovQA", "Web form", "Postal mail". */
export function howLabel(channel: string, agency?: Pick<Agency, 'portal_kind'> | null): string {
  if (channel === 'portal') return agency?.portal_kind || 'Portal'
  return filesBy(channel, agency)
}

/** `https://ucdavis.govqa.us/WEBAPP/` -> `ucdavis.govqa.us/WEBAPP`: an address as a person reads it. */
export function hostOf(url: string): string {
  return url
    .replace(/^[a-z]+:\/\//i, '')
    .replace(/^www\./i, '')
    .replace(/\/+$/, '')
}

type Address = Partial<
  Pick<Agency, 'default_request_email' | 'portal_url' | 'mailing_street' | 'mailing_city_state_zip'>
>

/** Where the letter goes, by channel: the address, the portal, or the street. */
export function sendsTo(channel: string, agency?: Address | null): string {
  if (channel === 'email') return agency?.default_request_email || 'No address set'
  if (channel === 'portal') return agency?.portal_url ? hostOf(agency.portal_url) : 'No portal set'
  const street = [agency?.mailing_street, agency?.mailing_city_state_zip].filter(Boolean)
  return street.length > 0 ? street.join(', ') : 'No mailing address set'
}

/** The postal address in full, one line per part, for "Mail it". */
export function mailingLines(
  agency?: Partial<
    Pick<Agency, 'recipient_office' | 'mailing_street' | 'mailing_city_state_zip'>
  > | null,
): string[] {
  return [agency?.recipient_office, agency?.mailing_street, agency?.mailing_city_state_zip].filter(
    (line): line is string => Boolean(line),
  )
}

/** Stage 3's two groups: what the portal sends itself, and what the person files by hand. */
export function groupRecipients<R extends { channel: string }>(
  recipients: R[],
): Array<{ key: 'email' | 'you'; title: string; rows: R[] }> {
  const email = recipients.filter((r) => r.channel === 'email')
  const you = recipients.filter((r) => r.channel !== 'email')
  return [
    { key: 'email', title: 'Sent by email', rows: email },
    { key: 'you', title: 'You file', rows: you },
  ].filter((group) => group.rows.length > 0) as Array<{
    key: 'email' | 'you'
    title: string
    rows: R[]
  }>
}

/** "This will create": one request per agency that is neither skipped nor held (an unticked row
 *  in stage 3, 048), counted by how it files. */
export function willCreate(
  recipients: Array<Pick<Recipient, 'agency_id' | 'channel' | 'status'>>,
  agencies: Array<Pick<Agency, 'id' | 'portal_kind'>>,
): { requests: number; rows: Array<{ label: string; count: number }> } {
  const live = recipients.filter((r) => r.status !== 'skipped' && r.status !== 'held')
  const counts = new Map<string, number>()
  for (const recipient of live) {
    const agency = agencies.find((a) => a.id === recipient.agency_id)
    const label =
      recipient.channel === 'email' ? 'Sent by email' : filesBy(recipient.channel, agency)
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return {
    requests: live.length,
    rows: [...counts].map(([label, count]) => ({ label, count })),
  }
}

export type ResultKind =
  'filed' | 'failed' | 'waiting' | 'pending' | 'skipped' | 'held' | 'unreserved'

export interface Result {
  kind: ResultKind
  text: string
  tone: 'neutral' | 'accent' | 'danger' | 'warn' | 'ok'
}

/** What one agency's row reads after filing: "Sent", "Filed on GovQA", "Failed · why",
 *  "Waiting for you · print and mail", "Pending filing", "Not sent". */
export function resultOf(
  recipient: Pick<Recipient, 'status' | 'channel' | 'request_id'>,
  agency: Pick<Agency, 'portal_kind'> | null | undefined,
  failure?: string | null,
): Result {
  if (recipient.status === 'filed') {
    if (recipient.channel === 'email') return { kind: 'filed', text: 'Sent', tone: 'ok' }
    if (recipient.channel === 'mail') return { kind: 'filed', text: 'Mailed', tone: 'ok' }
    return { kind: 'filed', text: `Filed on ${howLabel('portal', agency)}`, tone: 'ok' }
  }
  if (recipient.status === 'skipped') return { kind: 'skipped', text: 'Not sent', tone: 'neutral' }
  if (recipient.status === 'held') return { kind: 'held', text: 'Held', tone: 'warn' }
  if (failure) return { kind: 'failed', text: `Failed · ${failure}`, tone: 'danger' }
  if (!recipient.request_id) return { kind: 'unreserved', text: 'Not reserved', tone: 'neutral' }
  if (recipient.channel === 'mail')
    return { kind: 'waiting', text: 'Waiting for you · print and mail', tone: 'warn' }
  return { kind: 'pending', text: 'Pending filing', tone: 'neutral' }
}

export interface Tally {
  filed: number
  total: number
  failed: number
  waiting: number
}

/** "6 of 8 filed · 1 failed · 1 waiting for you", as numbers. Skipped agencies are not counted. */
export function tally(
  recipients: Array<Pick<Recipient, 'id' | 'status' | 'channel' | 'request_id'>>,
  failures: Record<string, string> = {},
): Tally {
  const live = recipients.filter((r) => r.status !== 'skipped')
  let filed = 0
  let failed = 0
  let waiting = 0
  for (const recipient of live) {
    const result = resultOf(recipient, null, failures[recipient.id])
    if (result.kind === 'filed') filed += 1
    else if (result.kind === 'failed') failed += 1
    else if (result.kind === 'waiting') waiting += 1
  }
  return { filed, total: live.length, failed, waiting }
}

export function tallyText(t: Tally): string {
  const parts = [`${t.filed} of ${t.total} filed`]
  if (t.failed > 0) parts.push(`${t.failed} failed`)
  if (t.waiting > 0) parts.push(`${t.waiting} waiting for you`)
  return parts.join(' · ')
}

export function pagesLabel(pages: number | null | undefined): string | null {
  if (!pages) return null
  return pages === 1 ? '1 page' : `${pages} pages`
}

/** `SCLU_CPRA_UCSC-PD_ALPR.pdf · 2 pages`, or the name alone until the count is known. */
export function attachmentLabel(filename: string, pages: number | null | undefined): string {
  const name = filename || 'letter.pdf'
  const count = pagesLabel(pages)
  return count ? `${name} · ${count}` : name
}

export function requestsLabel(n: number): string {
  return n === 1 ? '1 request' : `${n} requests`
}

export function itemsLabel(n: number): string {
  return n === 1 ? '1 item' : `${n} items`
}

/** The next agency still to deal with: pending, not the one just left, not one left pending on
 *  purpose this session. Null once everyone has been seen. */
export function nextPending<R extends { id: string; status: string }>(
  recipients: R[],
  deferred: ReadonlySet<string>,
  leaving: string | null = null,
): R | null {
  return (
    recipients.find((r) => r.status === 'pending' && r.id !== leaving && !deferred.has(r.id)) ??
    null
  )
}

/** Stage 1's search, over a master letter's name, basis and jurisdiction. */
export function templateMatches(
  template: Pick<Template, 'name' | 'legal_basis' | 'jurisdiction'>,
  query: string,
): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [template.name, template.legal_basis, template.jurisdiction]
    .filter(Boolean)
    .some((part) => String(part).toLowerCase().includes(q))
}

/** `2026-08-12T19:04:00Z` in a zone -> `Aug 12, 2026`: the day alone, for "Last used". */
export function formatDay(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  const options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', year: 'numeric' }
  try {
    return new Intl.DateTimeFormat('en-US', { ...options, timeZone }).format(when)
  } catch {
    return new Intl.DateTimeFormat('en-US', { ...options, timeZone: 'UTC' }).format(when)
  }
}

/** The organisation's name out of the settings list, for the requester line. */
export function orgNameOf(settings: Array<{ key: string; value: unknown }> | undefined): string {
  const found = settings?.find((s) => s.key === 'org.name')?.value
  return typeof found === 'string' ? found.trim() : ''
}

// ---- P20g: the stages on the v5 design ----------------------------------------------------------

/** The width of stage 4's green bar: the share of the agencies counted that are filed (049). */
export function filedShare(t: Tally): number {
  return t.total > 0 ? Math.round((t.filed / t.total) * 1000) / 10 : 0
}

/** The agencies "Review & file N →" reserves at the end of stage 3: pending, not yet reserved. */
export function toReserve<R extends Pick<Recipient, 'status' | 'request_id'>>(
  recipients: R[],
): R[] {
  return recipients.filter((r) => r.status === 'pending' && !r.request_id)
}

/** Where a draft opens without a stage in its address: the filing results (060) once every
 *  agency still counted has its tracking number, else the editor. */
export function defaultStage(
  recipients: Array<Pick<Recipient, 'status' | 'request_id'>>,
): 'draft' | 'filed' {
  const live = recipients.filter((r) => r.status !== 'skipped')
  return live.length > 0 && live.every((r) => r.request_id) ? 'filed' : 'draft'
}

/** "How" in the filing results (060): Email, GovQA, Web form, Postal. */
export function howShort(channel: string, agency?: Pick<Agency, 'portal_kind'> | null): string {
  return channel === 'mail' ? 'Postal' : howLabel(channel, agency)
}

export interface ResultParts {
  head: string
  tail: string | null
  tone: 'ok' | 'danger' | 'warn' | 'muted'
  /** What the row offers: try again, log the mailing, or open the agency in stage 4. */
  action: 'retry' | 'log' | 'open' | null
}

/** A row of the filing results (060): "Filed", "Failed · why", "Waiting for you · print and
 *  mail", "Pending filing", "Not reserved", "Held", "Not sent". */
export function resultParts(
  recipient: Pick<Recipient, 'status' | 'channel' | 'request_id'>,
  failure?: string | null,
): ResultParts {
  const result = resultOf(recipient, null, failure)
  switch (result.kind) {
    case 'filed':
      return { head: 'Filed', tail: null, tone: 'ok', action: null }
    case 'failed':
      return { head: 'Failed', tail: failure ?? null, tone: 'danger', action: 'retry' }
    case 'waiting':
      return { head: 'Waiting for you', tail: 'print and mail', tone: 'warn', action: 'log' }
    case 'pending':
      return { head: 'Pending filing', tail: null, tone: 'warn', action: 'open' }
    case 'unreserved':
      return { head: 'Not reserved', tail: null, tone: 'muted', action: 'open' }
    case 'held':
      return { head: 'Held', tail: null, tone: 'muted', action: null }
    default:
      return { head: 'Not sent', tail: null, tone: 'muted', action: null }
  }
}

/** "1 failed · 1 waiting for you" beside "6 of 8 filed" (060); empty when neither. */
export function tallyRest(t: Tally): string {
  const parts: string[] = []
  if (t.failed > 0) parts.push(`${t.failed} failed`)
  if (t.waiting > 0) parts.push(`${t.waiting} waiting for you`)
  return parts.join(' · ')
}

/** Stage 4's finished line (052): who is waiting to be mailed, and what happens to them. */
export function waitingSentence(names: string[]): string | null {
  if (names.length === 0) return null
  const one = names.length === 1
  const who =
    names.length <= 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
  return `${who} ${one ? 'is' : 'are'} waiting to be mailed. ${one ? 'It stays' : 'They stay'} pending filing until you mark ${one ? 'it' : 'each'} filed from its request.`
}
