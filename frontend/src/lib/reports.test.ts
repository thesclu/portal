import { describe, expect, it } from 'vitest'

import { barWidth, csvHref, dayCount, share } from './reports'

describe('figures', () => {
  it('counts days in words and shows a dash for none', () => {
    expect(dayCount(12)).toBe('12 days')
    expect(dayCount(1)).toBe('1 day')
    expect(dayCount(0)).toBe('0 days')
    expect(dayCount(null)).toBe('—')
  })
  it('gives a share as a whole percentage, or a dash when nothing was judged', () => {
    expect(share(5, 6)).toBe('83%')
    expect(share(0, 4)).toBe('0%')
    expect(share(0, 0)).toBe('—')
  })
  it('sizes a bar against the longest one', () => {
    expect(barWidth(10, 20)).toBe(50)
    expect(barWidth(20, 20)).toBe(100)
    expect(barWidth(null, 20)).toBe(0)
    expect(barWidth(5, 0)).toBe(0)
  })
})

describe('csvHref', () => {
  it('keeps only the filters that are set', () => {
    expect(csvHref('/api/x/export', { q: 'water', status: undefined, agency_id: '' })).toBe(
      '/api/x/export?q=water',
    )
    expect(csvHref('/api/x/export', { year: 2026, agency_id: null })).toBe(
      '/api/x/export?year=2026',
    )
    expect(csvHref('/api/x/export', {})).toBe('/api/x/export')
  })
})
