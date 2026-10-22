import { describe, expect, it } from 'vitest'

import {
  conflictingPair,
  fileMeta,
  fileTag,
  foldThread,
  itemState,
  senderName,
  statusSteps,
  taskDue,
  type DeadlineLike,
  type RequestLike,
} from './requestView'

const change = (status: string, at: string) => ({ status, effective_at: at, voided_at: null })
const deadline = (id: string, type: string, due: string, status = 'active'): DeadlineLike => ({
  id,
  deadline_type: type,
  request_item_id: null,
  due_at: due,
  status,
  basis: 'statutory',
  legal_basis: null,
  provision: null,
})
const request = (patch: Partial<RequestLike>): RequestLike => ({
  current_status: 'filed',
  filed_at: '2026-08-11T17:00:00Z',
  status_history: [change('filed', '2026-08-11T17:00:00Z')],
  deadlines: [],
  ...patch,
})
const shape = (steps: ReturnType<typeof statusSteps>) => steps.map((s) => `${s.state}:${s.label}`)

describe('status steps', () => {
  it('draws a request not yet filed as 076 does', () => {
    const steps = statusSteps(
      request({ current_status: 'pending_filing', filed_at: null, status_history: [] }),
    )
    expect(shape(steps)).toEqual([
      'current:Filing',
      'future:Acknowledged',
      'future:Determination',
      'future:Production',
      'future:Closed',
    ])
    expect(steps[0].sub).toBe('Pending')
  })

  it('draws a request just filed as 077 does: no Acknowledged, the determination due', () => {
    const steps = statusSteps(
      request({ deadlines: [deadline('d1', 'statutory_determination', '2026-09-18')] }),
    )
    expect(shape(steps)).toEqual([
      'done:Filed',
      'current:Determination due',
      'future:Production',
      'future:Closed',
    ])
    expect(steps[1].deadlines?.map((d) => d.deadline.due_at)).toEqual(['2026-09-18'])
  })

  it('draws a partial production as 062 does, with the second date in warn', () => {
    const steps = statusSteps(
      request({
        current_status: 'partial_production',
        agency_reference_number: 'REF-1',
        status_history: [
          change('filed', '2026-08-11T17:00:00Z'),
          change('acknowledged', '2026-08-13T17:00:00Z'),
          change('determination_issued', '2026-08-20T17:00:00Z'),
          change('partial_production', '2026-09-07T17:00:00Z'),
        ],
        deadlines: [
          deadline('a', 'production', '2026-10-03'),
          deadline('b', 'production', '2026-09-18'),
          deadline('c', 'statutory_determination', '2026-08-21', 'satisfied'),
        ],
      }),
    )
    expect(shape(steps)).toEqual([
      'done:Filed',
      'done:Acknowledged',
      'done:Determination',
      'done:Partial production',
      'current:Production due',
      'future:Closed',
    ])
    expect(steps[1].detail).toBe('Agency ref REF-1')
    const due = steps[4]
    expect(due.deadlines?.map((d) => [d.deadline.due_at, d.tone])).toEqual([
      ['2026-09-18', 'ink'],
      ['2026-10-03', 'warn'],
    ])
    expect(due.conflict?.map((d) => d.id)).toEqual(['b', 'a'])
  })

  it('draws a closed request as 078 does, every step done', () => {
    const steps = statusSteps(
      request({
        current_status: 'closed',
        status_history: [
          change('filed', '2026-08-11T17:00:00Z'),
          change('acknowledged', '2026-08-13T17:00:00Z'),
          change('determination_issued', '2026-08-20T17:00:00Z'),
          change('production_received', '2026-09-22T17:00:00Z'),
          change('closed', '2026-10-14T17:00:00Z'),
        ],
      }),
    )
    expect(shape(steps)).toEqual([
      'done:Filed',
      'done:Acknowledged',
      'done:Determination',
      'done:Production',
      'done:Closed',
    ])
  })

  it('ignores a voided status and shows a passed date in danger', () => {
    const steps = statusSteps(
      request({
        current_status: 'determination_overdue',
        status_history: [
          change('filed', '2026-08-11T17:00:00Z'),
          { ...change('acknowledged', '2026-08-12T17:00:00Z'), voided_at: '2026-08-13T00:00:00Z' },
          change('determination_overdue', '2026-08-22T17:00:00Z'),
        ],
        deadlines: [deadline('m', 'statutory_determination', '2026-08-21', 'missed')],
      }),
    )
    expect(shape(steps)).toEqual([
      'done:Filed',
      'current:Determination due',
      'future:Production',
      'future:Closed',
    ])
    expect(steps[1].deadlines?.[0].tone).toBe('danger')
  })

  it('keeps production dates already on file under the step ahead, with their conflict', () => {
    const steps = statusSteps(
      request({
        deadlines: [
          deadline('d', 'statutory_determination', '2026-09-18'),
          deadline('p1', 'production', '2026-10-17'),
          deadline('p2', 'production', '2026-10-26'),
        ],
      }),
    )
    const production = steps.find((s) => s.key === 'production')
    expect(production?.state).toBe('future')
    expect(production?.deadlines?.map((d) => d.tone)).toEqual(['ink', 'warn'])
    expect(production?.conflict?.map((d) => d.id)).toEqual(['p1', 'p2'])
  })

  it('stops at a denial and at no records', () => {
    const denied = statusSteps(
      request({
        current_status: 'no_records_claimed',
        status_history: [
          change('filed', '2026-08-11T17:00:00Z'),
          change('no_records_claimed', '2026-08-20T17:00:00Z'),
        ],
      }),
    )
    expect(shape(denied)).toEqual(['done:Filed', 'done:No records', 'future:Closed'])
  })

  it('adds the appeal and what is due on it', () => {
    const steps = statusSteps(
      request({
        current_status: 'appealed',
        status_history: [
          change('filed', '2026-08-11T17:00:00Z'),
          change('denied', '2026-08-20T17:00:00Z'),
          change('appealed', '2026-08-25T17:00:00Z'),
        ],
        deadlines: [deadline('c', 'challenge_response', '2026-09-30')],
      }),
    )
    expect(shape(steps)).toEqual([
      'done:Filed',
      'done:Denied',
      'done:Appealed',
      'current:Response due',
      'future:Closed',
    ])
  })
})

describe('the rest of the page', () => {
  it('finds the pair of dates for one thing', () => {
    expect(conflictingPair([deadline('a', 'production', '2026-10-01')])).toBeNull()
  })

  it('reads an item', () => {
    expect(itemState('produced')).toBe('produced')
    expect(itemState('partly')).toBe('withheld')
    expect(itemState('withheld')).toBe('withheld')
    expect(itemState(null)).toBe('open')
    expect(itemState('not_yet')).toBe('open')
  })

  it('reads a task’s due date', () => {
    expect(taskDue('2026-10-16', '2026-10-16')).toEqual({ text: 'Today', late: true, today: true })
    expect(taskDue('2026-10-10', '2026-10-16').late).toBe(true)
    expect(taskDue('2026-10-20', '2026-10-16').late).toBe(false)
    expect(taskDue(null, '2026-10-16').text).toBeNull()
  })

  it('folds a thread as 062 does', () => {
    expect(foldThread(6, false)).toEqual([
      { kind: 'message', index: 0 },
      { kind: 'more', indexes: [1, 2, 3] },
      { kind: 'message', index: 4 },
      { kind: 'message', index: 5 },
    ])
    expect(foldThread(3, false)).toHaveLength(3)
    expect(foldThread(6, true)).toHaveLength(6)
  })

  it('names files and senders', () => {
    expect(fileTag('a.b.pdf')).toBe('PDF')
    expect(fileTag('README')).toBe('FILE')
    expect(fileMeta(1, '96 KB')).toBe('1 page · 96 KB')
    expect(fileMeta(null, '96 KB')).toBe('96 KB')
    expect(senderName('Records Unit <records@agency.example>')).toEqual({
      name: 'Records Unit',
      email: 'records@agency.example',
    })
    expect(senderName('records@agency.example')).toEqual({
      name: 'records@agency.example',
      email: '',
    })
  })
})
