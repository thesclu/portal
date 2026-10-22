import { describe, expect, it } from 'vitest'

import {
  filingChoice,
  filingLabel,
  fromFilingChoice,
  knownJurisdictions,
  letterLines,
  medianText,
  parseLetter,
  sortAgencies,
} from './agencies'

const agency = (name: string, more: object = {}) => ({ name, jurisdiction: 'Testland', ...more })

describe('filingLabel', () => {
  it('reads each way of filing, a portal by its kind, and warns when none is set', () => {
    expect(filingLabel(agency('A', { filing_method: 'email' }))).toEqual({
      text: 'Email',
      missing: false,
    })
    expect(filingLabel(agency('B', { filing_method: 'mail' })).text).toBe('Postal mail')
    expect(
      filingLabel(agency('C', { filing_method: 'portal', portal_kind: 'Web form' })).text,
    ).toBe('Web form')
    expect(
      filingLabel(
        agency('D', {
          filing_method: 'portal',
          portal_kind: 'GovQA',
          portal_automation: 'filing_only',
        }),
      ).text,
    ).toBe('GovQA · automatic')
    expect(filingLabel(agency('E'))).toEqual({ text: 'Needs a filing method', missing: true })
  })
})

describe('the Files by choice', () => {
  it('turns the two directory fields into one choice and back', () => {
    expect(filingChoice({ filing_method: 'portal', portal_kind: 'GovQA' })).toBe('portal:GovQA')
    expect(filingChoice({ filing_method: 'email', portal_kind: null })).toBe('email')
    expect(fromFilingChoice('portal:Web form')).toEqual({
      filing_method: 'portal',
      portal_kind: 'Web form',
    })
    expect(fromFilingChoice('mail')).toEqual({ filing_method: 'mail', portal_kind: null })
    expect(fromFilingChoice('')).toEqual({ filing_method: null, portal_kind: null })
  })
})

describe('Letter goes to', () => {
  it('draws the office, street and city as lines, leaving out what is blank', () => {
    expect(
      letterLines(
        agency('A', {
          recipient_office: 'Records Unit',
          mailing_street: null,
          mailing_city_state_zip: 'Testville, TL 00000',
        }),
      ),
    ).toEqual(['Records Unit', 'Testville, TL 00000'])
  })

  it('reads written lines back into the three fields', () => {
    expect(parseLetter('Records Unit\n1 Test Street\nSuite 2\nTestville')).toEqual({
      recipient_office: 'Records Unit',
      mailing_street: '1 Test Street, Suite 2',
      mailing_city_state_zip: 'Testville',
    })
    expect(parseLetter('1 Test Street\nTestville')).toEqual({
      recipient_office: null,
      mailing_street: '1 Test Street',
      mailing_city_state_zip: 'Testville',
    })
    expect(parseLetter('  Records Unit  ')).toEqual({
      recipient_office: 'Records Unit',
      mailing_street: null,
      mailing_city_state_zip: null,
    })
    expect(parseLetter('\n')).toEqual({
      recipient_office: null,
      mailing_street: null,
      mailing_city_state_zip: null,
    })
  })
})

describe('the list', () => {
  const rows = [
    agency('Beta', { requests_total: 3, open_requests: 1, median_days_to_determination: 9 }),
    agency('Alpha', { requests_total: 3, open_requests: 2, median_days_to_determination: null }),
    agency('Gamma', { requests_total: 7, open_requests: 0, median_days_to_determination: 20 }),
  ]
  it('orders by most requests, most open, slowest or name, ties by name', () => {
    expect(sortAgencies(rows, 'requests').map((a) => a.name)).toEqual(['Gamma', 'Alpha', 'Beta'])
    expect(sortAgencies(rows, 'open').map((a) => a.name)).toEqual(['Alpha', 'Beta', 'Gamma'])
    expect(sortAgencies(rows, 'slowest').map((a) => a.name)).toEqual(['Gamma', 'Beta', 'Alpha'])
    expect(sortAgencies(rows, 'name').map((a) => a.name)).toEqual(['Alpha', 'Beta', 'Gamma'])
  })

  it('names a median in days, one day singular, nothing as a dash', () => {
    expect(medianText(11)).toBe('11 days')
    expect(medianText(1)).toBe('1 day')
    expect(medianText(null)).toBe('—')
  })

  it('offers each jurisdiction and law once', () => {
    expect(
      knownJurisdictions([
        agency('A', { records_law: 'TESTLAW' }),
        agency('B', { records_law: 'TESTLAW' }),
        agency('C', { jurisdiction: 'Otherland', records_law: null }),
      ]).map((k) => k.label),
    ).toEqual(['Otherland', 'Testland · TESTLAW'])
  })
})
