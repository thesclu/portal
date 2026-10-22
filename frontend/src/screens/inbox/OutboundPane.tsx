/**
 * A message the portal sent, is about to send, or could not send, and a draft being written
 * (design 116, 118). A sent message shows who it went from and to, its words, and where it was
 * delivered; a draft opens straight into the composer under "Replying to"; a queued one shows the
 * toast with its countdown and Undo (151); a failed one says why and offers "Fix and resend".
 *
 * `QueuedBanner`, `useLiveMessage` and `TestModeNotice` are shared with the request page.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'

import { api } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { needsApproval, useMe } from '../../app/session'
import { unwrap } from '../../components/ActionDialog'
import { CheckIcon, TrashIcon } from '../../components/icons'
import { LoadFailed, Loading } from '../../components/States'
import { Button, Notice, cx } from '../../components/ui'
import { formatDayTime, formatInstant, formatListTime } from '../../lib/dates'
import { senderName } from '../../lib/requestView'
import { useNameOf } from '../../lib/vocab'
import { FollowUp } from '../request/FollowUp'
import { Composer } from './Composer'
import {
  recipientsOf,
  secondsUntil,
  useNow,
  useRefreshMail,
  useSending,
  type Message,
  type OutboundBucket,
  type Recipient,
} from './mail'
import { PaneCard, QuietButton, Toast } from './parts'
import { sendOrSubmit } from '../../lib/outgoing'

const messageFrom = (caught: unknown): string =>
  caught instanceof Error ? caught.message : 'Something went wrong. Try again.'

const inFlight = (status: string) => status === 'queued' || status === 'sending'

/** The message, kept fresh: every few seconds while it is on its way, so the pane sees it go. */
export function useLiveMessage(messageId: string, placeholder?: Message) {
  return $api.useQuery(
    'get',
    '/api/mail/messages/{message_id}',
    { params: { path: { message_id: messageId } } },
    {
      placeholderData: placeholder,
      refetchInterval: (query) => (inFlight(query.state.data?.status ?? '') ? 3000 : false),
    },
  )
}

/** "Sending in N s · Undo" while the undo window is open; "Sending…" once it has closed. */
export function QueuedBanner({
  message,
  onChanged,
}: {
  message: Message
  onChanged: (message: Message) => void
}) {
  const refresh = useRefreshMail()
  const active = inFlight(message.status)
  const now = useNow(active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!active) return null
  const seconds = secondsUntil(message.send_after, now) ?? 0

  async function undo() {
    setBusy(true)
    setError(null)
    try {
      const back = unwrap(
        await api.POST('/api/mail/messages/{message_id}/cancel', {
          params: { path: { message_id: message.id } },
        }),
      )
      await refresh()
      onChanged(back)
    } catch (caught) {
      setError(messageFrom(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-3 space-y-2">
      <div
        role="status"
        className="flex items-center justify-between gap-3 rounded-9 bg-brand-tint px-3 py-2 text-125 text-brand"
      >
        <span>
          {message.status === 'sending' || seconds === 0 ? 'Sending…' : `Sending in ${seconds} s`}
        </span>
        {message.status === 'queued' ? (
          <Button busy={busy} onClick={() => void undo()}>
            Undo
          </Button>
        ) : null}
      </div>
      {error ? <Notice tone="danger">{error}</Notice> : null}
    </div>
  )
}

export function TestModeNotice() {
  return (
    <Notice tone="warn">
      Test mode: this message was accepted by the mail provider's test token and was not delivered
      to anyone.
    </Notice>
  )
}

/** "Reply sent to … · Undo" (151): the countdown as a line that runs out. */
export function SentToast({
  message,
  to,
  onUndone,
  bottom = 84,
}: {
  message: Message
  to: string
  onUndone: (draft: Message) => void
  bottom?: number
}) {
  const refresh = useRefreshMail()
  const sending = useSending()
  const queued = message.status === 'queued'
  const now = useNow(queued)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!inFlight(message.status)) return null
  const total = sending.data?.undo_send_seconds ?? 30
  const left = secondsUntil(message.send_after, now) ?? 0

  async function undo() {
    setBusy(true)
    setError(null)
    try {
      const back = unwrap(
        await api.POST('/api/mail/messages/{message_id}/cancel', {
          params: { path: { message_id: message.id } },
        }),
      )
      await refresh()
      onUndone(back)
    } catch (caught) {
      setError(messageFrom(caught))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Toast
      width={320}
      done
      bordered={false}
      bottom={bottom}
      action={queued && !busy ? 'Undo' : undefined}
      onAction={() => void undo()}
      progress={queued ? 1 - left / Math.max(1, total) : 1}
    >
      {error ?? (queued ? `${kindWords(message)} sent to ${to}` : 'Sending…')}
    </Toast>
  )
}

const kindWords = (message: Message) =>
  message.kind === 'reply' ? 'Reply' : message.kind === 'forward' ? 'Forward' : 'Message'

/** "Delivered to a and b · Sep 11, 4:02 PM" (116), or what happened instead. */
function DeliveryLine({ message }: { message: Message }) {
  const me = useMe()
  const sent = recipientsOf(message, 'to')
    .concat(recipientsOf(message, 'cc'))
    .concat(recipientsOf(message, 'bcc'))
  const delivered = sent.filter((r) => r.delivery_state === 'delivered')
  const bounced = sent.filter(
    (r) => r.delivery_state === 'bounced' || r.delivery_state === 'complained',
  )
  const latest = delivered
    .map((r) => r.delivered_at)
    .filter((at): at is string => Boolean(at))
    .toSorted()
    .at(-1)
  const list = (rows: Recipient[]) =>
    rows.length <= 2
      ? rows.map((r) => r.address).join(' and ')
      : `${rows
          .slice(0, -1)
          .map((r) => r.address)
          .join(', ')} and ${rows.at(-1)?.address}`
  let words: string
  let ok = false
  if (message.status === 'failed')
    words = `Not sent. ${message.error || 'The mail provider gave no reason.'}`
  else if (message.test_send) words = 'Sent in test mode · delivered to nobody'
  else if (bounced.length)
    words = `Bounced at ${list(bounced)}${bounced[0].bounce_description ? ` · ${bounced[0].bounce_description}` : ''}`
  else if (delivered.length) {
    ok = true
    words = `Delivered to ${list(delivered)}${latest ? ` · ${formatDayTime(latest, me.timezone)}` : ''}`
  } else
    words = `Sent${message.sent_at ? ` ${formatDayTime(message.sent_at, me.timezone)}` : ''} · waiting for delivery`
  return (
    <div className="flex items-center gap-2 border-t border-hairline px-[22px] py-3 text-12 text-ink-2">
      {ok ? <CheckIcon size={14} width={2.2} stroke="var(--color-success-ink-2)" /> : null}
      {words}
    </div>
  )
}

/** "Replying to CSUDH Records  We are extending…  Sep 22" (118). */
function ReplyingTo({ sourceId }: { sourceId: string }) {
  const me = useMe()
  const original = $api.useQuery(
    'get',
    '/api/records/sources/{source_id}/message',
    { params: { path: { source_id: sourceId } } },
    { staleTime: 5 * 60_000 },
  )
  const mail = original.data?.readable ? original.data : null
  if (!mail) return null
  const who = mail.from_name || senderName(mail.from_addr).name
  return (
    <div className="flex items-baseline gap-3 border-b border-hairline bg-surface-row px-[22px] py-3 text-125">
      <span className="whitespace-nowrap text-muted">Replying to</span>
      <span className="font-medium whitespace-nowrap text-ink">{who}</span>
      <span className="min-w-0 flex-1 truncate text-ink-3">
        {mail.body.replace(/\s+/g, ' ').slice(0, 160)}
      </span>
      {mail.date ? (
        <span className="text-12 whitespace-nowrap text-muted">
          {formatListTime(mail.date, me.timezone)}
        </span>
      ) : null}
    </div>
  )
}

/**
 * The pane. `onOpen` moves the Inbox to a list and a message: after "Fix and resend" the new
 * draft, after "Send again" the message now under Drafts, and a sent one that left Drafts.
 */
export function OutboundPane({
  messageId,
  placeholder,
  bucket,
  onClose,
  onOpen,
}: {
  messageId: string
  placeholder?: Message
  bucket: OutboundBucket | 'all'
  onClose: () => void
  onOpen: (bucket: OutboundBucket, messageId: string) => void
}) {
  const me = useMe()
  const client = useQueryClient()
  const nameOf = useNameOf()
  const refresh = useRefreshMail()
  const live = useLiveMessage(messageId, placeholder)
  const [pane, setPane] = useState<HTMLElement | null>(null)
  const [discardAsked, setDiscardAsked] = useState(0)
  const [busy, setBusy] = useState<'again' | 'fix' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [followingUp, setFollowingUp] = useState(false)
  const requestId = live.data?.request_id ?? ''
  const request = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}',
    { params: { path: { request_id: requestId } } },
    { enabled: Boolean(requestId) },
  )

  if (live.isPending) {
    return (
      <PaneCard>
        <Loading what="Opening the message" />
      </PaneCard>
    )
  }
  if (live.isError) {
    return (
      <PaneCard>
        <LoadFailed what="the message" error={live.error} onRetry={() => void live.refetch()} />
      </PaneCard>
    )
  }
  const message = live.data
  const draft = message.status === 'draft'
  const agencyName = request.data?.agency_name

  async function sendAgain() {
    setBusy('again')
    setError(null)
    try {
      const outcome = await sendOrSubmit(message.id, needsApproval(me))
      await refresh()
      onOpen('drafts', outcome ? outcome.id : message.id)
    } catch (caught) {
      setError(messageFrom(caught))
    } finally {
      setBusy(null)
    }
  }

  return (
    <PaneCard label={message.subject || 'A message'} paneRef={setPane}>
      <div className="flex items-start gap-3.5 border-b border-hairline px-[22px] pt-[18px] pb-3.5">
        <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
          <h2 className="min-w-0 truncate font-display text-xl leading-[1.3] font-extrabold tracking-dialog text-ink">
            {message.subject || '(no subject)'}
          </h2>
          <span className="flex min-w-0 items-center gap-2 whitespace-nowrap">
            {agencyName && request.data ? (
              <Link
                to={`/agencies/${request.data.agency_id}`}
                className="shrink-0 text-125 text-ink-2 underline decoration-line-control underline-offset-[3px] hover:text-ink"
              >
                {agencyName}
              </Link>
            ) : null}
            {agencyName ? <span className="shrink-0 text-125 text-line-control">·</span> : null}
            <Link
              to={`/requests/${message.request_id}`}
              className="shrink-0 text-125 text-ink-2 underline decoration-line-control underline-offset-[3px] hover:text-ink"
            >
              {message.tracking_number}
            </Link>
          </span>
        </div>
        <span className="-mt-0.5 flex shrink-0 items-center gap-1.5">
          {draft ? (
            <DiscardButton onClick={() => setDiscardAsked((n) => n + 1)} />
          ) : message.status === 'failed' ? (
            <>
              <QuietButton onClick={() => void sendAgain()} disabled={busy === 'again'}>
                Send again
              </QuietButton>
            </>
          ) : message.status === 'sent' && request.data ? (
            <QuietButton onClick={() => setFollowingUp(true)}>Follow up</QuietButton>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close the message"
            className="sr-only focus:not-sr-only"
          >
            Close
          </button>
        </span>
      </div>

      {draft ? (
        <>
          {message.in_reply_to_source_id ? (
            <ReplyingTo sourceId={message.in_reply_to_source_id} />
          ) : null}
          <MessageReviewBanner message={message} />
          <Composer
            key={message.id}
            messageId={message.id}
            frame="draft"
            overlayHost={pane}
            discardAsked={discardAsked}
            onClose={onClose}
            onSent={() => void live.refetch()}
          />
        </>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <div className="px-[22px] pt-4 pb-1.5">
            <div className="grid grid-cols-[44px_1fr_auto] items-baseline gap-x-2.5 gap-y-0.5 text-12">
              <span className="font-semibold text-ink">From</span>
              <span className="min-w-0 truncate">
                <span className="text-ink">
                  {nameOf(message.created_by_user_id) ?? 'Your team'}
                </span>{' '}
                <span className="text-ink-2">&lt;{message.from_address}&gt;</span>
              </span>
              <span
                title={formatInstant(message.sent_at ?? message.updated_at, me.timezone)}
                className="text-12 whitespace-nowrap text-muted"
              >
                {formatListTime(message.sent_at ?? message.updated_at, me.timezone)}
              </span>
              {(['to', 'cc', 'bcc'] as const).map((kind) => {
                const rows = recipientsOf(message, kind)
                if (rows.length === 0) return null
                return (
                  <span key={kind} className="contents">
                    <span className="font-semibold text-ink capitalize">{kind}</span>
                    <span className="min-w-0 truncate">
                      {rows.map((r, i) => (
                        <span key={r.id}>
                          {i ? ', ' : ''}
                          {r.name ? <span className="text-ink">{r.name}</span> : null}{' '}
                          <span className="text-ink-2">
                            {r.name ? `<${r.address}>` : r.address}
                          </span>
                        </span>
                      ))}
                    </span>
                    <span />
                  </span>
                )
              })}
            </div>
          </div>
          <div className="flex-1 px-[22px] py-4 text-125 leading-[1.8] whitespace-pre-wrap text-ink">
            {message.body_text || '(no text)'}
          </div>
          {error ? (
            <div className="px-[22px] pb-3">
              <Notice tone="danger">{error}</Notice>
            </div>
          ) : null}
          {bucket === 'drafts' && (message.status === 'sent' || message.status === 'failed') ? (
            <div className="flex items-center justify-between gap-3 border-t border-hairline px-[22px] py-3 text-12 text-ink-2">
              <span>
                {message.status === 'sent' ? 'It went.' : 'It could not be sent.'} It is now under
                Sent.
              </span>
              <QuietButton onClick={() => onOpen('sent', message.id)}>Open in Sent</QuietButton>
            </div>
          ) : null}
          {message.status === 'sent' || message.status === 'failed' ? (
            <DeliveryLine message={message} />
          ) : null}
        </div>
      )}
      {inFlight(message.status) ? (
        <SentToast
          message={message}
          to={agencyName ?? recipientsOf(message, 'to')[0]?.address ?? 'the agency'}
          bottom={28}
          onUndone={() => {
            void client.invalidateQueries({ queryKey: ['get', '/api/mail/messages/{message_id}'] })
            void live.refetch()
          }}
        />
      ) : null}
      {followingUp && request.data ? (
        <FollowUp
          request={request.data}
          onClose={() => setFollowingUp(false)}
          onDone={() => {
            setFollowingUp(false)
            void refresh()
          }}
        />
      ) : null}
    </PaneCard>
  )
}

/** The Drafts pane's header button (118): a red bin that asks before it deletes. */
export function DiscardButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      title="Discard draft"
      aria-label="Discard draft"
      onClick={onClick}
      className={cx(
        'inline-flex h-[30px] w-8 items-center justify-center rounded-9 hover:bg-danger-tint',
      )}
    >
      <TrashIcon size={15} width={1.8} stroke="var(--color-danger)" />
    </button>
  )
}

/** P13: while review is required, a message needs a director's approval before Send works. The
 *  server refuses either way; this says why first and offers to ask for it. */
function MessageReviewBanner({ message }: { message: Message }) {
  const client = useQueryClient()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const clearance = $api.useQuery('get', '/api/core/reviews/clearance', {
    params: { query: { subject_type: 'message', subject_id: message.id } },
  })
  if (!clearance.data || !clearance.data.required) return null
  const found = clearance.data
  const waiting = found.review && !found.review.decision
  async function submit() {
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/core/reviews', {
      body: { subject_type: 'message', subject_id: message.id, kind: 'approval', note },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    setNote('')
    await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/clearance'] })
  }
  return (
    <div
      role="status"
      className="border-b border-hairline bg-row-alt px-[22px] py-3 text-125 leading-[1.5] text-ink-2"
    >
      <span className="block">
        {found.why}
        {found.review?.decision === 'sent_back' && found.review.comment ? (
          <span className="block">
            {found.review.decided_by_name ?? 'A director'}: “{found.review.comment}”
          </span>
        ) : null}
      </span>
      {!found.cleared && !waiting ? (
        <form
          className="mt-2 flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <input
            aria-label="A note for the director"
            placeholder="A note for the director"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="h-8 min-w-0 flex-1 rounded-9 border-[1.5px] border-line-2 bg-card-white px-2.5 text-125 text-ink outline-none focus:border-brand"
          />
          <button
            type="submit"
            disabled={busy}
            className="h-8 rounded-pill bg-brand px-3.5 text-125 font-medium text-white hover:bg-brand-dark disabled:opacity-55"
          >
            Submit for review
          </button>
        </form>
      ) : null}
      {error ? <span className="mt-1 block text-danger">{error}</span> : null}
    </div>
  )
}
