import { describe, expect, it } from 'vitest'

import {
  answerProblem,
  assistantUnavailable,
  cardHeading,
  chipText,
  draftBodyFor,
  highlightOf,
  highlightsOf,
  kindLabel,
  pieceLabel,
  providerLabel,
  summaryChips,
  tokensLabel,
} from './assistant'

const pieces = [
  { id: 'a', kind: 'prose', item_number: '' },
  { id: 'b', kind: 'item', item_number: '1' },
  { id: 'c', kind: 'prose', item_number: '' },
  { id: 'd', kind: 'item', item_number: '2' },
  { id: null, kind: 'item', item_number: '3' },
]

describe('cards', () => {
  it('puts pink on a likely fight and yellow on everything else', () => {
    expect(highlightOf('likely_fight')).toBe('fight')
    expect(highlightOf('scope')).toBe('suggest')
    expect(highlightOf('house_style')).toBe('suggest')
    expect(highlightOf('anything')).toBe('suggest')
  })
  it('labels a kind as the design does and humanises an unknown one', () => {
    expect(kindLabel('likely_fight')).toBe('Likely fight')
    expect(kindLabel('house_style')).toBe('House style')
    expect(kindLabel('new_kind')).toBe('New kind')
  })
  it('names an item by its number and a paragraph by its place among paragraphs', () => {
    expect(pieceLabel(pieces, 'd')).toBe('Item 2')
    expect(pieceLabel(pieces, 'c')).toBe('¶2')
    expect(pieceLabel(pieces, 'zzz')).toBeNull()
    expect(pieceLabel(pieces, null)).toBeNull()
  })
  it('heads a card "Item 2 · Scope", or just the kind for the letter as a whole', () => {
    expect(cardHeading(pieces, { kind: 'scope', item_id: 'd' })).toBe('Item 2 · Scope')
    expect(cardHeading(pieces, { kind: 'house_style', item_id: null })).toBe('House style')
  })
  it('highlights only open cards and lets a fight win over a suggestion on one piece', () => {
    const marks = highlightsOf([
      { kind: 'scope', item_id: 'b', status: 'open' },
      { kind: 'likely_fight', item_id: 'b', status: 'open' },
      { kind: 'scope', item_id: 'd', status: 'applied' },
      { kind: 'wording', item_id: null, status: 'open' },
      { kind: 'likely_fight', item_id: 'c', status: 'open' },
      { kind: 'scope', item_id: 'c', status: 'open' },
    ])
    expect(marks.get('b')).toBe('fight')
    expect(marks.get('c')).toBe('fight')
    expect(marks.has('d')).toBe(false)
    expect(marks.size).toBe(2)
  })
  it('says why the assistant cannot be asked, and nothing when it can', () => {
    expect(assistantUnavailable('on')).toBeNull()
    expect(assistantUnavailable('off')).toMatch(/off/)
    expect(assistantUnavailable('privileged')).toMatch(/privileged/)
  })
  it('turns a non-answer into a sentence, preferring the backend’s own', () => {
    expect(answerProblem({ status: 'ok', message: '' })).toBeNull()
    expect(answerProblem({ status: 'off', message: 'Switched off today.' })).toBe(
      'Switched off today.',
    )
    expect(answerProblem({ status: 'refused', message: '' })).toMatch(/declined/)
    expect(answerProblem({ status: 'failed', message: '' })).toMatch(/could not/)
  })
})

describe('stage 1', () => {
  it('starts a draft from a master letter or a past request, and from nothing else', () => {
    expect(draftBodyFor({ kind: 'master_letter', id: 't1' })).toEqual({ from_template_id: 't1' })
    expect(draftBodyFor({ kind: 'past_request', id: 'r1' })).toEqual({ from_request_id: 'r1' })
    expect(draftBodyFor({ kind: 'campaign', id: 'c1' })).toBeNull()
  })
})

describe('the run log', () => {
  it('reads the backend’s summary into chips, in its order, skipping what has no size', () => {
    const chips = summaryChips({
      draft: { chars: 1234, sha256: 'abc' },
      agency: { chars: 80 },
      odd: { sha256: 'x' },
      flat: 12,
      bad: { chars: 'many' },
    })
    expect(chips).toEqual([
      { part: 'draft', chars: 1234 },
      { part: 'agency', chars: 80 },
      { part: 'flat', chars: 12 },
    ])
    expect(chipText(chips[0]!)).toBe('draft: 1,234 chars')
    expect(summaryChips(null)).toEqual([])
  })
  it('labels tokens and the provider', () => {
    expect(tokensLabel(1200, 340)).toBe('1,200 in · 340 out')
    expect(tokensLabel(null, 5)).toBe('5 out')
    expect(tokensLabel(null, null)).toBe('—')
    expect(providerLabel('gemini', 'gemini-2.5-flash-lite')).toBe('gemini · gemini-2.5-flash-lite')
    expect(providerLabel('claude_code', '')).toBe('claude_code')
  })
})
