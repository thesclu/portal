/** The one-row toolbar above the page, acting on whichever piece was last focused. */
import { useEditorState, type ChainedCommands, type Editor } from '@tiptap/react'
import { useEffect, useRef, useState, type ReactNode } from 'react'

import { cx } from '../../../components/ui'
import { PLACEHOLDER_NAMES, type PlaceholderName } from './letter'

export interface MenuItem {
  label: string
  onPick: () => void
  disabled?: boolean
}

/** A button that opens a small list of choices. Mouse-down is swallowed so the editor keeps its
 *  selection while the choice is made. */
export function Menu({
  title,
  items,
  className,
  buttonClassName,
  align = 'left',
  disabled,
  children,
}: {
  title: string
  items: MenuItem[]
  className?: string
  buttonClassName?: string
  align?: 'left' | 'right'
  disabled?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open])
  return (
    <div ref={ref} className={cx('menu', className)}>
      <button
        type="button"
        className={cx('tb', buttonClassName)}
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => setOpen((value) => !value)}
      >
        {children}
      </button>
      {open ? (
        <div role="menu" className={cx('pop', align === 'right' && 'pop-right')}>
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                setOpen(false)
                item.onPick()
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function Tb({
  title,
  pressed,
  disabled,
  onClick,
  children,
}: {
  title: string
  pressed?: boolean
  disabled?: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      className="tb"
      title={title}
      aria-label={title}
      aria-pressed={pressed}
      disabled={disabled}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

export function Toolbar({
  editor,
  disabled,
  onFootnote,
  fluid = false,
}: {
  editor: Editor | null
  disabled: boolean
  onFootnote: () => void
  /** As wide as what it sits above, not the page, and not sticky. */
  fluid?: boolean
}) {
  const state = useEditorState({
    editor,
    selector: ({ editor: e }: { editor: Editor | null }) =>
      e
        ? {
            bold: e.isActive('bold'),
            italic: e.isActive('italic'),
            underline: e.isActive('underline'),
            h2: e.isActive('heading', { level: 2 }),
            h3: e.isActive('heading', { level: 3 }),
            bullet: e.isActive('bulletList'),
            ordered: e.isActive('orderedList'),
            table: e.isActive('table'),
            undo: e.can().undo(),
            redo: e.can().redo(),
          }
        : null,
  })
  const off = disabled || !editor
  const run = (fn: (chain: ChainedCommands) => ChainedCommands) => () => {
    if (editor) fn(editor.chain().focus()).run()
  }
  return (
    <div className={cx('letter-toolbar', fluid && 'fluid')} role="toolbar" aria-label="Formatting">
      <Tb
        title="Bold (⌘B)"
        pressed={state?.bold}
        disabled={off}
        onClick={run((c) => c.toggleBold())}
      >
        <strong>B</strong>
      </Tb>
      <Tb
        title="Italic (⌘I)"
        pressed={state?.italic}
        disabled={off}
        onClick={run((c) => c.toggleItalic())}
      >
        <em>I</em>
      </Tb>
      <Tb
        title="Underline (⌘U)"
        pressed={state?.underline}
        disabled={off}
        onClick={run((c) => c.toggleUnderline())}
      >
        <u>U</u>
      </Tb>
      <span className="tb-sep" />
      <Tb
        title="Heading 2"
        pressed={state?.h2}
        disabled={off}
        onClick={run((c) => c.toggleHeading({ level: 2 }))}
      >
        H2
      </Tb>
      <Tb
        title="Heading 3"
        pressed={state?.h3}
        disabled={off}
        onClick={run((c) => c.toggleHeading({ level: 3 }))}
      >
        H3
      </Tb>
      <span className="tb-sep" />
      <Tb
        title="Bulleted list"
        pressed={state?.bullet}
        disabled={off}
        onClick={run((c) => c.toggleBulletList())}
      >
        •≡
      </Tb>
      <Tb
        title="Numbered list"
        pressed={state?.ordered}
        disabled={off}
        onClick={run((c) => c.toggleOrderedList())}
      >
        1≡
      </Tb>
      <Menu
        title="Table"
        disabled={off}
        items={[
          {
            label: 'Insert 2×2 table',
            onPick: run((c) => c.insertTable({ rows: 2, cols: 2, withHeaderRow: false })),
          },
          { label: 'Add row', disabled: !state?.table, onPick: run((c) => c.addRowAfter()) },
          {
            label: 'Add column',
            disabled: !state?.table,
            onPick: run((c) => c.addColumnAfter()),
          },
          { label: 'Delete table', disabled: !state?.table, onPick: run((c) => c.deleteTable()) },
        ]}
      >
        ⊞
      </Menu>
      <span className="tb-sep" />
      <Menu
        title="Insert placeholder"
        disabled={off}
        items={PLACEHOLDER_NAMES.map((name: PlaceholderName) => ({
          label: name,
          onPick: run((c) => c.insertPlaceholder(name)),
        }))}
      >
        {'{ }'}
      </Menu>
      <Tb title="Add footnote" disabled={off} onClick={onFootnote}>
        x<sup>1</sup>
      </Tb>
      <span className="tb-sep" />
      <Tb title="Undo (⌘Z)" disabled={off || !state?.undo} onClick={run((c) => c.undo())}>
        ↶
      </Tb>
      <Tb title="Redo (⇧⌘Z)" disabled={off || !state?.redo} onClick={run((c) => c.redo())}>
        ↷
      </Tb>
    </div>
  )
}
