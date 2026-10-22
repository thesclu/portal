/**
 * Letterheads (design 1z): a card per letterhead ("Times New Roman 12 · 1 in margins · seal
 * left · 38 templates", or "Adds FOIA reference block · 4 templates" when it brings blocks of
 * its own), and a letterhead's own page: its lines, opening and closing in the rich-text editor,
 * the page's font, size and margins, the seal and where it sits, the signer, and the shared
 * blocks a template or a letter started on it begins with (P19h).
 */
import type { JSONContent } from '@tiptap/core'
import type { Editor } from '@tiptap/react'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { Empty, LoadFailed, Loading } from '../../components/States'
import { Badge, Button, Field, Input, Notice, Select } from '../../components/ui'
import { isFullStaff } from '../../lib/roles'
import { docOf, firstWords, plainText } from '../newrequest/editor/letter'
import { TextEditor } from '../newrequest/editor/TextEditor'
import { Toolbar } from '../newrequest/editor/Toolbar'
import { LETTERHEADS, LibraryNav } from './LibraryNav'
import { confirm, text, useRefreshLibrary } from './shared'

type Letterhead = Schemas['ShellOut']
type Block = Schemas['BlockOut']

const FONTS = ['Times New Roman', 'Georgia', 'Garamond', 'Arial', 'Helvetica', 'Calibri']
const SEAL_POSITIONS = [
  { value: 'left', label: 'Left' },
  { value: 'center', label: 'Centre' },
  { value: 'none', label: 'None' },
]

function newLetterhead(onMade: (letterhead: Letterhead) => void): ActionSpec {
  return {
    key: 'new-letterhead',
    title: 'New letterhead',
    submit: 'Create',
    fields: [{ name: 'name', label: 'Name', required: true }],
    run: async (values) => {
      onMade(
        unwrap(
          await api.POST('/api/drafting/shells', {
            body: {
              name: text(values, 'name'),
              letterhead: '',
              opening_body: '',
              closing_body: '',
              is_default: false,
              block_ids: [],
            },
          }),
        ),
      )
    },
  }
}

function deleteLetterhead(letterhead: Letterhead, onDeleted?: () => void): ActionSpec {
  return confirm({
    key: `delete-letterhead-${letterhead.id}`,
    title: 'Delete letterhead',
    intro: `“${letterhead.name}” will be deleted. This cannot be undone.`,
    submit: 'Delete',
    danger: true,
    run: async () => {
      unwrap(
        await api.DELETE('/api/drafting/shells/{shell_id}', {
          params: { path: { shell_id: letterhead.id } },
        }),
      )
      onDeleted?.()
    },
  })
}

function templatesLabel(count: number): string {
  return count === 1 ? '1 template' : `${count} templates`
}

/** "Times New Roman 12 · 1 in margins · seal left · 38 templates"; a letterhead with blocks of
 *  its own says what it adds instead: "Adds FOIA reference block · 4 templates". */
export function letterheadLine(
  letterhead: Pick<
    Letterhead,
    'font' | 'font_size_pt' | 'margin_inches' | 'seal_position' | 'block_ids'
  >,
  templates: number,
  blockNames: ReadonlyMap<string, string> = new Map(),
): string {
  const added = letterhead.block_ids.map((id) => blockNames.get(id)).filter(Boolean)
  if (added.length > 0) return [`Adds ${added.join(', ')}`, templatesLabel(templates)].join(' · ')
  const margin = letterhead.margin_inches ?? 1
  return [
    `${letterhead.font ?? 'Times New Roman'} ${letterhead.font_size_pt ?? 12}`,
    `${margin} in margins`,
    `seal ${letterhead.seal_position ?? 'left'}`,
    templatesLabel(templates),
  ].join(' · ')
}

export function Letterheads() {
  const me = useMe()
  const navigate = useNavigate()
  const refresh = useRefreshLibrary()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const letterheads = $api.useQuery('get', '/api/drafting/shells')
  const templates = $api.useQuery('get', '/api/drafting/templates', {
    params: { query: { include_archived: false } },
  })
  const blocks = $api.useQuery('get', '/api/drafting/blocks')
  const blockNames = new Map((blocks.data ?? []).map((block) => [block.id, block.name]))
  const count = new Map<string, number>()
  for (const template of templates.data ?? []) {
    if (template.shell_id) count.set(template.shell_id, (count.get(template.shell_id) ?? 0) + 1)
  }

  return (
    <div>
      <PageHeader
        title="Letterheads"
        trail={['Settings', 'Letter templates']}
        actions={
          isFullStaff(me.records_role) ? (
            <Button
              kind="primary"
              onClick={() =>
                setSpec(newLetterhead((made) => void navigate(`${LETTERHEADS}/${made.id}`)))
              }
            >
              New letterhead
            </Button>
          ) : null
        }
      />
      <div className="px-7 pb-10">
        <LibraryNav />
        {letterheads.isPending ? (
          <Loading what="Loading letterheads" />
        ) : letterheads.isError ? (
          <LoadFailed
            what="the letterheads"
            error={letterheads.error}
            onRetry={() => void letterheads.refetch()}
          />
        ) : letterheads.data.length === 0 ? (
          <Empty title="No letterheads yet">
            A letterhead is the page a letter is printed on: its lines at the top, the opening
            before the items and the closing after them, the font and the seal.
          </Empty>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {letterheads.data.map((letterhead) => (
              <li key={letterhead.id}>
                <Link
                  to={`${LETTERHEADS}/${letterhead.id}`}
                  className="block h-full rounded-16 border border-line bg-card p-4 shadow-card hover:border-brand"
                >
                  <p className="flex items-center gap-2 text-13 font-semibold">
                    {letterhead.name}
                    {letterhead.is_default ? <Badge tone="accent">Default</Badge> : null}
                    {letterhead.archived_at ? <Badge>Archived</Badge> : null}
                  </p>
                  <p className="mt-1 text-12 text-ink-2">
                    {letterheadLine(letterhead, count.get(letterhead.id) ?? 0, blockNames)}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
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

export function LetterheadEditor() {
  const { shellId = '' } = useParams()
  const letterheads = $api.useQuery('get', '/api/drafting/shells')
  if (letterheads.isPending) return <Loading what="Loading the letterhead" />
  if (letterheads.isError)
    return (
      <div className="px-7 pb-10">
        <LoadFailed
          what="the letterhead"
          error={letterheads.error}
          onRetry={() => void letterheads.refetch()}
        />
      </div>
    )
  const letterhead = letterheads.data.find((l) => l.id === shellId)
  if (!letterhead)
    return (
      <div className="px-7 pb-10">
        <Empty title="No such letterhead">
          <Link className="text-brand hover:underline" to={LETTERHEADS}>
            All letterheads
          </Link>
        </Empty>
      </div>
    )
  return <LetterheadForm key={letterhead.id} letterhead={letterhead} />
}

function LetterheadForm({ letterhead }: { letterhead: Letterhead }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const refresh = useRefreshLibrary()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [form, setForm] = useState({
    name: letterhead.name,
    letterhead: letterhead.letterhead,
    font: letterhead.font ?? 'Times New Roman',
    font_size_pt: String(letterhead.font_size_pt ?? 12),
    margin_inches: String(letterhead.margin_inches ?? 1),
    seal_position: letterhead.seal_position ?? 'left',
    signer_name: letterhead.signer_name ?? '',
    signer_title: letterhead.signer_title ?? '',
    is_default: letterhead.is_default,
    block_ids: letterhead.block_ids,
  })
  const blocks = $api.useQuery('get', '/api/drafting/blocks')
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [key]: value }))
  const [opening, setOpening] = useState<JSONContent>(() =>
    docOf(letterhead.opening_rich, letterhead.opening_body),
  )
  const [closing, setClosing] = useState<JSONContent>(() =>
    docOf(letterhead.closing_rich, letterhead.closing_body),
  )
  const [editor, setEditor] = useState<Editor | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  // The seal is cached for five minutes; a new one is asked for under a new query.
  const [sealVersion, setSealVersion] = useState(0)
  const [sealError, setSealError] = useState<string | null>(null)
  const [sealBusy, setSealBusy] = useState(false)

  async function save() {
    const size = Number(form.font_size_pt)
    const margin = Number(form.margin_inches)
    if (!form.name.trim()) return setError('A letterhead needs a name.')
    if (!Number.isFinite(size) || size < 6 || size > 24)
      return setError('The size is in points, between 6 and 24.')
    if (!Number.isFinite(margin) || margin < 0.25 || margin > 3)
      return setError('The margins are in inches, between 0.25 and 3.')
    setBusy(true)
    setError(null)
    setSaid(null)
    const result = await api.PATCH('/api/drafting/shells/{shell_id}', {
      params: { path: { shell_id: letterhead.id } },
      body: {
        name: form.name.trim(),
        letterhead: form.letterhead,
        opening_rich: opening as Record<string, unknown>,
        opening_body: plainText(opening),
        closing_rich: closing as Record<string, unknown>,
        closing_body: plainText(closing),
        font: form.font,
        font_size_pt: size,
        margin_inches: margin,
        seal_position: form.seal_position,
        signer_name: form.signer_name.trim() || null,
        signer_title: form.signer_title.trim() || null,
        is_default: form.is_default,
        block_ids: form.block_ids,
      },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    await refresh()
    setSaid('Saved.')
  }

  async function uploadSeal(file: File | undefined) {
    if (!file) return
    setSealBusy(true)
    setSealError(null)
    const payload = new FormData()
    payload.set('file', file)
    const result = await api.POST('/api/drafting/shells/{shell_id}/seal', {
      params: { path: { shell_id: letterhead.id } },
      body: payload as never,
      bodySerializer: (body) => body as unknown as FormData,
    })
    setSealBusy(false)
    if (result.error) {
      setSealError(messageOf(result.error))
      return
    }
    setSealVersion(Date.now())
    await client.invalidateQueries({ queryKey: ['get', '/api/drafting/shells'] })
  }

  return (
    <div>
      <PageHeader
        title={letterhead.name}
        trail={['Settings', 'Letter templates', 'Letterheads']}
        actions={
          <>
            <Button onClick={() => void navigate(LETTERHEADS)}>Cancel</Button>
            <Button kind="primary" busy={busy} onClick={() => void save()}>
              Save
            </Button>
          </>
        }
      />
      <div className="max-w-3xl px-7 pb-10">
        <div className="mb-4">
          <Notice>
            A change here reaches every template on this letterhead, and every letter not yet filed.
          </Notice>
        </div>
        <div className="space-y-4">
          <Field label="Name">
            <Input value={form.name} onChange={(event) => set('name', event.target.value)} />
          </Field>
          <Field label="Letterhead lines" hint="Printed at the top of the page, one per line.">
            <textarea
              rows={4}
              value={form.letterhead}
              onChange={(event) => set('letterhead', event.target.value)}
              className="w-full rounded-12 border border-line bg-card-white px-2.5 py-2 text-13"
            />
          </Field>
          <div>
            <p className="mb-1 text-12 font-medium text-ink-2">Opening</p>
            <p className="mb-1 text-12 text-ink-3">
              Printed before the first item or shared block.
            </p>
            <Toolbar editor={editor} disabled={false} onFootnote={() => {}} fluid />
            <TextEditor
              doc={opening}
              hint="The opening"
              label="Opening"
              onDoc={setOpening}
              onFocus={setEditor}
            />
          </div>
          <div>
            <p className="mb-1 text-12 font-medium text-ink-2">Closing</p>
            <p className="mb-1 text-12 text-ink-3">Printed at the end, after the citations.</p>
            <TextEditor
              doc={closing}
              hint="The closing"
              label="Closing"
              onDoc={setClosing}
              onFocus={setEditor}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Font">
              <Select value={form.font} onChange={(event) => set('font', event.target.value)}>
                {[...new Set([form.font, ...FONTS])].map((font) => (
                  <option key={font} value={font}>
                    {font}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Size" hint="Points.">
              <Input
                type="number"
                min={6}
                max={24}
                step={0.5}
                value={form.font_size_pt}
                onChange={(event) => set('font_size_pt', event.target.value)}
              />
            </Field>
            <Field label="Margins" hint="Inches, on every side.">
              <Input
                type="number"
                min={0.25}
                max={3}
                step={0.25}
                value={form.margin_inches}
                onChange={(event) => set('margin_inches', event.target.value)}
              />
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Seal">
              <Select
                value={form.seal_position}
                onChange={(event) => set('seal_position', event.target.value)}
              >
                {SEAL_POSITIONS.map((position) => (
                  <option key={position.value} value={position.value}>
                    {position.label}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="sm:col-span-2">
              <Field
                label="Seal image"
                hint="PNG, JPEG or SVG, up to four megabytes. It is kept as soon as it is chosen."
                error={sealError}
              >
                <input
                  type="file"
                  accept="image/*"
                  disabled={sealBusy}
                  className="block w-full text-125"
                  onChange={(event) => void uploadSeal(event.target.files?.[0])}
                />
              </Field>
              {letterhead.has_seal || sealVersion ? (
                <img
                  className="mt-2 h-20 w-auto rounded-9 border border-line bg-card-white p-1"
                  src={`/api/drafting/shells/${letterhead.id}/seal?v=${sealVersion}`}
                  alt="The seal as it stands"
                />
              ) : (
                <p className="mt-2 text-12 text-ink-3">No seal image yet.</p>
              )}
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Signer name">
              <Input
                value={form.signer_name}
                onChange={(event) => set('signer_name', event.target.value)}
              />
            </Field>
            <Field label="Signer title">
              <Input
                value={form.signer_title}
                onChange={(event) => set('signer_title', event.target.value)}
              />
            </Field>
          </div>
          <BlockPicker
            blocks={blocks.data ?? null}
            failed={blocks.isError}
            chosen={form.block_ids}
            onChange={(block_ids) => set('block_ids', block_ids)}
          />
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={form.is_default}
              onChange={(event) => set('is_default', event.target.checked)}
            />
            <span>
              <span className="block text-13 font-medium">Default letterhead</span>
              <span className="block text-12 text-ink-3">
                Used when a letter is started without one. Only one letterhead is the default.
              </span>
            </span>
          </label>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          {said ? <Notice tone="ok">{said}</Notice> : null}
          <div className="border-t border-line-2 pt-4">
            <Button
              kind="danger"
              onClick={() =>
                setSpec(deleteLetterhead(letterhead, () => void navigate(LETTERHEADS)))
              }
            >
              Delete letterhead
            </Button>
          </div>
        </div>
      </div>
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

/** The shared blocks a template made on this letterhead starts with, in the order listed here;
 *  a letter written from scratch on it starts with them as prose. */
function BlockPicker({
  blocks,
  failed,
  chosen,
  onChange,
}: {
  blocks: Block[] | null
  failed: boolean
  chosen: string[]
  onChange: (ids: string[]) => void
}) {
  const toggle = (id: string, on: boolean) =>
    onChange(on ? [...chosen.filter((c) => c !== id), id] : chosen.filter((c) => c !== id))
  return (
    <fieldset>
      <legend className="mb-1 text-12 font-medium text-ink-2">Blocks this letterhead adds</legend>
      <p className="mb-2 text-12 text-ink-3">
        A template made on this letterhead starts with these shared blocks, in this order; a letter
        written without a template starts with their words. Templates already made keep what they
        have.
      </p>
      {failed ? (
        <p className="text-125 text-danger">Couldn’t load the shared blocks.</p>
      ) : blocks === null ? (
        <p className="text-125 text-ink-3">Loading the shared blocks…</p>
      ) : blocks.length === 0 ? (
        <p className="text-125 text-ink-3">
          There are no shared blocks yet.{' '}
          <Link className="text-brand hover:underline" to="/templates/blocks">
            Add one under Shared blocks
          </Link>
          .
        </p>
      ) : (
        <ul className="space-y-1.5">
          {[
            ...chosen.map((id) => blocks.find((b) => b.id === id)).filter(Boolean),
            ...blocks.filter((b) => !chosen.includes(b.id)),
          ].map((block) => (
            <li key={block!.id}>
              <label className="flex items-start gap-2 text-125">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={chosen.includes(block!.id)}
                  onChange={(event) => toggle(block!.id, event.target.checked)}
                />
                <span>
                  <span className="block font-medium">{block!.name}</span>
                  <span className="block text-12 text-ink-3">
                    {firstWords(docOf(block!.body_rich, block!.body), 72)}
                  </span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  )
}
