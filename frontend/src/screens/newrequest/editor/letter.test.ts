import type { JSONContent } from '@tiptap/core'
import { describe, expect, it } from 'vitest'

import {
  agoLabel,
  canDemote,
  demotePiece,
  docFromText,
  firstWords,
  inlineFromText,
  insertPiece,
  letterOf,
  localCitations,
  localPieces,
  metaLine,
  movePiece,
  nextItemNumber,
  nextMarker,
  normalize,
  plainText,
  promotePiece,
  removePiece,
  reorderPieces,
  savePayload,
  withServerIds,
  type LocalPiece,
} from './letter'

const piece = (over: Partial<LocalPiece> & { key: string }): LocalPiece => ({
  id: null,
  kind: 'item',
  item_number: '',
  parent_key: null,
  provision: null,
  doc: { type: 'doc', content: [{ type: 'paragraph' }] },
  ...over,
})

describe('placeholder chips from text', () => {
  it('turns known tokens into placeholder nodes and leaves unknown ones as text', () => {
    expect(inlineFromText('VIA {{CHANNEL_METHOD}}')).toEqual([
      { type: 'text', text: 'VIA ' },
      { type: 'placeholder', attrs: { name: 'CHANNEL_METHOD' } },
    ])
    expect(inlineFromText('{{NOT_A_NAME}} x')).toEqual([{ type: 'text', text: '{{NOT_A_NAME}} x' }])
    expect(inlineFromText('')).toEqual([])
  })
  it('reads blank lines as paragraphs and single newlines as hard breaks', () => {
    expect(docFromText('{{FILING_DATE}}\n{{AGENCY_FULL}}\n\nsecond')).toEqual({
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [
            { type: 'placeholder', attrs: { name: 'FILING_DATE' } },
            { type: 'hardBreak' },
            { type: 'placeholder', attrs: { name: 'AGENCY_FULL' } },
          ],
        },
        { type: 'paragraph', content: [{ type: 'text', text: 'second' }] },
      ],
    })
    expect(docFromText('  \n\n ')).toEqual({ type: 'doc', content: [{ type: 'paragraph' }] })
  })
})

describe('numbering', () => {
  it('letters sub-items', () => {
    expect(letterOf(1)).toBe('a')
    expect(letterOf(26)).toBe('z')
    expect(letterOf(27)).toBe('aa')
  })
  it('numbers by position: top-level items count, sub-items letter under the item above', () => {
    const out = normalize([
      piece({ key: 'a', item_number: '7' }),
      piece({ key: 'b', kind: 'prose' }),
      piece({ key: 'c', parent_key: 'a' }),
      piece({ key: 'd', parent_key: 'zzz' }),
      piece({ key: 'e' }),
    ])
    expect(out.map((p) => [p.item_number, p.parent_key])).toEqual([
      ['1', null],
      ['', null],
      ['1a', 'a'],
      ['1b', 'a'],
      ['2', null],
    ])
  })
  it('promotes a sub-item with nothing above it and keeps untouched pieces by identity', () => {
    const first = piece({ key: 'a', parent_key: 'b' })
    const second = piece({ key: 'b', item_number: '2' })
    const out = normalize([first, second])
    expect(out[0]).toEqual({ ...first, parent_key: null, item_number: '1' })
    expect(out[1]).toBe(second)
  })
  it('names the next number or letter', () => {
    const pieces = normalize([piece({ key: 'a' }), piece({ key: 'b', parent_key: 'a' })])
    expect(nextItemNumber(pieces, null)).toBe('2')
    expect(nextItemNumber(pieces, pieces[0]!)).toBe('1b')
  })
  it('picks the next footnote marker past the highest number', () => {
    expect(nextMarker([])).toBe('1')
    expect(nextMarker([{ marker: '3' }, { marker: '1' }, { marker: '*' }])).toBe('4')
  })
})

describe('edits', () => {
  const base = () =>
    normalize([
      piece({ key: 'a' }),
      piece({ key: 'b' }),
      piece({ key: 'c', parent_key: 'b' }),
      piece({ key: 'd', kind: 'prose' }),
    ])

  it('inserts an item that joins the group it lands in, or a paragraph with no number', () => {
    const items = insertPiece(base(), 3, 'item')
    expect(items.map((p) => p.item_number)).toEqual(['1', '2', '2a', '2b', ''])
    expect(items[3]!.parent_key).toBe('b')
    const prose = insertPiece(base(), 0, 'prose')
    expect(prose[0]!.kind).toBe('prose')
    expect(prose.map((p) => p.item_number)).toEqual(['', '1', '2', '2a', ''])
  })
  it('removes and renumbers', () => {
    expect(removePiece(base(), 'a').map((p) => p.item_number)).toEqual(['1', '1a', ''])
  })
  it('moves and renumbers, re-parenting a sub-item to the item it now sits under', () => {
    const moved = movePiece(base(), 2, 0)
    expect(moved.map((p) => [p.key, p.item_number])).toEqual([
      ['c', '1'],
      ['a', '2'],
      ['b', '3'],
      ['d', ''],
    ])
    const same = base()
    expect(movePiece(same, 1, 1)).toBe(same)
  })
  it('demotes under the previous item and promotes back', () => {
    const demoted = demotePiece(base(), 'b')
    expect(demoted.map((p) => p.item_number)).toEqual(['1', '1a', '1b', ''])
    expect(canDemote(base(), 'a')).toBe(false)
    expect(canDemote(base(), 'b')).toBe(true)
    expect(canDemote(base(), 'c')).toBe(false)
    const promoted = promotePiece(base(), 'c')
    expect(promoted.map((p) => p.item_number)).toEqual(['1', '2', '3', ''])
  })
})

describe('the save', () => {
  it('sends pieces in order with ids kept, parents by id or by position, and no plain body', () => {
    const pieces = normalize([
      piece({ key: 'a', id: 'id-a' }),
      piece({ key: 'b' }),
      piece({ key: 'c', parent_key: 'b' }),
      piece({ key: 'd', kind: 'prose', id: 'id-d' }),
      piece({ key: 'e', parent_key: 'a' }),
    ])
    const body = savePayload('Subject', pieces, [
      { key: 'x', id: 'cit-1', marker: '1', citation_text: 'one', item_key: 'a' },
      { key: 'y', id: null, marker: '2', citation_text: 'two', item_key: 'b' },
    ])
    expect(body.subject).toBe('Subject')
    expect(body.pieces.map((p) => p.id)).toEqual(['id-a', null, null, 'id-d', null])
    // 'e' sits under the nearest top-level item above it, which is 'b'.
    expect(body.pieces.map((p) => p.item_number)).toEqual(['1', '2', '2a', '', '2b'])
    expect(body.pieces[2]).toMatchObject({ parent_index: 1 })
    expect(body.pieces[2]).not.toHaveProperty('parent_item_id')
    expect(body.pieces.every((p) => !('body' in p))).toBe(true)
    expect(body.pieces[0]!.body_rich).toEqual(pieces[0]!.doc)
    expect(body.citations).toEqual([
      { id: 'cit-1', marker: '1', citation_text: 'one', item_id: 'id-a' },
      { id: null, marker: '2', citation_text: 'two', item_id: null },
    ])
  })
  it('takes the ids the server minted by position', () => {
    const local = [
      { key: 'a', id: 'id-a' },
      { key: 'b', id: null },
      { key: 'c', id: null },
    ]
    const out = withServerIds(local, ['a', 'b'], [{ id: 'id-a' }, { id: 'id-b' }])
    expect(out.map((r) => r.id)).toEqual(['id-a', 'id-b', null])
    expect(out[0]).toBe(local[0])
  })
})

describe('from the server', () => {
  it('reads pieces in sort order with parents by key and a document from body text', () => {
    const pieces = localPieces([
      {
        id: 'p2',
        kind: 'item',
        item_number: '2',
        parent_item_id: null,
        sort_order: 2,
        provision: null,
        body: 'two',
        body_rich: null,
      },
      {
        id: 'p1',
        kind: 'item',
        item_number: '1',
        parent_item_id: null,
        sort_order: 1,
        provision: 'x',
        body: '',
        body_rich: { type: 'doc', content: [{ type: 'paragraph' }] },
      },
      {
        id: 'p2a',
        kind: 'item',
        item_number: '2a',
        parent_item_id: 'p2',
        sort_order: 3,
        provision: null,
        body: 'sub',
        body_rich: null,
      },
    ])
    expect(pieces.map((p) => p.id)).toEqual(['p1', 'p2', 'p2a'])
    expect(pieces[2]!.parent_key).toBe(pieces[1]!.key)
    expect(pieces[1]!.doc).toEqual({
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text: 'two' }] }],
    })
    const cites = localCitations(
      [{ id: 'c', marker: '1', citation_text: 't', item_id: 'p2a', sort_order: 1 }],
      pieces,
    )
    expect(cites[0]!.item_key).toBe(pieces[2]!.key)
  })
})

describe('labels', () => {
  it('ticks seconds, then minutes', () => {
    expect(agoLabel(8)).toBe('saved 8s ago')
    expect(agoLabel(125)).toBe('saved 2m ago')
    expect(agoLabel(7200)).toBe('saved 2h ago')
  })
  it('joins the header line, skipping what is unknown', () => {
    expect(metaLine(['CA', null, 'CPRA', undefined, '', 'rev 4'])).toBe('CA · CPRA · rev 4')
  })
})

describe('plain text', () => {
  const p = (...content: JSONContent[]): JSONContent => ({ type: 'paragraph', content })
  const t = (text: string): JSONContent => ({ type: 'text', text })

  it('mirrors the server: blank lines between blocks, chips and footnotes as tokens', () => {
    expect(
      plainText({
        type: 'doc',
        content: [
          p(t('one '), { type: 'placeholder', attrs: { name: 'AGENCY_FULL' } }),
          { type: 'paragraph' },
          p(t('two'), { type: 'hardBreak' }, t('three'), {
            type: 'footnoteRef',
            attrs: { marker: '2' },
          }),
          { type: 'heading', attrs: { level: 2 }, content: [t(' four ')] },
        ],
      }),
    ).toBe('one {{AGENCY_FULL}}\n\ntwo\nthree[2]\n\nfour')
  })

  it('marks list items and nests a list inside an item', () => {
    expect(
      plainText({
        type: 'doc',
        content: [
          {
            type: 'bulletList',
            content: [
              { type: 'listItem', content: [p(t('one'))] },
              {
                type: 'listItem',
                content: [
                  p(t('two')),
                  {
                    type: 'orderedList',
                    content: [{ type: 'listItem', content: [p(t('three'))] }],
                  },
                ],
              },
            ],
          },
          {
            type: 'orderedList',
            attrs: { start: 4 },
            content: [{ type: 'listItem', content: [p(t('four'))] }],
          },
        ],
      }),
    ).toBe('- one\n\n- two\n  1. three\n\n4. four')
  })

  it('joins table cells with a bar and rows with a newline', () => {
    expect(
      plainText({
        type: 'doc',
        content: [
          {
            type: 'table',
            content: [
              {
                type: 'tableRow',
                content: [
                  { type: 'tableHeader', content: [p(t('one'))] },
                  { type: 'tableCell', content: [p(t('two'))] },
                ],
              },
              { type: 'tableRow', content: [{ type: 'tableCell', content: [p(t('three'))] }] },
            ],
          },
        ],
      }),
    ).toBe('one | two\nthree')
    expect(plainText(null)).toBe('')
    expect(plainText({ type: 'doc', content: [{ type: 'paragraph' }] })).toBe('')
  })

  it('names a part by its first words', () => {
    const doc = docFromText('one two three four five six')
    expect(firstWords(doc)).toBe('one two three four five six')
    expect(firstWords(doc, 12)).toBe('one two…')
    expect(firstWords(docFromText(''))).toBe('')
  })
})

describe('reorderPieces', () => {
  it('follows the keys given and renumbers', () => {
    const pieces = normalize([piece({ key: 'a' }), piece({ key: 'b' }), piece({ key: 'c' })])
    const out = reorderPieces(pieces, ['c', 'a', 'missing', 'b'])
    expect(out.map((p) => [p.key, p.item_number])).toEqual([
      ['c', '1'],
      ['a', '2'],
      ['b', '3'],
    ])
  })
})
