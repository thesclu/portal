/**
 * Stage 2 of a new request (design 1e): the letter as a page edited in place. One editor per
 * piece, the letterhead's parts read-only around them, footnotes under a rule, and one PUT that
 * saves the whole letter 800 ms after the last change.
 */
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type Modifier,
} from '@dnd-kit/core'
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { useQueryClient } from '@tanstack/react-query'
import type { Editor } from '@tiptap/react'
import { Fragment, useCallback, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router'

import { api, type Schemas } from '../../../api/client'
import { $api, messageOf } from '../../../api/query'
import { LoadFailed, Loading } from '../../../components/States'
import { Callout, Select, cx } from '../../../components/ui'
import { highlightOf, highlightsOf } from '../../../lib/assistant'
import './editor.css'
import { applyFix, type Fix } from './fixes'
import { FootnoteDialog } from './FootnoteDialog'
import {
  canDemote,
  docFromText,
  docOf,
  lettersLabel,
  metaLine,
  nextMarker,
  type LetterSave,
  type LocalCitation,
  type PieceKind,
} from './letter'
import { PieceEditor } from './Piece'
import { HtmlPreview, PdfView, useFitScale, usePageCount } from './Preview'
import { PreviewAs } from './PreviewAs'
import { RAIL_KEY, Rail, useRail } from './Rail'
import { RichView } from './RichView'
import { Menu, Toolbar } from './Toolbar'
import { useLetterDocument } from './useLetterDocument'

type Draft = Schemas['DraftDetailOut']
type Agency = Schemas['AgencyOut']
type Letter = Schemas['LetterOut']

const DRAFT_KEY = ['get', '/api/drafting/drafts/{draft_id}']
const LETTERHEADS = '/templates/letterheads'
/** The page as 045 draws it: a US Letter page scaled to the drawn page's 784 px across (660 px
 *  of text and 62 px sides, content-box). */
const PAGE_WIDTH = 784

export function DraftEditor({
  draft,
  agencies,
  onNext,
}: {
  draft: Draft
  agencies: Agency[]
  onNext: () => void
}) {
  const letter = $api.useQuery('get', '/api/drafting/drafts/{draft_id}/letter', {
    params: { path: { draft_id: draft.id } },
  })
  if (letter.isPending) return <Loading what="Opening the page" />
  if (letter.isError)
    return (
      <LoadFailed what="the letter" error={letter.error} onRetry={() => void letter.refetch()} />
    )
  return (
    <LetterEditor
      key={draft.id}
      draft={draft}
      letter={letter.data}
      agencies={agencies}
      onNext={onNext}
    />
  )
}

function LetterEditor({
  draft,
  letter,
  agencies,
  onNext,
}: {
  draft: Draft
  letter: Letter
  agencies: Agency[]
  onNext: () => void
}) {
  const client = useQueryClient()
  const frozen = draft.recipients.some((r) => r.request_id)
  const shell = letter.shell
  const base = `/api/drafting/drafts/${draft.id}`
  const letterKey = $api.queryOptions('get', '/api/drafting/drafts/{draft_id}/letter', {
    params: { path: { draft_id: draft.id } },
  }).queryKey

  const doc = useLetterDocument({
    letter,
    frozen,
    put: (body: LetterSave) =>
      api.PUT('/api/drafting/drafts/{draft_id}/letter', {
        params: { path: { draft_id: draft.id } },
        // The payload omits each piece's plain `body` (the server derives it), which the
        // generated type lists as required because the API gives it a default.
        body: body as unknown as Schemas['LetterSaveIn'],
      }),
    onSaved: (saved) => {
      client.setQueryData(letterKey, saved)
      void client.invalidateQueries({ queryKey: DRAFT_KEY })
      // The checks run on what was saved.
      void client.invalidateQueries({ queryKey: RAIL_KEY })
    },
  })
  const { subject, setSubject, pieces, citations, active, save, saveLabel, version } = doc
  const { onDoc, onFocus, onDemote, onPromote, onDelete } = doc
  const pageCount = usePageCount(base, version)
  const add = (index: number, kind: PieceKind) => void doc.add(index, kind)

  // ---- the rail --------------------------------------------------------------------------------
  // Each piece's editor, by key, so a card can reach its piece without a click first.
  const editors = useRef<Record<string, Editor | null>>({})
  const onEditor = useCallback((key: string, editor: Editor | null) => {
    editors.current[key] = editor
  }, [])
  const rail = useRail(draft.id)
  const cards = rail.data?.suggestions ?? []
  const marks = highlightsOf(cards)
  const [selectedCard, setSelectedCard] = useState<string | null>(null)
  const selectedPiece = cards.find((c) => c.id === selectedCard)?.item_id ?? null
  const focusPiece = (pieceId: string) => {
    const piece = pieces.find((p) => p.id === pieceId)
    const editor = piece ? editors.current[piece.key] : null
    if (!editor) return
    editor.view.dom.closest('.piece')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    editor.commands.focus('end')
  }
  /** The assistant's wording into a piece, the way a person's typing goes in: the editor shows
   *  it, the document holds it, and the usual save follows. */
  const replaceWording = (pieceId: string, text: string): boolean => {
    const piece = pieces.find((p) => p.id === pieceId)
    if (!piece || frozen) return false
    const next = docFromText(text)
    doc.replaceDoc(piece.key, next)
    editors.current[piece.key]?.commands.setContent(next, { emitUpdate: false })
    return true
  }
  /** A check's fix in its piece's editor, then the save at once so the checks run again on the
   *  result; false when the piece is gone or nothing in it changed. */
  const fixPiece = async (pieceId: string, fix: Fix): Promise<boolean> => {
    const piece = pieces.find((p) => p.id === pieceId)
    const editor = piece ? editors.current[piece.key] : null
    if (!editor || frozen) return false
    if (!applyFix(editor, fix)) return false
    await doc.flush()
    return true
  }
  const onHighlightClick = (key: string) => {
    const piece = pieces.find((p) => p.key === key)
    if (!piece?.id) return
    const mine = cards.filter((c) => c.status === 'open' && c.item_id === piece.id)
    const card = mine.find((c) => highlightOf(c.kind) === 'fight') ?? mine[0]
    if (card) setSelectedCard(card.id)
  }

  // The page may be scaled to fit its column; a drag's travel is in screen pixels and is
  // brought back to the page's own.
  const fit = useFitScale(PAGE_WIDTH)
  const { column: fitColumn, page: fitPage } = fit
  const unscale: Modifier = ({ transform }) =>
    fit.scale < 1
      ? { ...transform, x: transform.x / fit.scale, y: transform.y / fit.scale }
      : transform
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const onDragEnd = (event: DragEndEvent) => {
    const { active: dragged, over } = event
    if (!over || dragged.id === over.id) return
    doc.move(
      pieces.findIndex((p) => p.key === dragged.id),
      pieces.findIndex((p) => p.key === over.id),
    )
  }

  // ---- footnotes -------------------------------------------------------------------------------
  const [footnote, setFootnote] = useState<{ editing: LocalCitation | null; nonce: number } | null>(
    null,
  )
  const addFootnote = (text: string) => {
    if (footnote?.editing) doc.editCitation(footnote.editing.key, text)
    else doc.addCitation(text, active?.key ?? null, { editor: active?.editor })
    setFootnote(null)
  }

  // ---- the header ------------------------------------------------------------------------------
  const template = $api.useQuery(
    'get',
    '/api/drafting/templates/{template_id}',
    { params: { path: { template_id: draft.source_template_id ?? '' } } },
    { enabled: Boolean(draft.source_template_id) },
  )
  const revisions = $api.useQuery(
    'get',
    '/api/drafting/templates/{template_id}/revisions',
    { params: { path: { template_id: draft.source_template_id ?? '' } } },
    { enabled: Boolean(draft.source_template_id && draft.source_revision_id) },
  )
  const revision = revisions.data?.find((r) => r.id === draft.source_revision_id)?.revision
  const [view, setView] = useState<'edit' | 'pdf'>('edit')
  const [previewAs, setPreviewAs] = useState('')
  const choices = draft.recipients
    .map((r) => {
      const agency = agencies.find((a) => a.id === r.agency_id)
      return { id: r.agency_id, name: agency?.short_name || agency?.name || 'An agency' }
    })
    .filter((r, index, all) => all.findIndex((other) => other.id === r.id) === index)

  const pageStyle = {
    '--letter-font': `'${shell?.font ?? 'Times New Roman'}'`,
    '--letter-size': `${shell?.font_size_pt ?? 12}pt`,
    '--letter-margin': `${shell?.margin_inches ?? 1}in`,
  } as CSSProperties
  const editing = view === 'edit' && !previewAs

  return (
    <section
      aria-label="The letter"
      className="mx-2 mb-2 flex flex-1 flex-col overflow-hidden rounded-16 bg-card shadow-float"
    >
      <div className="flex items-center gap-3 border-b border-fill-muted px-5 py-[13px] leading-[normal]">
        <div className="min-w-0 flex-1">
          <p className="truncate text-14 font-semibold text-ink">
            {subject.trim() || 'Untitled letter'}{' '}
            <span className="text-12 font-normal text-muted">
              {lettersLabel(draft.recipients.length)}
            </span>
          </p>
          <p className="text-115 text-muted">
            {metaLine([
              template.data?.jurisdiction,
              template.data?.legal_basis,
              revision != null && `rev ${revision}`,
              saveLabel,
            ]) || ' '}
          </p>
        </div>
        <PreviewAs choices={choices} value={previewAs} onChange={setPreviewAs} />
        <div
          role="group"
          aria-label="View"
          className="ml-auto flex shrink-0 gap-0.5 rounded-10 bg-page p-[3px]"
        >
          {(['edit', 'pdf'] as const).map((which) => (
            <button
              key={which}
              type="button"
              aria-pressed={view === which}
              onClick={() => setView(which)}
              className={cx(
                'rounded-8 px-3 py-1.5 text-12 whitespace-nowrap',
                view === which
                  ? 'bg-card-white font-semibold text-ink shadow-xs'
                  : 'font-medium text-muted hover:text-ink-2',
              )}
            >
              {which === 'edit' ? 'Edit' : 'PDF'}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onNext}
          className="shrink-0 rounded-pill bg-brand px-[15px] py-2 text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark"
        >
          Choose agencies →
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 p-6">
          {save.error || frozen ? (
            <div className="mb-4 flex max-w-letter flex-col gap-2">
              {save.error ? (
                <Callout tone="danger" role="alert">
                  {save.error}
                </Callout>
              ) : null}
              {frozen ? (
                <Callout tone="amber" role="status">
                  A letter already reserved for an agency keeps the text it was reserved with.
                  Changes here reach only agencies not reserved yet.
                </Callout>
              ) : null}
            </div>
          ) : null}
          {view === 'pdf' ? (
            <PdfView base={base} agencyId={previewAs} version={version} />
          ) : previewAs ? (
            <HtmlPreview base={base} agencyId={previewAs} version={version} width={PAGE_WIDTH} />
          ) : (
            <>
              {/* The toolbar floats over the top of the page once a part is being edited (no
                  screen draws it; 045 is the page at rest). */}
              {editing && active && !frozen ? (
                <div className="sticky top-1.5 z-10 -mt-[18px] mb-[18px] h-0">
                  <Toolbar
                    editor={active.editor}
                    disabled={frozen}
                    onFootnote={() => setFootnote({ editing: null, nonce: Date.now() })}
                  />
                </div>
              ) : null}
              <div ref={fitColumn} style={fit.columnStyle}>
                <div style={fit.boxStyle}>
                  <div ref={fitPage} style={fit.pageStyle}>
                    <div className={cx('letter-page', frozen && 'frozen')} style={pageStyle}>
                      {shell?.has_seal && shell.id && shell.seal_position !== 'none' ? (
                        <img
                          className={cx('seal', shell.seal_position === 'center' && 'center')}
                          src={`/api/drafting/shells/${shell.id}/seal`}
                          alt=""
                        />
                      ) : shell?.seal_position === 'none' ? null : (
                        <div className="seal seal-empty" aria-hidden />
                      )}

                      <div className="letterhead shell-part">
                        {shell ? (
                          <>
                            <RichView doc={docFromText(shell.letterhead)} />
                            <Link className="shell-hint" to={LETTERHEADS}>
                              Edit letterheads in Settings
                            </Link>
                          </>
                        ) : (
                          <NoLetterhead draftId={draft.id} frozen={frozen} />
                        )}
                      </div>

                      <p className="subject">
                        <span>Re:</span>
                        <input
                          aria-label="Subject"
                          value={subject}
                          readOnly={frozen}
                          onChange={(event) => setSubject(event.target.value)}
                        />
                      </p>

                      {shell && (shell.opening_rich || shell.opening_body) ? (
                        <div className="opening shell-part">
                          <RichView doc={docOf(shell.opening_rich, shell.opening_body)} />
                          <Link className="shell-hint" to={LETTERHEADS}>
                            Edit letterheads in Settings
                          </Link>
                        </div>
                      ) : null}

                      {letter.blocks.map((block) => (
                        <div key={block.id} className="locked-block">
                          <RichView doc={docOf(block.body_rich, block.body)} />
                          <span className="shell-hint">Shared block · edit in Shared blocks</span>
                        </div>
                      ))}

                      <DndContext
                        sensors={sensors}
                        modifiers={[unscale]}
                        collisionDetection={closestCenter}
                        onDragEnd={onDragEnd}
                      >
                        <SortableContext
                          items={pieces.map((p) => p.key)}
                          strategy={verticalListSortingStrategy}
                        >
                          <Adder index={0} always={pieces.length === 0} onAdd={add} />
                          {pieces.map((piece, index) => (
                            <Fragment key={piece.key}>
                              <PieceEditor
                                piece={piece}
                                frozen={frozen}
                                demotable={canDemote(pieces, piece.key)}
                                onDoc={onDoc}
                                onFocus={onFocus}
                                onDemote={onDemote}
                                onPromote={onPromote}
                                onDelete={onDelete}
                                onEditor={onEditor}
                                highlight={piece.id ? (marks.get(piece.id) ?? null) : null}
                                selected={piece.id !== null && piece.id === selectedPiece}
                                onHighlightClick={onHighlightClick}
                              />
                              <Adder index={index + 1} onAdd={add} />
                            </Fragment>
                          ))}
                        </SortableContext>
                      </DndContext>

                      {shell && (shell.closing_rich || shell.closing_body) ? (
                        <div className="closing shell-part">
                          <RichView doc={docOf(shell.closing_rich, shell.closing_body)} />
                          <Link className="shell-hint" to={LETTERHEADS}>
                            Edit letterheads in Settings
                          </Link>
                        </div>
                      ) : null}
                      {shell?.signer_name ? (
                        <p className="signer">
                          {shell.signer_name}
                          {shell.signer_title ? (
                            <>
                              <br />
                              {shell.signer_title}
                            </>
                          ) : null}
                        </p>
                      ) : null}

                      {citations.length > 0 ? (
                        <ol className="footnotes" aria-label="Footnotes">
                          {citations.map((citation) => (
                            <li key={citation.key} className="footnote">
                              <span className="m">{citation.marker}</span> {citation.citation_text}
                              <span className="footnote-tools">
                                <button
                                  type="button"
                                  className="tb"
                                  title={`Edit footnote ${citation.marker}`}
                                  onClick={() =>
                                    setFootnote({ editing: citation, nonce: Date.now() })
                                  }
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className="tb"
                                  title={`Remove footnote ${citation.marker}`}
                                  onClick={() => doc.removeCitation(citation.key)}
                                >
                                  ×
                                </button>
                              </span>
                            </li>
                          ))}
                        </ol>
                      ) : null}
                      <p className="page-count">page 1 of {pageCount ?? '…'}</p>
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
        <div className="box-content flex w-[330px] shrink-0 flex-col border-l-[1.5px] border-line bg-card-white">
          <Rail
            draftId={draft.id}
            rail={rail}
            pieces={pieces}
            recipients={draft.recipients}
            agencies={agencies}
            frozen={frozen}
            selectedCard={selectedCard}
            onSelectCard={setSelectedCard}
            onFocusPiece={focusPiece}
            onReplaceWording={replaceWording}
            onFix={fixPiece}
          />
        </div>
      </div>

      <FootnoteDialog
        key={footnote ? (footnote.editing?.key ?? `new-${footnote.nonce}`) : 'closed'}
        open={footnote !== null}
        initial={footnote?.editing?.citation_text ?? ''}
        marker={footnote?.editing?.marker ?? nextMarker(citations)}
        onClose={() => setFootnote(null)}
        onSubmit={addFootnote}
      />
    </section>
  )
}

/** The ＋ between two pieces, offering an item or a paragraph. */
function Adder({
  index,
  always = false,
  onAdd,
}: {
  index: number
  always?: boolean
  onAdd: (index: number, kind: PieceKind) => void
}) {
  return (
    <div className={cx('adder', always && 'always')}>
      <Menu
        className="adder-menu"
        title="Add here"
        items={[
          { label: 'Add item', onPick: () => onAdd(index, 'item') },
          { label: 'Add paragraph', onPick: () => onAdd(index, 'prose') },
        ]}
      >
        ＋
      </Menu>
    </div>
  )
}

/** A draft with no letterhead yet: pick one here; the letterheads themselves live in Settings. */
function NoLetterhead({ draftId, frozen }: { draftId: string; frozen: boolean }) {
  const client = useQueryClient()
  const shells = $api.useQuery('get', '/api/drafting/shells')
  const [error, setError] = useState<string | null>(null)
  const choose = async (shellId: string) => {
    setError(null)
    const result = await api.PATCH('/api/drafting/drafts/{draft_id}', {
      params: { path: { draft_id: draftId } },
      body: { shell_id: shellId || null },
    })
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    await client.invalidateQueries({ queryKey: DRAFT_KEY })
  }
  return (
    <div className="font-ui text-12 text-ink-3">
      <label className="flex items-center gap-2">
        No letterhead on this letter.
        <Select
          className="w-56"
          aria-label="Letterhead"
          value=""
          disabled={frozen}
          onChange={(event) => void choose(event.target.value)}
        >
          <option value="">Choose a letterhead…</option>
          {(shells.data ?? []).map((shell) => (
            <option key={shell.id} value={shell.id}>
              {shell.name}
            </option>
          ))}
        </Select>
      </label>
      {error ? <span className="text-danger">{error}</span> : null}
    </div>
  )
}
