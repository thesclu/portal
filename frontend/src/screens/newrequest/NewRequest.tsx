import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { useMe } from '../../app/session'
import { unwrap } from '../../components/ActionDialog'
import { ChevronDown, EyeIcon, PencilIcon } from '../../components/icons'
import { Menu } from '../../components/Menu'
import { Empty, LoadFailed, Loading } from '../../components/States'
import { Button, Callout } from '../../components/ui'
import { formatInstant, formatShortInstant } from '../../lib/dates'
import { useStatusLabel } from '../../lib/vocab'
import { AskAssistant } from './AskAssistant'
import { FlowSearch, UseButton } from './parts'
import { itemsLabel, requestsLabel, templateMatches } from './stages/stages'
import { Stepper } from './stages/Stepper'

type Template = Schemas['TemplateOut']
type Row = Schemas['DocketRowOut']

const PAST_LIMIT = 8
/** The table's columns as 044 draws them, 16 px apart. */
const COLUMNS = '1.7fr 1fr 108px 128px 100px'

/** The typed search, a moment after the typing stops, so the server is not asked per keystroke. */
function useSettled(value: string, delay = 250): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const wait = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(wait)
  }, [value, delay])
  return settled
}

/** The letter a past request was sent with, in a new tab: its draft's PDF for that agency. The
 *  tab is opened first, inside the click, so the browser does not take it for a pop-up. */
async function previewPast(requestId: string): Promise<string | null> {
  const opened = window.open('', '_blank')
  const found = await api.GET('/api/drafting/requests/{request_id}/draft', {
    params: { path: { request_id: requestId } },
  })
  if (!found.data) {
    opened?.close()
    return found.response.status === 404
      ? 'That request was not written in the portal, so there is no letter to show.'
      : messageOf(found.error)
  }
  const url = `/api/drafting/drafts/${found.data.draft_id}/pdf?recipient_id=${encodeURIComponent(found.data.recipient_id)}`
  if (opened) opened.location.href = url
  else window.location.href = url
  return null
}

/**
 * New request, stage 1 (044, "Pick a letter"): the stages with "Write from scratch" at the right,
 * one underlined search over master letters and past requests, one table of both with Preview
 * and Use per row, and "Not sure which fits? Ask the assistant" under it (058). Every choice
 * makes a draft and opens it; nothing is filed, and no tracking number is used, until stage 3
 * reserves. The letters already started are a menu at the left of the last line (no screen draws
 * them; they are how a draft is opened again).
 */
export function NewRequest() {
  const me = useMe()
  const navigate = useNavigate()
  const statusLabel = useStatusLabel()
  const [params] = useSearchParams()
  const fromRequest = params.get('from')
  const [query, setQuery] = useState('')
  const settled = useSettled(query)
  const templates = $api.useQuery('get', '/api/drafting/templates')
  const past = $api.useQuery('get', '/api/records/requests', {
    params: { query: { q: settled.trim() || undefined, limit: PAST_LIMIT, sort: 'newest' } },
  })
  const drafts = $api.useQuery('get', '/api/drafting/drafts')
  const source = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}',
    { params: { path: { request_id: fromRequest ?? '' } } },
    { enabled: Boolean(fromRequest) },
  )
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scratch, setScratch] = useState(false)
  const [subject, setSubject] = useState('')
  const [asking, setAsking] = useState(false)

  async function start(key: string, body: Record<string, string>) {
    setBusy(key)
    setError(null)
    try {
      const draft = unwrap(await api.POST('/api/drafting/drafts', { body }))
      void navigate(`/drafts/${draft.id}`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong.')
      setBusy(null)
    }
  }
  const startScratch = (event: FormEvent) => {
    event.preventDefault()
    if (subject.trim()) void start('scratch', { subject: subject.trim() })
  }

  const masters = (templates.data ?? [])
    .filter((template) => !template.archived_at)
    .filter((template) => templateMatches(template, settled))
  const pastRows = past.data?.items ?? []
  const loading = templates.isPending || past.isPending
  const nothing = !loading && masters.length === 0 && pastRows.length === 0
  const started = drafts.data ?? []

  return (
    <>
      <PageHeader title="New request" />
      <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
        <Stepper
          current="start"
          right={
            <button
              type="button"
              aria-expanded={scratch}
              onClick={() => setScratch((was) => !was)}
              className="inline-flex items-center gap-1.5 text-125 leading-[normal] font-medium whitespace-nowrap text-brand hover:text-brand-dark"
            >
              <PencilIcon size={14} width={2} stroke="var(--color-brand)" />
              Write from scratch
            </button>
          }
        />

        {scratch ? (
          <form className="flex items-center gap-3" onSubmit={startScratch}>
            <label className="box-border flex h-[34px] w-full max-w-[380px] items-center border-b-[1.5px] border-brand pr-1">
              <input
                aria-label="Subject of the new letter"
                placeholder="What the request is about"
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                autoFocus
                required
                className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-ink-3"
              />
            </label>
            <Button type="submit" kind="pill" busy={busy === 'scratch'} disabled={!subject.trim()}>
              Start
            </Button>
            <Button kind="text" onClick={() => setScratch(false)}>
              Cancel
            </Button>
          </form>
        ) : (
          <FlowSearch
            className="max-w-[380px]"
            placeholder="Search master letters and past requests"
            value={query}
            onChange={setQuery}
          />
        )}

        {fromRequest ? (
          <Callout tone="grey">
            {source.data ? (
              <span className="flex items-center gap-3">
                <span className="min-w-0 flex-1 text-125 text-ink">
                  Start from {source.data.tracking_number} · {source.data.subject}
                </span>
                <Button
                  kind="pill"
                  busy={busy === 'source'}
                  onClick={() => void start('source', { from_request_id: fromRequest })}
                >
                  Start a letter from it
                </Button>
              </span>
            ) : source.isError ? (
              <LoadFailed
                what="that request"
                error={source.error}
                onRetry={() => void source.refetch()}
              />
            ) : (
              <Loading />
            )}
          </Callout>
        ) : null}
        {error ? (
          <Callout tone="danger" role="alert">
            {error}
          </Callout>
        ) : null}

        <section
          aria-label="Master letters and past requests"
          className="flex-1 overflow-hidden rounded-16 bg-card shadow-float"
        >
          {templates.isError ? (
            <LoadFailed
              what="the master letters"
              error={templates.error}
              onRetry={() => void templates.refetch()}
            />
          ) : loading ? (
            <Loading what="Loading letters" />
          ) : nothing ? (
            settled.trim() ? (
              <Empty title="Nothing matches">
                No master letter or past request fits “{settled.trim()}”.
              </Empty>
            ) : (
              <Empty title="No letters yet">
                Write a master letter under Templates, or write this one from scratch.
              </Empty>
            )
          ) : (
            <div role="table" className="leading-[normal]">
              <div
                role="row"
                style={{ gridTemplateColumns: COLUMNS }}
                className="grid gap-x-4 border-b border-hairline px-9 py-[9px] text-105 font-semibold tracking-label text-muted uppercase"
              >
                <span role="columnheader">Letter</span>
                <span role="columnheader">Contents</span>
                <span role="columnheader">Filed with</span>
                <span role="columnheader">Last used</span>
                <span role="columnheader">
                  <span className="sr-only">Preview and use</span>
                </span>
              </div>
              {masters.map((template) => (
                <MasterRow
                  key={template.id}
                  template={template}
                  timeZone={me.timezone}
                  busy={busy === template.id}
                  onUse={() => void start(template.id, { from_template_id: template.id })}
                />
              ))}
              {pastRows.map((row) => (
                <PastRow
                  key={row.id}
                  row={row}
                  status={statusLabel(row.current_status).toLowerCase()}
                  timeZone={me.timezone}
                  busy={busy === row.id}
                  onPreview={() =>
                    void previewPast(row.id).then((problem) => problem && setError(problem))
                  }
                  onUse={() => void start(row.id, { from_request_id: row.id })}
                />
              ))}
            </div>
          )}
          {past.isError ? (
            <p className="border-t border-hairline px-9 py-2 text-12 text-danger" role="alert">
              Past requests could not be searched.{' '}
              <button className="underline" onClick={() => void past.refetch()}>
                Try again
              </button>
            </p>
          ) : null}
        </section>

        <div className="flex items-center gap-2.5 text-12 leading-[normal] text-muted">
          {started.length > 0 ? (
            <Menu
              label="Letters already started"
              align="left"
              width={300}
              panelClassName="max-h-72 overflow-y-auto"
              items={started.map((draft) => ({
                label: draft.subject || 'Untitled letter',
                hint: formatShortInstant(draft.created_at, me.timezone),
                onPick: () => void navigate(`/drafts/${draft.id}`),
              }))}
            >
              <span
                className="inline-flex items-center gap-1.5 text-12 text-muted hover:text-ink-2"
                title={`Newest started ${formatInstant(started[0].created_at, me.timezone)}`}
              >
                {started.length === 1 ? '1 letter' : `${started.length} letters`} already started
                <ChevronDown size={9} width={2.6} stroke="var(--color-muted)" />
              </span>
            </Menu>
          ) : null}
          <span className="ml-auto text-ink-2">
            Not sure which fits?{' '}
            <button
              type="button"
              aria-expanded={asking}
              onClick={() => setAsking((was) => !was)}
              className="font-medium text-brand hover:text-brand-dark"
            >
              Ask the assistant
            </button>
          </span>
        </div>
      </div>
      {asking ? (
        <AskAssistant
          busy={busy}
          onUse={(key, body) => void start(key, body)}
          onClose={() => setAsking(false)}
        />
      ) : null}
    </>
  )
}

const ROW =
  'grid items-center gap-x-4 border-t border-transparent px-9 py-[13px] hover:bg-row-alt [&:nth-child(2)]:border-t-0'

/** Preview (the eye) and Use, at the row's right. */
function RowActions({
  preview,
  onPreview,
  busy,
  onUse,
  what,
}: {
  preview?: string
  onPreview?: () => void
  busy: boolean
  onUse: () => void
  what: string
}) {
  const eye = <EyeIcon size={16} stroke="var(--color-muted-2)" className="block" />
  return (
    <span className="flex items-center gap-1.5 justify-self-end">
      {preview ? (
        <a
          href={preview}
          target="_blank"
          rel="noreferrer"
          title={`Preview ${what}`}
          aria-label={`Preview ${what}`}
          className="inline-flex w-[22px] items-center justify-center"
        >
          {eye}
        </a>
      ) : onPreview ? (
        <button
          type="button"
          title={`Preview ${what}`}
          aria-label={`Preview ${what}`}
          onClick={onPreview}
          className="inline-flex w-[22px] items-center justify-center"
        >
          {eye}
        </button>
      ) : null}
      <UseButton busy={busy} onClick={onUse} aria-label={`Use ${what}`} />
    </span>
  )
}

function MasterRow({
  template,
  timeZone,
  busy,
  onUse,
}: {
  template: Template
  timeZone: string
  busy: boolean
  onUse: () => void
}) {
  return (
    <div role="row" style={{ gridTemplateColumns: COLUMNS }} className={ROW}>
      <span role="cell" className="min-w-0 text-13 font-medium text-ink">
        {template.name}
      </span>
      <span role="cell" className="text-12 text-muted">
        {[itemsLabel(template.item_count), template.legal_basis].filter(Boolean).join(' · ')}
      </span>
      <span role="cell" className="text-12 text-ink-2">
        {requestsLabel(template.filed_count)}
      </span>
      <span role="cell" className="text-12 text-muted">
        {template.last_used_at ? formatShortInstant(template.last_used_at, timeZone) : '—'}
      </span>
      <RowActions
        // A template kept in the drafting tool has its words there, not here (P21c).
        preview={
          template.fork_template_id ? undefined : `/api/drafting/templates/${template.id}/pdf`
        }
        busy={busy}
        onUse={onUse}
        what={template.name}
      />
    </div>
  )
}

function PastRow({
  row,
  status,
  timeZone,
  busy,
  onPreview,
  onUse,
}: {
  row: Row
  status: string
  timeZone: string
  busy: boolean
  onPreview: () => void
  onUse: () => void
}) {
  const when = row.filed_at ?? row.current_status_changed_at
  return (
    <div role="row" style={{ gridTemplateColumns: COLUMNS }} className={ROW}>
      <span role="cell" className="min-w-0 text-13 leading-[1.35] font-medium text-ink">
        {row.subject}
        <br />
        <span className="text-11 font-normal text-muted">
          past request · {row.tracking_number} · {status}
        </span>
      </span>
      <span role="cell" className="text-12 text-muted">
        {itemsLabel(row.item_count)}
      </span>
      <span role="cell" className="min-w-0 truncate text-12 text-ink-2">
        {row.agency_name}
      </span>
      <span role="cell" className="text-12 text-muted">
        {when ? formatShortInstant(when, timeZone) : '—'}
      </span>
      <RowActions onPreview={onPreview} busy={busy} onUse={onUse} what={row.subject} />
    </div>
  )
}
