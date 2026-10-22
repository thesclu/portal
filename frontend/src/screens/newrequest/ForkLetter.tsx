/**
 * Stage 2 when the letter is written in the drafting tool (P21c; DECISIONS 30e, 49): the portal
 * draws the letter header row (title, revision, saved state, "Preview as ▾", PDF, the suggestions
 * waiting, "Choose agencies →") and frames the fork's `/embed/letters/<master>` below it, which
 * draws the page and the rail. They talk `sclu-letter/1` (apps/mike SCLU.md 10.3, 11.3, 13.6 c):
 * on `ready` the portal sends the wave (the master and every agency letter made so far) and each
 * agency's details; Preview as sends `active` for an agency whose letter exists and `preview`
 * (its merge values, from the portal) for one whose letter is made only at reserve; the frame's
 * `active`, `document` and `problem` are followed here. Every message is origin-checked both ways.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { Callout } from '../../components/ui'
import {
  LETTER_PROTOCOL,
  acceptMessage,
  letterFrameUrl,
  parseLetterMessage,
  post,
  signInAgainUrl,
  type AgencyDetails,
  type SaveState,
  type ToLetterFrame,
} from '../../lib/fork'
import { metaLine } from './editor/letter'
import { PreviewAs } from './editor/PreviewAs'
import { filesBy, mailingLines, sendsTo } from './stages/stages'

type Draft = Schemas['DraftDetailOut']
type Agency = Schemas['AgencyOut']
type Recipient = Schemas['RecipientOut']

/** Whether letters are written in the drafting tool now, and where its pages are. */
export function useFork() {
  return $api.useQuery('get', '/api/drafting/fork', {}, { staleTime: 60_000 })
}

/** The Agency tab's details: the portal's, kept by the frame in memory only. */
export function agencyDetails(agency: Agency | undefined, channel: string): AgencyDetails | null {
  if (!agency) return null
  const letter = mailingLines(agency)
  return {
    name: agency.name,
    ...(agency.jurisdiction ? { jurisdiction: agency.jurisdiction } : {}),
    files_by: filesBy(channel, agency),
    filing_address: sendsTo(channel, agency),
    ...(letter.length > 0 ? { letter_address: letter } : {}),
    ...(agency.short_name ? { abbreviation: agency.short_name } : {}),
  }
}

const SAVED: Record<SaveState, string> = {
  saved: 'saved',
  saving: 'saving…',
  unsaved: 'not saved yet',
  error: 'could not save',
}

export function ForkLetter({
  draft,
  agencies,
  web,
  origin,
  onNext,
}: {
  draft: Draft
  agencies: Agency[]
  web: string
  origin: string
  onNext: () => void
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const masterId = draft.fork_master_document_id ?? ''
  const [shown, setShown] = useState('') // '' the master; else a recipient's id
  const [revision, setRevision] = useState<{ number: number | null; save: SaveState } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const agencyOf = useCallback((id: string) => agencies.find((a) => a.id === id), [agencies])
  const made = useMemo(
    () => draft.recipients.filter((r) => r.fork_document_id && r.status !== 'skipped'),
    [draft.recipients],
  )
  // The frame is loaded once per master: later letters arrive by `wave`, not by reloading it.
  const [src] = useState(() =>
    letterFrameUrl(
      web,
      masterId,
      made.map((r) => r.fork_document_id as string),
    ),
  )
  const label = useCallback(
    (r: Recipient) => {
      const agency = agencyOf(r.agency_id)
      return agency?.short_name || agency?.name || 'An agency'
    },
    [agencyOf],
  )

  const send = useCallback(
    (message: ToLetterFrame) => post(frame.current?.contentWindow, origin, message),
    [origin],
  )
  const sendWave = useCallback(() => {
    const subject = draft.subject || 'Letter'
    const active = made.find((r) => r.id === shown)?.fork_document_id ?? undefined
    send({
      protocol: LETTER_PROTOCOL,
      type: 'wave',
      master: { document_id: masterId, label: `${subject} · master` },
      agencies: made.map((r) => ({
        document_id: r.fork_document_id as string,
        label: label(r),
        key: r.id,
      })),
      ...(active ? { active } : {}),
    })
    for (const r of made)
      send({
        protocol: LETTER_PROTOCOL,
        type: 'agency',
        document_id: r.fork_document_id as string,
        details: agencyDetails(agencyOf(r.agency_id), r.channel),
      })
  }, [draft.subject, made, masterId, send, shown, agencyOf, label])

  // The latest wave, for the message handler (which is registered once).
  const latest = useRef({ sendWave, made })
  useEffect(() => {
    latest.current = { sendWave, made }
  })
  // When a reserve made more letters, the frame learns of them.
  const madeKey = made.map((r) => `${r.id}:${r.fork_document_id}`).join(',')
  const ready = useRef(false)
  useEffect(() => {
    if (ready.current) latest.current.sendWave()
  }, [madeKey])

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const message = parseLetterMessage(acceptMessage(event, origin, frame.current?.contentWindow))
      if (!message) return
      if (message.type === 'ready') {
        ready.current = true
        latest.current.sendWave()
      } else if (message.type === 'active') {
        const r = latest.current.made.find((x) => x.fork_document_id === message.document_id)
        setShown(r ? r.id : '')
      } else if (message.type === 'document') {
        setRevision({ number: message.version_number, save: message.save })
      } else if (message.type === 'problem') {
        if (message.code === 'signed_out') {
          window.location.assign(signInAgainUrl(web, window.location.href))
          return
        }
        setProblem(
          message.code === 'not_found'
            ? 'The drafting tool cannot find this letter, or you cannot open it.'
            : 'The drafting tool could not show this letter. Reload the page to try again.',
        )
      } else if (message.type === 'preview' && message.status === 'failed') {
        setProblem('The drafting tool could not show the letter for that agency.')
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [origin, web])

  async function previewAs(id: string) {
    setShown(id)
    setProblem(null)
    if (!id) {
      send({ protocol: LETTER_PROTOCOL, type: 'active', document_id: masterId })
      return
    }
    const recipient = draft.recipients.find((r) => r.id === id)
    if (!recipient) return
    if (recipient.fork_document_id) {
      send({ protocol: LETTER_PROTOCOL, type: 'active', document_id: recipient.fork_document_id })
      return
    }
    // Its letter is made at reserve; until then, the master filled in memory.
    const values = await api.GET('/api/drafting/drafts/{draft_id}/fork/values', {
      params: { path: { draft_id: draft.id }, query: { agency_id: recipient.agency_id } },
    })
    if (!values.data) {
      setProblem(messageOf(values.error))
      return
    }
    send({
      protocol: LETTER_PROTOCOL,
      type: 'preview',
      agency: {
        key: recipient.id,
        label: values.data.label,
        values: values.data.values,
        details: agencyDetails(agencyOf(recipient.agency_id), recipient.channel),
      },
    })
  }

  const choices = draft.recipients
    .filter((r) => r.status !== 'skipped')
    .map((r) => ({ id: r.id, name: label(r) }))
  const pdf =
    `/api/drafting/drafts/${draft.id}/fork/file` +
    (shown && made.some((r) => r.id === shown) ? `?recipient_id=${encodeURIComponent(shown)}` : '')
  const suggestions =
    (shown
      ? draft.recipients.find((r) => r.id === shown)?.fork_suggestions
      : draft.fork_suggestions) ?? 0
  const number = revision?.number ?? draft.fork_master_version_number

  return (
    <section
      aria-label="The letter"
      className="mx-2 mb-2 flex flex-1 flex-col overflow-hidden rounded-16 bg-card shadow-float"
    >
      <div className="flex items-center gap-3 border-b border-fill-muted px-5 py-[13px] leading-[normal]">
        <div className="min-w-0 flex-1">
          <p className="truncate text-14 font-semibold text-ink">
            {draft.subject.trim() || 'Untitled letter'}
          </p>
          <p className="text-115 text-muted">
            {metaLine([
              number != null && `rev ${number}`,
              revision ? SAVED[revision.save] : null,
            ]) || ' '}
          </p>
        </div>
        <PreviewAs choices={choices} value={shown} onChange={(id) => void previewAs(id)} />
        {suggestions > 0 ? (
          <button
            type="button"
            onClick={() => send({ protocol: LETTER_PROTOCOL, type: 'tab', tab: 'suggestions' })}
            className="shrink-0 text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark"
          >
            {suggestions === 1 ? '1 suggestion' : `${suggestions} suggestions`}
          </button>
        ) : null}
        <a
          href={pdf}
          target="_blank"
          rel="noreferrer"
          className="ml-auto shrink-0 rounded-8 px-3 py-1.5 text-12 font-medium whitespace-nowrap text-muted hover:text-ink-2"
        >
          PDF
        </a>
        <button
          type="button"
          onClick={onNext}
          className="shrink-0 rounded-pill bg-brand px-[15px] py-2 text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark"
        >
          Choose agencies →
        </button>
      </div>
      {problem ? (
        <div className="px-5 pt-3">
          <Callout tone="danger" role="alert">
            {problem}
          </Callout>
        </div>
      ) : null}
      <iframe
        ref={frame}
        src={src}
        title="The letter in the drafting tool"
        className="block w-full flex-1 border-0"
        style={{ minHeight: 640, height: 'calc(100vh - 210px)' }}
        allow="clipboard-write"
      />
    </section>
  )
}

/** A letter written in the drafting tool while the tool is switched off in Settings. */
export function ForkSwitchedOff() {
  return (
    <div className="px-2 pb-2">
      <Callout tone="amber" role="status">
        This letter is written in the drafting tool, which is switched off in Settings → System.
        Switch it back on to work on it.
      </Callout>
    </div>
  )
}
