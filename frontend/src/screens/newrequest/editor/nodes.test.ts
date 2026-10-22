import { getSchema } from '@tiptap/core'
import { describe, expect, it } from 'vitest'

import { letterExtensions } from './nodes'

describe('the editor vocabulary', () => {
  const schema = getSchema(letterExtensions('hint'))

  it('serialises the two inline atoms exactly as the server reads them', () => {
    const chip = schema.nodeFromJSON({ type: 'placeholder', attrs: { name: 'AGENCY_FULL' } })
    expect(chip.toJSON()).toEqual({ type: 'placeholder', attrs: { name: 'AGENCY_FULL' } })
    expect(chip.isAtom && chip.isInline).toBe(true)
    const ref = schema.nodeFromJSON({ type: 'footnoteRef', attrs: { marker: '3' } })
    expect(ref.toJSON()).toEqual({ type: 'footnoteRef', attrs: { marker: '3' } })
    expect(ref.textContent).toBe('')
  })

  it('keeps to the node names the server knows', () => {
    const names = Object.keys(schema.nodes)
    for (const name of [
      'doc',
      'paragraph',
      'heading',
      'bulletList',
      'orderedList',
      'listItem',
      'table',
      'tableRow',
      'tableCell',
      'tableHeader',
      'blockquote',
      'horizontalRule',
      'hardBreak',
      'text',
      'placeholder',
      'footnoteRef',
    ])
      expect(names).toContain(name)
    expect(names).not.toContain('codeBlock')
    expect(Object.keys(schema.marks).sort()).toEqual([
      'bold',
      'code',
      'italic',
      'strike',
      'underline',
    ])
  })
})
