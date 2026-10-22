import { describe, expect, it } from 'vitest'

import {
  attachmentLabel,
  filesBy,
  formatDay,
  groupRecipients,
  hostOf,
  howLabel,
  mailingLines,
  nextPending,
  orgNameOf,
  resultOf,
  sendsTo,
  tally,
  tallyText,
  templateMatches,
  willCreate,
} from './stages'

const govqa = { id: 'a1', portal_kind: 'GovQA' }
const form = { id: 'a2', portal_kind: 'Web form' }

describe('filesBy and howLabel', () => {
  it('names the channel the way the design does', () => {
    expect(filesBy('email')).toBe('Email')
    expect(filesBy('mail')).toBe('Postal mail')
    expect(filesBy('portal', govqa)).toBe('GovQA portal')
    expect(filesBy('portal', form)).toBe('Web form')
    expect(filesBy('portal', { portal_kind: null })).toBe('Portal')
  })
  it('shortens a portal to its kind in a list', () => {
    expect(howLabel('portal', govqa)).toBe('GovQA')
    expect(howLabel('portal', null)).toBe('Portal')
    expect(howLabel('mail')).toBe('Postal mail')
  })
})

describe('sendsTo', () => {
  const agency = {
    default_request_email: 'records@agency.example.org',
    portal_url: 'https://www.ucdavis.govqa.us/',
    mailing_street: '3500 Canyon Crest Dr',
    mailing_city_state_zip: 'Riverside, CA 92507',
  }
  it('picks the address for the channel', () => {
    expect(sendsTo('email', agency)).toBe('records@agency.example.org')
    expect(sendsTo('portal', agency)).toBe('ucdavis.govqa.us')
    expect(sendsTo('mail', agency)).toBe('3500 Canyon Crest Dr, Riverside, CA 92507')
  })
  it('says when nothing is set', () => {
    expect(sendsTo('email', null)).toBe('No address set')
    expect(sendsTo('portal', { ...agency, portal_url: null })).toBe('No portal set')
    expect(sendsTo('mail', {})).toBe('No mailing address set')
  })
  it('reads a host off a URL', () => {
    expect(hostOf('http://records.coastlinestate.edu/public-records-request/')).toBe(
      'records.coastlinestate.edu/public-records-request',
    )
  })
  it('lists the postal lines that exist', () => {
    expect(mailingLines({ recipient_office: 'Police Records Unit', ...agency })).toEqual([
      'Police Records Unit',
      '3500 Canyon Crest Dr',
      'Riverside, CA 92507',
    ])
    expect(mailingLines(null)).toEqual([])
  })
})

describe('groupRecipients and willCreate', () => {
  const recipients = [
    { id: 'r1', agency_id: 'a1', channel: 'portal', status: 'pending' },
    { id: 'r2', agency_id: 'e1', channel: 'email', status: 'pending' },
    { id: 'r3', agency_id: 'm1', channel: 'mail', status: 'pending' },
    { id: 'r4', agency_id: 'e2', channel: 'email', status: 'skipped' },
    { id: 'r5', agency_id: 'a1', channel: 'portal', status: 'filed' },
  ]
  it('splits what is emailed from what the person files', () => {
    const groups = groupRecipients(recipients)
    expect(groups.map((g) => [g.title, g.rows.map((r) => r.id)])).toEqual([
      ['Sent by email', ['r2', 'r4']],
      ['You file', ['r1', 'r3', 'r5']],
    ])
    expect(groupRecipients([recipients[1]]).map((g) => g.key)).toEqual(['email'])
  })
  it('counts one request per agency not skipped, by how it files', () => {
    const held = { id: 'r6', agency_id: 'm2', channel: 'mail', status: 'held' }
    expect(willCreate([...recipients, held], [govqa, form])).toEqual({
      requests: 4,
      rows: [
        { label: 'GovQA portal', count: 2 },
        { label: 'Sent by email', count: 1 },
        { label: 'Postal mail', count: 1 },
      ],
    })
  })
})

describe('resultOf and tally', () => {
  it('reads each state the way the results table does', () => {
    expect(resultOf({ status: 'filed', channel: 'email', request_id: 'q' }, null).text).toBe('Sent')
    expect(resultOf({ status: 'filed', channel: 'portal', request_id: 'q' }, govqa).text).toBe(
      'Filed on GovQA',
    )
    expect(resultOf({ status: 'filed', channel: 'portal', request_id: 'q' }, form).text).toBe(
      'Filed on Web form',
    )
    expect(resultOf({ status: 'filed', channel: 'mail', request_id: 'q' }, null).text).toBe(
      'Mailed',
    )
    expect(resultOf({ status: 'pending', channel: 'mail', request_id: 'q' }, null)).toMatchObject({
      kind: 'waiting',
      text: 'Waiting for you · print and mail',
    })
    expect(resultOf({ status: 'pending', channel: 'email', request_id: 'q' }, null).text).toBe(
      'Pending filing',
    )
    expect(resultOf({ status: 'pending', channel: 'email', request_id: null }, null).kind).toBe(
      'unreserved',
    )
    expect(resultOf({ status: 'skipped', channel: 'email', request_id: null }, null).text).toBe(
      'Not sent',
    )
    expect(resultOf({ status: 'held', channel: 'email', request_id: null }, null).kind).toBe('held')
  })
  it('puts a failure ahead of pending but never ahead of filed', () => {
    const pending = { status: 'pending', channel: 'email', request_id: 'q' }
    expect(resultOf(pending, null, 'the provider refused it').text).toBe(
      'Failed · the provider refused it',
    )
    expect(resultOf({ ...pending, status: 'filed' }, null, 'stale').kind).toBe('filed')
  })
  it('adds up filed, failed and waiting, leaving skipped out', () => {
    const rows = [
      { id: '1', status: 'filed', channel: 'email', request_id: 'q' },
      { id: '2', status: 'filed', channel: 'portal', request_id: 'q' },
      { id: '3', status: 'pending', channel: 'email', request_id: 'q' },
      { id: '4', status: 'pending', channel: 'mail', request_id: 'q' },
      { id: '5', status: 'skipped', channel: 'email', request_id: null },
    ]
    const t = tally(rows, { '3': 'the portal rejected the PDF' })
    expect(t).toEqual({ filed: 2, total: 4, failed: 1, waiting: 1 })
    expect(tallyText(t)).toBe('2 of 4 filed · 1 failed · 1 waiting for you')
    expect(tallyText({ filed: 8, total: 8, failed: 0, waiting: 0 })).toBe('8 of 8 filed')
  })
})

describe('attachmentLabel', () => {
  it('names the file and its pages', () => {
    expect(attachmentLabel('SCLU_CPRA_UCSC-PD_ALPR.pdf', 2)).toBe(
      'SCLU_CPRA_UCSC-PD_ALPR.pdf · 2 pages',
    )
    expect(attachmentLabel('x.pdf', 1)).toBe('x.pdf · 1 page')
    expect(attachmentLabel('x.pdf', null)).toBe('x.pdf')
    expect(attachmentLabel('', null)).toBe('letter.pdf')
  })
})

describe('nextPending', () => {
  const rows = [
    { id: 'a', status: 'filed' },
    { id: 'b', status: 'pending' },
    { id: 'c', status: 'pending' },
    { id: 'd', status: 'held' },
  ]
  it('finds the first pending one not deferred and not the one being left', () => {
    expect(nextPending(rows, new Set())?.id).toBe('b')
    expect(nextPending(rows, new Set(), 'b')?.id).toBe('c')
    expect(nextPending(rows, new Set(['b', 'c']))).toBeNull()
  })
})

describe('templateMatches and formatDay and orgNameOf', () => {
  it('matches on name, basis or jurisdiction, any case', () => {
    const t = { name: 'ALPR contracts', legal_basis: 'CPRA', jurisdiction: 'CA' }
    expect(templateMatches(t, '')).toBe(true)
    expect(templateMatches(t, 'alpr')).toBe(true)
    expect(templateMatches(t, 'cpra')).toBe(true)
    expect(templateMatches(t, 'texas')).toBe(false)
  })
  it('formats the day in the reader’s zone', () => {
    expect(formatDay('2026-08-13T03:30:00Z', 'America/Los_Angeles')).toBe('Aug 12, 2026')
    expect(formatDay('2026-08-13T03:30:00Z', 'Not/AZone')).toBe('Aug 13, 2026')
    expect(formatDay(null, 'UTC')).toBe('—')
  })
  it('reads the organisation name out of the settings', () => {
    expect(orgNameOf([{ key: 'org.name', value: ' SCLU ' }])).toBe('SCLU')
    expect(orgNameOf([{ key: 'org.name', value: 3 }])).toBe('')
    expect(orgNameOf(undefined)).toBe('')
  })
})
