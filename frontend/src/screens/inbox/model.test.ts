import { describe, expect, it } from 'vitest'

import {
  chainOf,
  countWords,
  flagWords,
  foldChain,
  mergeAll,
  movedTitle,
  movedWhen,
  neighbour,
  nextStep,
  parseBucket,
  parseSort,
  senderLine,
  senderOf,
  snippetOf,
  statusLine,
  subjectOf,
  tabOf,
  timeSpan,
  titleOf,
  type Message,
  type RequestEvent,
  type Row,
} from './model'

function row(overrides: {
  flag?: string | null
  reviewed?: boolean
  landed?: boolean
  meta?: Record<string, unknown>
  at?: string
  id?: string
}): Row {
  return {
    source: {
      id: overrides.id ?? 's1',
      source_type: 'postmark_email',
      external_ref: null,
      message_id: null,
      raw_metadata: overrides.meta ?? { from: 'records@agency.example.org', subject: 'Re: A' },
      raw_storage_url: 'x',
      flagged_for_review: Boolean(overrides.flag),
      flag_note: null,
      flag_reason: overrides.flag ?? null,
      assigned_to_user_id: null,
      resolution: null,
      resolution_note: null,
      reviewed_at: overrides.reviewed ? '2026-09-09T10:00:00Z' : null,
      reviewed_by_id: null,
      captured_at: overrides.at ?? '2026-09-09T09:00:00Z',
    },
    attachments: [],
    events: overrides.landed
      ? [
          {
            id: 'e1',
            request_id: 'r1',
            tracking_number: 'REQ-2026-000001',
            agency_id: 'a1',
            agency_name: 'Test Agency',
            event_type: 'correspondence',
            channel: 'email',
            direction: 'inbound',
            occurred_at: '2026-09-09T09:00:00Z',
            display_body: 'Body',
          },
        ]
      : [],
    note_count: 0,
    assignee_name: null,
  } as Row
}

describe('the lists', () => {
  it('reads a bucket from the URL, flagged when it names none', () => {
    expect(parseBucket('new')).toBe('new')
    expect(parseBucket('sent')).toBe('sent')
    expect(parseBucket('bogus')).toBe('flagged')
    expect(parseBucket(null)).toBe('flagged')
  })
  it('puts Sent and Drafts under All', () => {
    expect(tabOf('sent')).toBe('all')
    expect(tabOf('drafts')).toBe('all')
    expect(tabOf('new')).toBe('new')
    expect(titleOf('all')).toBe('All mail')
    expect(titleOf('drafts')).toBe('Drafts')
  })
  it('counts in words', () => {
    expect(countWords('flagged', 4)).toBe('4 flagged')
    expect(countWords('new', 3)).toBe('3 new')
    expect(countWords('sent', 48)).toBe('48 sent')
    expect(countWords('drafts', 1)).toBe('1 draft')
    expect(countWords('drafts', 0)).toBe('0 drafts')
    expect(countWords('all', 126)).toBe('126 messages')
    expect(countWords('all', 1)).toBe('1 message')
  })
})

describe('flag reasons', () => {
  it('says every reason in plain words, a short and a long form', () => {
    expect(flagWords('may_cover_others')).toEqual({
      row: 'May cover others',
      long: 'May cover other requests',
    })
    expect(flagWords('reply_to_ignored').row).toBe('Reply-to ignored')
    expect(flagWords('attachment_too_large').row).toBe('Attachment too large')
    expect(flagWords('assistant').row).toBe('The assistant thinks this is misfiled')
    expect(flagWords('something new').row).toBe('Other reason')
    expect(flagWords(null).row).toBe('Other reason')
  })
  it('offers the next step the reason calls for', () => {
    expect(nextStep(row({ flag: 'delivery_failed' }))).toBe('fix_resend')
    expect(nextStep(row({ flag: 'no_matching_request' }))).toBe('attach')
    expect(nextStep(row({ flag: 'reply_to_ignored' }))).toBe('attach')
    expect(nextStep(row({ flag: 'may_cover_others', landed: true }))).toBe('attach_others')
    expect(nextStep(row({ flag: 'may_cover_others' }))).toBe('attach')
    expect(nextStep(row({ flag: 'other', landed: true }))).toBe('handled')
    expect(nextStep(row({ flag: 'attachment_too_large' }))).toBe('attach')
  })
})

describe('a row', () => {
  it('names the sender by name, else address, else what it is', () => {
    expect(senderOf(row({ meta: { from: 'a@x.org', from_name: 'Records Unit' } }))).toBe(
      'Records Unit',
    )
    expect(senderOf(row({ meta: { from: 'a@x.org' } }))).toBe('a@x.org')
    expect(senderOf(row({ meta: {} }))).toBe('Email')
    expect(subjectOf(row({ meta: {} }))).toBe('(no subject)')
  })
  it('adds the agency once it landed', () => {
    expect(senderLine(row({ landed: true, meta: { from_name: 'K' } }))).toBe('K · Test Agency')
    expect(senderLine(row({ meta: { from_name: 'K' } }))).toBe('K')
  })
  it('says where an All row stands', () => {
    expect(statusLine(row({ flag: 'no_matching_request' }))).toEqual({
      text: 'Flagged · No matching request',
      flagged: true,
    })
    expect(statusLine(row({ landed: true }))).toEqual({
      text: 'New · REQ-2026-000001',
      flagged: false,
    })
    expect(statusLine(row({ landed: true, reviewed: true }))).toEqual({
      text: 'REQ-2026-000001',
      flagged: false,
    })
  })
})

describe('All mail', () => {
  it('merges what arrived and what was sent, newest first', () => {
    const sent = {
      id: 'm1',
      sent_at: '2026-09-09T09:30:00Z',
      updated_at: '2026-09-09T09:30:00Z',
    } as Message
    const merged = mergeAll(
      [row({ id: 'a', at: '2026-09-09T09:00:00Z' }), row({ id: 'b', at: '2026-09-09T10:00:00Z' })],
      [sent],
    )
    expect(merged.map((item) => item.id)).toEqual(['b', 'm1', 'a'])
    expect(mergeAll([row({ id: 'a' })], [sent], true).map((i) => i.id)).toEqual(['a', 'm1'])
  })
  it('opens the next message, else the one before', () => {
    expect(neighbour(['a', 'b', 'c'], 'b')).toBe('c')
    expect(neighbour(['a', 'b', 'c'], 'c')).toBe('b')
    expect(neighbour(['a'], 'a')).toBe(null)
    expect(neighbour(['a', 'b'], 'z')).toBe('a')
  })
  it('reads a sort from the URL', () => {
    expect(parseSort('agency')).toBe('agency')
    expect(parseSort('loudest')).toBe('newest')
  })
})

describe('search', () => {
  it('shows the words around the first hit, cut at word ends', () => {
    const text =
      'Enclosed are the agreement and the Flock Safety data-sharing agreement, and a map.'
    expect(snippetOf(text, 'flock', 18)).toEqual({
      before: '…agreement and the ',
      hit: 'Flock',
      after: ' Safety…',
    })
    expect(snippetOf('Short flock', 'flock')).toEqual({ before: 'Short ', hit: 'flock', after: '' })
    expect(snippetOf(text, 'absent')).toBe(null)
    expect(snippetOf(null, 'x')).toBe(null)
  })
})

describe('a thread', () => {
  const event = (id: string, at: string, extra: Partial<RequestEvent> = {}) =>
    ({
      id,
      source_id: id,
      occurred_at: at,
      event_type: 'correspondence',
      channel: 'email',
      voided_at: null,
      ...extra,
    }) as RequestEvent
  it('lists the earlier messages on the request, oldest first, without notes or voids', () => {
    const events = [
      event('c', '2026-09-03'),
      event('a', '2026-09-01'),
      event('n', '2026-09-02', { event_type: 'note', channel: 'system', source_id: null }),
      event('v', '2026-09-02', { voided_at: '2026-09-04' }),
      event('me', '2026-09-05'),
      event('later', '2026-09-06'),
    ]
    expect(chainOf(events, 'me', '2026-09-05').map((e) => e.id)).toEqual(['a', 'c'])
  })
  it('folds more than three to the first, the rest, and the last', () => {
    expect(foldChain(2, false)).toEqual([
      { kind: 'row', index: 0 },
      { kind: 'row', index: 1 },
    ])
    expect(foldChain(4, false)).toEqual([
      { kind: 'row', index: 0 },
      { kind: 'more', indexes: [1, 2] },
      { kind: 'row', index: 3 },
    ])
    expect(foldChain(4, true)).toHaveLength(4)
  })
})

describe('P23b: the time chip and who moved a message', () => {
  const now = new Date(2026, 2, 9, 12, 0, 0)

  it('reads a span, chosen dates, or any time', () => {
    expect(timeSpan(null, null, null, now)).toEqual({ label: 'Any time' })
    expect(timeSpan('today', null, null, now)).toEqual({ label: 'Today', since: '2026-03-09' })
    expect(timeSpan('7', null, null, now)).toEqual({ label: 'Last 7 days', since: '2026-03-02' })
    expect(timeSpan('custom', '2026-03-02', '2026-03-05', now)).toEqual({
      label: 'Mar 2 – Mar 5',
      since: '2026-03-02',
      until: '2026-03-05',
    })
    expect(timeSpan('custom', '2026-03-02', '', now).label).toBe('Since Mar 2')
    expect(timeSpan('custom', '', '2026-03-05', now).label).toBe('Until Mar 5')
    expect(timeSpan('custom', '', '', now)).toEqual({ label: 'Any time' })
  })

  it('says how long ago it moved', () => {
    const at = new Date(now.getTime() - 20_000).toISOString()
    expect(movedWhen(at, 'UTC', now)).toBe('20 seconds ago')
    expect(movedWhen(new Date(now.getTime() - 4 * 60_000).toISOString(), 'UTC', now)).toBe(
      '4 min ago',
    )
  })

  it('names who moved it', () => {
    const at = new Date(now.getTime() - 20_000).toISOString()
    expect(movedTitle('Maya Torres', at, 'Dana Nolan', now)).toBe(
      'Maya just moved this to Flagged',
    )
    expect(movedTitle('Dana Nolan', at, 'Dana Nolan', now)).toBe(
      'You just moved this to Flagged',
    )
    expect(movedTitle('Incoming mail', '2026-01-01T00:00:00Z', 'Dana Nolan', now)).toBe(
      'Incoming mail moved this to Flagged',
    )
    expect(movedTitle(null, null, 'Dana Nolan', now)).toBe('This was moved to Flagged')
  })
})
