import { describe, expect, it } from 'vitest'

import {
  SYSTEM,
  activitySubject,
  activityWhen,
  activityExportHref,
  activityQuery,
  daysAgo,
  emailOrder,
  emailWhenLabel,
  kindLabel,
  summaryHourLabel,
} from './notifications'

const none = { who: '', action: '', since: '', until: '' }

describe('kindLabel and emailWhenLabel', () => {
  it('name the kinds they know and pass the rest through', () => {
    expect(kindLabel('mention')).toBe('Mention')
    expect(kindLabel('arrived')).toBe('New mail')
    expect(kindLabel('something_new')).toBe('something_new')
    expect(emailWhenLabel('bounce')).toBe('A message I sent bounces')
    expect(emailWhenLabel('something_new')).toBe('something_new')
  })
  it('names how far ahead a deadline is told when the window is known', () => {
    expect(emailWhenLabel('deadline', 3)).toBe('A deadline is 3 days out')
    expect(emailWhenLabel('deadline', 1)).toBe('A deadline is 1 day out')
    expect(emailWhenLabel('deadline', 0)).toBe('A deadline is today')
    expect(emailWhenLabel('deadline')).toBe('A deadline on my request is near')
  })
})

describe('emailOrder and summaryHourLabel', () => {
  it('puts the kinds in the order 201 draws them, unknown kinds after', () => {
    expect(
      emailOrder(['mention', 'assigned', 'bounce', 'deadline', 'new_kind', 'flagged']),
    ).toEqual(['deadline', 'flagged', 'assigned', 'mention', 'bounce', 'new_kind'])
  })
  it('reads the summary hour on a twelve-hour clock', () => {
    expect(summaryHourLabel(7)).toBe('7:00 AM')
    expect(summaryHourLabel(0)).toBe('12:00 AM')
    expect(summaryHourLabel(12)).toBe('12:00 PM')
    expect(summaryHourLabel(23)).toBe('11:00 PM')
  })
})

describe('activityQuery', () => {
  it('sends nothing for a filter left empty', () => {
    expect(activityQuery(none)).toEqual({})
  })

  it('turns the person filter into actor_id or system, never both', () => {
    expect(activityQuery({ ...none, who: SYSTEM })).toEqual({ system: true })
    expect(activityQuery({ ...none, who: 'u-1' })).toEqual({ actor_id: 'u-1' })
  })

  it('passes the rest through', () => {
    expect(
      activityQuery({ who: '', action: 'request.filed', since: '2026-09-29', until: '2026-10-06' }),
    ).toEqual({ action: 'request.filed', since: '2026-09-29', until: '2026-10-06' })
  })
})

describe('activityExportHref', () => {
  it('is the bare path with no filters', () => {
    expect(activityExportHref(none)).toBe('/api/core/activity/export')
  })

  it('carries the same filters as the list, encoded', () => {
    expect(activityExportHref({ who: SYSTEM, action: 'a b', since: '2026-09-29', until: '' })).toBe(
      '/api/core/activity/export?system=true&action=a+b&since=2026-09-29',
    )
    expect(activityExportHref({ ...none, who: 'u-1' })).toBe(
      '/api/core/activity/export?actor_id=u-1',
    )
  })
})

describe('daysAgo', () => {
  it('counts back in calendar days and pads', () => {
    // Local-time constructor on purpose: the result is the reader's own calendar date.
    expect(daysAgo(7, new Date(2026, 9, 6, 12))).toBe('2026-09-29')
    expect(daysAgo(1, new Date(2026, 2, 1, 12))).toBe('2026-02-28')
    expect(daysAgo(0, new Date(2026, 0, 5, 12))).toBe('2026-01-05')
  })
})

describe('activityWhen and activitySubject', () => {
  const now = new Date('2026-10-17T18:00:00Z')
  it('reads today and yesterday by name, else the date', () => {
    expect(activityWhen('2026-10-17T16:12:00Z', 'America/Los_Angeles', now)).toBe('Today 9:12 AM')
    expect(activityWhen('2026-10-16T23:18:00Z', 'America/Los_Angeles', now)).toBe(
      'Yesterday 4:18 PM',
    )
    expect(activityWhen('2026-09-06T21:40:00Z', 'America/Los_Angeles', now)).toBe('Sep 6, 2:40 PM')
  })
  it('names the kinds of thing it knows', () => {
    expect(activitySubject('user')).toBe('Users')
    expect(activitySubject('access_request')).toBe('Access')
    expect(activitySubject('shared_block')).toBe('Shared block')
    expect(activitySubject('')).toBe('—')
  })
})
