import { describe, expect, it } from 'vitest'

import {
  avatarColour,
  byDay,
  chatLinkTarget,
  conversationName,
  dayLabel,
  fileSize,
  insertMention,
  listTime,
  matchPeople,
  memberCount,
  mentionQuery,
  mergeMessages,
  notificationFor,
  onlineSummary,
  placeholderFor,
  seenLine,
  viewingLine,
} from './chat'

const LA = 'America/Los_Angeles'
const NOW = new Date('2026-10-17T18:00:00Z') // 11:00 AM in Los Angeles, a Saturday

describe('times', () => {
  it('lists today by the clock, then Yesterday, the weekday, the date', () => {
    expect(listTime('2026-10-17T17:06:00Z', LA, NOW)).toBe('10:06 AM')
    expect(listTime('2026-10-16T17:06:00Z', LA, NOW)).toBe('Yesterday')
    expect(listTime('2026-10-12T17:06:00Z', LA, NOW)).toBe('Mon')
    expect(listTime('2026-09-03T17:06:00Z', LA, NOW)).toBe('Sep 3')
  })

  it('counts days on the reader’s own calendar', () => {
    // 06:30 UTC on the 17th is still the 16th in Los Angeles.
    expect(listTime('2026-10-17T06:30:00Z', LA, NOW)).toBe('Yesterday')
    expect(dayLabel('2026-10-17T06:30:00Z', LA, NOW)).toBe('Yesterday')
  })

  it('labels days and groups messages under them', () => {
    expect(dayLabel('2026-10-17T16:12:00Z', LA, NOW)).toBe('Today')
    expect(dayLabel('2026-10-12T16:12:00Z', LA, NOW)).toBe('Mon, Oct 12')
    expect(dayLabel('2025-10-12T16:12:00Z', LA, NOW)).toBe('Sun, Oct 12, 2025')
    const days = byDay(
      [
        { id: 'a', created_at: '2026-10-16T16:00:00Z', changed_at: '2026-10-16T16:00:00Z' },
        { id: 'b', created_at: '2026-10-17T16:00:00Z', changed_at: '2026-10-17T16:00:00Z' },
        { id: 'c', created_at: '2026-10-17T16:05:00Z', changed_at: '2026-10-17T16:05:00Z' },
      ],
      LA,
      NOW,
    )
    expect(days.map((d) => [d.label, d.messages.map((m) => m.id)])).toEqual([
      ['Yesterday', ['a']],
      ['Today', ['b', 'c']],
    ])
  })
})

describe('merging live messages', () => {
  it('replaces a message with a newer copy, adds new ones, keeps oldest first', () => {
    const held = [
      { id: 'a', created_at: '1', changed_at: '1', text: 'old' },
      { id: 'b', created_at: '2', changed_at: '2', text: 'b' },
    ]
    const merged = mergeMessages(held, [
      { id: 'a', created_at: '1', changed_at: '5', text: 'edited' },
      { id: 'c', created_at: '3', changed_at: '3', text: 'new' },
      { id: 'b', created_at: '2', changed_at: '1', text: 'stale' },
    ])
    expect(merged.map((m) => m.text)).toEqual(['edited', 'b', 'new'])
  })
})

describe('who is online', () => {
  it('counts as drawn', () => {
    expect(onlineSummary(2, 1)).toBe('2 online · 1 away')
    expect(onlineSummary(1, 0)).toBe('1 online')
    expect(onlineSummary(0, 3)).toBe('3 away')
    expect(onlineSummary(0, 0)).toBe('No one else online')
  })

  it('says where people are, or how long they have been away', () => {
    const base = { state: 'online', where: 'Inbox · Flagged', on_request: false, minutes_since: 0 }
    expect(seenLine(base)).toBe('Inbox · Flagged')
    expect(seenLine({ ...base, state: 'away', minutes_since: 14 })).toBe('Away 14 min')
    expect(viewingLine({ ...base, on_request: true, where: 'A request' })).toEqual({
      lead: 'Viewing a request',
      label: '',
    })
    expect(
      viewingLine({ ...base, on_request: true, request_label: 'REQ-2026-000001', where: 'x' }),
    ).toEqual({ lead: 'Viewing ', label: 'REQ-2026-000001' })
    expect(viewingLine(undefined)).toBeNull()
  })
})

describe('conversations', () => {
  const dm = {
    id: 'd',
    kind: 'dm',
    name: '',
    people: [{ id: 'p', name: 'Seed Staff', initials: 'SS' }],
  }
  const channel = { id: 'c', kind: 'channel', name: 'seed-lobby', people: [] }

  it('names them and their composers', () => {
    expect(conversationName(dm)).toBe('Seed Staff')
    expect(placeholderFor(dm)).toBe('Message Seed')
    expect(placeholderFor(channel)).toBe('Message #seed-lobby')
    expect(memberCount(1)).toBe('1 member')
    expect(memberCount(6)).toBe('6 members')
  })

  it('notifies with the sender and the conversation, never the words', () => {
    expect(notificationFor('Seed Staff', channel)).toEqual({
      title: 'Seed Staff',
      body: 'in #seed-lobby',
    })
    expect(notificationFor('Seed Staff', dm)).toEqual({
      title: 'Seed Staff',
      body: 'Direct message',
    })
  })

  it('reads the conversation a notification opens', () => {
    expect(chatLinkTarget('/?chat=abc')).toBe('abc')
    expect(chatLinkTarget('/requests/x')).toBeNull()
  })
})

describe('mentions', () => {
  it('finds the name being typed after an @', () => {
    expect(mentionQuery('hello @Se', 9)).toBe('Se')
    expect(mentionQuery('hello @Seed St', 14)).toBe('Seed St')
    expect(mentionQuery('hello @', 7)).toBe('')
    expect(mentionQuery('mail a@b', 8)).toBeNull()
    expect(mentionQuery('hello', 5)).toBeNull()
  })

  it('puts the full name in place of what was typed', () => {
    expect(insertMention('hi @Se and', 6, 'Seed Staff')).toEqual({
      text: 'hi @Seed Staff  and',
      caret: 15,
    })
  })

  it('matches first and later names', () => {
    const people = [{ name: 'Seed Staff' }, { name: 'Seed Admin' }, { name: 'Other Person' }]
    expect(matchPeople(people, 'st').map((p) => p.name)).toEqual(['Seed Staff'])
    expect(matchPeople(people, 'seed').map((p) => p.name)).toEqual(['Seed Staff', 'Seed Admin'])
  })
})

it('gives each person one colour and sizes files', () => {
  expect(avatarColour('same')).toBe(avatarColour('same'))
  expect(fileSize(512)).toBe('512 B')
  expect(fileSize(12_400)).toBe('12 KB')
  expect(fileSize(3_565_158)).toBe('3.4 MB')
})
