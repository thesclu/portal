import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { isFullStaff, useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { DialogField, RaisedTextarea } from '../../components/Fields'
import { CardHead, ListCard, ListHead, ListRow } from '../../components/ListTable'
import { LoadFailed } from '../../components/States'
import { Button, cx } from '../../components/ui'
import { formatSubmitted } from '../../lib/dates'
import { roleShort } from '../../lib/roles'
import { DECISION, answerSpec, invalidateReviews, reviewKind } from '../Approval'

type Review = Schemas['ApprovalOut']
type Ask = Schemas['AccessRequestOut']

const MINE = '1.5fr 120px 1.4fr 120px'

/** What waits on this person: the approvals and questions (`/core/reviews/pending`) and, for an
 *  administrator, the asks for access. The tab's count is all of it. */
export function usePendingApprovals() {
  const me = useMe()
  const reviews = $api.useQuery('get', '/api/core/reviews/pending')
  const asks = $api.useQuery(
    'get',
    '/api/records/access-requests/pending',
    {},
    { enabled: isFullStaff(me) },
  )
  const total = (reviews.data?.length ?? 0) + (asks.data?.length ?? 0)
  const ready = reviews.data !== undefined && (asks.data !== undefined || !isFullStaff(me))
  return { reviews, asks, count: ready ? total : undefined }
}

/** Every agency's name by id, for the cells that only know ids. */
function useAgencyNames() {
  const agencies = $api.useQuery('get', '/api/records/agencies')
  return new Map((agencies.data ?? []).map((agency) => [agency.id, agency.name]))
}

const ACTION = 'font-medium whitespace-nowrap text-brand hover:text-brand-dark'

/** 234's table: the letter, its request, who it is from, when, and Review at the right. */
const QUEUE = '1.4fr 1.3fr 200px 150px 90px'

/** "REQ-2026-000077 · UC Merced PD" (234): the request as the reader may see it. */
function requestCell(review: Review): string {
  return [review.tracking_number, review.agency_name].filter(Boolean).join(' · ') || '—'
}

/**
 * Tasks → Approvals (234): what waits on this person's decision, oldest first. The interns'
 * letters and messages they may approve (Review opens 235); under them, when there are any, the
 * questions put to them and, for an administrator, the asks for access (196); then what they
 * submitted themselves.
 */
export function Approvals() {
  const me = useMe()
  const client = useQueryClient()
  const { reviews, asks } = usePendingApprovals()
  const mine = $api.useQuery('get', '/api/core/reviews/mine')
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [deciding, setDeciding] = useState<Ask | null>(null)

  const refresh = async () => {
    setSpec(null)
    setDeciding(null)
    await invalidateReviews(client)
    await client.invalidateQueries({ queryKey: ['get', '/api/records/access-requests/pending'] })
    await client.invalidateQueries({ queryKey: ['get', '/api/records/access-requests/mine'] })
    await client.invalidateQueries({ queryKey: ['get', '/api/records/requests/{request_id}'] })
  }

  const approvals = (reviews.data ?? []).filter((r) => r.kind === 'approval')
  const questions = (reviews.data ?? []).filter((r) => r.kind === 'question')
  const pendingAsks = asks.data ?? []
  const waiting = (mine.data ?? []).filter((row) => !row.decision)
  const decided = (mine.data ?? []).filter((row) => Boolean(row.decision))
  return (
    <>
      <div
        aria-label="Approvals"
        className="overflow-hidden rounded-16 bg-card-white pb-1.5 leading-[normal] shadow-card"
      >
        <div
          style={{ gridTemplateColumns: QUEUE }}
          className="grid h-[34px] items-center gap-x-3.5 px-9 text-105 font-semibold tracking-[.07em] text-muted uppercase"
        >
          <span>Letter</span>
          <span>Request</span>
          <span>From</span>
          <span>Submitted</span>
          <span />
        </div>
        {reviews.isPending ? (
          Array.from({ length: 2 }, (_, i) => (
            <div
              key={i}
              aria-hidden
              style={{ gridTemplateColumns: QUEUE, opacity: 1 - i * 0.2 }}
              className="grid h-12 items-center gap-x-3.5 px-9"
            >
              {['80%', '60%', '70%', '60%'].map((width, j) => (
                <span
                  key={j}
                  className="block h-[11px] rounded-4 bg-fill-skeleton"
                  style={{ width }}
                />
              ))}
            </div>
          ))
        ) : reviews.isError ? (
          <LoadFailed
            what="the approvals"
            error={reviews.error}
            onRetry={() => void reviews.refetch()}
          />
        ) : approvals.length === 0 ? (
          <div className="flex flex-col items-center gap-[9px] px-5 py-[44px]">
            <div className="font-display text-lg font-extrabold text-ink">Nothing to approve</div>
            <div className="max-w-[44ch] text-center text-125 leading-[1.6] text-muted">
              No intern’s letter or message is waiting on you.
            </div>
          </div>
        ) : (
          approvals.map((review, index) => (
            <Link
              key={review.id}
              to={`/approvals/${review.id}`}
              style={{ gridTemplateColumns: QUEUE }}
              className={cx(
                'grid h-12 items-center gap-x-3.5 px-9 text-125 text-ink hover:text-ink',
                index % 2 === 1 && 'bg-row-alt',
              )}
            >
              <span className="min-w-0 truncate font-medium" title={review.note || undefined}>
                {review.title || reviewKind(review)}
              </span>
              <span className="min-w-0 truncate text-ink-2">{requestCell(review)}</span>
              <span className="min-w-0 truncate">
                {review.submitted_by_name}
                {review.submitted_by_role ? (
                  <span className="text-muted"> · {roleShort(review.submitted_by_role)}</span>
                ) : null}
              </span>
              <span className="min-w-0 truncate text-ink-2">
                {formatSubmitted(review.submitted_at, me.timezone)}
              </span>
              <span className={cx('justify-self-end', ACTION)}>Review</span>
            </Link>
          ))
        )}
      </div>
      {questions.length > 0 ? (
        <ListCard aria-label="Questions for you">
          <CardHead title="Questions for you" count={questions.length} />
          {questions.map((review, index) => (
            <ListRow key={review.id} columns={MINE} index={index} zebra={false}>
              <span className="min-w-0 truncate" title={review.note || undefined}>
                {review.title || reviewKind(review)}
              </span>
              <span className="min-w-0 truncate text-ink-2">{review.submitted_by_name}</span>
              <span className="min-w-0 truncate text-ink-2">
                {formatSubmitted(review.submitted_at, me.timezone)}
              </span>
              <span className="min-w-0 truncate text-right">
                <button
                  type="button"
                  className={ACTION}
                  onClick={() => setSpec(answerSpec(review))}
                >
                  Answer
                </button>
              </span>
            </ListRow>
          ))}
        </ListCard>
      ) : null}
      {pendingAsks.length > 0 ? (
        <ListCard aria-label="Asks for access">
          <CardHead title="Asks for access" count={pendingAsks.length} />
          {pendingAsks.map((ask, index) => (
            <ListRow key={ask.id} columns={MINE} index={index} zebra={false}>
              <span className="min-w-0 truncate" title={ask.reason}>
                Access to {ask.tracking_number}
              </span>
              <span className="min-w-0 truncate text-ink-2">{ask.user_name}</span>
              <span className="min-w-0 truncate text-ink-2">
                {formatSubmitted(ask.created_at, me.timezone)}
              </span>
              <span className="min-w-0 truncate text-right">
                <button type="button" className={ACTION} onClick={() => setDeciding(ask)}>
                  Review
                </button>
              </span>
            </ListRow>
          ))}
        </ListCard>
      ) : null}
      {waiting.length > 0 ? (
        <Submissions title="Yours, waiting for a decision" rows={waiting} />
      ) : null}
      {decided.length > 0 ? <Submissions title="Yours, decided" rows={decided} /> : null}
      {mine.isError ? (
        <LoadFailed
          what="your submissions"
          error={mine.error}
          onRetry={() => void mine.refetch()}
        />
      ) : null}
      <ActionDialog spec={spec} onClose={() => setSpec(null)} onDone={() => void refresh()} />
      {deciding ? (
        <AccessDialog
          ask={deciding}
          onClose={() => setDeciding(null)}
          onDone={() => void refresh()}
        />
      ) : null}
    </>
  )
}

function Submissions({ title, rows }: { title: string; rows: Review[] }) {
  const me = useMe()
  return (
    <ListCard aria-label={title}>
      <CardHead title={title} count={rows.length} />
      <ListHead columns={MINE} ruled="both">
        <span>Letter</span>
        <span>Kind</span>
        <span>Decision</span>
        <span>When</span>
      </ListHead>
      {rows.map((row, index) => {
        const decision = row.decision ? DECISION[row.decision] : null
        return (
          <ListRow key={row.id} columns={MINE} index={index} to={`/approvals/${row.id}`}>
            <span className="min-w-0 truncate">{row.title || reviewKind(row)}</span>
            <span className="min-w-0 truncate text-ink-2">{reviewKind(row)}</span>
            <span className="min-w-0 truncate" title={row.comment || undefined}>
              {decision ? (
                <>
                  {decision.label}
                  <span className="text-muted"> by {row.decided_by_name ?? 'someone'}</span>
                </>
              ) : (
                <span className="text-muted">Waiting</span>
              )}
            </span>
            <span className="min-w-0 truncate text-ink-2">
              {formatSubmitted(row.decided_at ?? row.submitted_at, me.timezone)}
            </span>
          </ListRow>
        )
      })}
    </ListCard>
  )
}

/**
 * Approving access (196): who asks, for which request, and why, quoted; Allow writes the grant
 * (the same rule as sharing), Decline asks for the reason they will read. The design's "Allow
 * for" (a time limit) is not drawn: a grant lasts until it is taken back.
 */
export function AccessDialog({
  ask,
  onClose,
  onDone,
}: {
  ask: Ask
  onClose: () => void
  onDone: () => void
}) {
  const names = useAgencyNames()
  const request = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}',
    { params: { path: { request_id: ask.request_id } } },
    { retry: false },
  )
  const [declining, setDeclining] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const subtitle = [
    ask.tracking_number,
    request.data?.subject,
    request.data ? names.get(request.data.agency_id) : undefined,
  ]
    .filter(Boolean)
    .join(' · ')

  async function decide(allow: boolean) {
    if (!allow && !note.trim()) return setError('Say why it is declined; they will read it.')
    setBusy(true)
    setError(null)
    try {
      unwrap(
        await api.POST('/api/records/access-requests/{ask_id}/decide', {
          params: { path: { ask_id: ask.id } },
          body: allow ? { allow: true } : { allow: false, note: note.trim() },
        }),
      )
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The server refused this.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      title={`${ask.user_name} asks for access`}
      subtitle={subtitle}
      open
      onClose={onClose}
      width={460}
      top={90}
      footer={
        declining ? (
          <>
            <Button kind="text" pill onClick={() => setDeclining(false)}>
              Cancel
            </Button>
            <Button kind="danger" pill busy={busy} onClick={() => void decide(false)}>
              Decline
            </Button>
          </>
        ) : (
          <>
            <Button kind="text" onClick={() => setDeclining(true)}>
              Decline
            </Button>
            <Button kind="primary" pill busy={busy} onClick={() => void decide(true)}>
              Allow
            </Button>
          </>
        )
      }
    >
      <div className="rounded-10 bg-page px-3.5 py-3 text-125 leading-body text-ink-2">
        “{ask.reason}”
      </div>
      {declining ? (
        <DialogField label={`Tell ${ask.user_name.split(' ')[0]} why`} required>
          <RaisedTextarea
            autoFocus
            minHeight={52}
            rows={1}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </DialogField>
      ) : null}
      {error ? (
        <p
          role="alert"
          className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
        >
          {error}
        </p>
      ) : null}
    </Dialog>
  )
}
