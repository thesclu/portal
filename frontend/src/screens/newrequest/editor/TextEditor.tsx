/**
 * One rich-text document on its own: the same vocabulary and look as a piece on the page, with
 * no drag handle or menu. The template editor's detail panel, a shared block and a letterhead's
 * opening and closing are edited in it; the Toolbar acts on it through `onFocus`.
 */
import { EditorContent, useEditor, type Editor, type JSONContent } from '@tiptap/react'
import { useEffect, useRef } from 'react'

import { cx } from '../../../components/ui'
import './editor.css'
import { letterExtensions } from './nodes'

export function TextEditor({
  doc,
  hint,
  editable = true,
  autofocus = false,
  label,
  className,
  onDoc,
  onFocus,
}: {
  /** What the editor starts with. A later change is not applied: give the editor a new `key`. */
  doc: JSONContent
  hint: string
  editable?: boolean
  /** Take the focus on mount, the caret at the end. */
  autofocus?: boolean
  label: string
  className?: string
  onDoc: (doc: JSONContent) => void
  onFocus?: (editor: Editor) => void
}) {
  const latest = useRef({ onDoc, onFocus })
  useEffect(() => {
    latest.current = { onDoc, onFocus }
  })
  const editor = useEditor(
    {
      extensions: letterExtensions(hint),
      content: doc,
      editable,
      autofocus: autofocus ? 'end' : false,
      editorProps: { attributes: { class: 'piece-text', 'aria-label': label } },
      // Tiptap also announces an update that changed nothing (on mount, with autofocus); only
      // a real change is a change.
      onUpdate: ({ editor: e, transaction }) => {
        if (transaction.docChanged) latest.current.onDoc(e.getJSON())
      },
      onFocus: ({ editor: e }) => latest.current.onFocus?.(e),
    },
    [],
  )
  useEffect(() => {
    editor?.setEditable(editable)
  }, [editor, editable])
  return (
    <div className={cx('text-editor', className)}>
      <EditorContent editor={editor} />
    </div>
  )
}
