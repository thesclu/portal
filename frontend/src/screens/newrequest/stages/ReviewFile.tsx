/**
 * Stage 4 of a new request (049–052): one agency at a time. At the left a 250 px card with "N of
 * M filed", the green bar and the agencies, each with its state (a tick when filed, the brand
 * ring on the one open, a dashed orange ring on one left pending); at the right one card of three
 * parts (the cover message, the letter as a page, and how to file it by its channel) and the two
 * ways out under it. Email agencies (050) get the cover message, To/From/Subject/Attached and
 * Send, which shows the exact message first (`ConfirmSend`, as every composer has since P20d);
 * portal and postal agencies (049, 051) get the packet (the portal link, a stored login, the
 * reply-to alias with its Copy, the PDF) and "I submitted it / I mailed it — mark filed" or
 * "Next — leave pending filing". Copy and download are icon-only with their labels as tooltips,
 * grey on the email screen and blue where the person files by hand (v6). The tracking number is
 * shown, never offered to copy (§7.1); of the identifiers only the alias has a Copy.
 * When everyone has been dealt with, 052's summary takes the right column.
 *
 * Reserving happens at the end of stage 3 (Agencies.tsx); an agency that could not be reserved
 * there (blanks, review) is reserved here, on its own button. Nothing here writes a word of the
 * letter or the cover message: the message starts empty and the letter is the server's.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'

import { api, type Schemas } from '../../../api/client'
import { $api, messageOf } from '../../../api/query'
import { needsApproval, useMe } from '../../../app/session'
import {
  instantOf,
  nowLocal,
  unwrap,
  type ActionSpec,
  type Values,
} from '../../../components/ActionDialog'
import {
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  LetterFullScreenIcon,
  MoreIcon,
  PencilIcon,
} from '../../../components/icons'
import { Menu } from '../../../components/Menu'
import { Loading } from '../../../components/States'
import { Callout, cx } from '../../../components/ui'
import { formatDayTime } from '../../../lib/dates'
import { ConfirmSend } from '../../inbox/Composer'
import { parseAddresses } from '../../inbox/mail'
import { FitPage, screenPage } from '../editor/Preview'
import { FlowPrimaryButton, FlowTextButton, IconAction, IconTool, SectionTitle } from '../parts'
import {
  attachmentLabel,
  filedShare,
  hostOf,
  howLabel,
  mailingLines,
  nextPending,
  orgNameOf,
  pagesLabel,
  resultOf,
  tally,
  waitingSentence,
  type Agency,
  type Recipient,
  type StepKey,
} from './stages'
import { Stepper } from './Stepper'

type Draft = Schemas['DraftDetailOut']
type Login = Schemas['AgencyLoginOut']

const DRAFT_KEY = ['get', '/api/drafting/drafts/{draft_id}']
const text = (values: Values, name: string) => String(values[name] ?? '').trim()
const PAGE_PX = 816 // 8.5in at 96 dpi: the page the server renders
/** The letter in the packet as 049 draws it: 430 px of text and 40 px sides, content-box. */
const MINI_PAGE = 510

export const pdfUrl = (draftId: string, recipientId: string) =>
  `/api/drafting/drafts/${draftId}/pdf?recipient_id=${encodeURIComponent(recipientId)}`
/** P21c: the agency's letter from the drafting tool, fetched as the person asking. */
export const forkPdfUrl = (draftId: string, recipientId: string) =>
  `/api/drafting/drafts/${draftId}/fork/file?recipient_id=${encodeURIComponent(recipientId)}`

/** Whether this agency's letter is the drafting tool's (made there at reserve). */
export function isForkLetter(
  draft: Pick<Draft, 'fork_master_document_id'> | undefined,
  recipient: Pick<Recipient, 'fork_document_id'> | undefined,
): boolean {
  return Boolean(draft?.fork_master_document_id && recipient?.fork_document_id)
}

/** The PDF a person downloads, prints or uploads: the drafting tool's for its letters. */
export function letterPdfUrl(
  draft: Pick<Draft, 'id' | 'fork_master_document_id'>,
  recipient: Pick<Recipient, 'id' | 'fork_document_id'>,
): string {
  return isForkLetter(draft, recipient)
    ? forkPdfUrl(draft.id, recipient.id)
    : pdfUrl(draft.id, recipient.id)
}

/** A reserved agency whose letter the drafting tool has not made (it was down, or refused, at
 *  reserve): made now, for every such agency of the letter. */
function MakeForkLetter({ draftId }: { draftId: string }) {
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function make() {
    setBusy(true)
    setError(null)
    const made = await api.POST('/api/drafting/drafts/{draft_id}/fork/merge', {
      params: { path: { draft_id: draftId } },
    })
    if (made.error) setError(messageOf(made.error))
    else if (made.data.failed.length > 0) setError(made.data.failed[0].reason)
    setBusy(false)
    await client.invalidateQueries({ queryKey: DRAFT_KEY })
  }
  return (
    <Callout tone="amber" role="status">
      <span className="flex items-center gap-3">
        <span className="min-w-0 flex-1">
          {error ?? 'The drafting tool has not made this agency’s letter yet.'}
        </span>
        <button
          type="button"
          disabled={busy}
          onClick={() => void make()}
          className="shrink-0 font-medium text-brand hover:text-brand-dark disabled:opacity-55"
        >
          {busy ? 'Making it…' : 'Make it now'}
        </button>
      </span>
    </Callout>
  )
}

/** A letter from the drafting tool shown as its PDF, in a frame of the portal's own origin. */
export function ForkLetterPdf({ url }: { url: string }) {
  return (
    <iframe
      title="The letter as this agency reads it"
      src={url}
      className="block w-full rounded-10 border-0 bg-card-white shadow-sm"
      style={{ height: 720 }}
    />
  )
}
const htmlUrl = (draftId: string, recipientId: string) =>
  `/api/drafting/drafts/${draftId}/html?recipient_id=${encodeURIComponent(recipientId)}`

/** How many pages this agency's PDF has, from the `X-Page-Count` header; null until known. */
export function usePageCount(draftId: string, recipientId: string, version: string): number | null {
  const key = `${draftId}/${recipientId}/${version}`
  const [found, setFound] = useState<{ key: string; pages: number } | null>(null)
  useEffect(() => {
    let on = true
    fetch(pdfUrl(draftId, recipientId), { credentials: 'include' })
      .then((response) => {
        void response.body?.cancel()
        const n = Number(response.headers.get('X-Page-Count'))
        if (on && response.ok && Number.isFinite(n) && n > 0) setFound({ key, pages: n })
      })
      .catch(() => {})
    return () => {
      on = false
    }
  }, [draftId, recipientId, key])
  return found?.key === key ? found.pages : null
}

function fileSpec(recipient: Recipient, name: string): ActionSpec {
  return {
    key: `file-${recipient.id}`,
    title: `Mark filed with ${name}`,
    intro: 'Say when it actually went out. The deadlines run from this.',
    submit: 'Mark filed',
    fields: [
      { name: 'at', label: 'Sent', type: 'datetime', required: true, initial: nowLocal() },
      { name: 'reference', label: 'Their reference number', hint: 'If they gave one.' },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/drafting/recipients/{recipient_id}/file', {
          params: { path: { recipient_id: recipient.id } },
          body: {
            filed_at: instantOf(text(values, 'at')),
            agency_reference_number: text(values, 'reference') || null,
          },
        }),
      )
    },
  }
}

function skipSpec(recipient: Recipient, name: string): ActionSpec {
  return {
    key: `skip-${recipient.id}`,
    title: `Don't send to ${name}`,
    intro: recipient.request_id
      ? `${recipient.tracking_number} is withdrawn. Its number is not reused.`
      : 'This agency is left out of the filing.',
    submit: "Don't send",
    danger: true,
    fields: [{ name: 'note', label: 'Why', required: Boolean(recipient.request_id) }],
    run: async (values) => {
      unwrap(
        await api.POST('/api/drafting/recipients/{recipient_id}/skip', {
          params: { path: { recipient_id: recipient.id } },
          body: { note: text(values, 'note') || null },
        }),
      )
    },
  }
}

/** The name an agency goes by in stage 4's lists (049 "UC San Diego PD"). */
export function listName(agency: Agency | undefined): string {
  return agency?.short_name || agency?.name || 'An agency'
}

export function ReviewFileStage({
  draft,
  agencies,
  failures,
  onFailure,
  setSpec,
  onGo,
}: {
  draft: Draft
  agencies: Agency[]
  /** Why a filing failed, by recipient: this session's refusals and the filing mail that
   *  failed (DraftPage reads both). */
  failures: Record<string, string>
  onFailure: (recipientId: string, reason: string | null) => void
  setSpec: (spec: ActionSpec | null) => void
  onGo: (key: StepKey) => void
}) {
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const settings = $api.useQuery('get', '/api/core/settings', {}, { staleTime: 60_000 })
  // Agencies left pending on purpose this session, so "Next" does not come straight back to them.
  const [deferred, setDeferred] = useState<ReadonlySet<string>>(() => new Set())
  const agencyOf = (id: string) => agencies.find((a) => a.id === id)
  const current = draft.recipients.find((r) => r.id === params.get('agency')) ?? null
  // A stale id in the address (a recipient since removed) counts as none chosen.
  const wanted = current?.id ?? null
  const select = useCallback(
    (id: string | null) =>
      setParams(id ? { stage: 'file', agency: id } : { stage: 'file' }, { replace: true }),
    [setParams],
  )

  useEffect(() => {
    if (wanted) return
    const next = nextPending(draft.recipients, deferred)
    if (next) select(next.id)
  }, [wanted, draft.recipients, deferred, select])

  if (draft.recipients.length === 0)
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
        <Stepper current="file" onGo={onGo} />
        <p className="text-125 text-muted">
          Add an agency in stage 3 first.{' '}
          <button className="font-medium text-brand" onClick={() => onGo('agencies')}>
            Choose agencies
          </button>
        </p>
      </div>
    )

  const counts = tally(draft.recipients, failures)
  const requester = [me.name, orgNameOf(settings.data)].filter(Boolean).join(', ')
  function leave(id: string) {
    const left = new Set(deferred).add(id)
    setDeferred(left)
    const next = nextPending(draft.recipients, left, id)
    select(next ? next.id : null)
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
      <Stepper current="file" onGo={onGo} />
      <div className="flex flex-1 items-start gap-5">
        <section
          aria-label="Agencies"
          className="w-[250px] shrink-0 self-start overflow-hidden rounded-16 bg-card leading-[normal] shadow-float"
        >
          <div className="border-b border-hairline px-3.5 pt-[13px] pb-3">
            <p className="text-125 font-semibold text-ink" aria-live="polite">
              {counts.filed} of {counts.total} filed
            </p>
            <div className="mt-[9px] h-1 overflow-hidden rounded-2xs bg-fill-skeleton">
              <div className="h-full bg-ok" style={{ width: `${filedShare(counts)}%` }} />
            </div>
          </div>
          <ul>
            {draft.recipients.map((recipient, index) => (
              <AgencyRow
                key={recipient.id}
                first={index === 0}
                recipient={recipient}
                agency={agencyOf(recipient.agency_id)}
                here={current?.id === recipient.id}
                failed={Boolean(failures[recipient.id]) && recipient.status !== 'filed'}
                left={deferred.has(recipient.id)}
                onSelect={() => select(recipient.id)}
              />
            ))}
          </ul>
        </section>

        <div className="flex min-w-0 flex-1 flex-col gap-3">
          {current ? (
            <OneAgency
              key={current.id}
              draft={draft}
              recipient={current}
              agency={agencyOf(current.agency_id)}
              requester={requester}
              failure={failures[current.id] ?? null}
              setSpec={setSpec}
              onNext={() => leave(current.id)}
              onFailure={(reason) => onFailure(current.id, reason)}
            />
          ) : (
            <Finished draft={draft} agencies={agencies} failures={failures} onSelect={select} />
          )}
        </div>
      </div>
    </div>
  )
}

/** One agency in the list (049): its state, its name, how it files. */
function AgencyRow({
  first,
  recipient,
  agency,
  here,
  failed,
  left,
  onSelect,
}: {
  first: boolean
  recipient: Recipient
  agency: Agency | undefined
  here: boolean
  failed: boolean
  left: boolean
  onSelect: () => void
}) {
  const filed = recipient.status === 'filed'
  const closed = filed || recipient.status === 'skipped'
  const label =
    recipient.status === 'skipped'
      ? 'Not sent'
      : recipient.status === 'held'
        ? 'Held'
        : failed
          ? 'Failed'
          : left && !filed
            ? 'Pending'
            : howLabel(recipient.channel, agency)
  return (
    <li>
      <button
        type="button"
        aria-current={here ? 'true' : undefined}
        onClick={onSelect}
        className={cx(
          'flex w-full items-center gap-[11px] px-3.5 py-[11px] text-left',
          !first && 'border-t border-line-row',
          here ? 'bg-brand-tint' : 'hover:bg-surface-row',
        )}
      >
        {filed ? (
          <span className="inline-flex size-4 shrink-0 items-center justify-center rounded-pill bg-ok-tint">
            <CheckIcon size={9} width={3.4} stroke="var(--color-ok)" />
          </span>
        ) : here ? (
          <span className="box-border inline-flex size-4 shrink-0 items-center justify-center rounded-pill border-2 border-brand">
            <span className="size-1.5 rounded-pill bg-brand" />
          </span>
        ) : failed ? (
          <span className="box-border size-4 shrink-0 rounded-pill border-[1.5px] border-dashed border-danger" />
        ) : left ? (
          <span className="box-border size-4 shrink-0 rounded-pill border-[1.5px] border-dashed border-attention" />
        ) : (
          <span className="box-border size-4 shrink-0 rounded-pill border-[1.5px] border-line-control" />
        )}
        <span
          className={cx(
            'min-w-0 flex-1 truncate text-125',
            here ? 'font-semibold text-ink' : 'font-normal',
            !here && (closed ? 'text-muted' : 'text-ink'),
            recipient.status === 'skipped' && 'line-through',
          )}
        >
          {listName(agency)}
        </span>
        <span
          className={cx(
            'text-115 whitespace-nowrap',
            failed ? 'text-danger' : left && !filed ? 'text-warn' : 'text-muted-2',
          )}
        >
          {label}
        </span>
      </button>
    </li>
  )
}

/** The letter as the server lays it out for this agency, at 430 px on the page grey (049): the
 *  HTML is fetched, then shown in a frame sized to its content and scaled. The frame is never
 *  rendered without its document, so nothing ever points at the site root. */
export function LetterFrame({
  draftId,
  recipientId,
  forkPdf,
}: {
  draftId: string
  recipientId: string
  /** P21c: the drafting tool's PDF of this letter, shown instead of the portal's page. */
  forkPdf?: string
}) {
  if (forkPdf) return <ForkLetterPdf url={forkPdf} />
  return <PortalLetterFrame draftId={draftId} recipientId={recipientId} />
}

function PortalLetterFrame({ draftId, recipientId }: { draftId: string; recipientId: string }) {
  const [page, setPage] = useState<{ key: string; html: string | null; error: string | null }>({
    key: '',
    html: null,
    error: null,
  })
  const [height, setHeight] = useState(PAGE_PX * (11 / 8.5))
  const key = `${draftId}/${recipientId}`
  useEffect(() => {
    let on = true
    fetch(htmlUrl(draftId, recipientId), { credentials: 'include' })
      .then(async (response) => {
        if (!response.ok) {
          let detail: string | undefined
          try {
            detail = (await response.json()).detail
          } catch {
            detail = undefined
          }
          throw { status: response.status, detail }
        }
        return response.text()
      })
      .then((html) => {
        if (on) setPage({ key, html, error: null })
      })
      .catch((failure: unknown) => {
        if (on) setPage({ key, html: null, error: messageOf(failure) })
      })
    return () => {
      on = false
    }
  }, [draftId, recipientId, key])
  if (page.key !== key || page.html === null)
    return page.error && page.key === key ? (
      <Callout tone="danger">{page.error}</Callout>
    ) : (
      <Loading what="Rendering the letter" />
    )
  return (
    <FitPage width={MINI_PAGE} center className="w-full">
      <iframe
        title="The letter as this agency reads it"
        sandbox="allow-same-origin"
        srcDoc={screenPage(page.html)}
        style={{ height }}
        onLoad={(event) => {
          const doc = event.currentTarget.contentDocument
          if (doc) setHeight(doc.documentElement.offsetHeight + 2)
        }}
        className="block w-full border-0 bg-card-white shadow-sm"
      />
    </FitPage>
  )
}

/** One line of the packet (049): a 132 px muted label, then the value. */
function PacketLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <span className="inline-block w-[132px] align-top text-muted">{label}</span>
      {children}
    </div>
  )
}

/** One row of "Sending" and "Mail it" (050, 051): an 84 px muted label, 14 px apart. */
export function InfoLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3.5 text-125 leading-[1.5]">
      <span className="w-[84px] shrink-0 text-muted">{label}</span>
      <span className="min-w-0 flex-1 text-ink">{children}</span>
    </div>
  )
}

/** A field that reads as the row's words until it is changed (050 To, Subject). */
const QUIET_FIELD =
  'w-full min-w-0 rounded-4 bg-transparent text-125 leading-[1.5] text-ink outline-none placeholder:text-muted-2 hover:bg-surface-row focus:bg-surface-row focus:shadow-ring-focus'

/** The agency's stored portal login in the packet (ARCHITECTURE §4.5): the login, the password
 *  hidden until Reveal (twenty seconds; every reveal is in the activity log). No Copy: 049 draws
 *  none here, and a password is not put on the clipboard unasked. Nothing is drawn when no login
 *  is stored or none can be read. */
function LoginLine({ agencyId }: { agencyId: string }) {
  const stored = $api.useQuery('get', '/api/records/agencies/{agency_id}/login', {
    params: { path: { agency_id: agencyId } },
  })
  const [shown, setShown] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (shown === null) return
    const id = window.setTimeout(() => setShown(null), 20_000)
    return () => window.clearTimeout(id)
  }, [shown])
  const login = stored.data as Login | undefined
  if (!login) return null
  async function reveal() {
    setError(null)
    try {
      const { password } = unwrap(
        await api.POST('/api/records/agencies/{agency_id}/login/reveal', {
          params: { path: { agency_id: agencyId } },
        }),
      )
      setShown(password)
    } catch (caught) {
      setError(messageOf(caught, caught instanceof Error ? caught.message : undefined))
    }
  }
  return (
    <PacketLine label="Login">
      {login.login}
      {login.has_password ? (
        <>
          {' · '}
          {shown !== null ? (
            <span className="break-all">{shown}</span>
          ) : (
            <span className="tracking-[.15em]">••••••</span>
          )}{' '}
          <button
            type="button"
            className="font-medium text-brand hover:text-brand-dark"
            onClick={() => (shown !== null ? setShown(null) : void reveal())}
          >
            {shown !== null ? 'Hide' : 'Reveal'}
          </button>
        </>
      ) : null}
      {error ? <span className="block text-danger">{error}</span> : null}
    </PacketLine>
  )
}

function OneAgency({
  draft,
  recipient,
  agency,
  requester,
  failure,
  setSpec,
  onNext,
  onFailure,
}: {
  draft: Draft
  recipient: Recipient
  agency: Agency | undefined
  requester: string
  failure: string | null
  setSpec: (spec: ActionSpec | null) => void
  onNext: () => void
  onFailure: (reason: string | null) => void
}) {
  const client = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [tracking, setTracking] = useState('')
  const messageRef = useRef<HTMLTextAreaElement>(null)
  const name = agency?.name ?? 'this agency'
  const reserved = Boolean(recipient.request_id)
  const open = recipient.status === 'pending'
  const preview = $api.useQuery('get', '/api/drafting/recipients/{recipient_id}/preview', {
    params: { path: { recipient_id: recipient.id } },
  })
  const request = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}',
    { params: { path: { request_id: recipient.request_id ?? '' } } },
    { enabled: reserved },
  )
  const alias = request.data?.inbound_alias_email ?? null
  const pages = usePageCount(draft.id, recipient.id, recipient.request_id ?? 'draft')
  const unresolved = preview.data?.unresolved ?? []
  const fromFork = isForkLetter(draft, recipient)
  const pdf = letterPdfUrl(draft, recipient)
  const attachment = attachmentLabel(recipient.letter_filename, pages)
  const email = useEmailFiling({
    recipient,
    agency,
    subject: draft.subject,
    message,
    onOutcome: onFailure,
    enabled: open && reserved && recipient.channel === 'email',
  })
  const [confirming, setConfirming] = useState(false)
  // P20h (232): an intern submits the letter for approval instead of sending or marking it filed.
  const me = useMe()
  const intern = needsApproval(me)
  const approval = useLetterApproval(recipient, intern && reserved)
  // An intern's cover message kept from an earlier submission (withdrawn or sent back) comes back
  // into the box once, so it is edited rather than written again.
  const prefilled = useRef(false)
  const kept = email.draftMessage
  useEffect(() => {
    if (prefilled.current || !kept) return
    prefilled.current = true
    setMessage((was) => was || kept.body_text)
    if (kept.subject) email.setSubject(kept.subject)
  }, [kept, email])

  async function reserve() {
    setBusy('reserve')
    setError(null)
    const result = await api.POST('/api/drafting/recipients/{recipient_id}/reserve', {
      params: { path: { recipient_id: recipient.id } },
      body: {},
    })
    if (result.error) setError(messageOf(result.error))
    setBusy(null)
    await client.invalidateQueries({ queryKey: DRAFT_KEY })
  }
  /** "I submitted it" / "I mailed it": filed now, by this channel, with a carrier's number if any. */
  async function markFiled() {
    setBusy('file')
    setError(null)
    const result = await api.POST('/api/drafting/recipients/{recipient_id}/file', {
      params: { path: { recipient_id: recipient.id } },
      body: { channel: recipient.channel, tracking_number: tracking.trim() || null },
    })
    setBusy(null)
    if (result.error) {
      const why = messageOf(result.error)
      setError(why)
      onFailure(why)
      return
    }
    onFailure(null)
    await client.invalidateQueries({ queryKey: DRAFT_KEY })
  }

  if (!open) {
    return (
      <Callout tone="grey" role="status">
        <span className="flex flex-wrap items-center gap-3 text-125">
          <span className="flex-1 text-ink">
            {recipient.status === 'filed' ? (
              <>
                Filed with {name}
                {recipient.request_id ? (
                  <>
                    {' · '}
                    <Link
                      className="text-ink-2 underline decoration-line-control underline-offset-[3px] hover:text-ink"
                      to={`/requests/${recipient.request_id}`}
                    >
                      {recipient.tracking_number}
                    </Link>
                  </>
                ) : null}
              </>
            ) : recipient.status === 'held' ? (
              <>Held: {recipient.hold_reason}. Release it in stage 3 to file it.</>
            ) : (
              <>Not sent to {name}.</>
            )}
          </span>
          <FlowTextButton onClick={onNext}>Next →</FlowTextButton>
        </span>
      </Callout>
    )
  }

  const channel = recipient.channel
  // v6: copy and download are grey where the letter goes by email, blue where the person files by
  // hand (049–051).
  const tone = channel === 'email' ? 'muted' : 'brand'
  const sendingNow = channel === 'email' && email.existing && !email.failedWhy
  const more = (
    <Menu
      label="More for this agency"
      align="left"
      width={250}
      items={[
        ...(channel === 'email' && reserved && !sendingNow && !intern
          ? [
              {
                label: 'Mark filed (sent some other way)…',
                onPick: () => setSpec(fileSpec(recipient, name)),
              },
            ]
          : []),
        {
          label: "Don't send to this agency…",
          danger: true,
          onPick: () => setSpec(skipSpec(recipient, name)),
        },
      ]}
    >
      <span className="inline-flex size-8 items-center justify-center rounded-9 hover:bg-hover">
        <MoreIcon size={16} stroke="var(--color-ink-3)" />
      </span>
    </Menu>
  )

  return (
    <>
      {error ? (
        <Callout tone="danger" role="alert">
          {error}
        </Callout>
      ) : null}
      {failure && !error ? (
        <Callout tone="danger" role="alert">
          Failed · {failure}
        </Callout>
      ) : null}

      <section
        aria-label={name}
        className="overflow-hidden rounded-16 bg-card leading-[normal] shadow-float"
      >
        {!reserved ? (
          <div className="flex flex-col gap-3 border-b border-hairline px-6 py-5">
            <SectionTitle>Not reserved yet</SectionTitle>
            {unresolved.length > 0 ? (
              <Callout tone="amber">
                Not filled in for this agency: {unresolved.join(', ')}. It cannot be reserved until
                they are.
              </Callout>
            ) : null}
            <p className="text-125 leading-[1.6] text-ink-2">
              Reserving gives this letter its tracking number and its own request address, and fixes
              its text. A reserved number is never reused, so reserve when you are ready to send.
            </p>
            <div>
              <FlowPrimaryButton
                busy={busy === 'reserve'}
                disabled={preview.isPending || unresolved.length > 0}
                onClick={() => void reserve()}
              >
                Reserve a tracking number
              </FlowPrimaryButton>
            </div>
          </div>
        ) : null}

        <div className="flex flex-col gap-3.5 px-6 py-5">
          <div className="flex items-center gap-2.5">
            <SectionTitle>Cover Message</SectionTitle>
            <span className="ml-auto flex items-center gap-1">
              <IconAction title="Edit this one" onClick={() => messageRef.current?.focus()}>
                <PencilIcon size={14} width={1.9} stroke="var(--color-ink-3)" />
              </IconAction>
              <IconTool
                label="Copy message"
                tone={tone}
                icon={<CopyIcon size={15} width={1.9} />}
                disabled={!message.trim()}
                copyText={() => message}
              />
            </span>
          </div>
          <textarea
            ref={messageRef}
            aria-label="Cover message"
            rows={3}
            value={message}
            disabled={Boolean(sendingNow)}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Your note to the agency; the letter is attached."
            className="box-border w-full resize-none rounded-8 bg-surface-soft px-4 py-3.5 text-125 leading-[1.75] text-ink outline-none field-sizing-content placeholder:text-muted-2 focus:shadow-ring-focus"
          />
        </div>

        <div className="flex flex-col gap-3 border-t border-hairline px-6 py-5">
          <div className="flex items-center gap-2.5">
            <SectionTitle>Letter</SectionTitle>
            {pagesLabel(pages) ? (
              <span className="text-12 text-muted">{pagesLabel(pages)}</span>
            ) : null}
            <span className="ml-auto flex items-center gap-1">
              <IconAction title="Full screen" onClick={() => window.open(pdf, '_blank')}>
                <LetterFullScreenIcon size={14} width={1.9} stroke="var(--color-ink-3)" />
              </IconAction>
              <IconTool
                label="Copy as text"
                tone={tone}
                icon={<CopyIcon size={15} width={1.9} />}
                disabled={!preview.data}
                copyText={() => preview.data?.text}
              />
              <IconTool
                label="Download PDF"
                tone={tone}
                icon={<DownloadIcon size={15} width={1.9} />}
                href={pdf}
                download={recipient.letter_filename || undefined}
              />
            </span>
          </div>
          {unresolved.length > 0 && reserved ? (
            <Callout tone="amber">Not filled in for this agency: {unresolved.join(', ')}.</Callout>
          ) : null}
          {draft.fork_master_document_id && reserved && !recipient.fork_document_id ? (
            <MakeForkLetter draftId={draft.id} />
          ) : null}
          <div className="flex justify-center rounded-10 bg-page p-5">
            {preview.isError ? (
              <Callout tone="danger">{messageOf(preview.error)}</Callout>
            ) : (
              <LetterFrame
                draftId={draft.id}
                recipientId={recipient.id}
                forkPdf={fromFork ? pdf : undefined}
              />
            )}
          </div>
        </div>

        {reserved && channel === 'portal' ? (
          <div className="flex flex-col gap-[13px] border-t border-hairline px-6 py-5">
            <SectionTitle>Filing instructions</SectionTitle>
            <div className="rounded-8 bg-surface-soft px-4 py-3.5 text-125 leading-[2] text-ink-2">
              <PacketLine label="Form">
                {agency?.portal_url ? (
                  <a
                    className="text-brand hover:text-brand-dark"
                    href={agency.portal_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {hostOf(agency.portal_url)}
                  </a>
                ) : (
                  <span className="text-muted">No portal address set for this agency</span>
                )}
              </PacketLine>
              {agency ? <LoginLine agencyId={agency.id} /> : null}
              <PacketLine label="Contact email">
                {alias ? (
                  <>
                    <span className="select-all">{alias}</span>{' '}
                    <IconTool
                      label="Copy"
                      tone="brand"
                      icon={<CopyIcon size={15} width={1.9} />}
                      copyText={() => alias}
                    />
                  </>
                ) : (
                  '…'
                )}
              </PacketLine>
              <PacketLine label="Requester">{requester}</PacketLine>
              <PacketLine label="Upload to the form">
                <a
                  className="text-ink-2 hover:text-ink"
                  href={pdf}
                  download={recipient.letter_filename || undefined}
                >
                  {recipient.letter_filename || attachment}
                </a>
              </PacketLine>
            </div>
          </div>
        ) : null}

        {reserved && channel === 'email' ? (
          <EmailSection email={email} alias={alias} attachment={attachment} intern={intern} />
        ) : null}

        {reserved && channel === 'mail' ? (
          <div className="flex flex-col gap-[13px] border-t border-hairline px-6 py-5">
            <span className="text-14 font-semibold text-ink">Mail it</span>
            <InfoLine label="Address">
              {mailingLines(agency).length > 0 ? (
                mailingLines(agency).map((line) => (
                  <span key={line} className="block">
                    {line}
                  </span>
                ))
              ) : (
                <span className="text-muted">No mailing address set for this agency</span>
              )}
            </InfoLine>
            <InfoLine label="Print">
              <IconTool
                label="Download PDF"
                tone="brand"
                icon={<DownloadIcon size={15} width={1.9} />}
                href={pdf}
                download={recipient.letter_filename || undefined}
              />
              {pagesLabel(pages) ? ` · ${pagesLabel(pages)}` : ''}
            </InfoLine>
            <label className="flex max-w-[360px] flex-col gap-[5px]">
              <span className="text-12 font-medium text-ink-2">
                Tracking number <span className="font-normal text-muted-2">optional</span>
              </span>
              <input
                value={tracking}
                onChange={(event) => setTracking(event.target.value)}
                className="box-border rounded-12 border-[1.5px] border-transparent bg-card-white px-3 py-[7.5px] text-125 text-ink shadow-field outline-none placeholder:text-muted-2 focus:rounded-10 focus:border-brand focus:shadow-none"
              />
            </label>
          </div>
        ) : null}
      </section>

      {intern && approval.waiting ? (
        <div
          role="status"
          className="flex items-center gap-3 rounded-12 bg-brand-tint px-4 py-[11px] text-13 leading-[normal] text-brand"
        >
          <span className="font-semibold">Waiting for approval</span>
          <span className="text-ink-2">
            Submitted {formatDayTime(approval.waiting.submitted_at, me.timezone)}
          </span>
          <button
            type="button"
            disabled={approval.busy}
            onClick={() => void approval.withdraw()}
            className="ml-auto font-medium text-ink-2 hover:text-ink disabled:opacity-55"
          >
            Withdraw
          </button>
        </div>
      ) : null}
      {intern && approval.error ? (
        <Callout tone="danger" role="alert">
          {approval.error}
        </Callout>
      ) : null}

      <div className="flex items-center justify-end gap-[11px]">
        <span className="mr-auto">{more}</span>
        {intern && reserved && !approval.released ? (
          approval.waiting ? (
            <FlowTextButton onClick={onNext}>Next →</FlowTextButton>
          ) : (
            <>
              <FlowTextButton onClick={onNext}>Skip for now</FlowTextButton>
              <button
                type="button"
                disabled={approval.busy || (channel === 'email' && !email.to.trim())}
                onClick={() =>
                  void approval.submit(
                    channel === 'email'
                      ? {
                          subject: email.subject.trim(),
                          body_text: message,
                          to: emailAddresses(email.to),
                          cc: emailAddresses(email.cc),
                          attach_letter: email.attach,
                        }
                      : {},
                  )
                }
                className="rounded-9 border-[1.5px] border-brand bg-brand px-[17px] py-2.5 text-13 leading-[normal] font-medium text-white hover:bg-brand-dark disabled:opacity-55"
              >
                Submit for approval
              </button>
            </>
          )
        ) : channel === 'email' && reserved ? (
          sendingNow ? (
            <FlowTextButton onClick={onNext}>Next →</FlowTextButton>
          ) : (
            <>
              <FlowTextButton onClick={onNext}>Skip for now</FlowTextButton>
              <button
                type="button"
                disabled={email.busy || !email.to.trim()}
                onClick={() => setConfirming(true)}
                className="rounded-9 border-[1.5px] border-brand bg-brand px-[17px] py-2.5 text-13 leading-[normal] font-medium text-white hover:bg-brand-dark disabled:opacity-55"
              >
                Send
              </button>
            </>
          )
        ) : (
          <>
            <FlowTextButton onClick={onNext}>Next — leave pending filing</FlowTextButton>
            {reserved ? (
              <FlowPrimaryButton busy={busy === 'file'} onClick={() => void markFiled()}>
                {channel === 'mail' ? 'I mailed it — mark filed' : 'I submitted it — mark filed'}
              </FlowPrimaryButton>
            ) : null}
          </>
        )}
      </div>

      {confirming ? (
        // "Send this message?" (150) over the whole window, centred, on the scrim.
        <div
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setConfirming(false)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setConfirming(false)
          }}
          className="fixed inset-0 z-50 flex items-center justify-center scrim"
        >
          <ConfirmSend
            from={alias ?? ''}
            to={parseAddresses(email.to).addresses}
            cc={parseAddresses(email.cc).addresses}
            bcc={[]}
            files={email.attach ? [attachment] : []}
            body={message}
            title="Send this message?"
            busy={email.busy}
            onKeep={() => setConfirming(false)}
            onSend={() =>
              void email.send().then((sent) => {
                if (sent) setConfirming(false)
              })
            }
            testMode={email.testMode}
          />
        </div>
      ) : null}
    </>
  )
}

/** "a@b.org, c@d.org" as the API's address rows. */
function emailAddresses(value: string) {
  return value
    .split(/[,;\n]/)
    .map((a) => a.trim())
    .filter(Boolean)
    .map((address) => ({ address }))
}

/**
 * An intern's letter and its approval (P20h, 232–233): the newest approval on its request, whether
 * it waits, whether an approver released a letter filed by hand (approved, nothing changed since,
 * so the intern's own "mark filed" is cleared), and the two doors, submit and withdraw.
 */
function useLetterApproval(recipient: Recipient, enabled: boolean) {
  const client = useQueryClient()
  const reviews = $api.useQuery(
    'get',
    '/api/core/reviews',
    { params: { query: { request_id: recipient.request_id ?? '' } } },
    { enabled: enabled && Boolean(recipient.request_id) },
  )
  const clearance = $api.useQuery(
    'get',
    '/api/core/reviews/clearance',
    { params: { query: { subject_type: 'recipient', subject_id: recipient.id } } },
    { enabled: enabled && recipient.channel !== 'email' },
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const newest = (reviews.data ?? [])[0]
  const waiting = newest && !newest.decision ? newest : null
  const released = Boolean(clearance.data?.required && clearance.data.cleared)
  async function refresh() {
    await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews'] })
    await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/clearance'] })
    await client.invalidateQueries({ queryKey: DRAFT_KEY })
    await client.invalidateQueries({ queryKey: ['get', '/api/mail/messages'] })
  }
  async function submit(cover: {
    subject?: string
    body_text?: string
    to?: Array<{ address: string }>
    cc?: Array<{ address: string }>
    attach_letter?: boolean
  }) {
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/mail/recipients/{recipient_id}/submit', {
      params: { path: { recipient_id: recipient.id } },
      body: {
        subject: cover.subject ?? '',
        body_text: cover.body_text ?? '',
        to: cover.to ?? [],
        cc: cover.cc ?? [],
        attach_letter: cover.attach_letter ?? true,
        note: '',
      },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    await refresh()
  }
  async function withdraw() {
    if (!waiting) return
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/core/reviews/{review_id}/withdraw', {
      params: { path: { review_id: waiting.id } },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    await refresh()
  }
  return { waiting, released, busy, error, submit, withdraw }
}

/** The filing email of one agency (P11's send-and-file door, kept as it was): who it goes to,
 *  the subject, whether the letter is attached; the message that went, and whether it failed. */
function useEmailFiling({
  recipient,
  agency,
  subject,
  message,
  onOutcome,
  enabled,
}: {
  recipient: Recipient
  agency: Agency | undefined
  subject: string
  message: string
  onOutcome: (failure: string | null) => void
  enabled: boolean
}) {
  const client = useQueryClient()
  const sending = $api.useQuery('get', '/api/mail/sending', {}, { enabled })
  const contacts = $api.useQuery(
    'get',
    '/api/records/agencies/{agency_id}/contacts',
    { params: { path: { agency_id: recipient.agency_id } } },
    { enabled: enabled && Boolean(agency) },
  )
  const filing = $api.useQuery(
    'get',
    '/api/mail/messages',
    { params: { query: { request_id: recipient.request_id ?? '', bucket: 'all', limit: 5 } } },
    { enabled: enabled && Boolean(recipient.request_id), refetchInterval: 5000 },
  )
  const [to, setTo] = useState(agency?.default_request_email ?? '')
  const [cc, setCc] = useState<string | null>(null)
  const [mailSubject, setMailSubject] = useState(subject)
  const [attach, setAttach] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const copied = (contacts.data ?? [])
    .filter((c) => c.copy_on_every_request && c.email && !c.bad_address_at)
    .map((c) => c.email as string)
    .join(', ')
  const ccValue = cc ?? copied
  // A filing message on its way or gone (an intern's cover message waiting for approval is a
  // draft, and is not "sending").
  const existing =
    (filing.data?.items ?? []).find((m) => m.kind === 'filing' && m.status !== 'draft') ?? null
  const draftMessage =
    (filing.data?.items ?? []).find((m) => m.kind === 'filing' && m.status === 'draft') ?? null
  const sent = existing?.status === 'sent'
  const failedWhy =
    existing?.status === 'failed' ? (existing.error ?? 'the provider refused it') : null
  // The worker files the request out of band; once the message settles, the list beside it (the
  // draft's recipients) is read again so it says Filed. A failure is told to the results.
  useEffect(() => {
    if (sent || failedWhy) void client.invalidateQueries({ queryKey: DRAFT_KEY })
  }, [sent, failedWhy, client])
  const outcome = useRef(onOutcome)
  useEffect(() => {
    outcome.current = onOutcome
  })
  useEffect(() => {
    if (sent) outcome.current(null)
    else if (failedWhy) outcome.current(failedWhy)
  }, [sent, failedWhy])

  const addresses = (value: string) =>
    value
      .split(/[,;\n]/)
      .map((a) => a.trim())
      .filter(Boolean)
      .map((address) => ({ address }))

  async function send(): Promise<boolean> {
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/mail/recipients/{recipient_id}/send-and-file', {
      params: { path: { recipient_id: recipient.id } },
      body: {
        subject: mailSubject.trim(),
        body_text: message,
        to: addresses(to),
        cc: addresses(ccValue),
        attach_letter: attach,
      },
    })
    setBusy(false)
    if (result.error) {
      const why = messageOf(result.error)
      setError(why)
      onOutcome(why)
      return false
    }
    await client.invalidateQueries({ queryKey: ['get', '/api/mail/messages'] })
    await client.invalidateQueries({ queryKey: DRAFT_KEY })
    return true
  }
  async function undo() {
    if (!existing) return
    await api.POST('/api/mail/messages/{message_id}/cancel', {
      params: { path: { message_id: existing.id } },
    })
    await client.invalidateQueries({ queryKey: ['get', '/api/mail/messages'] })
  }
  return {
    to,
    setTo,
    cc: ccValue,
    setCc,
    subject: mailSubject,
    setSubject: setMailSubject,
    attach,
    setAttach,
    busy,
    error,
    existing,
    draftMessage,
    sent,
    failedWhy,
    testMode: sending.data ? !sending.data.enabled : false,
    send,
    undo,
  }
}

/** "Sending" (050): To, From, Subject, Attached as the screen's rows; To and Subject change in
 *  place, a Cc row shows once there is one to show (the agency's copied contacts). */
function EmailSection({
  email,
  alias,
  attachment,
  intern = false,
}: {
  email: ReturnType<typeof useEmailFiling>
  alias: string | null
  attachment: string
  /** An intern sends nothing from here (it goes to an approver), so test mode is not theirs. */
  intern?: boolean
}) {
  const [showCc, setShowCc] = useState(false)
  if (email.existing && !email.failedWhy) {
    const state =
      email.existing.status === 'queued' || email.existing.status === 'sending'
        ? 'Sending…'
        : email.sent
          ? `Sent${email.existing.test_send ? ' (test mode: nothing was delivered)' : ''}`
          : email.existing.status
    return (
      <div className="flex flex-col gap-[13px] border-t border-hairline px-6 py-5">
        <span className="text-14 font-semibold text-ink">Sending</span>
        <p className="text-125 text-ink">
          {state}
          {email.existing.status === 'queued' ? (
            <>
              {' '}
              <button
                type="button"
                className="font-medium text-brand hover:text-brand-dark"
                onClick={() => void email.undo()}
              >
                Undo
              </button>
            </>
          ) : null}
        </p>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-[13px] border-t border-hairline px-6 py-5">
      <span className="text-14 font-semibold text-ink">Sending</span>
      {email.testMode && !intern ? (
        <Callout tone="amber">
          Sending is in test mode: the provider accepts the message and delivers nothing. The
          request is still marked filed, so use this only to try the flow.
        </Callout>
      ) : null}
      {email.failedWhy ? <Callout tone="danger">Could not send: {email.failedWhy}</Callout> : null}
      {email.error ? <Callout tone="danger">{email.error}</Callout> : null}
      <InfoLine label="To">
        <span className="flex items-center gap-2">
          <input
            aria-label="To"
            className={QUIET_FIELD}
            value={email.to}
            placeholder="Who it goes to"
            onChange={(event) => email.setTo(event.target.value)}
          />
          {!email.cc && !showCc ? (
            <button
              type="button"
              className="shrink-0 text-12 text-muted hover:text-ink-2"
              onClick={() => setShowCc(true)}
            >
              Cc
            </button>
          ) : null}
        </span>
      </InfoLine>
      {email.cc || showCc ? (
        <InfoLine label="Cc">
          <input
            aria-label="Cc"
            className={QUIET_FIELD}
            value={email.cc}
            autoFocus={showCc && !email.cc}
            onChange={(event) => email.setCc(event.target.value)}
          />
        </InfoLine>
      ) : null}
      <InfoLine label="From">
        <span className="select-all">{alias ?? '…'}</span>
      </InfoLine>
      <InfoLine label="Subject">
        <input
          aria-label="Subject"
          className={QUIET_FIELD}
          value={email.subject}
          onChange={(event) => email.setSubject(event.target.value)}
        />
      </InfoLine>
      <InfoLine label="Attached">
        <label className="inline-flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            className="sr-only"
            checked={email.attach}
            onChange={(event) => email.setAttach(event.target.checked)}
          />
          <span className={cx(!email.attach && 'text-muted line-through')}>{attachment}</span>
          <span className="text-12 text-muted hover:text-ink-2">
            {email.attach ? 'Leave out' : 'Attach'}
          </span>
        </label>
      </InfoLine>
    </div>
  )
}

/** Every agency dealt with (052): the tally and who is still waiting on the person, each agency
 *  with its tracking number and its result, and the two ways on. */
function Finished({
  draft,
  agencies,
  failures,
  onSelect,
}: {
  draft: Draft
  agencies: Agency[]
  failures: Record<string, string>
  onSelect: (id: string) => void
}) {
  const navigate = useNavigate()
  const agencyOf = (id: string) => agencies.find((a) => a.id === id)
  const rows = draft.recipients.map((recipient) => ({
    recipient,
    agency: agencyOf(recipient.agency_id),
    result: resultOf(recipient, agencyOf(recipient.agency_id), failures[recipient.id]),
  }))
  const counts = tally(draft.recipients, failures)
  const firstFailed = rows.find((row) => row.result.kind === 'failed')
  const waiting = waitingSentence(
    rows.filter((row) => row.result.kind === 'waiting').map((row) => listName(row.agency)),
  )
  return (
    <>
      <section className="overflow-hidden rounded-16 bg-card leading-[normal] shadow-float">
        <div className="px-6 pt-[22px] pb-4">
          <p className="font-display text-xl font-extrabold text-ink">
            {counts.filed} of {counts.total} filed
          </p>
          {waiting ? <p className="mt-[5px] text-13 text-ink-2">{waiting}</p> : null}
        </div>
        {rows.map(({ recipient, agency, result }, index) => (
          <div
            key={recipient.id}
            style={{ gridTemplateColumns: '1.3fr 1fr 1fr' }}
            className={cx(
              'grid items-center gap-x-3.5 border-t px-6 py-[11px] text-125',
              index === 0 ? 'border-hairline' : 'border-line-row',
            )}
          >
            <span className="min-w-0 truncate text-ink">{listName(agency)}</span>
            <span className="min-w-0 truncate">
              {recipient.request_id ? (
                <Link
                  className="text-ink-2 underline decoration-line-control underline-offset-[3px] hover:text-ink"
                  to={`/requests/${recipient.request_id}`}
                >
                  {recipient.tracking_number}
                </Link>
              ) : (
                <span className="text-muted">—</span>
              )}
            </span>
            <span className="text-right">
              {result.kind === 'failed' ? (
                <button
                  type="button"
                  className="text-danger hover:underline"
                  onClick={() => onSelect(recipient.id)}
                >
                  {result.text}
                </button>
              ) : (
                <span
                  className={
                    result.kind === 'pending' ||
                    result.kind === 'waiting' ||
                    result.kind === 'unreserved'
                      ? 'text-warn'
                      : 'text-muted'
                  }
                >
                  {result.kind === 'waiting' || result.kind === 'pending'
                    ? 'Pending filing'
                    : result.text}
                </span>
              )}
            </span>
          </div>
        ))}
      </section>
      <div className="flex items-center justify-end gap-[11px]">
        {firstFailed ? (
          <FlowTextButton className="mr-auto" onClick={() => onSelect(firstFailed.recipient.id)}>
            Retry failed
          </FlowTextButton>
        ) : null}
        <FlowTextButton className="py-2.5" onClick={() => void navigate('/new')}>
          Start another request
        </FlowTextButton>
        <FlowPrimaryButton onClick={() => void navigate('/docket')}>Go to Docket</FlowPrimaryButton>
      </div>
    </>
  )
}
