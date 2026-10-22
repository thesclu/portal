/**
 * The Letter tab of a template (design 1z): the Parts list at the left, one row per piece or
 * shared block in the order the letter prints, and at the right the detail of the selected row
 * with the same rich-text editor and toolbar the draft editor has.
 */
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { Editor } from '@tiptap/react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import type { Schemas } from '../../api/client'
import { Empty } from '../../components/States'
import { Badge, Button, Notice, Select, cx } from '../../components/ui'
import {
  canDemote,
  docOf,
  firstWords,
  type LocalCitation,
  type LocalPiece,
  type PieceKind,
} from '../newrequest/editor/letter'
import { RichView } from '../newrequest/editor/RichView'
import { TextEditor } from '../newrequest/editor/TextEditor'
import { Menu, Toolbar, type MenuItem } from '../newrequest/editor/Toolbar'
import type { LetterDocument } from '../newrequest/editor/useLetterDocument'
import { rowKey, type PartRow } from './parts'

type Block = Schemas['BlockOut']
type LetterBlock = Schemas['LetterBlockOut']
type BlockCitation = Schemas['BlockCitationOut']

/** What "Own deadline under" may offer for this template, and why it may offer nothing. */
export type Provisions =
  | { state: 'loading' }
  | { state: 'failed'; retry: () => void }
  | { state: 'ready'; law: string | null; options: string[] }

const BLOCKS = '/templates/blocks'

function provisionOptions(provisions: Provisions, current: string | null) {
  const known = provisions.state === 'ready' ? provisions.options : []
  return [
    { value: '', label: 'None' },
    ...known.map((value) => ({ value, label: value })),
    // What the item already has is shown even when no rule offers it, so it is never changed
    // just by opening the page. It can be kept or cleared, not chosen afresh.
    ...(current && !known.includes(current)
      ? [{ value: current, label: `${current} (no rule under this template’s law)` }]
      : []),
  ]
}

export interface LetterProps {
  doc: LetterDocument
  rows: PartRow[]
  /** Every shared block the list may name: the ones in the letter and the whole catalogue. */
  blockOf: Map<string, Block | LetterBlock>
  /** Blocks not yet in this template, offered by "Insert shared block". */
  available: Block[]
  /** Each shared block's own footnotes, by block id (the letter's copy of a block carries none). */
  blockCitations: Map<string, BlockCitation[]>
  selected: string | null
  provisions: Provisions
  onSelect: (key: string | null) => void
  onAdd: (kind: PieceKind) => void
  onInsertBlock: (blockId: string) => void
  onRemove: (row: PartRow) => void
  onMove: (from: number, to: number) => void
  onFootnote: () => void
}

export function Letter(props: LetterProps) {
  const { doc, rows, blockOf, available, blockCitations, selected, onSelect, onMove } = props
  const pieceOf = new Map(doc.pieces.map((piece) => [piece.key, piece]))
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const keys = rows.map(rowKey)
  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    onMove(keys.indexOf(String(active.id)), keys.indexOf(String(over.id)))
  }
  const selectedRow = rows.find((row) => rowKey(row) === selected) ?? null

  return (
    <div className="flex flex-wrap items-start gap-6">
      <section className="min-w-[22rem] flex-1" aria-labelledby="parts-heading">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 id="parts-heading" className="min-w-0 flex-1 text-14 font-semibold">
            Parts
          </h3>
          <Button onClick={() => props.onAdd('item')}>＋ Add item</Button>
          <Button onClick={() => props.onAdd('prose')}>＋ Add paragraph</Button>
          <Menu
            title="Insert shared block"
            buttonClassName="h-8 rounded-9 border border-line bg-card-white px-3 text-125 text-ink hover:bg-page"
            disabled={available.length === 0}
            items={available.map((block) => ({
              label: block.name,
              onPick: () => props.onInsertBlock(block.id),
            }))}
          >
            ＋ Insert shared block
          </Menu>
        </div>
        {rows.length === 0 ? (
          <Empty title="Nothing in this template yet">
            Add the items the letter asks for, and any shared blocks that go between them.
          </Empty>
        ) : (
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={keys} strategy={verticalListSortingStrategy}>
              <ul role="listbox" aria-label="Parts" className="border-t border-line-2">
                {rows.map((row) => (
                  <Part
                    key={rowKey(row)}
                    row={row}
                    piece={row.kind === 'piece' ? pieceOf.get(row.key) : undefined}
                    block={row.kind === 'block' ? blockOf.get(row.id) : undefined}
                    citations={
                      row.kind === 'piece'
                        ? doc.citations.filter((c) => c.item_key === row.key)
                        : (blockCitations.get(row.id) ?? [])
                    }
                    demotable={row.kind === 'piece' && canDemote(doc.pieces, row.key)}
                    selected={rowKey(row) === selected}
                    onSelect={() => onSelect(rowKey(row))}
                    onRemove={() => props.onRemove(row)}
                    onDemote={row.kind === 'piece' ? () => doc.onDemote(row.key) : undefined}
                    onPromote={row.kind === 'piece' ? () => doc.onPromote(row.key) : undefined}
                  />
                ))}
              </ul>
            </SortableContext>
          </DndContext>
        )}
        {available.length === 0 && rows.length > 0 ? (
          <p className="mt-2 text-12 text-ink-3">
            {blockOf.size === 0 ? (
              <>
                There are no shared blocks to insert.{' '}
                <Link className="text-brand hover:underline" to={BLOCKS}>
                  Add one under Shared blocks
                </Link>
                .
              </>
            ) : (
              'Every shared block is already in this template.'
            )}
          </p>
        ) : null}
      </section>
      <aside className="w-full lg:w-[30rem] lg:shrink-0">
        <Detail
          row={selectedRow}
          piece={selectedRow?.kind === 'piece' ? pieceOf.get(selectedRow.key) : undefined}
          block={selectedRow?.kind === 'block' ? blockOf.get(selectedRow.id) : undefined}
          doc={doc}
          provisions={props.provisions}
          onFootnote={props.onFootnote}
        />
      </aside>
    </div>
  )
}

function Part({
  row,
  piece,
  block,
  citations,
  demotable,
  selected,
  onSelect,
  onRemove,
  onDemote,
  onPromote,
}: {
  row: PartRow
  piece: LocalPiece | undefined
  block: Block | LetterBlock | undefined
  /** The piece's citations, or for a shared block the block's own footnotes. */
  citations: Array<Pick<LocalCitation, 'marker' | 'citation_text'> & { key?: string; id?: string }>
  demotable: boolean
  selected: boolean
  onSelect: () => void
  onRemove: () => void
  onDemote?: () => void
  onPromote?: () => void
}) {
  const id = rowKey(row)
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
  })
  const label =
    row.kind === 'block'
      ? `shared block ${block?.name ?? ''}`
      : piece?.kind === 'item'
        ? `item ${piece.item_number}`
        : 'paragraph'
  const items: MenuItem[] = []
  if (piece?.kind === 'item' && !piece.parent_key && onDemote)
    items.push({ label: 'Make sub-item of the previous', disabled: !demotable, onPick: onDemote })
  if (piece?.parent_key && onPromote) items.push({ label: 'Promote', onPick: onPromote })
  items.push({
    label: row.kind === 'block' ? 'Remove from this template' : 'Delete',
    onPick: onRemove,
  })

  let body: ReactNode
  if (row.kind === 'block') {
    const blockRich = block && 'body_rich' in block ? block.body_rich : null
    body = (
      <>
        <p className="text-13 leading-[1.45]">
          <span className="font-semibold">Shared · {block?.name ?? 'A shared block'}</span>
          {block ? (
            <span className="text-ink-2"> — {firstWords(docOf(blockRich, block.body))}</span>
          ) : null}
        </p>
        <p className="mt-0.5 text-115 text-ink-3">
          Shared block ·{' '}
          <Link
            className="text-muted hover:underline"
            to={`${BLOCKS}/${row.id}`}
            onClick={(event) => event.stopPropagation()}
          >
            edit in Shared blocks
          </Link>
        </p>
        {citations.length > 0 ? (
          <p className="mt-1 flex flex-wrap gap-1.5">
            {citations.map((citation) => (
              <Badge key={citation.key ?? citation.id ?? citation.marker}>
                Cites {firstWords(docOf(null, citation.citation_text), 48) || citation.marker}
              </Badge>
            ))}
          </p>
        ) : null}
      </>
    )
  } else if (piece?.kind === 'item') {
    const words = firstWords(piece.doc)
    body = (
      <>
        <p className={cx('text-13 leading-[1.45]', !words && 'text-muted-2')}>
          {words || 'Nothing written yet'}
        </p>
        {piece.provision || citations.length > 0 ? (
          <p className="mt-1 flex flex-wrap gap-1.5">
            {piece.provision ? <Badge tone="accent">Own deadline · {piece.provision}</Badge> : null}
            {citations.map((citation) => (
              <Badge key={citation.key ?? citation.id ?? citation.marker}>
                Cites {firstWords(docOf(null, citation.citation_text), 48) || citation.marker}
              </Badge>
            ))}
          </p>
        ) : null}
      </>
    )
  } else {
    const words = piece ? firstWords(piece.doc) : ''
    body = (
      <p className="text-13 leading-[1.45]">
        <span className="font-semibold">Paragraph</span>
        <span className={cx(words ? 'text-ink-2' : 'text-muted-2')}>
          {' '}
          — {words || 'Nothing written yet'}
        </span>
      </p>
    )
  }

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      role="option"
      aria-selected={selected}
      aria-label={label}
      tabIndex={0}
      className={cx('part', isDragging && 'dragging')}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
    >
      <button
        type="button"
        className="part-handle"
        title="Drag to reorder"
        {...attributes}
        {...listeners}
        aria-label={`Reorder ${label}`}
        onClick={(event) => event.stopPropagation()}
      >
        ⠿
      </button>
      {piece?.kind === 'item' ? (
        <span className="part-n" aria-hidden>
          {piece.item_number}
        </span>
      ) : null}
      <div className="min-w-0 flex-1">{body}</div>
      <div className="part-menu" onClick={(event) => event.stopPropagation()}>
        <Menu title={`Options for ${label}`} items={items} align="right">
          ⋯
        </Menu>
      </div>
    </li>
  )
}

/** The right-hand panel: the selected part, editable where it can be. */
function Detail({
  row,
  piece,
  block,
  doc,
  provisions,
  onFootnote,
}: {
  row: PartRow | null
  piece: LocalPiece | undefined
  block: Block | LetterBlock | undefined
  doc: LetterDocument
  provisions: Provisions
  onFootnote: () => void
}) {
  if (!row) {
    return (
      <div className="rounded-16 border border-line bg-card p-4 text-125 text-ink-3">
        Select a part to edit it.
      </div>
    )
  }
  if (row.kind === 'block') {
    const blockRich = block && 'body_rich' in block ? block.body_rich : null
    return (
      <div className="rounded-16 border border-line bg-card p-4">
        <h3 className="text-14 font-semibold">Shared · {block?.name ?? 'A shared block'}</h3>
        <p className="mt-0.5 text-12 text-ink-3">
          Shared block · its words are edited once, under Shared blocks.
        </p>
        {block ? (
          <div className="text-editor mt-3">
            <RichView doc={docOf(blockRich, block.body)} />
          </div>
        ) : null}
        <div className="mt-3">
          <Link className="text-125 text-brand hover:underline" to={`${BLOCKS}/${row.id}`}>
            Edit in Shared blocks
          </Link>
        </div>
      </div>
    )
  }
  if (!piece) return null
  const editor: Editor | null = doc.active?.key === piece.key ? doc.active.editor : null
  const own = doc.citations.filter((c) => c.item_key === piece.key)
  const options = provisionOptions(provisions, piece.provision)
  return (
    <div className="rounded-16 border border-line bg-card p-4">
      <h3 className="text-14 font-semibold">
        {piece.kind === 'item' ? `Item ${piece.item_number}` : 'Paragraph'}
      </h3>
      <div className="mt-3">
        <p className="mb-1 text-125 font-medium text-ink-2">Text</p>
        <Toolbar editor={editor} disabled={false} onFootnote={onFootnote} fluid />
        <TextEditor
          key={piece.key}
          doc={piece.doc}
          hint={piece.kind === 'item' ? 'What is asked for' : 'A paragraph'}
          label={piece.kind === 'item' ? `Text of item ${piece.item_number}` : 'Paragraph'}
          autofocus
          onDoc={(next) => doc.onDoc(piece.key, next)}
          onFocus={(e) => doc.onFocus(piece.key, e)}
        />
      </div>
      {piece.kind === 'item' ? (
        <>
          <div className="mt-4">
            <label className="block">
              <span className="mb-1 block text-125 font-medium text-ink-2">Own deadline under</span>
              <Select
                value={piece.provision ?? ''}
                disabled={provisions.state !== 'ready'}
                onChange={(event) => {
                  const provision = event.target.value || null
                  doc.change((ps) => ps.map((p) => (p.key === piece.key ? { ...p, provision } : p)))
                }}
              >
                {options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </label>
            <ProvisionsNote provisions={provisions} />
          </div>
          <div className="mt-4">
            <p className="mb-1 text-125 font-medium text-ink-2">Footnote</p>
            {own.length > 0 ? (
              <ul className="mb-2 space-y-1">
                {own.map((citation) => (
                  <li key={citation.key} className="flex items-start gap-2 text-125">
                    <span className="w-6 shrink-0 font-semibold">{citation.marker}</span>
                    <span className="min-w-0 flex-1 whitespace-pre-wrap">
                      {citation.citation_text}
                    </span>
                    <Button
                      kind="quiet"
                      aria-label={`Remove footnote ${citation.marker}`}
                      onClick={() => doc.removeCitation(citation.key)}
                    >
                      Remove
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
            <Button onClick={onFootnote}>Add a citation</Button>
            <p className="mt-1 text-12 text-ink-3">
              Its reference goes in where the caret is in the text.
            </p>
          </div>
        </>
      ) : null}
    </div>
  )
}

function ProvisionsNote({ provisions }: { provisions: Provisions }) {
  if (provisions.state === 'loading') return null
  if (provisions.state === 'failed') {
    return (
      <div className="mt-2 flex items-center justify-between gap-3 rounded-9 bg-warn-tint px-3 py-2">
        <p className="text-125 text-warn">Couldn’t load the rules, so this cannot be chosen now.</p>
        <Button onClick={provisions.retry}>Try again</Button>
      </div>
    )
  }
  if (provisions.options.length > 0) return null
  return (
    <div className="mt-2">
      <Notice>
        {provisions.law
          ? `The rules file has no provision under “${provisions.law}”, so an item in this template cannot have a deadline of its own.`
          : 'This template has no law set, so an item in it cannot have a deadline of its own. Set the law under Edit details.'}
      </Notice>
    </div>
  )
}
