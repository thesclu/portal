import { describe, expect, it } from 'vitest'

import {
  characterDiffs,
  fixReplacements,
  fixedText,
  typographicQuotes,
  withCapitalisationOf,
  wordReplacements,
} from './fixes'

describe('a house-style word', () => {
  it('replaces the word as a whole word only, in any case', () => {
    expect(fixedText('Utilize the utilize-r; utilized; UTILIZE.', word('utilize', 'use'))).toBe(
      'Use the use-r; utilized; Use.',
    )
  })
  it('keeps the capital of the first letter and lowers the rest', () => {
    expect(withCapitalisationOf('Utilize', 'use')).toBe('Use')
    expect(withCapitalisationOf('utilize', 'Use')).toBe('use')
    expect(withCapitalisationOf('UTILIZE', 'use')).toBe('Use')
    expect(withCapitalisationOf('', 'use')).toBe('use')
  })
  it('takes a phrase and escapes what regular expressions would read', () => {
    expect(fixedText('pursuant to (a.k.a.) the act', word('a.k.a.', 'also known as'))).toBe(
      'pursuant to (also known as) the act',
    )
    expect(fixedText('prior to noon, prior to dusk', word('prior to', 'before'))).toBe(
      'before noon, before dusk',
    )
  })
  it('gives each match its place in the text', () => {
    expect(wordReplacements('a cat, a Cat', 'cat', 'dog')).toEqual([
      { from: 2, to: 5, text: 'dog' },
      { from: 9, to: 12, text: 'Dog' },
    ])
    expect(wordReplacements('concatenate', 'cat', 'dog')).toEqual([])
    expect(wordReplacements('anything', '  ', 'dog')).toEqual([])
  })
})

describe('straight quotes', () => {
  it('opens a double quote at the start or after a space, bracket or parenthesis', () => {
    expect(typographicQuotes('"a" ("b") ["c"] x"d"')).toBe('“a” (“b”) [“c”] x”d”')
  })
  it('closes an apostrophe after a letter or digit and opens one at a word start', () => {
    expect(typographicQuotes("the agency's records, Fred's")).toBe('the agency’s records, Fred’s')
    // At a word start the quote opens, even before a year: the rule reads the word, not the sense.
    expect(typographicQuotes("the '90s")).toBe('the ‘90s')
    expect(typographicQuotes("'quoted' and 'more'")).toBe('‘quoted’ and ‘more’')
    expect(typographicQuotes("it is ' alone")).toBe('it is ’ alone')
  })
  it('reads the character before the text when the text continues a run', () => {
    expect(typographicQuotes('"a"', 'x')).toBe('”a”')
    expect(typographicQuotes('"a"', 'x ')).toBe('“a”')
    expect(typographicQuotes("'s", 'e')).toBe('’s')
  })
  it('leaves typographic quotes and everything else alone', () => {
    expect(typographicQuotes('“fine” ‘still’ 5 < 6')).toBe('“fine” ‘still’ 5 < 6')
  })
  it('reports only the characters that change, as runs', () => {
    expect(characterDiffs('a "b"', 'a “b”')).toEqual([
      { from: 2, to: 3, text: '“' },
      { from: 4, to: 5, text: '”' },
    ])
    expect(characterDiffs('""', '“”')).toEqual([{ from: 0, to: 2, text: '“”' }])
    expect(characterDiffs('same', 'same')).toEqual([])
  })
})

describe('fixReplacements', () => {
  it('routes each mode and ignores one it does not know', () => {
    expect(fixReplacements('say "hi"', { mode: 'quotes', find: '', replace: '' })).toEqual([
      { from: 4, to: 5, text: '“' },
      { from: 7, to: 8, text: '”' },
    ])
    expect(fixReplacements('say hi', word('hi', 'hello'))).toEqual([
      { from: 4, to: 6, text: 'hello' },
    ])
    expect(fixReplacements('say hi', { mode: 'later', find: 'hi', replace: 'x' })).toEqual([])
  })
})

function word(find: string, replace: string) {
  return { mode: 'word', find, replace }
}
