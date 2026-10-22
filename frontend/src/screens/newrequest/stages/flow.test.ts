import { describe, expect, it } from 'vitest'

import { groupChecks } from '../editor/Rail'
import {
  defaultStage,
  filedShare,
  howShort,
  resultParts,
  tally,
  tallyRest,
  toReserve,
  waitingSentence,
} from './stages'

describe('the stages on the v5 design (P20g)', () => {
  const recipients = [
    { id: 'r1', channel: 'email', status: 'filed', request_id: 'q1' },
    { id: 'r2', channel: 'portal', status: 'pending', request_id: 'q2' },
    { id: 'r3', channel: 'mail', status: 'pending', request_id: 'q3' },
    { id: 'r4', channel: 'email', status: 'pending', request_id: null },
    { id: 'r5', channel: 'email', status: 'held', request_id: null },
    { id: 'r6', channel: 'email', status: 'skipped', request_id: null },
  ]

  it('fills the bar by the share filed', () => {
    expect(filedShare(tally(recipients))).toBe(20)
    expect(filedShare({ filed: 7, total: 8, failed: 0, waiting: 0 })).toBe(87.5)
    expect(filedShare({ filed: 0, total: 0, failed: 0, waiting: 0 })).toBe(0)
  })

  it('reserves at the end of stage 3 only what is ticked and not reserved', () => {
    expect(toReserve(recipients).map((r) => r.id)).toEqual(['r4'])
  })

  it('opens a draft on the results once every agency still counted is reserved', () => {
    expect(defaultStage(recipients)).toBe('draft')
    const reserved = recipients.filter((r) => r.request_id || r.status === 'skipped')
    expect(defaultStage(reserved)).toBe('filed')
    expect(defaultStage([])).toBe('draft')
    expect(defaultStage([{ status: 'skipped', request_id: null }])).toBe('draft')
  })

  it('names how an agency files in the results, the post as Postal', () => {
    expect(howShort('mail', null)).toBe('Postal')
    expect(howShort('portal', { portal_kind: 'GovQA' })).toBe('GovQA')
    expect(howShort('email', null)).toBe('Email')
  })

  it('reads each result the way 060 draws it, with the row’s action', () => {
    expect(resultParts(recipients[0])).toMatchObject({ head: 'Filed', tone: 'ok', action: null })
    expect(resultParts(recipients[1])).toMatchObject({ head: 'Pending filing', action: 'open' })
    expect(resultParts(recipients[2])).toMatchObject({
      head: 'Waiting for you',
      tail: 'print and mail',
      tone: 'warn',
      action: 'log',
    })
    expect(resultParts(recipients[1], 'the provider refused it')).toMatchObject({
      head: 'Failed',
      tail: 'the provider refused it',
      tone: 'danger',
      action: 'retry',
    })
    expect(resultParts(recipients[3])).toMatchObject({ head: 'Not reserved', action: 'open' })
    expect(resultParts(recipients[4]).head).toBe('Held')
    expect(resultParts(recipients[5]).head).toBe('Not sent')
    // Filed wins over a failure remembered from before.
    expect(resultParts(recipients[0], 'old').head).toBe('Filed')
  })

  it('says what failed and what waits beside the tally', () => {
    expect(tallyRest({ filed: 6, total: 8, failed: 1, waiting: 1 })).toBe(
      '1 failed · 1 waiting for you',
    )
    expect(tallyRest({ filed: 8, total: 8, failed: 0, waiting: 0 })).toBe('')
  })

  it('says who is waiting to be mailed, one or several', () => {
    expect(waitingSentence([])).toBeNull()
    expect(waitingSentence(['TAC'])).toBe(
      'TAC is waiting to be mailed. It stays pending filing until you mark it filed from its request.',
    )
    expect(waitingSentence(['A', 'B', 'C'])).toBe(
      'A, B and C are waiting to be mailed. They stay pending filing until you mark each filed from its request.',
    )
  })

  it('puts the house-style checks in one card and the others alone, in order', () => {
    const check = (kind: string, title: string) => ({
      kind,
      title,
      detail: '',
      piece_id: null,
      fix: null,
    })
    const grouped = groupChecks([
      check('placeholder', 'Blanks'),
      check('house_style', 'Straight quote'),
      check('empty', 'Empty piece'),
      check('house_style', '“utilize” → use'),
    ])
    expect(grouped.style.map((c) => [c.index, c.check.title])).toEqual([
      [1, 'Straight quote'],
      [3, '“utilize” → use'],
    ])
    expect(grouped.others.map((c) => c.index)).toEqual([0, 2])
  })
})
