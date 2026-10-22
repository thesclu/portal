/**
 * Shared blocks (design 1z): paragraphs written once and put into several templates. The table
 * Block · Used in · Updated · ⋯, and a block's own page with its words in the rich-text editor
 * and its own footnotes under them (P19h), numbered 1, 2, … as the block's own; a letter
 * renumbers them after the template's when it is assembled.
 */
import type { JSONContent } from '@tiptap/core'
import type { Editor } from '@tiptap/react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { Empty, LoadFailed, Loading } from '../../components/States'
import { Button, Field, Input, Notice } from '../../components/ui'
import { formatInstant } from '../../lib/dates'
import { isFullStaff } from '../../lib/roles'
import { FootnoteDialog } from '../newrequest/editor/FootnoteDialog'
import { docOf, nextMarker, plainText } from '../newrequest/editor/letter'
import { TextEditor } from '../newrequest/editor/TextEditor'
import { Menu, Toolbar } from '../newrequest/editor/Toolbar'
import { BLOCKS, LibraryNav, ROW_MENU_BUTTON, Th } from './LibraryNav'
import { confirm, useRefreshLibrary } from './shared'

type Block = Schemas['BlockOut']

/** The document the API keeps for a block, once it sends one. Until then the plain text. */
function richOf(block: Block): Record<string, unknown> | null {
  const rich = (block as { body_rich?: unknown }).body_rich
  return rich && typeof rich === 'object' ? (rich as Record<string, unknown>) : null
}

function updatedAt(block: Block): string | null {
  const when = (block as { updated_at?: unknown }).updated_at
  return typeof when === 'string' ? when : null
}

function deleteBlock(block: Block, onDeleted?: () => void): ActionSpec {
  return confirm({
    key: `delete-block-${block.id}`,
    title: 'Delete shared block',
    intro: `“${block.name}” will be deleted. This cannot be undone.`,
    submit: 'Delete',
    danger: true,
    run: async () => {
      unwrap(
        await api.DELETE('/api/drafting/blocks/{block_id}', {
          params: { path: { block_id: block.id } },
        }),
      )
      onDeleted?.()
    },
  })
}

export function usedIn(count: number): string {
  return count === 1 ? '1 template' : `${count} templates`
}

/** "Used in 3 templates · 1 letterhead", or "Not in any template" when in neither. */
export function usedInLine(block: Pick<Block, 'used_in' | 'used_in_letterheads'>): string {
  const parts: string[] = []
  if (block.used_in > 0) parts.push(usedIn(block.used_in))
  if (block.used_in_letterheads > 0)
    parts.push(
      block.used_in_letterheads === 1 ? '1 letterhead' : `${block.used_in_letterheads} letterheads`,
    )
  return parts.length ? parts.join(' · ') : 'Not in any template'
}

export function Blocks() {
  const me = useMe()
  const navigate = useNavigate()
  const refresh = useRefreshLibrary()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const blocks = $api.useQuery('get', '/api/drafting/blocks')

  return (
    <div>
      <PageHeader
        title="Shared blocks"
        trail={['Settings', 'Letter templates']}
        actions={
          isFullStaff(me.records_role) ? (
            <Button kind="primary" onClick={() => void navigate(`${BLOCKS}/new`)}>
              Add block
            </Button>
          ) : null
        }
      />
      <div className="px-7 pb-10">
        <LibraryNav />
        {blocks.isPending ? (
          <Loading what="Loading shared blocks" />
        ) : blocks.isError ? (
          <LoadFailed
            what="the shared blocks"
            error={blocks.error}
            onRetry={() => void blocks.refetch()}
          />
        ) : blocks.data.length === 0 ? (
          <Empty title="No shared blocks yet">
            A shared block is a paragraph written once and put into several templates. Add one for
            any paragraph that several templates should say in the same words.
          </Empty>
        ) : (
          <table className="w-full border-collapse text-125">
            <thead>
              <tr className="border-b border-line">
                <Th>Block</Th>
                <Th>Used in</Th>
                <Th>Updated</Th>
                <Th className="w-8 pr-0">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {blocks.data.map((block) => {
                const when = updatedAt(block)
                return (
                  <tr key={block.id} className="border-b border-line-2">
                    <td className="py-2.5 pr-3">
                      <Link
                        className="text-125 font-medium text-ink hover:underline"
                        to={`${BLOCKS}/${block.id}`}
                      >
                        {block.name}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-3 text-ink-2">{usedInLine(block)}</td>
                    <td className="py-2.5 pr-3 text-ink-2">
                      {when ? formatInstant(when, me.timezone) : '—'}
                    </td>
                    <td className="py-1.5 text-right">
                      <Menu
                        title={`Options for ${block.name}`}
                        align="right"
                        buttonClassName={ROW_MENU_BUTTON}
                        items={[
                          { label: 'Edit', onPick: () => void navigate(`${BLOCKS}/${block.id}`) },
                          { label: 'Delete', onPick: () => setSpec(deleteBlock(block)) },
                        ]}
                      >
                        ⋯
                      </Menu>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
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

/** One block's page: its name and its words. `new` makes one. */
export function BlockEditor() {
  const { blockId = '' } = useParams()
  const blocks = $api.useQuery('get', '/api/drafting/blocks')
  if (blockId === 'new') return <BlockForm key="new" block={null} />
  if (blocks.isPending) return <Loading what="Loading the shared block" />
  if (blocks.isError)
    return (
      <div className="px-7 pb-10">
        <LoadFailed
          what="the shared block"
          error={blocks.error}
          onRetry={() => void blocks.refetch()}
        />
      </div>
    )
  const block = blocks.data.find((b) => b.id === blockId)
  if (!block)
    return (
      <div className="px-7 pb-10">
        <Empty title="No such shared block">
          <Link className="text-brand hover:underline" to={BLOCKS}>
            All shared blocks
          </Link>
        </Empty>
      </div>
    )
  return <BlockForm key={block.id} block={block} />
}

function BlockForm({ block }: { block: Block | null }) {
  const navigate = useNavigate()
  const refresh = useRefreshLibrary()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [name, setName] = useState(block?.name ?? '')
  const [doc, setDoc] = useState<JSONContent>(() =>
    block ? docOf(richOf(block), block.body) : { type: 'doc', content: [{ type: 'paragraph' }] },
  )
  const [editor, setEditor] = useState<Editor | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The footnote dialog, keyed by when it was opened so each opening starts blank.
  const [footnote, setFootnote] = useState<number | null>(null)
  const citations = block?.citations ?? []
  const marker = nextMarker(citations)

  const failed = (caught: unknown) =>
    setError(messageOf(caught, caught instanceof Error ? caught.message : undefined))

  /** The block's name and words to the server; the words as given, so a change the editor has
   *  just made is sent without waiting for React. */
  async function persist(words: JSONContent): Promise<boolean> {
    const body = plainText(words)
    if (!name.trim()) {
      setError('A shared block needs a name.')
      return false
    }
    if (!body) {
      setError('A shared block needs some words.')
      return false
    }
    const payload = { name: name.trim(), body, body_rich: words as Record<string, unknown> }
    unwrap(
      block
        ? await api.PATCH('/api/drafting/blocks/{block_id}', {
            params: { path: { block_id: block.id } },
            body: payload as Schemas['BlockPatchIn'],
          })
        : await api.POST('/api/drafting/blocks', { body: payload as Schemas['BlockIn'] }),
    )
    return true
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      if (!(await persist(doc))) return
      await refresh()
      void navigate(BLOCKS)
    } catch (caught) {
      failed(caught)
    } finally {
      setBusy(false)
    }
  }

  /** A footnote of the block's own: kept at once, its reference put in at the caret, and the
   *  block's words saved with it so the reference is never left behind. */
  async function addFootnote(text: string) {
    if (!block) return
    setFootnote(null)
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.POST('/api/drafting/blocks/{block_id}/citations', {
          params: { path: { block_id: block.id } },
          body: { marker, citation_text: text, sort_order: citations.length },
        }),
      )
      let words = doc
      if (editor) {
        editor.chain().focus().insertFootnoteRef(marker).run()
        words = editor.getJSON()
        setDoc(words)
      }
      await persist(words)
      await refresh()
    } catch (caught) {
      failed(caught)
    } finally {
      setBusy(false)
    }
  }

  async function removeFootnote(citation: Schemas['BlockCitationOut']) {
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.DELETE('/api/drafting/block-citations/{citation_id}', {
          params: { path: { citation_id: citation.id } },
        }),
      )
      await refresh()
    } catch (caught) {
      failed(caught)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <PageHeader
        title={block ? block.name : 'New shared block'}
        trail={['Settings', 'Letter templates', 'Shared blocks']}
        actions={
          <>
            <Button onClick={() => void navigate(BLOCKS)}>Cancel</Button>
            <Button kind="primary" busy={busy} onClick={() => void save()}>
              {block ? 'Save' : 'Add block'}
            </Button>
          </>
        }
      />
      <div className="max-w-3xl px-7 pb-10">
        {block && block.used_in > 0 ? (
          <div className="mb-4">
            <Notice>
              This block is in {usedIn(block.used_in)}. Saving changes the words in each of them;
              letters already started keep the words they have.
            </Notice>
          </div>
        ) : null}
        <div className="space-y-4">
          <Field label="Name" hint="Only for finding it here. It is not printed in a letter.">
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <div>
            <p className="mb-1 text-125 font-medium text-ink-2">Text</p>
            <Toolbar
              editor={editor}
              disabled={false}
              onFootnote={block ? () => setFootnote(Date.now()) : () => {}}
              fluid
            />
            <TextEditor
              doc={doc}
              hint="What the block says"
              label="Text of the shared block"
              onDoc={setDoc}
              onFocus={setEditor}
            />
          </div>
          <div>
            <p className="mb-1 text-125 font-medium text-ink-2">Footnote</p>
            {block ? (
              <>
                {citations.length > 0 ? (
                  <ul className="mb-2 space-y-1" aria-label="Footnotes of this block">
                    {citations.map((citation) => (
                      <li key={citation.id} className="flex items-start gap-2 text-12">
                        <span className="w-6 shrink-0 font-semibold">{citation.marker}</span>
                        <span className="min-w-0 flex-1 whitespace-pre-wrap">
                          {citation.citation_text}
                        </span>
                        <Button
                          kind="quiet"
                          aria-label={`Remove footnote ${citation.marker}`}
                          disabled={busy}
                          onClick={() => void removeFootnote(citation)}
                        >
                          Remove
                        </Button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <Button disabled={busy} onClick={() => setFootnote(Date.now())}>
                  Add a citation
                </Button>
                <p className="mt-1 text-12 text-ink-3">
                  Its reference goes in where the caret is in the text, and the block is saved with
                  it. In a letter the block’s footnotes follow the template’s own.
                </p>
              </>
            ) : (
              <p className="text-12 text-ink-3">Footnotes can be added once the block is saved.</p>
            )}
          </div>
          {error ? <Notice tone="danger">{error}</Notice> : null}
          {block ? (
            <div className="border-t border-line-2 pt-4">
              <Button
                kind="danger"
                onClick={() => setSpec(deleteBlock(block, () => void navigate(BLOCKS)))}
              >
                Delete block
              </Button>
            </div>
          ) : null}
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
      <FootnoteDialog
        key={footnote ?? 'closed'}
        open={footnote !== null}
        initial=""
        marker={marker}
        onClose={() => setFootnote(null)}
        onSubmit={(text) => void addFootnote(text)}
      />
    </div>
  )
}
