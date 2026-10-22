import { describe, expect, it } from 'vitest'

import { civilToday, isCivilDate, isRetired, parseHolidayLines, weekendNames } from './rules'

describe('parseHolidayLines', () => {
  it('reads a date alone, a date with a name, and skips blank lines', () => {
    const { holidays, errors } = parseHolidayLines('2027-01-01\n\n2027-03-15  A name\n')
    expect(errors).toEqual([])
    expect(holidays).toEqual([
      { date: '2027-01-01', name: '' },
      { date: '2027-03-15', name: 'A name' },
    ])
  })

  it('names every bad line by number and keeps the good ones', () => {
    const { holidays, errors } = parseHolidayLines('2027-01-01\nnot a date\n2027-02-30\n2027-03-01')
    expect(holidays.map((h) => h.date)).toEqual(['2027-01-01', '2027-03-01'])
    expect(errors).toEqual([
      'Line 2 does not start with a date written YYYY-MM-DD.',
      'Line 3 does not start with a date written YYYY-MM-DD.',
    ])
  })

  it('refuses a date given twice', () => {
    const { holidays, errors } = parseHolidayLines('2027-01-01 One\n2027-01-01 Again')
    expect(holidays).toHaveLength(1)
    expect(errors).toEqual(['Line 2 repeats line 1.'])
  })

  it('accepts Windows line endings and surrounding spaces', () => {
    const { holidays, errors } = parseHolidayLines('  2027-01-01 \r\n2027-01-02\tName here\r\n')
    expect(errors).toEqual([])
    expect(holidays).toEqual([
      { date: '2027-01-01', name: '' },
      { date: '2027-01-02', name: 'Name here' },
    ])
  })

  it('returns nothing for empty text', () => {
    expect(parseHolidayLines('')).toEqual({ holidays: [], errors: [] })
  })
})

describe('isCivilDate', () => {
  it('wants a real date in YYYY-MM-DD form', () => {
    expect(isCivilDate('2028-02-29')).toBe(true)
    expect(isCivilDate('2027-02-29')).toBe(false)
    expect(isCivilDate('2027-13-01')).toBe(false)
    expect(isCivilDate('27-01-01')).toBe(false)
    expect(isCivilDate('2027/01/01')).toBe(false)
  })
})

describe('weekendNames', () => {
  it('names the days in order, Monday being zero', () => {
    expect(weekendNames([6, 5])).toBe('Saturday, Sunday')
    expect(weekendNames([4])).toBe('Friday')
    expect(weekendNames([])).toBe('none')
  })
})

describe('isRetired and civilToday', () => {
  it('retires a rule only once its end date has passed', () => {
    expect(isRetired({ effective_to: null }, '2026-10-06')).toBe(false)
    expect(isRetired({ effective_to: '2026-10-06' }, '2026-10-06')).toBe(false)
    expect(isRetired({ effective_to: '2026-10-05' }, '2026-10-06')).toBe(true)
  })
  it('gives today in the zone asked for', () => {
    const late = new Date('2026-10-07T03:00:00Z')
    expect(civilToday('America/Los_Angeles', late)).toBe('2026-10-06')
    expect(civilToday('UTC', late)).toBe('2026-10-07')
    expect(civilToday('Not/AZone', late)).toBe('2026-10-07')
  })
})
