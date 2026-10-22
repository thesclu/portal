import { describe, expect, it } from 'vitest'

import { namesSentence } from './Access'

describe('who has access, in words (231)', () => {
  it('joins first names as the meta row does', () => {
    expect(namesSentence([])).toBe('')
    expect(namesSentence(['Ana'])).toBe('Ana')
    expect(namesSentence(['Ana', 'Ben'])).toBe('Ana and Ben')
    expect(namesSentence(['Ana', 'Ben', 'Cy'])).toBe('Ana, Ben and Cy')
  })
})
