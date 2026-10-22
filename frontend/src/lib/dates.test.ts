import { describe, expect, it } from 'vitest'

import {
  formatLastActive,
  formatSubmitted,
  formatAgo,
  formatDate,
  formatDateRange,
  formatInstant,
  formatListTime,
  formatTime,
} from './dates'

describe('formatInstant', () => {
  const instant = '2026-09-08T22:04:00Z'

  it('shows one instant differently in two zones, and names the zone', () => {
    const west = formatInstant(instant, 'America/Los_Angeles')
    const east = formatInstant(instant, 'America/New_York')
    expect(west).toBe('Sep 8, 2026, 3:04 PM PDT')
    expect(east).toBe('Sep 8, 2026, 6:04 PM EDT')
    expect(west).not.toBe(east)
  })

  it('crosses midnight with the zone', () => {
    expect(formatInstant('2026-09-09T03:30:00Z', 'America/Los_Angeles')).toBe(
      'Sep 8, 2026, 8:30 PM PDT',
    )
  })

  it('names standard time in winter', () => {
    expect(formatInstant('2026-01-15T20:00:00Z', 'America/Los_Angeles')).toContain('PST')
  })

  it('never throws: an unknown zone falls back to UTC, and nothing is a dash', () => {
    expect(formatInstant(instant, 'Not/AZone')).toContain('UTC')
    expect(formatInstant(null, 'UTC')).toBe('—')
    expect(formatInstant('not a date', 'UTC')).toBe('—')
  })
})

describe('formatDate', () => {
  it('is the same date for every reader, with no time zone involved', () => {
    expect(formatDate('2026-09-18')).toBe('Sep 18, 2026')
    // The first of a month is where a local-time Date would slip back a day west of UTC.
    expect(formatDate('2026-03-01')).toBe('Mar 1, 2026')
    expect(formatDate('2026-12-31')).toBe('Dec 31, 2026')
  })

  it('reads the date off a longer string and dashes anything else', () => {
    expect(formatDate('2026-09-18T00:00:00Z')).toBe('Sep 18, 2026')
    expect(formatDate(undefined)).toBe('—')
    expect(formatDate('soon')).toBe('—')
  })
})

describe('formatAgo', () => {
  const now = new Date('2026-09-08T12:00:00Z')
  it('speaks in the largest unit that fits', () => {
    expect(formatAgo('2026-09-08T11:59:40Z', now)).toBe('just now')
    expect(formatAgo('2026-09-08T11:56:00Z', now)).toBe('4 min ago')
    expect(formatAgo('2026-09-08T09:00:00Z', now)).toBe('3 hr ago')
    expect(formatAgo('2026-09-01T12:00:00Z', now)).toBe('7 days ago')
    expect(formatAgo(null, now)).toBe('never')
  })
})

describe('formatTime', () => {
  it('shows the clock alone, in the zone', () => {
    expect(formatTime('2026-09-08T22:04:00Z', 'America/Los_Angeles')).toBe('3:04 PM')
    expect(formatTime('2026-09-08T22:04:00Z', 'America/New_York')).toBe('6:04 PM')
  })

  it('never throws', () => {
    expect(formatTime('2026-09-08T22:04:00Z', 'Not/AZone')).toBe('10:04 PM')
    expect(formatTime(null, 'UTC')).toBe('—')
  })
})

describe('formatDateRange', () => {
  it('joins two dates with a dash', () => {
    expect(formatDateRange('2026-09-01', '2026-09-03')).toBe('Sep 1, 2026 – Sep 3, 2026')
  })

  it('shows one date when the range is a single day', () => {
    expect(formatDateRange('2026-09-01', '2026-09-01')).toBe('Sep 1, 2026')
  })
})

describe('formatListTime', () => {
  const now = new Date('2026-09-09T16:00:00Z') // 9:00 AM in Los Angeles
  const zone = 'America/Los_Angeles'
  it('shows the clock for today, Yesterday, then the short date', () => {
    expect(formatListTime('2026-09-09T13:20:00Z', zone, now)).toBe('6:20 AM')
    expect(formatListTime('2026-09-08T18:00:00Z', zone, now)).toBe('Yesterday')
    expect(formatListTime('2026-09-06T18:00:00Z', zone, now)).toBe('Sep 6')
  })
  it('reads the day in the reader zone, not UTC', () => {
    // 5 AM UTC on the 9th is still the 8th in Los Angeles.
    expect(formatListTime('2026-09-09T05:00:00Z', zone, now)).toBe('Yesterday')
    expect(formatListTime(null, zone, now)).toBe('—')
  })
})

describe('formatLastActive', () => {
  const now = new Date('2026-10-17T18:00:00Z')
  const zone = 'America/Los_Angeles'
  it('reads recent times as words and older ones as dates', () => {
    expect(formatLastActive('2026-10-17T17:59:30Z', zone, now)).toBe('Now')
    expect(formatLastActive('2026-10-17T17:48:00Z', zone, now)).toBe('12 min ago')
    expect(formatLastActive('2026-10-17T14:30:00Z', zone, now)).toBe('3 hr ago')
    expect(formatLastActive('2026-10-16T20:00:00Z', zone, now)).toBe('Yesterday')
    expect(formatLastActive('2026-09-08T20:00:00Z', zone, now)).toBe('Sep 8')
    expect(formatLastActive('2025-09-08T20:00:00Z', zone, now)).toBe('Sep 8, 2025')
    expect(formatLastActive(null, zone, now)).toBe('Never')
  })
})

describe('formatSubmitted', () => {
  const now = new Date('2026-10-17T18:00:00Z')
  const zone = 'America/Los_Angeles'
  it('reads today with its time, yesterday by name, else the date', () => {
    expect(formatSubmitted('2026-10-17T15:40:00Z', zone, now)).toBe('Today 8:40 AM')
    expect(formatSubmitted('2026-10-16T20:00:00Z', zone, now)).toBe('Yesterday')
    expect(formatSubmitted('2026-09-30T20:00:00Z', zone, now)).toBe('Sep 30')
  })
})
