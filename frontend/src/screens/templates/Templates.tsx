import { useState } from 'react'
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { Empty, LoadFailed, Loading } from '../../components/States'
import { templatesFramePath } from '../../lib/fork'
import { useFork } from '../newrequest/ForkLetter'
import { Badge, Button } from '../../components/ui'
import { formatInstant } from '../../lib/dates'
import { isFullStaff } from '../../lib/roles'
import { Menu } from '../newrequest/editor/Toolbar'
import { BlockEditor, Blocks } from './Blocks'
import { LetterheadEditor, Letterheads } from './Letterheads'
import { HOME, LibraryNav, ROW_MENU_BUTTON, Th } from './LibraryNav'
import { confirm, messageFrom, text, used, useRefreshLibrary } from './shared'
import { SavedReplies } from '../replies/SavedReplies'
import { TemplatesFrame } from './ForkTemplates'
import { TemplateEditor } from './TemplateEditor'
import { detailBody, detailFields, lawsOf } from './templateFields'

type Template = Schemas['TemplateOut']
type Letterhead = Schemas['ShellOut']

/** Settings › Letter templates (design 1z): the templates, the shared blocks they pull in, and
 *  the letterheads, each with pages of its own. P21c: while the drafting tool is in use, every one
 *  of those pages is its (`ForkTemplates.tsx`), framed in the main column; Saved replies stays
 *  the portal's. */
export function Templates() {
  const fork = useFork()
  const location = useLocation()
  if (fork.isPending) return <Loading what="Opening Templates" />
  const framed = fork.data?.in_use ? templatesFramePath(location.pathname, location.search) : null
  if (framed && fork.data) {
    return (
      <TemplatesFrame
        web={fork.data.web_url ?? ''}
        origin={fork.data.web_origin ?? ''}
        path={framed}
        height="calc(100vh - 72px)"
        title="Templates"
      />
    )
  }
  return <PortalTemplates />
}

function PortalTemplates() {
  return (
    <Routes>
      <Route index element={<TemplateList />} />
      <Route path="blocks" element={<Blocks />} />
      <Route path="blocks/:blockId" element={<BlockEditor />} />
      <Route path="letterheads" element={<Letterheads />} />
      <Route path="letterheads/:shellId" element={<LetterheadEditor />} />
      <Route path="replies" element={<RepliesTab />} />
      <Route path=":templateId" element={<TemplateEditor />} />
    </Routes>
  )
}

/** Saved replies (148's ready-made texts the Inbox composer inserts), a tab of Templates since
 *  P20h (42h): the same list and editor Settings had; the API is unchanged. */
function RepliesTab() {
  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Templates" />
      <div className="px-7 pb-10">
        <LibraryNav />
        <SavedReplies />
      </div>
    </div>
  )
}

/**
 * A copy is a new template given the other's items, citations and shared blocks one call at a
 * time. If any call is refused the half-made copy is deleted, so a failure leaves nothing behind.
 */
async function copyOf(sourceId: string, name: string): Promise<Template> {
  const source = unwrap(
    await api.GET('/api/drafting/templates/{template_id}', {
      params: { path: { template_id: sourceId } },
    }),
  )
  const made = unwrap(
    await api.POST('/api/drafting/templates', {
      body: {
        name,
        jurisdiction: source.jurisdiction,
        legal_basis: source.legal_basis,
        shell_id: source.shell_id,
        notes: source.notes,
      },
    }),
  )
  const path = { template_id: made.id }
  try {
    // An item under another item needs its parent's new id, so parents are made first.
    const newId = new Map<string, string>()
    let waiting = source.items
    while (waiting.length > 0) {
      const ready = waiting.filter((item) => !item.parent_item_id || newId.has(item.parent_item_id))
      if (ready.length === 0) throw new Error('An item sits under an item that is not there.')
      for (const item of ready) {
        const copied = unwrap(
          await api.POST('/api/drafting/templates/{template_id}/items', {
            params: { path },
            body: {
              kind: item.kind ?? 'item',
              item_number: item.item_number,
              body: item.body,
              sort_order: item.sort_order,
              provision: item.provision ?? null,
              parent_item_id: item.parent_item_id ? newId.get(item.parent_item_id) : null,
            },
          }),
        )
        newId.set(item.id, copied.id)
      }
      waiting = waiting.filter((item) => !newId.has(item.id))
    }
    for (const citation of source.citations) {
      unwrap(
        await api.POST('/api/drafting/templates/{template_id}/citations', {
          params: { path },
          body: {
            marker: citation.marker,
            citation_text: citation.citation_text,
            sort_order: citation.sort_order,
            item_id: citation.item_id ? (newId.get(citation.item_id) ?? null) : null,
          },
        }),
      )
    }
    if (source.block_ids.length > 0) {
      unwrap(
        await api.PUT('/api/drafting/templates/{template_id}/blocks', {
          params: { path },
          body: { block_ids: source.block_ids, sort_orders: source.block_sort_orders },
        }),
      )
    }
  } catch (caught) {
    const removed = await api.DELETE('/api/drafting/templates/{template_id}', { params: { path } })
    throw new Error(
      removed.response.ok
        ? `The copy was not made. ${messageFrom(caught)}`
        : `Only part of “${source.name}” was copied into “${name}”. ${messageFrom(caught)}`,
      { cause: caught },
    )
  }
  return made
}

function newTemplate(
  templates: Template[],
  letterheads: Letterhead[],
  laws: string[],
  onMade: (template: Template) => void,
): ActionSpec {
  const blank = (values: Record<string, unknown>) => !values.from
  return {
    key: 'new-template',
    title: 'New template',
    submit: 'Add template',
    fields: [
      { name: 'name', label: 'Name', required: true },
      {
        name: 'from',
        label: 'Start from',
        type: 'select',
        initial: '',
        options: [
          { value: '', label: 'A blank template' },
          ...templates.map((template) => ({
            value: template.id,
            label: `A copy of ${template.name}`,
          })),
        ],
        hint: 'A copy takes the other template’s law, jurisdiction, letterhead, notes, items, citations and shared blocks.',
      },
      ...detailFields(letterheads, laws, undefined, blank),
    ],
    run: async (values) => {
      const name = text(values, 'name')
      const from = text(values, 'from')
      onMade(
        from
          ? await copyOf(from, name)
          : unwrap(
              await api.POST('/api/drafting/templates', {
                body: { name, ...detailBody(values) },
              }),
            ),
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

function TemplateList() {
  const me = useMe()
  const navigate = useNavigate()
  const refresh = useRefreshLibrary()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [archived, setArchivedShown] = useState(false)
  const templates = $api.useQuery(
    'get',
    '/api/drafting/templates',
    { params: { query: { include_archived: archived } } },
    { placeholderData: (previous) => previous },
  )
  const letterheads = $api.useQuery('get', '/api/drafting/shells')
  const rules = $api.useQuery('get', '/api/records/rules')
  const nameOfLetterhead = new Map((letterheads.data ?? []).map((l) => [l.id, l.name]))

  return (
    <div>
      <PageHeader
        title="Letter templates"
        trail={['Settings']}
        actions={
          // P20h: interns and limited-scope attorneys read the library; only full staff change it.
          isFullStaff(me.records_role) && templates.data && letterheads.data ? (
            <Button
              kind="primary"
              onClick={() =>
                setSpec(
                  newTemplate(
                    templates.data,
                    letterheads.data,
                    lawsOf(rules.data),
                    (made) => void navigate(`${HOME}/${made.id}`),
                  ),
                )
              }
            >
              New template
            </Button>
          ) : null
        }
      />
      <div className="px-7 pb-10">
        <LibraryNav />
        <div className="mb-3 flex items-center gap-3">
          <label className="flex min-w-0 flex-1 items-center gap-2 text-125">
            <input
              type="checkbox"
              checked={archived}
              onChange={(event) => setArchivedShown(event.target.checked)}
            />
            Include archived
          </label>
        </div>
        {templates.isPending ? (
          <Loading what="Loading templates" />
        ) : templates.isError ? (
          <LoadFailed
            what="the templates"
            error={templates.error}
            onRetry={() => void templates.refetch()}
          />
        ) : templates.data.length === 0 ? (
          <Empty title={archived ? 'No templates yet' : 'No templates to show'}>
            {archived
              ? 'A template is a letter written once and reused.'
              : 'Archived templates are left out. Tick “Include archived” to see them.'}
          </Empty>
        ) : (
          <table className="w-full border-collapse text-125">
            <thead>
              <tr className="border-b border-line">
                <Th>Template</Th>
                <Th>Law</Th>
                <Th>Letterhead</Th>
                <Th>Revision</Th>
                <Th>Used</Th>
                <Th>Last used</Th>
                <Th className="w-8 pr-0">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {templates.data.map((template) => (
                <tr key={template.id} className="border-b border-line-2">
                  <td className="py-2.5 pr-3">
                    <p className="flex flex-wrap items-center gap-2">
                      <Link
                        className="text-125 font-medium text-ink hover:underline"
                        to={`${HOME}/${template.id}`}
                      >
                        {template.name}
                      </Link>
                      {template.archived_at ? <Badge>Archived</Badge> : null}
                    </p>
                  </td>
                  <td className="py-2.5 pr-3 text-ink-2">
                    {[template.jurisdiction, template.legal_basis].filter(Boolean).join(' · ') ||
                      '—'}
                  </td>
                  <td className="py-2.5 pr-3 text-ink-2">
                    {(template.shell_id && nameOfLetterhead.get(template.shell_id)) || '—'}
                  </td>
                  <td className="py-2.5 pr-3 text-ink-2">
                    {template.latest_revision != null
                      ? `Revision ${template.latest_revision}`
                      : '—'}
                  </td>
                  <td className="py-2.5 pr-3 text-ink-2">{used(template.filed_count)}</td>
                  <td className="py-2.5 pr-3 text-ink-2">
                    {template.last_used_at
                      ? formatInstant(template.last_used_at, me.timezone)
                      : '—'}
                  </td>
                  <td className="py-1.5 text-right">
                    <Menu
                      title={`Options for ${template.name}`}
                      align="right"
                      buttonClassName={ROW_MENU_BUTTON}
                      items={[
                        { label: 'Open', onPick: () => void navigate(`${HOME}/${template.id}`) },
                        ...(isFullStaff(me.records_role)
                          ? [
                              {
                                label: template.archived_at ? 'Unarchive' : 'Archive',
                                onPick: () => setSpec(setArchived(template, !template.archived_at)),
                              },
                            ]
                          : []),
                      ]}
                    >
                      ⋯
                    </Menu>
                  </td>
                </tr>
              ))}
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
