/**
 * The editor's vocabulary (P19c): Tiptap's starter kit trimmed to what the server's renderer
 * understands, tables, and the two inline atoms of ours, each serialising to exactly the JSON the
 * server reads: `{ type: 'placeholder', attrs: { name } }` and `{ type: 'footnoteRef', attrs:
 * { marker } }`.
 */
import { Node, mergeAttributes, type Extensions } from '@tiptap/core'
import { Placeholder } from '@tiptap/extension-placeholder'
import { Table } from '@tiptap/extension-table'
import { TableCell } from '@tiptap/extension-table-cell'
import { TableHeader } from '@tiptap/extension-table-header'
import { TableRow } from '@tiptap/extension-table-row'
import StarterKit from '@tiptap/starter-kit'

import { PLACEHOLDER_NAMES, type PlaceholderName } from './letter'

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    placeholderChip: {
      /** A placeholder chip at the caret. */
      insertPlaceholder: (name: PlaceholderName) => ReturnType
    }
    footnoteRef: {
      /** A footnote superscript at the caret. */
      insertFootnoteRef: (marker: string) => ReturnType
    }
  }
}

/** `{{NAME}}` as a small blue chip the person cannot type into. */
export const PlaceholderChip = Node.create({
  name: 'placeholder',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: false,

  addAttributes() {
    return {
      name: {
        default: PLACEHOLDER_NAMES[0],
        parseHTML: (element) => element.getAttribute('data-name'),
        renderHTML: (attributes) => ({ 'data-name': attributes.name as string }),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'span.chip[data-name]' }]
  },

  renderHTML({ node, HTMLAttributes }) {
    return ['span', mergeAttributes({ class: 'chip' }, HTMLAttributes), String(node.attrs.name)]
  },

  renderText({ node }) {
    return `{{${String(node.attrs.name)}}}`
  },

  addCommands() {
    return {
      insertPlaceholder:
        (name) =>
        ({ commands }) =>
          commands.insertContent({ type: this.name, attrs: { name } }),
    }
  },
})

/** A citation's marker as a superscript in the text. */
export const FootnoteRef = Node.create({
  name: 'footnoteRef',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: false,

  addAttributes() {
    return {
      marker: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-marker'),
        renderHTML: (attributes) => ({ 'data-marker': attributes.marker as string }),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'sup[data-marker]' }]
  },

  renderHTML({ node, HTMLAttributes }) {
    return ['sup', mergeAttributes({ class: 'fn' }, HTMLAttributes), String(node.attrs.marker)]
  },

  renderText({ node }) {
    return `[${String(node.attrs.marker)}]`
  },

  addCommands() {
    return {
      insertFootnoteRef:
        (marker) =>
        ({ commands }) =>
          commands.insertContent({ type: this.name, attrs: { marker } }),
    }
  },
})

/** A fresh set for each editor on the page (an extension's storage belongs to one editor). */
export function letterExtensions(hint: string): Extensions {
  return [
    StarterKit.configure({
      heading: { levels: [2, 3] },
      // Not in the server's vocabulary, so not offered.
      codeBlock: false,
      link: false,
    }),
    Table.configure({ resizable: false }),
    TableRow,
    TableHeader,
    TableCell,
    Placeholder.configure({ placeholder: hint }),
    PlaceholderChip,
    FootnoteRef,
  ]
}
