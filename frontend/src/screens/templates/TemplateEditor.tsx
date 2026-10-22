/**
 * One template, as design 1z draws it: the header line "CA · CPRA · Standard letterhead ·
 * Revision 7 · used 52 times" with Preview, Archive and Save; the tabs Letter · Citations ·
 * Revisions. The Letter and Citations tabs edit one document (useLetterDocument) saved through
 * PUT /templates/{id}/letter 800 ms after the last change; where the shared blocks sit is saved
 * through PUT /templates/{id}/blocks the same way.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { LoadFailed, Loading } from '../../components/States'
import { Badge, Button, Notice, cx } from '../../components/ui'
import { FootnoteDialog } from '../newrequest/editor/FootnoteDialog'
import { metaLine, nextMarker, type LetterSave, type PieceKind } from '../newrequest/editor/letter'
import { PdfView } from '../newrequest/editor/Preview'
import { Menu } from '../newrequest/editor/Toolbar'
import { SAVE_DELAY, useLetterDocument } from '../newrequest/editor/useLetterDocument'
import { Citations } from './Citations'
import { Letter, type Provisions } from './Letter'
import {
  blocksPayload,
  indexAfter,
  moveRow,
  partRows,
  partsOf,
  piecesBefore,
  rowKey,
  samePlacements,
  type PartRow,
  type Placement,
} from './parts'
import { Revisions } from './Revisions'
import { confirm, text, usedTimes, useRefreshLibrary } from './shared'
import { detailBody, detailFields, lawsOf } from './templateFields'

type Template = Schemas['TemplateDetailOut']
type Letterhead = Schemas['ShellOut']
type Block = Schemas['BlockOut']
type LetterOut = Schemas['LetterOut']
type Revision = Schemas['RevisionOut']

const HOME = '/templates'
const TABS = [
  { key: 'letter', label: 'Letter' },
  { key: 'citations', label: 'Citations' },
  { key: 'revisions', label: 'Revisions' },
] as const
type Tab = (typeof TABS)[number]['key']

function saveTemplate(template: Template, flush: () => Promise<boolean>): ActionSpec {
  return {
    key: `save-${template.id}`,
    title: 'Save template',
    intro:
      'Saving records the template as it stands now. Letters started from it afterwards point at this version.',
    submit: 'Save',
    fields: [
      {
        name: 'summary',
        label: 'What changed',
        required: true,
        hint: 'One line. It is kept with this version.',
      },
    ],
    run: async (values) => {
      if (!(await flush())) {
        throw new Error('The latest changes could not be saved, so no revision was recorded.')
      }
      unwrap(
        await api.POST('/api/drafting/templates/{template_id}/save', {
          params: { path: { template_id: template.id } },
          body: { summary: text(values, 'summary') },
        }),
      )
    },
  }
}

function editDetails(template: Template, letterheads: Letterhead[], laws: string[]): ActionSpec {
  return {
    key: `details-${template.id}`,
    title: 'Edit details',
    submit: 'Done',
    fields: [
      { name: 'name', label: 'Name', required: true, initial: template.name },
      ...detailFields(letterheads, laws, template),
    ],
    run: async (values) => {
      unwrap(
        await api.PATCH('/api/drafting/templates/{template_id}', {
          params: { path: { template_id: template.id } },
          body: { name: text(values, 'name'), ...detailBody(values) },
        }),
      )
    },
  }
}

function setArchived(template: Template, archived: boolean): ActionSpec {
  return confirm({
    key: `archive-${template.id}-${archived}`,
    title: archived ? 'Archive template' : 'Bring template back',
    intro: archived
      ? `“${template.name}” will no longer be offered when a letter is started. Nothing filed from it changes, and it can be brought back.`
      : `“${template.name}” will be offered again when a letter is started.`,
    submit: archived ? 'Archive' : 'Unarchive',
    run: async () => {
      unwrap(
        await api.POST('/api/drafting/templates/{template_id}/archive', {
          params: { path: { template_id: template.id }, query: { archived } },
        }),
      )
    },
  })
}

function deleteTemplate(template: Template, onDeleted: () => void): ActionSpec {
  return confirm({
    key: `delete-${template.id}`,
    title: 'Delete template',
    intro: `“${template.name}”, its items and its citations will be deleted. This cannot be undone.`,
    submit: 'Delete',
    danger: true,
    run: async () => {
      unwrap(
        await api.DELETE('/api/drafting/templates/{template_id}', {
          params: { path: { template_id: template.id } },
        }),
      )
      onDeleted()
    },
  })
}

function savedLine(template: Template): { tone: 'ok' | 'warn'; label: string } {
  if (template.saved) return { tone: 'ok', label: `Saved as revision ${template.latest_revision}` }
  return {
    tone: 'warn',
    label:
      template.latest_revision == null
        ? 'Not saved yet'
        : `Changed since revision ${template.latest_revision}`,
  }
}

/** Loads everything the editor needs, then hands over to the editor keyed on the template. */
export function TemplateEditor() {
  const { templateId = '' } = useParams()
  const path = { template_id: templateId }
  const template = $api.useQuery('get', '/api/drafting/templates/{template_id}', {
    params: { path },
  })
  const letter = $api.useQuery('get', '/api/drafting/templates/{template_id}/letter', {
    params: { path },
  })
  const letterheads = $api.useQuery('get', '/api/drafting/shells')
  const blocks = $api.useQuery('get', '/api/drafting/blocks')
  const revisions = $api.useQuery('get', '/api/drafting/templates/{template_id}/revisions', {
    params: { path },
  })
  const rules = $api.useQuery('get', '/api/records/rules')

  const back = (
    <Link className="text-125 text-brand hover:underline" to={HOME}>
      All templates
    </Link>
  )
  const failed = [
    { query: template, what: 'the template' },
    { query: letter, what: 'the letter' },
    { query: letterheads, what: 'the letterheads' },
    { query: blocks, what: 'the shared blocks' },
  ].find((part) => part.query.isError)
  if (failed) {
    return (
      <div className="px-7 pb-10">
        {back}
        <LoadFailed
          what={failed.what}
          error={failed.query.error}
          onRetry={() => void failed.query.refetch()}
        />
      </div>
    )
  }
  if (!template.data || !letter.data || !letterheads.data || !blocks.data) {
    return <Loading what="Loading the template" />
  }
  const law = template.data.legal_basis?.trim() || null
  // The only choices for an item's own deadline: the provisions the rules file has for this
  // template's law. A rule with no provision is the law's general deadline, which every request
  // already has, so it is not something an item can be given.
  const provisions: Provisions = rules.isPending
    ? { state: 'loading' }
    : rules.isError
      ? { state: 'failed', retry: () => void rules.refetch() }
      : {
          state: 'ready',
          law,
          options: [
            ...new Set(
              rules.data.rules
                .filter((rule) => rule.law === law && rule.provision)
                .map((rule) => rule.provision),
            ),
          ].sort(),
        }
  return (
    <Editor
      key={template.data.id}
      template={template.data}
      letter={letter.data}
      letterheads={letterheads.data}
      catalogue={blocks.data}
      revisions={revisions.data}
      laws={lawsOf(rules.data)}
      provisions={provisions}
    />
  )
}

function Editor({
  template,
  letter,
  letterheads,
  catalogue,
  revisions,
  laws,
  provisions,
}: {
  template: Template
  letter: LetterOut
  letterheads: Letterhead[]
  catalogue: Block[]
  revisions: Revision[] | undefined
  laws: string[]
  provisions: Provisions
}) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const refresh = useRefreshLibrary()
  const [params, setParams] = useSearchParams()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const tab: Tab = TABS.find((t) => t.key === params.get('tab'))?.key ?? 'letter'
  const path = { template_id: template.id }
  const base = `/api/drafting/templates/${template.id}`
  const detailKey = $api.queryOptions('get', '/api/drafting/templates/{template_id}', {
    params: { path },
  }).queryKey
  const letterKey = $api.queryOptions('get', '/api/drafting/templates/{template_id}/letter', {
    params: { path },
  }).queryKey

  // ---- the document ----------------------------------------------------------------------------
  const doc = useLetterDocument({
    letter,
    live: true,
    put: (body: LetterSave) =>
      api.PUT('/api/drafting/templates/{template_id}/letter', {
        params: { path },
        // A template has no subject of its own (its name is printed), and each piece's plain
        // `body` is the server's to derive; the generated type lists `body` as required because
        // the API gives it a default.
        body: {
          pieces: body.pieces,
          citations: body.citations,
        } as unknown as Schemas['LetterSaveIn'],
      }),
    onSaved: (saved) => {
      client.setQueryData(letterKey, saved)
      void client.invalidateQueries({ queryKey: detailKey })
    },
  })

  // ---- where the shared blocks sit -------------------------------------------------------------
  const [placements, setPlacements] = useState<Placement[]>(
    () =>
      partsOf(
        partRows(
          doc.pieces.map((p) => p.key),
          [...letter.blocks]
            .sort((a, b) => a.sort_order - b.sort_order)
            .map((b) => ({ id: b.id, position: b.sort_order })),
        ),
      ).blocks,
  )
  const written = useRef(placements)
  const latestPlacements = useRef(placements)
  const [blocksError, setBlocksError] = useState<string | null>(null)
  const writeBlocks = useRef(async () => {})
  useEffect(() => {
    writeBlocks.current = async () => {
      const next = latestPlacements.current
      if (samePlacements(written.current, next)) return
      const result = await api.PUT('/api/drafting/templates/{template_id}/blocks', {
        params: { path: { template_id: template.id } },
        body: blocksPayload(next),
      })
      if (result.error) {
        setBlocksError(messageOf(result.error))
        return
      }
      written.current = next
      setBlocksError(null)
      void client.invalidateQueries({ queryKey: detailKey })
    }
  })
  useEffect(() => {
    latestPlacements.current = placements
    if (samePlacements(written.current, placements)) return
    const timer = window.setTimeout(() => void writeBlocks.current(), SAVE_DELAY)
    return () => window.clearTimeout(timer)
  }, [placements])
  useEffect(
    () => () => {
      // Leaving with a move still waiting: write it on the way out.
      void writeBlocks.current()
    },
    [],
  )

  // ---- the Parts list --------------------------------------------------------------------------
  const rows = partRows(
    doc.pieces.map((p) => p.key),
    placements,
  )
  const [selected, setSelected] = useState<string | null>(null)
  const blockOf = new Map<string, Block | Schemas['LetterBlockOut']>()
  for (const block of catalogue) blockOf.set(block.id, block)
  for (const block of letter.blocks) blockOf.set(block.id, block)
  const placed = new Set(placements.map((b) => b.id))
  const available = catalogue.filter((block) => !placed.has(block.id))
  const blockCitations = new Map(catalogue.map((block) => [block.id, block.citations]))

  const addPiece = (kind: PieceKind) => {
    const index = indexAfter(rows, selected)
    const key = doc.add(piecesBefore(rows, index), kind)
    const next: PartRow[] = [...rows]
    next.splice(index, 0, { kind: 'piece', key })
    setPlacements(partsOf(next).blocks)
    setSelected(rowKey({ kind: 'piece', key }))
  }
  const insertBlock = (blockId: string) => {
    const index = indexAfter(rows, selected)
    const row: PartRow = { kind: 'block', id: blockId }
    const next: PartRow[] = [...rows]
    next.splice(index, 0, row)
    setPlacements(partsOf(next).blocks)
    setSelected(rowKey(row))
  }
  const removeRow = (row: PartRow) => {
    if (row.kind === 'piece') doc.onDelete(row.key)
    setPlacements(partsOf(rows.filter((r) => rowKey(r) !== rowKey(row))).blocks)
    if (selected === rowKey(row)) setSelected(null)
  }
  const move = (from: number, to: number) => {
    const { pieceKeys, blocks } = partsOf(moveRow(rows, from, to))
    doc.reorder(pieceKeys)
    setPlacements(blocks)
  }

  // ---- footnotes -------------------------------------------------------------------------------
  const [footnote, setFootnote] = useState<number | null>(null)
  const selectedPiece = selected?.startsWith('piece:') ? selected.slice('piece:'.length) : null
  const addFootnote = (words: string) => {
    const editor = doc.active?.key === selectedPiece ? doc.active.editor : null
    doc.addCitation(words, selectedPiece, { editor })
    setFootnote(null)
  }

  // ---- the header ------------------------------------------------------------------------------
  const [view, setView] = useState<'edit' | 'pdf'>('edit')
  const letterhead = letterheads.find((l) => l.id === template.shell_id)
  const newest =
    template.latest_revision ?? revisions?.reduce((n, r) => Math.max(n, r.revision), 0) ?? null
  const status = savedLine(template)
  const more = [
    {
      label: 'Edit details',
      onPick: () => setSpec(editDetails(template, letterheads, laws)),
    },
    { label: 'Delete', onPick: () => setSpec(deleteTemplate(template, () => void navigate(HOME))) },
  ]
  const counts: Record<Tab, number | null> = {
    letter: null,
    citations: doc.citations.length,
    revisions: revisions?.length ?? null,
  }

  return (
    <div>
      <PageHeader
        title={template.name}
        trail={['Settings', 'Letter templates']}
        actions={
          <>
            <Button
              aria-pressed={view === 'pdf'}
              onClick={() => {
                if (view === 'pdf') setView('edit')
                else void doc.flush().then(() => setView('pdf'))
              }}
            >
              {view === 'pdf' ? 'Back to editing' : 'Preview'}
            </Button>
            <Button onClick={() => setSpec(setArchived(template, !template.archived_at))}>
              {template.archived_at ? 'Unarchive' : 'Archive'}
            </Button>
            <Button kind="primary" onClick={() => setSpec(saveTemplate(template, doc.flush))}>
              Save
            </Button>
            <Menu
              title="More"
              items={more}
              align="right"
              buttonClassName="h-8 rounded-9 border border-line bg-card-white px-3 text-125 text-ink hover:bg-page"
            >
              ⋯
            </Menu>
          </>
        }
      />
      <div className="px-7 pb-10">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-125 text-ink-2">
          <span>
            {metaLine([
              template.jurisdiction,
              template.legal_basis,
              letterhead?.name,
              newest != null && newest > 0 && `Revision ${newest}`,
              usedTimes(template.filed_count),
              doc.saveLabel,
            ])}
          </span>
          <Badge tone={status.tone}>{status.label}</Badge>
          {template.archived_at ? <Badge>Archived</Badge> : null}
          <Link className="text-12 text-brand hover:underline" to={HOME}>
            All templates
          </Link>
        </p>
        {doc.save.error ? (
          <div className="mt-3">
            <Notice tone="danger">{doc.save.error}</Notice>
          </div>
        ) : null}
        {blocksError ? (
          <div className="mt-3">
            <Notice tone="danger">{blocksError}</Notice>
          </div>
        ) : null}

        {view === 'pdf' ? (
          <div className="mt-4">
            <PdfView base={base} version={doc.version} />
          </div>
        ) : (
          <>
            <div
              role="tablist"
              aria-label="Parts of the template"
              className="mt-4 mb-4 flex gap-1 border-b border-line"
            >
              {TABS.map((t) => (
                <button
                  key={t.key}
                  role="tab"
                  aria-selected={tab === t.key}
                  onClick={() =>
                    setParams(t.key === 'letter' ? {} : { tab: t.key }, { replace: true })
                  }
                  className={cx(
                    '-mb-px border-b-2 px-3 py-2 text-13',
                    tab === t.key
                      ? 'border-ink font-semibold text-ink'
                      : 'border-transparent text-ink-2',
                  )}
                >
                  {t.label}
                  {counts[t.key] != null ? (
                    <span className="ml-1.5 text-13 text-ink-3">{counts[t.key]}</span>
                  ) : null}
                </button>
              ))}
            </div>
            {tab === 'letter' ? (
              <Letter
                doc={doc}
                rows={rows}
                blockOf={blockOf}
                available={available}
                blockCitations={blockCitations}
                selected={selected}
                provisions={provisions}
                onSelect={setSelected}
                onAdd={addPiece}
                onInsertBlock={insertBlock}
                onRemove={removeRow}
                onMove={move}
                onFootnote={() => setFootnote(Date.now())}
              />
            ) : tab === 'citations' ? (
              <Citations doc={doc} onAction={setSpec} />
            ) : (
              <Revisions templateId={template.id} />
            )}
          </>
        )}
      </div>
      <FootnoteDialog
        key={footnote ?? 'closed'}
        open={footnote !== null}
        initial=""
        marker={nextMarker(doc.citations)}
        onClose={() => setFootnote(null)}
        onSubmit={addFootnote}
      />
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          void refresh()
        }}
      />
    </div>
  )
}
