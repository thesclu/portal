/** One piece of the body (an item or a paragraph): its own editor, its number outside the
 *  editable text, a drag handle at the left and a small menu at the right, both on hover. */
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { EditorContent, useEditor, type Editor, type JSONContent } from '@tiptap/react'
import { memo, useEffect, useRef } from 'react'

import { cx } from '../../../components/ui'
import type { Highlight } from '../../../lib/assistant'
import { depthOf, type LocalPiece } from './letter'
import { letterExtensions } from './nodes'
import { Menu, type MenuItem } from './Toolbar'

export interface PieceProps {
  piece: LocalPiece
  frozen: boolean
  demotable: boolean
  onDoc: (key: string, doc: JSONContent) => void
  onFocus: (key: string, editor: Editor) => void
  onDemote: (key: string) => void
  onPromote: (key: string) => void
  onDelete: (key: string) => void
  /** The editor as soon as it exists, and null when it goes, so the page can reach a piece
   *  (focus it, put the assistant's wording in it) without waiting for a click. */
  onEditor?: (key: string, editor: Editor | null) => void
  /** The rail's mark on this piece (design 1e): yellow for a suggestion, pink for a likely
   *  fight; `selected` when its card is the open one. */
  highlight?: Highlight | null
  selected?: boolean
  /** Clicking a marked piece opens its card. */
  onHighlightClick?: (key: string) => void
}

export const PieceEditor = memo(
  function PieceEditor(props: PieceProps) {
    const { piece, frozen, demotable, highlight = null, selected = false } = props
    const latest = useRef(props)
    useEffect(() => {
      latest.current = props
    })
    const editor = useEditor(
      {
        extensions: letterExtensions(piece.kind === 'item' ? 'What is asked for' : 'A paragraph'),
        content: piece.doc,
        editable: !frozen,
        editorProps: { attributes: { class: 'piece-text' } },
        // Tiptap also announces an update that changed nothing (on mount); only a real change
        // schedules a save.
        onUpdate: ({ editor: e, transaction }) => {
          if (transaction.docChanged) latest.current.onDoc(piece.key, e.getJSON())
        },
        onFocus: ({ editor: e }) => latest.current.onFocus(piece.key, e),
      },
      [],
    )
    useEffect(() => {
      editor?.setEditable(!frozen)
    }, [editor, frozen])
    useEffect(() => {
      if (!editor) return
      latest.current.onEditor?.(piece.key, editor)
      return () => latest.current.onEditor?.(piece.key, null)
    }, [editor, piece.key])
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
      id: piece.key,
      disabled: frozen,
    })
    const label = piece.kind === 'item' ? `item ${piece.item_number}` : 'paragraph'
    const items: MenuItem[] = []
    if (piece.kind === 'item' && !piece.parent_key)
      items.push({
        label: 'Make sub-item of the previous',
        disabled: !demotable,
        onPick: () => props.onDemote(piece.key),
      })
    if (piece.parent_key) items.push({ label: 'Promote', onPick: () => props.onPromote(piece.key) })
    items.push({ label: 'Delete', onPick: () => props.onDelete(piece.key) })
    return (
      <div
        ref={setNodeRef}
        style={{ transform: CSS.Transform.toString(transform), transition }}
        className={cx(
          'piece',
          piece.kind,
          depthOf(piece) > 0 && 'sub',
          isDragging && 'dragging',
          highlight && `hl-${highlight}`,
          highlight && selected && 'hl-selected',
        )}
        onClick={highlight ? () => latest.current.onHighlightClick?.(piece.key) : undefined}
      >
        {frozen ? null : (
          <button
            type="button"
            className="piece-handle"
            title="Drag to reorder"
            {...attributes}
            {...listeners}
            aria-label={`Reorder ${label}`}
          >
            ⠿
          </button>
        )}
        {piece.kind === 'item' ? (
          <span className="n" aria-hidden>
            {piece.item_number}.
          </span>
        ) : null}
        <EditorContent editor={editor} />
        {frozen ? null : (
          <Menu className="piece-menu" title={`Options for ${label}`} items={items} align="right">
            ⋯
          </Menu>
        )}
      </div>
    )
  },
  (a, b) =>
    a.piece === b.piece &&
    a.frozen === b.frozen &&
    a.demotable === b.demotable &&
    a.onDemote === b.onDemote &&
    a.onPromote === b.onPromote &&
    a.onDelete === b.onDelete &&
    a.highlight === b.highlight &&
    a.selected === b.selected,
)
