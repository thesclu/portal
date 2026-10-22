import { describe, expect, it } from 'vitest'

import { diffLines, revisionLines } from './diff'
import {
  blocksPayload,
  indexAfter,
  moveRow,
  partRows,
  partsOf,
  piecesBefore,
  samePlacements,
  type PartRow,
} from './parts'

describe('partRows and partsOf', () => {
  it('puts each block before the piece its position names, and past the end after them all', () => {
    const rows = partRows(
      ['one', 'two'],
      [
        { id: 'before-two', position: 1 },
        { id: 'first', position: 0 },
        { id: 'last', position: 5 },
      ],
    )
    expect(rows).toEqual([
      { kind: 'block', id: 'first' },
      { kind: 'piece', key: 'one' },
      { kind: 'block', id: 'before-two' },
      { kind: 'piece', key: 'two' },
      { kind: 'block', id: 'last' },
    ])
    expect(partsOf(rows)).toEqual({
      pieceKeys: ['one', 'two'],
      blocks: [
        { id: 'first', position: 0 },
        { id: 'before-two', position: 1 },
        { id: 'last', position: 2 },
      ],
    })
  })

  it('keeps two blocks at one place in the order given', () => {
    const rows = partRows(
      ['one'],
      [
        { id: 'a', position: 0 },
        { id: 'b', position: 0 },
      ],
    )
    expect(rows.map((row) => (row.kind === 'block' ? row.id : row.key))).toEqual(['a', 'b', 'one'])
  })
})

describe('moving and placing rows', () => {
  const rows: PartRow[] = [
    { kind: 'piece', key: 'one' },
    { kind: 'block', id: 'b' },
    { kind: 'piece', key: 'two' },
  ]

  it('moves a row and reads the new places back', () => {
    const moved = moveRow(rows, 1, 0)
    expect(partsOf(moved)).toEqual({
      pieceKeys: ['one', 'two'],
      blocks: [{ id: 'b', position: 0 }],
    })
    expect(moveRow(rows, 1, 1)).toBe(rows)
    expect(moveRow(rows, 3, 0)).toBe(rows)
  })

  it('places a new row after the selected one, or at the end', () => {
    expect(indexAfter(rows, 'piece:one')).toBe(1)
    expect(indexAfter(rows, 'block:b')).toBe(2)
    expect(indexAfter(rows, null)).toBe(3)
    expect(indexAfter(rows, 'piece:gone')).toBe(3)
    expect(piecesBefore(rows, 2)).toBe(1)
    expect(piecesBefore(rows, 0)).toBe(0)
  })

  it('writes the blocks the way the API takes them', () => {
    const blocks = [
      { id: 'a', position: 0 },
      { id: 'b', position: 2 },
    ]
    expect(blocksPayload(blocks)).toEqual({ block_ids: ['a', 'b'], sort_orders: [0, 2] })
    expect(samePlacements(blocks, [...blocks])).toBe(true)
    expect(samePlacements(blocks, [{ id: 'a', position: 1 }, blocks[1]!])).toBe(false)
    expect(samePlacements(blocks, [])).toBe(false)
  })
})

describe('revisionLines and diffLines', () => {
  const content = (body: string) => ({
    meta: { template_name: 'Sample' },
    sections: [
      { kind: 'opening', label: '', body: 'Hello.\nSecond line.' },
      { kind: 'item', label: '1', body, provision: 'sample-provision' },
      { kind: 'citation', label: 'a', body: 'A note.' },
    ],
  })

  it('prints a revision the way the letter reads', () => {
    expect(revisionLines(content('First ask'))).toEqual([
      'Name: Sample',
      '',
      'Hello.',
      'Second line.',
      '',
      '1. First ask',
      'Own deadline under: sample-provision',
      '',
      '[a] A note.',
    ])
  })

  it('reports only the line that changed', () => {
    const diff = diffLines(revisionLines(content('First ask')), revisionLines(content('New ask')))
    expect(diff.filter((line) => line.kind !== 'same')).toEqual([
      { kind: 'removed', text: '1. First ask' },
      { kind: 'added', text: '1. New ask' },
    ])
  })

  it('handles an empty side', () => {
    expect(diffLines([], ['a'])).toEqual([{ kind: 'added', text: 'a' }])
    expect(diffLines(['a'], [])).toEqual([{ kind: 'removed', text: 'a' }])
  })
})
