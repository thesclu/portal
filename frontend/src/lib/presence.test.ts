import { describe, expect, it } from 'vitest'

import { initialsOf, pageLabel, viewersOf, zoneAbbreviation } from './presence'

describe('initialsOf', () => {
  it('takes the first and last names', () => {
    expect(initialsOf('Dana Nolan')).toBe('DN')
    expect(initialsOf('Maya Elena Torres')).toBe('MT')
    expect(initialsOf('  priya  ')).toBe('P')
    expect(initialsOf('')).toBe('')
  })
})

describe('zoneAbbreviation', () => {
  it('names the zone as it stands at the moment', () => {
    expect(zoneAbbreviation('America/Los_Angeles', new Date('2026-07-01T12:00:00Z'))).toBe('PDT')
    expect(zoneAbbreviation('America/Los_Angeles', new Date('2026-01-15T12:00:00Z'))).toBe('PST')
    expect(zoneAbbreviation('UTC')).toBe('UTC')
  })
  it('gives nothing for a zone it does not know', () => {
    expect(zoneAbbreviation('Nowhere/Nothing')).toBe('')
  })
})

describe('pageLabel', () => {
  it('reads the trail, then the name', () => {
    expect(pageLabel('Docket')).toBe('Docket')
    expect(pageLabel('Flagged', ['Inbox'])).toBe('Inbox › Flagged')
    expect(pageLabel('Axon contracts', ['Docket', 'Cal Poly Pomona'])).toBe(
      'Docket › Cal Poly Pomona › Axon contracts',
    )
    expect(pageLabel('Docket', [''])).toBe('Docket')
  })
})

describe('viewersOf', () => {
  const people = [
    { user_id: 'me', name: 'Me', path: '/requests/r1', state: 'online' },
    { user_id: 'a', name: 'Maya Torres', path: '/requests/r1?tab=notes', state: 'online' },
    { user_id: 'b', name: 'Alex Kim', path: '/requests/r1/review/9', state: 'away' },
    { user_id: 'c', name: 'Priya Shah', path: '/requests/r10', state: 'online' },
    { user_id: 'd', name: 'Jordan Reyes', path: '/docket', state: 'online' },
  ]
  it('keeps the others on this request only', () => {
    expect(viewersOf(people, 'r1', 'me').map((p) => p.name)).toEqual(['Maya Torres', 'Alex Kim'])
    expect(viewersOf(people, 'r10', 'me').map((p) => p.name)).toEqual(['Priya Shah'])
    expect(viewersOf(people, 'r2', 'me')).toEqual([])
  })
})
