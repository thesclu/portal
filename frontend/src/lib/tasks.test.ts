import { describe, expect, it } from 'vitest'

import {
  byDue,
  civilDate,
  daysBetween,
  dueCell,
  dueCounts,
  dueNowCount,
  dueOf,
  historyLines,
  taskTitle,
} from './tasks'

// A Wednesday, mid-afternoon in Los Angeles.
const NOW = new Date('2026-10-14T22:00:00Z')
const LA = 'America/Los_Angeles'

describe('taskTitle', () => {
  it('names a system task by its type and a person’s task by its first line', () => {
    expect(taskTitle({ task_type: 'manual_followup', description: 'x', due_at: null })).toBe(
      'Manual follow-up',
    )
    expect(taskTitle({ task_type: 'confirm_filed', description: 'x', due_at: null })).toBe(
      'Confirm filed',
    )
    expect(taskTitle({ task_type: 'manual', description: 'Call them\nmore', due_at: null })).toBe(
      'Call them',
    )
  })
})

describe('dueOf', () => {
  it('counts overdue days on the reader’s calendar', () => {
    expect(dueOf('2026-10-11T18:00:00Z', NOW, LA)).toEqual({ group: 'overdue', label: '+3d' })
  })

  it('reads a date later today as today', () => {
    expect(dueOf('2026-10-15T05:00:00Z', NOW, LA)).toEqual({ group: 'today', label: 'Today' })
  })

  it('names a day later this week by its weekday, and later ones by date', () => {
    expect(dueOf('2026-10-16T18:00:00Z', NOW, LA)).toEqual({ group: 'week', label: 'Fri' })
    expect(dueOf('2026-10-20T18:00:00Z', NOW, LA)).toEqual({ group: 'later', label: 'Oct 20' })
  })

  it('puts a task with no date last, unlabelled', () => {
    expect(dueOf(null, NOW, LA)).toEqual({ group: 'later', label: '' })
  })

  it('counts the badge as due today or overdue', () => {
    const tasks = ['2026-10-11T18:00:00Z', '2026-10-15T05:00:00Z', '2026-10-20T18:00:00Z', null]
    expect(
      dueNowCount(
        tasks.map((due_at) => ({ task_type: 'x', description: '', due_at })),
        NOW,
        LA,
      ),
    ).toBe(2)
  })
})

describe('civil dates', () => {
  it('reads an instant on the zone’s calendar and counts whole days', () => {
    expect(civilDate(NOW, LA)).toBe('2026-10-14')
    expect(civilDate(NOW, 'Asia/Tokyo')).toBe('2026-10-15')
    expect(daysBetween('2026-10-14', '2026-10-20')).toBe(6)
  })
})

describe('dueCell', () => {
  it('draws an overdue date with its days, today as Today, a later date as a date', () => {
    expect(dueCell('2026-10-11T18:00:00Z', NOW, LA)).toMatchObject({
      text: 'Oct 11 · +3d',
      group: 'overdue',
    })
    expect(dueCell('2026-10-15T05:00:00Z', NOW, LA).text).toBe('Today')
    expect(dueCell('2026-10-30T18:00:00Z', NOW, LA).text).toBe('Oct 30')
    expect(dueCell(null, NOW, LA).text).toBe('')
  })
})

describe('byDue and dueCounts', () => {
  const task = (id: string, due_at: string | null, created_at = '2026-10-01T00:00:00Z') => ({
    id,
    task_type: 'manual',
    description: id,
    due_at,
    completed_at: null,
    created_at,
  })
  it('puts the soonest first and the undated last', () => {
    const order = byDue([
      task('none', null),
      task('later', '2026-10-30T18:00:00Z'),
      task('late', '2026-10-11T18:00:00Z'),
    ]).map((t) => t.id)
    expect(order).toEqual(['late', 'later', 'none'])
  })
  it('counts overdue, today and later this week', () => {
    expect(
      dueCounts(
        [
          task('a', '2026-10-11T18:00:00Z'),
          task('b', '2026-10-15T05:00:00Z'),
          task('c', '2026-10-16T18:00:00Z'),
          task('d', '2026-11-30T18:00:00Z'),
          task('e', null),
        ],
        NOW,
        LA,
      ),
    ).toEqual({ overdue: 1, today: 1, week: 1 })
  })
})

describe('historyLines', () => {
  const names: Record<string, string> = { u1: 'Ana Ruiz', u2: 'Ben Ito' }
  const nameOf = (id: string | null | undefined) => (id ? (names[id] ?? null) : null)
  it('says who made it, what changed and who completed it, oldest first', () => {
    const lines = historyLines(
      {
        id: 't',
        task_type: 'retry_delivery',
        description: 'x',
        due_at: null,
        created_at: '2026-10-01T16:00:00Z',
        created_by_system: 'postmark_delivery',
        created_by_user_id: null,
        completed_at: '2026-10-05T16:00:00Z',
        completed_by_id: 'u2',
      },
      [
        {
          at: '2026-10-02T16:00:00Z',
          field: 'assigned_to_user_id',
          old_value: null,
          new_value: 'u2',
          note: 'Assigned to Ben Ito.',
          actor_name: 'Ana Ruiz',
        },
        {
          at: '2026-10-03T16:00:00Z',
          field: 'due_at',
          old_value: null,
          new_value: '2026-10-20T17:00:00Z',
          note: 'they asked for time',
          actor_name: 'Ana Ruiz',
        },
      ],
      nameOf,
      LA,
    )
    expect(lines.map((l) => `${l.who} ${l.what}`)).toEqual([
      'System created this task',
      'Ana Ruiz reassigned it to Ben Ito',
      'Ana Ruiz moved the due date to Oct 20 · “they asked for time”',
      'Ben Ito completed it',
    ])
  })
})

describe('historyLines with notes (P23b)', () => {
  it('puts each note in its place, a voided one said to be voided', () => {
    const lines = historyLines(
      {
        id: 't',
        task_type: 'manual',
        description: 'x',
        due_at: null,
        created_at: '2026-10-01T16:00:00Z',
        created_by_system: null,
        created_by_user_id: 'u1',
        completed_at: null,
        completed_by_id: null,
      },
      [],
      (id) => (id === 'u1' ? 'Ana Ruiz' : null),
      LA,
      [
        {
          id: 'n2',
          author_name: 'Ben Ito',
          body: 'Wrong one',
          created_at: '2026-10-03T16:00:00Z',
          voided_at: '2026-10-03T17:00:00Z',
          voided_by_name: 'Ana Ruiz',
          void_reason: 'Not this task',
        },
        {
          id: 'n1',
          author_name: 'Ana Ruiz',
          body: 'Called the records desk',
          created_at: '2026-10-02T16:00:00Z',
          voided_at: null,
          voided_by_name: null,
          void_reason: null,
        },
      ],
    )
    expect(lines.map((l) => `${l.who} ${l.what}`)).toEqual([
      'Ana Ruiz created this task',
      'Ana Ruiz noted: “Called the records desk”',
      'Ben Ito noted: “Wrong one”',
    ])
    expect(lines[2].note).toEqual({
      id: 'n2',
      voided: true,
      voidedWords: 'Voided by Ana Ruiz · Not this task',
    })
    expect(lines[1].note?.voided).toBe(false)
  })
})
