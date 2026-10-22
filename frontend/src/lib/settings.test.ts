import { describe, expect, it } from 'vitest'

import {
  FILING_KEYS,
  MEBIBYTE,
  ORGANISATION_KEYS,
  PLACED,
  SYSTEM_KEYS,
  displayValue,
  storedValue,
  timeZones,
  unplaced,
  zoneLabel,
} from './settings'

describe('bytes and megabytes', () => {
  it('shows stored bytes as megabytes and stores typed megabytes as bytes', () => {
    expect(displayValue(104857600, MEBIBYTE)).toBe(100)
    expect(displayValue(62914560, MEBIBYTE)).toBe(60)
    expect(storedValue(100, MEBIBYTE)).toBe(104857600)
    expect(storedValue(60, MEBIBYTE)).toBe(62914560)
  })
  it('rounds to a whole stored unit and leaves unscaled numbers alone', () => {
    expect(storedValue(1.00000001, MEBIBYTE)).toBe(1048576)
    expect(displayValue(7)).toBe(7)
    expect(storedValue(7)).toBe(7)
    expect(storedValue(7.4)).toBe(7)
  })
})

describe('timeZones', () => {
  it('keeps the current zone in the list even when the browser does not know it', () => {
    expect(timeZones('America/Los_Angeles')).toContain('America/Los_Angeles')
    expect(timeZones('Mars/Olympus_Mons')[0]).toBe('Mars/Olympus_Mons')
  })
})

describe('placed keys', () => {
  it('places each key in exactly one tab', () => {
    const all = [...FILING_KEYS, ...ORGANISATION_KEYS, ...SYSTEM_KEYS]
    expect(new Set(all).size).toBe(all.length)
    expect(PLACED.size).toBe(all.length)
  })
  it('places the drafting tool’s settings on System, not under Organisation’s "Other"', () => {
    for (const key of SYSTEM_KEYS) expect(PLACED.has(key), key).toBe(true)
  })
  it('places every key the seed data has, so nothing is left under "Other"', () => {
    for (const key of [
      'org.short_name',
      'core.presence_away_minutes',
      'core.presence_gone_minutes',
      'ai.house_style',
      'records.followup_body',
      'chat.attachment_max_bytes',
    ])
      expect(PLACED.has(key), key).toBe(true)
  })
  it('lists only the settings neither tab places, in the order given', () => {
    const settings = [
      { key: 'org.name' },
      { key: 'zzz.new_thing' },
      { key: 'records.followup_body' },
      { key: 'aaa.other_thing' },
    ]
    expect(unplaced(settings).map((s) => s.key)).toEqual(['zzz.new_thing', 'aaa.other_thing'])
  })
})

describe('zoneLabel', () => {
  it('reads a zone as its generic name and its abbreviation then', () => {
    expect(zoneLabel('America/Los_Angeles', new Date('2026-07-01T12:00:00Z'))).toBe('Pacific · PDT')
    expect(zoneLabel('America/Los_Angeles', new Date('2026-01-01T12:00:00Z'))).toBe('Pacific · PST')
  })
  it('falls back to the IANA name when the browser has no name for it', () => {
    expect(zoneLabel('Not/A_Zone')).toBe('Not/A Zone')
  })
})
