import { describe, expect, it } from 'vitest'

import { parseAddresses, secondsUntil } from './mail'

describe('parseAddresses', () => {
  it('takes bare addresses split by commas, semicolons and lines', () => {
    const { addresses, rejected } = parseAddresses('a@x.gov, b@x.gov; c@x.gov\nd@x.gov')
    expect(addresses.map((a) => a.address)).toEqual(['a@x.gov', 'b@x.gov', 'c@x.gov', 'd@x.gov'])
    expect(rejected).toEqual([])
  })

  it('reads a pasted "Name <address>" and keeps a comma inside the quotes', () => {
    const { addresses } = parseAddresses('"Doe, Jane" <jane@x.gov>, Records Unit <pra@x.gov>')
    expect(addresses).toEqual([
      { address: 'jane@x.gov', name: 'Doe, Jane' },
      { address: 'pra@x.gov', name: 'Records Unit' },
    ])
  })

  it('rejects what is not an address and drops a repeat', () => {
    const { addresses, rejected } = parseAddresses('jane, jane@x.gov, JANE@x.gov, @nope')
    expect(addresses).toEqual([{ address: 'jane@x.gov' }])
    expect(rejected).toEqual(['jane', '@nope'])
  })

  it('is empty for nothing', () => {
    expect(parseAddresses('  ')).toEqual({ addresses: [], rejected: [] })
  })
})

describe('secondsUntil', () => {
  const now = Date.parse('2026-09-08T22:04:00Z')
  it('rounds up and never goes below zero', () => {
    expect(secondsUntil('2026-09-08T22:04:30.200Z', now)).toBe(31)
    expect(secondsUntil('2026-09-08T22:03:00Z', now)).toBe(0)
    expect(secondsUntil(null, now)).toBeNull()
    expect(secondsUntil('nope', now)).toBeNull()
  })
})
