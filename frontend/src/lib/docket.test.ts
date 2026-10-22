import { describe, expect, it } from 'vitest'

import { barHeight, dateAfter, sparkline, statFilter } from './docket'

describe('barHeight', () => {
  it('draws 12 px a request up to four, and a stub for none', () => {
    expect(barHeight(0, 4)).toBe(3)
    expect(barHeight(1, 4)).toBe(12)
    expect(barHeight(4, 4)).toBe(48)
  })

  it('scales to the busiest day past four', () => {
    expect(barHeight(10, 10)).toBe(48)
    expect(barHeight(5, 10)).toBe(24)
  })
})

describe('dateAfter', () => {
  it('is the next civil date, across a month', () => {
    expect(dateAfter('2026-10-16')).toBe('2026-10-17')
    expect(dateAfter('2026-10-31')).toBe('2026-11-01')
  })
})

describe('sparkline', () => {
  it('spreads the points across 120 and maps lowest to 26, highest to 9', () => {
    expect(sparkline([0, 5, 10])).toBe('0,26 60,17.5 120,9')
  })

  it('draws a flat series along the bottom and nothing for one value', () => {
    expect(sparkline([3, 3])).toBe('0,26 120,26')
    expect(sparkline([3])).toBe('')
  })
})

describe('statFilter', () => {
  const source = {
    today: '2026-10-14',
    due_by_date: ['12', '13', '14', '15', '16'].map((d) => ({ date: `2026-10-${d}` })),
    producing_statuses: ['new_production', 'partial_production'],
    waiting_statuses: ['filed', 'acknowledged'],
  }

  it('filters the list by what each box counted', () => {
    expect(statFilter('overdue', source)).toEqual({ due_before: '2026-10-14' })
    expect(statFilter('producing', source)).toEqual({
      status: 'new_production,partial_production',
    })
    expect(statFilter('waiting', source)).toEqual({ status: 'filed,acknowledged' })
    expect(statFilter('week', source)).toEqual({
      due_after: '2026-10-12',
      due_before: '2026-10-17',
    })
    expect(statFilter('mine', source)).toEqual({ assignee: 'me' })
    expect(statFilter(null, source)).toEqual({ open: true })
  })

  it('waits for the summary where a box needs it', () => {
    expect(statFilter('producing', undefined)).toBeNull()
    expect(statFilter('mine', undefined)).toEqual({ assignee: 'me' })
    expect(statFilter(null, undefined)).toEqual({ open: true })
  })
})
