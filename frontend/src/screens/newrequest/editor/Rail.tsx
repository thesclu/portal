/**
 * The rail beside the letter (045, P19d): Suggestions · Notes · Agency across the top, as drawn.
 * The design's first tab, Chat, is the drafting engine's conversation (P21d) and is not drawn
 * until it exists (design v2 rule 8). Suggestions holds the assistant's cards as 045 draws them
 * (the amber tint for a suggestion, the danger tint for a likely fight) and, under them, the
 * checks P19d runs on every save as grey cards: the house-style ones together in one "House
 * style" card (045), each line with its Fix, and each other check (blanks, an empty part, a clock
 * no rule covers, a footnote nothing points at) as its own card. As v6 draws them, a card of the
 * assistant's dismisses with the × in its corner and has Apply, solid blue, where it proposes
 * words; "Discuss →" opens Chat and is not drawn. The × v6 also draws on the house-style card is
 * left out: a check is recomputed on every save and has nothing to dismiss it.
 * "Suggest improvements" and "Ask about this letter…" (P19d) stay at the foot while
 * the assistant is on; the line under the cards says the cards are the assistant's. Apply is the
 * person's act, and nothing the assistant writes reaches the letter until they press it.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import { api, type Schemas } from '../../../api/client'
import { $api, messageOf } from '../../../api/query'
import { useMe } from '../../../app/session'
import { unwrap } from '../../../components/ActionDialog'
import { LoadFailed, Loading } from '../../../components/States'
import { CloseIcon } from '../../../components/icons'
import { cx } from '../../../components/ui'
import {
  answerProblem,
  assistantUnavailable,
  cardHeading,
  highlightOf,
  kindLabel,
  pieceLabel,
  type AiAnswer,
  type AiCheck,
  type AiNote,
  type AiSuggestion,
} from '../../../lib/assistant'
import { formatAgo, formatInstant } from '../../../lib/dates'
import { AskBox } from '../parts'
import { filesBy, sendsTo } from '../stages/stages'
import type { Fix } from './fixes'
import type { LocalPiece } from './letter'

type Agency = Schemas['AgencyOut']
type Recipient = Schemas['RecipientOut']

export const RAIL_PATH = '/api/core/ai/drafts/{draft_id}/rail'
export const RAIL_KEY = ['get', RAIL_PATH]

/** The rail's data, shared by the rail and the page (which paints the cards' highlights). */
export function useRail(draftId: string) {
  return $api.useQuery('get', RAIL_PATH, { params: { path: { draft_id: draftId } } })
}

const TABS = [
  { key: 'suggestions', label: 'Suggestions' },
  { key: 'notes', label: 'Notes' },
  { key: 'agency', label: 'Agency' },
] as const
type TabKey = (typeof TABS)[number]['key']

interface Exchange {
  id: number
  question: string
  answer: AiAnswer | null
  error: string | null
}

/** The checks as the rail draws them: the house-style ones in one card, the others alone. */
export function groupChecks(checks: AiCheck[]): {
  style: Array<{ check: AiCheck; index: number }>
  others: Array<{ check: AiCheck; index: number }>
} {
  const style: Array<{ check: AiCheck; index: number }> = []
  const others: Array<{ check: AiCheck; index: number }> = []
  checks.forEach((check, index) =>
    (check.kind === 'house_style' ? style : others).push({ check, index }),
  )
  return { style, others }
}

const PILL =
  'inline-flex h-7 items-center rounded-pill text-115 leading-[normal] disabled:opacity-55'
const CARD = 'rounded-12 px-[15px] py-[13px] text-12 leading-[1.55]'

export function Rail({
  draftId,
  rail,
  pieces,
  recipients,
  agencies,
  frozen,
  selectedCard,
  onSelectCard,
  onFocusPiece,
  onReplaceWording,
  onFix,
}: {
  draftId: string
  rail: ReturnType<typeof useRail>
  pieces: LocalPiece[]
  recipients: Recipient[]
  agencies: Agency[]
  frozen: boolean
  /** The card whose piece is marked as selected on the page. */
  selectedCard: string | null
  onSelectCard: (id: string | null) => void
  onFocusPiece: (pieceId: string) => void
  /** Puts the assistant's wording into a piece through the editor; false when the piece is not
   *  on the page any more. */
  onReplaceWording: (pieceId: string, text: string) => boolean
  /** Runs a check's fix in its piece's editor and saves; false when nothing changed (the piece
   *  is gone, or the words were already right). */
  onFix: (pieceId: string, fix: Fix) => Promise<boolean>
}) {
  const me = useMe()
  const client = useQueryClient()
  const refresh = () => client.invalidateQueries({ queryKey: RAIL_KEY })
  const [tab, setTab] = useState<TabKey>('suggestions')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [thread, setThread] = useState<Exchange[]>([])
  const [question, setQuestion] = useState('')
  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({})

  const data = rail.data
  const open = (data?.suggestions ?? []).filter((card) => card.status === 'open')
  const decided = data?.decided ?? []
  const checks = groupChecks(data?.checks ?? [])
  const state = data?.assistant ?? 'on'
  const unavailable = assistantUnavailable(state)

  // A card chosen from the page: show its tab (decided while rendering, from the change) and
  // bring the card into view once it is drawn.
  const [shownCard, setShownCard] = useState(selectedCard)
  if (selectedCard !== shownCard) {
    setShownCard(selectedCard)
    if (selectedCard) setTab('suggestions')
  }
  useEffect(() => {
    if (selectedCard) cardRefs.current[selectedCard]?.scrollIntoView({ block: 'nearest' })
  }, [selectedCard])

  const run = async (key: string, work: () => Promise<void>) => {
    setBusy(key)
    setProblem(null)
    try {
      await work()
    } catch (caught) {
      setProblem(caught instanceof Error ? caught.message : messageOf(caught))
    } finally {
      setBusy(null)
    }
  }

  const apply = (card: AiSuggestion) =>
    run(`apply-${card.id}`, async () => {
      unwrap(
        await api.POST('/api/core/ai/suggestions/{suggestion_id}/apply', {
          params: { path: { suggestion_id: card.id } },
        }),
      )
      if (card.replacement_text.trim() && card.item_id)
        onReplaceWording(card.item_id, card.replacement_text)
      if (selectedCard === card.id) onSelectCard(null)
      await refresh()
    })
  const dismiss = (card: AiSuggestion) =>
    run(`dismiss-${card.id}`, async () => {
      unwrap(
        await api.POST('/api/core/ai/suggestions/{suggestion_id}/dismiss', {
          params: { path: { suggestion_id: card.id } },
        }),
      )
      if (selectedCard === card.id) onSelectCard(null)
      await refresh()
    })
  const fix = (check: AiCheck, index: number) =>
    run(`fix-${index}`, async () => {
      if (!check.piece_id || !check.fix) return
      const changed = await onFix(check.piece_id, check.fix)
      if (!changed) setProblem('Nothing in that part needed the change.')
      await refresh()
    })
  const suggest = () =>
    run('suggest', async () => {
      const answer = unwrap(
        await api.POST('/api/core/ai/drafts/{draft_id}/suggest', {
          params: { path: { draft_id: draftId } },
        }),
      )
      const wrong = answerProblem(answer)
      if (wrong) setProblem(wrong)
      else if (answer.suggestions.length === 0) setProblem('The assistant found nothing to add.')
      await refresh()
    })
  const ask = async () => {
    const asked = question.trim()
    if (!asked || busy) return
    const id = Date.now()
    setThread((t) => [...t, { id, question: asked, answer: null, error: null }])
    setQuestion('')
    await run('ask', async () => {
      try {
        const answer = unwrap(
          await api.POST('/api/core/ai/drafts/{draft_id}/ask', {
            params: { path: { draft_id: draftId } },
            body: { question: asked },
          }),
        )
        setThread((t) => t.map((x) => (x.id === id ? { ...x, answer } : x)))
        if (answer.suggestions.length > 0) {
          setTab('suggestions')
          await refresh()
        }
      } catch (caught) {
        const error = caught instanceof Error ? caught.message : messageOf(caught)
        setThread((t) => t.map((x) => (x.id === id ? { ...x, error } : x)))
      }
    })
  }

  const counts: Partial<Record<TabKey, number | null>> = {
    suggestions: open.length + (checks.style.length ? 1 : 0) + checks.others.length || null,
    notes: data?.notes.length || null,
  }

  return (
    <div className="sticky top-0 flex max-h-screen min-h-0 flex-1 flex-col">
      <div role="tablist" aria-label="Rail" className="flex border-b border-hairline px-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={cx(
              'flex-1 pt-4 pb-3.5 text-center text-125 leading-[normal] whitespace-nowrap',
              tab === t.key
                ? 'font-semibold text-ink shadow-[inset_0_-2px_0_var(--color-ink)]'
                : 'font-medium text-ink-2 hover:text-ink',
            )}
          >
            {t.label}
            {counts[t.key] ? (
              <span className="ml-[5px] font-normal text-muted">{counts[t.key]}</span>
            ) : null}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-3.5">
        {rail.isPending ? (
          <Loading what="Loading the rail" />
        ) : rail.isError ? (
          <LoadFailed what="the rail" error={rail.error} onRetry={() => void rail.refetch()} />
        ) : tab === 'suggestions' ? (
          <>
            {open.map((card) => (
              <SuggestionCard
                key={card.id}
                ref={(el) => {
                  cardRefs.current[card.id] = el
                }}
                card={card}
                heading={cardHeading(pieces, card)}
                selected={selectedCard === card.id}
                frozen={frozen}
                busy={busy}
                onSelect={() => {
                  onSelectCard(card.id)
                  if (card.item_id) onFocusPiece(card.item_id)
                }}
                onApply={() => void apply(card)}
                onDismiss={() => void dismiss(card)}
              />
            ))}
            {checks.style.length > 0 ? (
              <div className={cx(CARD, 'bg-page text-ink-2')}>
                <p className="mb-1 text-13 font-semibold text-ink">House style</p>
                {checks.style.map(({ check, index }, at) => {
                  const where = pieceLabel(pieces, check.piece_id)
                  return (
                    <p key={index} className={cx('text-ink-2', at > 0 && 'mt-0.5')}>
                      {check.piece_id ? (
                        <button
                          type="button"
                          className="text-left hover:underline"
                          onClick={() => onFocusPiece(check.piece_id!)}
                        >
                          {check.title}
                          {where ? ` in ${where}` : ''}
                        </button>
                      ) : (
                        check.title
                      )}
                      {!frozen && check.fix ? (
                        <button
                          type="button"
                          aria-busy={busy === `fix-${index}` || undefined}
                          disabled={busy !== null}
                          onClick={() => void fix(check, index)}
                          className="ml-2 font-medium text-brand hover:text-brand-dark disabled:opacity-55"
                        >
                          Fix
                        </button>
                      ) : null}
                    </p>
                  )
                })}
              </div>
            ) : null}
            {checks.others.map(({ check, index }) => (
              <div key={index} className={cx(CARD, 'bg-page text-ink-2')}>
                {check.piece_id ? (
                  <button
                    type="button"
                    className="mb-1 block text-left text-13 font-semibold text-ink hover:underline"
                    onClick={() => onFocusPiece(check.piece_id!)}
                  >
                    {check.title}
                  </button>
                ) : (
                  <p className="mb-1 text-13 font-semibold text-ink">{check.title}</p>
                )}
                <p>{check.detail}</p>
              </div>
            ))}
            {open.length === 0 && (data?.checks ?? []).length === 0 ? (
              <p className="text-12 leading-[1.55] text-muted">
                Nothing to look at. The checks run on every save
                {unavailable ? '.' : ', and the assistant proposes changes as cards.'}
              </p>
            ) : null}
            {unavailable ? null : (
              <div>
                <button
                  type="button"
                  aria-busy={busy === 'suggest' || undefined}
                  disabled={busy !== null}
                  onClick={() => void suggest()}
                  className={cx(
                    PILL,
                    'border border-line-2 px-[11px] font-medium text-ink-2 hover:bg-page',
                  )}
                >
                  {busy === 'suggest' ? 'Asking the assistant…' : 'Suggest improvements'}
                </button>
              </div>
            )}
            {decided.length > 0 ? (
              <details className="text-115 leading-[1.5] text-muted">
                <summary className="cursor-pointer font-medium text-ink-2">
                  Applied and dismissed ({decided.length})
                </summary>
                <ul className="mt-1.5 flex flex-col gap-1.5">
                  {decided.map((card) => (
                    <li key={card.id}>
                      <span className="text-ink-2">
                        {card.status === 'applied' ? 'Applied' : 'Dismissed'} ·{' '}
                        {card.title || cardHeading(pieces, card)}
                      </span>{' '}
                      · {card.decided_by_name || 'someone'} ·{' '}
                      {card.decided_at ? formatInstant(card.decided_at, me.timezone) : '—'}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
            {problem ? (
              <p role="alert" className="text-12 text-danger">
                {problem}
              </p>
            ) : null}
            <div className="mt-auto flex flex-col gap-2.5 pt-1">
              {thread.map((exchange) => (
                <ExchangeView key={exchange.id} exchange={exchange} />
              ))}
              <p className="text-115 leading-[1.5] text-muted">
                {unavailable ??
                  'Suggestions are passive — the assistant reads the draft and flags.'}
              </p>
              {unavailable ? null : (
                <AskBox
                  placeholder="Ask about this letter…"
                  value={question}
                  onChange={setQuestion}
                  onSubmit={() => void ask()}
                  busy={busy === 'ask'}
                />
              )}
            </div>
          </>
        ) : tab === 'notes' ? (
          <Notes
            draftId={draftId}
            notes={data?.notes ?? []}
            busy={busy}
            problem={problem}
            onRun={run}
            onChanged={refresh}
          />
        ) : (
          <AgencyTab recipients={recipients} agencies={agencies} />
        )}
      </div>
    </div>
  )
}

// ---- cards -------------------------------------------------------------------------------------

function SuggestionCard({
  ref,
  card,
  heading,
  selected,
  frozen,
  busy,
  onSelect,
  onApply,
  onDismiss,
}: {
  ref: (el: HTMLDivElement | null) => void
  card: AiSuggestion
  heading: string
  selected: boolean
  frozen: boolean
  busy: string | null
  onSelect: () => void
  onApply: () => void
  onDismiss: () => void
}) {
  const fight = highlightOf(card.kind) === 'fight'
  // v6 (045): Apply where the card proposes words (none once the letter is frozen); every card
  // dismisses with the × in its corner.
  const canApply = !frozen && Boolean(card.replacement_text.trim())
  return (
    <div
      ref={ref}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
      className={cx(
        CARD,
        'relative',
        fight ? 'bg-danger-tint text-danger-2' : 'bg-amber-tint text-amber',
        selected && 'ring-2 ring-brand ring-offset-1',
      )}
    >
      <button
        type="button"
        title="Dismiss"
        aria-label={`Dismiss ${heading}`}
        aria-busy={busy === `dismiss-${card.id}` || undefined}
        disabled={busy !== null}
        onClick={(event) => {
          event.stopPropagation()
          onDismiss()
        }}
        className="absolute top-[9px] right-[9px] inline-flex size-[22px] items-center justify-center rounded-6 text-muted hover:bg-card-white hover:text-ink-2 disabled:opacity-55"
      >
        <CloseIcon size={12} width={2.4} />
      </button>
      <p
        className={cx(
          'mb-1 pr-[22px] text-13 font-semibold',
          fight ? 'text-red-ink-strong' : 'text-amber-ink-strong',
        )}
      >
        {heading}
      </p>
      {card.title && card.title !== heading && card.title !== kindLabel(card.kind) ? (
        <p className="font-medium">{card.title}</p>
      ) : null}
      <p>{card.text}</p>
      {card.replacement_text.trim() ? (
        <blockquote className="mt-2 rounded-8 bg-card-white px-2.5 py-2 text-ink">
          <span className="mb-1 block text-105 font-semibold tracking-label text-muted uppercase">
            Proposed wording
          </span>
          <span className="whitespace-pre-wrap">{card.replacement_text}</span>
        </blockquote>
      ) : null}
      {canApply ? (
        <div className="mt-2 flex gap-1.5" onClick={(event) => event.stopPropagation()}>
          <button
            type="button"
            aria-busy={busy === `apply-${card.id}` || undefined}
            disabled={busy !== null}
            onClick={onApply}
            className="inline-flex h-7 items-center rounded-8 bg-brand px-3 text-115 leading-[normal] font-semibold text-white shadow-apply hover:bg-brand-dark disabled:opacity-55"
          >
            Apply
          </button>
        </div>
      ) : null}
    </div>
  )
}

function ExchangeView({ exchange }: { exchange: Exchange }) {
  const wrong = exchange.answer ? answerProblem(exchange.answer) : null
  return (
    <>
      <p className="max-w-[88%] self-end rounded-12 rounded-br-4 bg-hover px-3 py-[9px] text-12 leading-[1.5] whitespace-pre-wrap text-ink">
        {exchange.question}
      </p>
      <div className="max-w-[94%] text-12 leading-[1.6] text-ink">
        {exchange.error ? (
          <span className="text-danger">{exchange.error}</span>
        ) : !exchange.answer ? (
          <span className="text-muted">Thinking…</span>
        ) : wrong ? (
          <span className="text-ink-2">{wrong}</span>
        ) : (
          <>
            <span className="whitespace-pre-wrap">{exchange.answer.answer}</span>
            {exchange.answer.suggestions.length > 0 ? (
              <span className="mt-1 block text-115 text-muted">
                {exchange.answer.suggestions.length === 1
                  ? 'One card added under Suggestions.'
                  : `${exchange.answer.suggestions.length} cards added under Suggestions.`}
              </span>
            ) : null}
          </>
        )}
      </div>
    </>
  )
}

// ---- notes -------------------------------------------------------------------------------------

function Notes({
  draftId,
  notes,
  busy,
  problem,
  onRun,
  onChanged,
}: {
  draftId: string
  notes: AiNote[]
  busy: string | null
  problem: string | null
  onRun: (key: string, work: () => Promise<void>) => Promise<void>
  onChanged: () => Promise<void>
}) {
  const [body, setBody] = useState('')
  const add = async () => {
    const text = body.trim()
    if (!text) return
    await onRun('note', async () => {
      unwrap(
        await api.POST('/api/core/ai/drafts/{draft_id}/notes', {
          params: { path: { draft_id: draftId } },
          body: { body: text },
        }),
      )
      setBody('')
      await onChanged()
    })
  }
  const voidNote = (note: AiNote) =>
    onRun(`void-${note.id}`, async () => {
      unwrap(
        await api.POST('/api/core/ai/notes/{note_id}/void', {
          params: { path: { note_id: note.id } },
        }),
      )
      await onChanged()
    })
  return (
    <>
      {notes.length === 0 ? (
        <p className="text-12 leading-[1.55] text-muted">
          No notes on this letter. Notes stay inside the portal.
        </p>
      ) : (
        notes.map((note) => (
          <div key={note.id} className={cx(CARD, 'bg-page text-ink')}>
            <p className="mb-1 flex items-baseline gap-2 text-115 leading-[normal] text-muted">
              <span className="text-12 font-semibold text-ink">{note.author_name}</span>
              <span>{formatAgo(note.created_at)}</span>
              <button
                type="button"
                className="ml-auto hover:text-danger disabled:opacity-40"
                title="Void this note"
                disabled={busy !== null}
                onClick={() => void voidNote(note)}
              >
                Void
              </button>
            </p>
            <p className="whitespace-pre-wrap">{note.body}</p>
          </div>
        ))
      )}
      {problem ? (
        <p role="alert" className="text-12 text-danger">
          {problem}
        </p>
      ) : null}
      <AskBox
        className="mt-auto"
        multiline
        placeholder="A note for the team…"
        value={body}
        onChange={setBody}
        onSubmit={() => void add()}
        busy={busy === 'note'}
      />
    </>
  )
}

// ---- agency ------------------------------------------------------------------------------------

function AgencyTab({ recipients, agencies }: { recipients: Recipient[]; agencies: Agency[] }) {
  const rows = recipients
    .filter((r, index, all) => all.findIndex((other) => other.agency_id === r.agency_id) === index)
    .map((r) => ({ recipient: r, agency: agencies.find((a) => a.id === r.agency_id) ?? null }))
  if (rows.length === 0)
    return (
      <p className="text-12 leading-[1.55] text-muted">
        No agency on this letter yet. Choose them at the next stage.
      </p>
    )
  return (
    <>
      {rows.map(({ recipient, agency }) => (
        <AgencyPanel
          key={recipient.agency_id}
          agencyId={recipient.agency_id}
          channel={recipient.channel}
          agency={agency}
          only={rows.length === 1}
        />
      ))}
    </>
  )
}

function AgencyPanel({
  agencyId,
  channel,
  agency,
  only,
}: {
  agencyId: string
  channel: string
  agency: Agency | null
  only: boolean
}) {
  const [open, setOpen] = useState(only)
  const path = { params: { path: { agency_id: agencyId } } }
  const detail = $api.useQuery('get', '/api/records/agencies/{agency_id}', path, {
    enabled: open,
  })
  const contacts = $api.useQuery('get', '/api/records/agencies/{agency_id}/contacts', path, {
    enabled: open,
  })
  const record = detail.data?.response_record
  const last = (detail.data?.requests ?? []).slice(0, 3)
  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="rounded-12 bg-page text-12 leading-[1.55]"
    >
      <summary className="flex cursor-pointer items-baseline gap-2 px-[15px] py-[13px]">
        <span className="min-w-0 flex-1 truncate text-13 font-semibold text-ink">
          {agency?.name ?? 'Agency'}
        </span>
        <span className="shrink-0 text-115 text-muted">{filesBy(channel, agency)}</span>
      </summary>
      <dl className="flex flex-col gap-2 border-t border-hairline px-[15px] pt-2.5 pb-[13px]">
        <Line label="Sends to">{sendsTo(channel, agency)}</Line>
        {agency?.recipient_office ? (
          <Line label="Addressed to">{agency.recipient_office}</Line>
        ) : null}
        <Line label="Contacts">
          {contacts.isPending && open ? (
            '…'
          ) : (contacts.data ?? []).length === 0 ? (
            <span className="text-muted">None recorded</span>
          ) : (
            <ul>
              {contacts.data!.map((contact) => (
                <li key={contact.id}>
                  {contact.name}
                  {contact.title ? <span className="text-muted"> · {contact.title}</span> : null}
                  {contact.email ? (
                    <span className="block text-115 text-muted">{contact.email}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Line>
        <Line label="Response record">
          {!detail.data ? (
            detail.isError ? (
              <span className="text-danger">{messageOf(detail.error)}</span>
            ) : (
              '…'
            )
          ) : record?.determinations_total ? (
            <>
              {record.determinations_on_time} of {record.determinations_total} determinations on
              time
              {record.median_days_to_determination != null
                ? ` · median ${record.median_days_to_determination} days`
                : ''}
              {record.extensions_taken ? ` · ${record.extensions_taken} extensions` : ''}
            </>
          ) : (
            <span className="text-muted">No determination recorded yet</span>
          )}
        </Line>
        <Line label="Last letters">
          {last.length === 0 ? (
            <span className="text-muted">{detail.data ? 'None yet' : '…'}</span>
          ) : (
            <ul>
              {last.map((row) => (
                <li key={row.id}>
                  <Link className="text-brand hover:text-brand-dark" to={`/requests/${row.id}`}>
                    {row.tracking_number}
                  </Link>
                  <span className="text-ink-2"> · {row.subject}</span>
                </li>
              ))}
            </ul>
          )}
        </Line>
        {agency ? (
          <Link
            className="text-115 font-medium text-brand hover:text-brand-dark"
            to={`/agencies/${agency.id}`}
          >
            Open the agency
          </Link>
        ) : null}
      </dl>
    </details>
  )
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-105 font-semibold tracking-label text-muted uppercase">{label}</dt>
      <dd className="text-ink">{children}</dd>
    </div>
  )
}
