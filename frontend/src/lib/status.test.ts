import { describe, expect, it } from 'vitest'

import { STATUS_WORDS, isOverdueStatus, statusGlyph, statusText, statusWords } from './status'

describe('status wording', () => {
  it('reads all nineteen codes as family · qualifier', () => {
    expect(Object.keys(STATUS_WORDS)).toHaveLength(19)
    expect(statusText('waiting_for_approval')).toBe('Filing · waiting for approval')
    expect(statusGlyph('waiting_for_approval')).toEqual(statusGlyph('pending_filing'))
    expect(statusText('clarification_requested')).toBe('Action needed · clarification')
    expect(statusText('fee_estimate_received')).toBe('Action needed · fee estimate')
    expect(statusText('determination_overdue')).toBe('Determination · overdue')
    expect(statusText('production_unreviewed')).toBe('Production · unreviewed')
    expect(statusText('in_progress')).toBe('Determination · awaited')
    expect(statusText('appealed')).toBe('Appeal · filed')
  })

  it('has the six families the design names', () => {
    const families = new Set(Object.values(STATUS_WORDS).map((w) => w.family))
    expect([...families].toSorted()).toEqual([
      'Action needed',
      'Appeal',
      'Closed',
      'Determination',
      'Filing',
      'Production',
    ])
  })

  it('drops the qualifier when the family says it all', () => {
    expect(statusText('closed')).toBe('Closed')
    expect(statusWords('closed').qualifier).toBeNull()
  })

  it('shows an unknown code legibly rather than blank', () => {
    expect(statusWords('some_new_state')).toEqual({ family: 'Some new state', qualifier: null })
  })

  it('marks the overdue statuses and draws them as the red dot', () => {
    expect(isOverdueStatus('production_overdue')).toBe(true)
    expect(isOverdueStatus('filed')).toBe(false)
    expect(statusGlyph('determination_overdue')).toEqual({ fill: 'dot', tone: 'danger' })
    expect(statusGlyph('partial_production')).toEqual({ fill: 'three-quarters', tone: 'brand' })
    expect(statusGlyph('acknowledged')).toEqual({ fill: 'quarter', tone: 'muted' })
  })
})
