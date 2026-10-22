import { useQueryClient } from '@tanstack/react-query'
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api, errorOf, messageOf } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import { unwrap, type ActionSpec } from '../components/ActionDialog'
import { DialogField, RaisedTextarea } from '../components/Fields'
import { LoadFailed, Loading } from '../components/States'
import { Avatar, Callout, cx } from '../components/ui'
import { formatSubmitted } from '../lib/dates'
import { initialsOf } from '../lib/presence'
import { statusText } from '../lib/status'
import { LetterReview } from './approval/LetterReview'
import { NotFound } from './ErrorPages'

type Review = Schemas['ApprovalOut']

/** What a review is, in a word: the letter, message or question behind it. */
export function reviewKind(review: Pick<Review, 'kind' | 'subject_type'>): string {
  if (review.kind === 'question') return 'Question'
  if (review.subject_type === 'draft' || review.subject_type === 'recipient') return 'Letter'
  if (review.subject_type === 'request') return 'Request'
  return 'Message'
}

export const DECISION: Record<string, { label: string; tone: 'ok' | 'warn' | 'accent' }> = {
  approved: { label: 'Approved', tone: 'ok' },
  sent_back: { label: 'Sent back', tone: 'warn' },
  answered: { label: 'Answered', tone: 'accent' },
  withdrawn: { label: 'Withdrawn', tone: 'accent' },
}

const comment = (values: Record<string, unknown>) => String(values.comment ?? '').trim()

export function answerSpec(review: Review): ActionSpec {
  return {
    key: `answer-${review.id}`,
    title: 'Answer',
    subtitle: review.title || undefined,
    intro: review.note ? <span className="whitespace-pre-wrap">{review.note}</span> : undefined,
    submit: 'Send the answer',
    fields: [{ name: 'comment', label: 'Your answer', type: 'textarea', required: true }],
    run: async (values) => {
      unwrap(
        await api.POST('/api/core/reviews/{review_id}/decide', {
          params: { path: { review_id: review.id } },
          body: { decision: 'answered', comment: comment(values) },
        }),
      )
    },
  }
}

export async function invalidateReviews(client: ReturnType<typeof useQueryClient>) {
  await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/pending'] })
  await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/mine'] })
  await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/{review_id}'] })
  await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/clearance'] })
}

/**
 * Reviewing a letter (187) and, for its author, what came back (191): the letter as it would go
 * on its grey ground at the left, the decision at the right (340 px). A director approves or
 * sends it back with a comment; a question is answered by the one person it was put to. The
 * author sees "Sent back to you" with the comment and Edit and resubmit. Approving does not file
 * the letter: the author still files it (so the button reads Approve, not "Approve and file").
 */
export function Approval() {
  const { reviewId = '' } = useParams()
  const review = $api.useQuery(
    'get',
    '/api/core/reviews/{review_id}',
    { params: { path: { review_id: reviewId } } },
    { retry: false },
  )
  const draftId = review.data?.subject_type === 'draft' ? review.data.subject_id : null
  const draft = $api.useQuery(
    'get',
    '/api/drafting/drafts/{draft_id}',
    { params: { path: { draft_id: draftId ?? '' } } },
    { enabled: Boolean(draftId) },
  )

  if (review.isPending)
    return (
      <div className="flex flex-col leading-[normal]">
        <PageHeader title="Approval" />
        <Loading what="Opening the submission" />
      </div>
    )
  if (review.isError) {
    const detail = errorOf(review.error).detail ?? ''
    if (/no (such )?review|not found/i.test(detail)) return <NotFound />
    return (
      <div className="flex flex-col leading-[normal]">
        <PageHeader title="Approval" />
        <LoadFailed
          what="this submission"
          error={review.error}
          onRetry={() => void review.refetch()}
        />
      </div>
    )
  }

  const row = review.data
  // P20h: an intern's letter or message is reviewed as it will go out (235–237).
  if (
    row.kind === 'approval' &&
    (row.subject_type === 'message' || row.subject_type === 'recipient')
  )
    return <LetterReview review={row} />
  const agencies = draft.data
    ? new Set(draft.data.recipients.map((recipient) => recipient.agency_id)).size
    : null
  const title = `${row.title || reviewKind(row)}${
    agencies ? ` · ${agencies} ${agencies === 1 ? 'agency' : 'agencies'}` : ''
  }`
  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title={title} />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        <div className="flex items-start gap-[18px]">
          <div className="min-w-0 flex-1 rounded-12 bg-fill-muted px-10 py-6">
            {row.subject_type === 'draft' ? (
              <DraftSubject draftId={row.subject_id} />
            ) : row.subject_type === 'message' ? (
              <MessageSubject messageId={row.subject_id} />
            ) : row.subject_type === 'request' ? (
              <RequestSubject requestId={row.subject_id} />
            ) : (
              <SourceSubject sourceId={row.subject_id} />
            )}
          </div>
          <div className="w-[340px] shrink-0">
            <Decision review={row} />
          </div>
        </div>
      </div>
    </div>
  )
}

/** The card at the right: a 14 px 600 title with the time beside it, then its body. */
function DecisionCard({
  title,
  when,
  children,
}: {
  title: string
  when?: string
  children: ReactNode
}) {
  return (
    <section className="overflow-hidden rounded-16 bg-card shadow-float">
      <div className="flex items-center gap-2.5 px-5 pt-3.5 pb-3">
        <span className="text-14 font-semibold text-ink">{title}</span>
        {when ? <span className="text-125 text-muted">{when}</span> : null}
      </div>
      <div className="flex flex-col gap-3 px-5 pb-4">{children}</div>
    </section>
  )
}

function Quote({ children }: { children: ReactNode }) {
  return <div className="text-125 leading-body whitespace-pre-wrap text-ink-2">“{children}”</div>
}

/** Who decided, and what they wrote (191). */
function Decided({ review }: { review: Review }) {
  const name = review.decided_by_name ?? 'Someone'
  return (
    <div className="flex gap-2.5">
      <Avatar initials={initialsOf(name)} size={26} />
      <div className="min-w-0 text-125 leading-body text-ink">
        <span className="font-medium">{name}</span>
        {review.comment ? (
          <>
            <br />
            <span className="whitespace-pre-wrap">{review.comment}</span>
          </>
        ) : null}
      </div>
    </div>
  )
}

const PILL =
  'box-border inline-flex h-8 items-center gap-1.5 rounded-pill bg-brand px-3.5 text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark disabled:opacity-55'
const QUIET =
  'box-border inline-flex h-8 items-center gap-1.5 rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover disabled:opacity-55'

function Decision({ review }: { review: Review }) {
  const me = useMe()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [words, setWords] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const question = review.kind === 'question'
  const mine = review.submitted_by_user_id === me.id
  // A question is answered by the one person it was put to; an approval by whoever the server
  // says may approve it (`can_decide`, P20h).
  const mayDecide =
    !review.decision && (question ? review.assigned_to_user_id === me.id : review.can_decide)

  async function decide(decision: 'approved' | 'sent_back' | 'answered') {
    if (decision !== 'approved' && !words.trim())
      return setError(
        decision === 'sent_back' ? 'Say what needs to change.' : 'Write the answer first.',
      )
    setBusy(decision)
    setError(null)
    try {
      unwrap(
        await api.POST('/api/core/reviews/{review_id}/decide', {
          params: { path: { review_id: review.id } },
          body: { decision, comment: words.trim() },
        }),
      )
      setWords('')
      await invalidateReviews(client)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The server refused this.')
    } finally {
      setBusy(null)
    }
  }

  if (review.decision) {
    const decision = DECISION[review.decision]
    const when = formatSubmitted(review.decided_at, me.timezone)
    if (mine && review.decision === 'sent_back') {
      return (
        <DecisionCard title="Sent back to you" when={when}>
          <Decided review={review} />
          <div className="flex gap-2">
            <span className="ml-auto">
              <button
                type="button"
                className={PILL}
                onClick={() =>
                  void navigate(
                    review.subject_type === 'draft'
                      ? `/drafts/${review.subject_id}`
                      : review.request_id
                        ? `/requests/${review.request_id}`
                        : '/tasks',
                  )
                }
              >
                Edit and resubmit
              </button>
            </span>
          </div>
        </DecisionCard>
      )
    }
    return (
      <DecisionCard title={decision?.label ?? review.decision} when={when}>
        <Decided review={review} />
      </DecisionCard>
    )
  }

  const submitted = formatSubmitted(review.submitted_at, me.timezone)
  const who = mine ? 'you' : review.submitted_by_name
  return (
    <DecisionCard title={`${question ? 'Asked' : 'Submitted'} by ${who}`} when={submitted}>
      {review.note ? <Quote>{review.note}</Quote> : null}
      {mayDecide ? (
        <>
          <DialogField label={question ? 'Your answer' : 'Comment'}>
            <RaisedTextarea
              minHeight={70}
              rows={1}
              value={words}
              onChange={(event) => setWords(event.target.value)}
            />
          </DialogField>
          <div className="flex gap-2">
            {question ? (
              <span className="ml-auto">
                <button
                  type="button"
                  className={PILL}
                  disabled={busy !== null}
                  onClick={() => void decide('answered')}
                >
                  Send the answer
                </button>
              </span>
            ) : (
              <>
                <button
                  type="button"
                  className={QUIET}
                  disabled={busy !== null}
                  onClick={() => void decide('sent_back')}
                >
                  Send back
                </button>
                <span className="ml-auto">
                  <button
                    type="button"
                    className={PILL}
                    disabled={busy !== null}
                    aria-busy={busy === 'approved' || undefined}
                    onClick={() => void decide('approved')}
                  >
                    Approve
                  </button>
                </span>
              </>
            )}
          </div>
          {error ? (
            <p role="alert" className="text-12 text-danger">
              {error}
            </p>
          ) : null}
        </>
      ) : (
        <Callout>
          {question
            ? `Waiting for ${review.assigned_to_user_id === me.id ? 'you' : 'the person it was put to'} to answer.`
            : 'Waiting for a director to decide.'}
        </Callout>
      )}
      {review.subject_type === 'draft' ? (
        <Link className="text-125 font-medium text-brand" to={`/drafts/${review.subject_id}`}>
          Open the draft
        </Link>
      ) : review.request_id ? (
        <Link className="text-125 font-medium text-brand" to={`/requests/${review.request_id}`}>
          Open the request
        </Link>
      ) : null}
    </DecisionCard>
  )
}

/** The white page the letter is drawn on (187): Times 13 px on 1.6, clipped at 520 px with a
 *  fade; when the letter is longer than that, "Show all" lifts the clip. */
function LetterPage({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [all, setAll] = useState(false)
  const [long, setLong] = useState(false)
  useLayoutEffect(() => {
    const page = ref.current
    if (!page) return
    const measure = () => setLong(page.scrollHeight > page.clientHeight + 1)
    measure()
    const watch = new ResizeObserver(measure)
    watch.observe(page)
    return () => watch.disconnect()
  }, [])
  return (
    <div
      ref={ref}
      className={cx(
        'relative overflow-hidden bg-card-white px-[46px] py-[38px] font-letter text-13 leading-[1.6] text-letter-ink shadow-sm-5',
        !all && 'max-h-[520px]',
      )}
    >
      {children}
      {all || !long ? null : (
        <div className="absolute inset-x-0 bottom-0 flex h-[60px] items-end justify-center bg-linear-to-b from-card-white/0 to-card-white pb-2">
          <button
            type="button"
            onClick={() => setAll(true)}
            className="font-ui text-12 font-medium text-brand hover:text-brand-dark"
          >
            Show all
          </button>
        </div>
      )}
    </div>
  )
}

/** The letter as it would read for its first recipient; with none yet, what it asks for. */
function DraftSubject({ draftId }: { draftId: string }) {
  const draft = $api.useQuery('get', '/api/drafting/drafts/{draft_id}', {
    params: { path: { draft_id: draftId } },
  })
  const agencyId = draft.data?.recipients[0]?.agency_id
  const preview = $api.useQuery(
    'get',
    '/api/drafting/drafts/{draft_id}/preview',
    { params: { path: { draft_id: draftId }, query: { agency_id: agencyId ?? '' } } },
    { enabled: Boolean(agencyId) },
  )
  if (draft.isPending) return <Loading what="Opening the letter" />
  if (draft.isError)
    return <LoadFailed what="the letter" error={draft.error} onRetry={() => void draft.refetch()} />
  const items = draft.data.items.filter((item) => !item.parent_item_id)
  return (
    <>
      <LetterPage>
        {agencyId ? (
          preview.isPending ? (
            <p className="font-ui text-125 text-muted">Rendering the letter…</p>
          ) : preview.isError ? (
            <p className="font-ui text-125 text-danger">{messageOf(preview.error)}</p>
          ) : (
            <div className="whitespace-pre-wrap">{preview.data.text}</div>
          )
        ) : items.length === 0 ? (
          <p className="font-ui text-125 text-muted">The draft has no agency and no items yet.</p>
        ) : (
          <ol className="flex flex-col gap-2.5">
            {items.map((item) => (
              <li key={item.id}>
                {item.item_number}. {item.body}
              </li>
            ))}
          </ol>
        )}
      </LetterPage>
      {agencyId && preview.data?.unresolved.length ? (
        <p className="mt-3 text-12 text-ink-2">
          Not filled in: {preview.data.unresolved.join(', ')}
        </p>
      ) : null}
    </>
  )
}

function Header({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="mb-4 flex flex-col gap-0.5 font-ui text-12 text-ink-2">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt className="inline font-semibold text-ink">{label} </dt>
          <dd className="inline">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function MessageSubject({ messageId }: { messageId: string }) {
  const message = $api.useQuery('get', '/api/mail/messages/{message_id}', {
    params: { path: { message_id: messageId } },
  })
  if (message.isPending) return <Loading what="Opening the message" />
  if (message.isError)
    return (
      <LoadFailed what="the message" error={message.error} onRetry={() => void message.refetch()} />
    )
  const row = message.data
  const to = row.recipients.filter((r) => r.kind === 'to').map((r) => r.name || r.address)
  const cc = row.recipients.filter((r) => r.kind === 'cc').map((r) => r.name || r.address)
  return (
    <LetterPage>
      <Header
        rows={[
          ['Subject', row.subject || '(no subject)'],
          ['To', to.join(', ') || '—'],
          ...(cc.length > 0 ? ([['Cc', cc.join(', ')]] as Array<[string, ReactNode]>) : []),
          [
            'On',
            <Link key="on" className="text-brand" to={`/requests/${row.request_id}`}>
              {row.tracking_number}
            </Link>,
          ],
        ]}
      />
      <div className="whitespace-pre-wrap">{row.body_text}</div>
    </LetterPage>
  )
}

/** P27d: a question about a whole request (069's "Ask a teammate"): what it asks for, under its
 *  number, agency and status (the decision column links to it, as for any question). */
function RequestSubject({ requestId }: { requestId: string }) {
  const request = $api.useQuery('get', '/api/records/requests/{request_id}', {
    params: { path: { request_id: requestId } },
  })
  if (request.isPending) return <Loading what="Opening the request" />
  if (request.isError)
    return (
      <LoadFailed what="the request" error={request.error} onRetry={() => void request.refetch()} />
    )
  const row = request.data
  return (
    <LetterPage>
      <Header
        rows={[
          ['Request', row.tracking_number],
          ['Agency', row.agency_name],
          ['Status', statusText(row.current_status)],
        ]}
      />
      <div className="whitespace-pre-wrap">{row.request_text || row.subject}</div>
    </LetterPage>
  )
}

function SourceSubject({ sourceId }: { sourceId: string }) {
  const message = $api.useQuery('get', '/api/records/sources/{source_id}/message', {
    params: { path: { source_id: sourceId } },
  })
  if (message.isPending) return <Loading what="Opening the message" />
  if (message.isError)
    return (
      <LoadFailed what="the message" error={message.error} onRetry={() => void message.refetch()} />
    )
  const row = message.data
  if (!row.readable)
    return (
      <LetterPage>
        <p className="font-ui text-125 text-muted">This message cannot be read here.</p>
      </LetterPage>
    )
  return (
    <LetterPage>
      <Header
        rows={[
          ['From', row.from_addr || '—'],
          ['To', row.to.join(', ') || '—'],
          ['Subject', row.subject || '(no subject)'],
        ]}
      />
      <div className="whitespace-pre-wrap">{row.body}</div>
    </LetterPage>
  )
}
