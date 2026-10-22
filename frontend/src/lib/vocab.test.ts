import { describe, expect, it } from 'vitest'

import { daysFigure, fileSize, humanize, money, statusTone } from './vocab'

describe('daysFigure', () => {
  it('shows time overdue with a plus, time left plain, and no deadline as a dash', () => {
    expect(daysFigure(-15)).toEqual({ text: '+15d', overdue: true })
    expect(daysFigure(10)).toEqual({ text: '10d', overdue: false })
    expect(daysFigure(0)).toEqual({ text: 'today', overdue: false })
    expect(daysFigure(null)).toEqual({ text: '—', overdue: false })
  })
})

describe('words', () => {
  it('reads codes', () => {
    expect(humanize('no_matching_request')).toBe('No matching request')
    expect(statusTone('determination_overdue')).toBe('danger')
    expect(statusTone('production_received')).toBe('ok')
    expect(statusTone('filed')).toBe('accent')
  })
  it('reads sizes and money', () => {
    expect(fileSize(900)).toBe('900 B')
    expect(fileSize(20480)).toBe('20 KB')
    expect(fileSize(3 * 1024 * 1024)).toBe('3.0 MB')
    expect(money(2500)).toBe('$25.00')
    expect(money(null)).toBe('—')
  })
})
