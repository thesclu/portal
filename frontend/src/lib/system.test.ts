import { describe, expect, it } from 'vitest'

import { cronPeriodMinutes, isLate, jobLabel, jobOrder, runsOut } from './system'

describe('runsOut', () => {
  it('says how far off the last date is in years, months or days', () => {
    expect(runsOut('2030-12-31', '2026-10-17')).toBe('Runs out in 4 years')
    expect(runsOut('2027-10-17', '2026-10-17')).toBe('Runs out in 1 year')
    expect(runsOut('2027-10-16', '2026-10-17')).toBe('Runs out in 11 months')
    expect(runsOut('2026-11-11', '2026-10-17')).toBe('Runs out in 25 days')
    expect(runsOut('2026-10-18', '2026-10-17')).toBe('Runs out in 1 day')
    expect(runsOut('2026-10-17', '2026-10-17')).toBe('Runs out today')
  })
  it('says when it has run out or has no dates', () => {
    expect(runsOut('2026-01-01', '2026-10-17')).toBe('Ran out')
    expect(runsOut(null, '2026-10-17')).toBe('No dates entered')
  })
})

describe('jobs', () => {
  it('names the jobs it knows and puts the three drawn first', () => {
    expect(jobLabel('records.sweep_overdue_deadlines')).toBe('Overdue check')
    expect(jobLabel('something.else')).toBe('something.else')
    const names = jobOrder([
      { name: 'mail.requeue_stuck_messages' },
      { name: 'records.production_review_timeout' },
      { name: 'records.sweep_overdue_deadlines' },
      { name: 'records.send_auto_followups' },
    ]).map((job) => job.name)
    expect(names).toEqual([
      'records.sweep_overdue_deadlines',
      'records.send_auto_followups',
      'records.production_review_timeout',
      'mail.requeue_stuck_messages',
    ])
  })
})

describe('cronPeriodMinutes and isLate', () => {
  it('reads how often a schedule runs', () => {
    expect(cronPeriodMinutes('*/15 * * * *')).toBe(15)
    expect(cronPeriodMinutes('0 * * * *')).toBe(60)
    expect(cronPeriodMinutes('5 * * * *')).toBe(60)
    expect(cronPeriodMinutes('15 3 * * *')).toBe(1440)
    expect(cronPeriodMinutes('nonsense')).toBeNull()
  })
  it('calls a job late after two of its periods', () => {
    const now = new Date('2026-10-17T12:00:00Z')
    expect(isLate('2026-10-17T11:00:00Z', '0 * * * *', now)).toBe(false)
    expect(isLate('2026-10-17T09:00:00Z', '0 * * * *', now)).toBe(true)
    expect(isLate('2026-10-16T03:15:00Z', '15 3 * * *', now)).toBe(false)
    expect(isLate('2026-10-13T03:15:00Z', '15 3 * * *', now)).toBe(true)
    expect(isLate(null, '0 * * * *', now)).toBe(false)
  })
})
