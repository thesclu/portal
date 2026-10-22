/**
 * Fix and resend (125–128): something we sent bounced. Choose the address to send it to instead
 * (the agency's contacts, addresses the agency has written from, or another), review the exact
 * message with the new address, and resend it. The bounced message is never edited: the resend
 * is a new message made of the same words (`copyForResend`), queued for its undo window like any
 * other; the flag is cleared once it is on its way, because a person has dealt with it.
 */
import { useState } from 'react'
import { Link } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { unwrap } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { CheckIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { formatShortInstant } from '../../lib/dates'
import { copyForResend, recipientsOf, useRefreshMail, type Message } from './mail'
import { Box } from './parts'
import type { Row } from './model'
import { needsApproval, useMe } from '../../app/session'
import { sendOrSubmit } from '../../lib/outgoing'

/** What the pane shows while the resend is on its way and once it was delivered (127, 128). */
export interface Resend {
  message: Message
  to: string
  delivered: string | null
}

type Contact = Schemas['ContactOut']

const STEPS = ['Choose address', 'Review', 'Send'] as const

function Stepper({ at }: { at: 0 | 1 }) {
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-0 px-6 pt-5">
      {STEPS.map((label, index) => {
        const done = index < at
        const now = index === at
        const step = (
          <span
            key={label}
            className={cx(
              'flex items-center gap-2 text-12 whitespace-nowrap',
              now
                ? 'font-semibold text-ink'
                : done
                  ? 'font-medium text-success-ink'
                  : 'font-medium text-muted',
            )}
          >
            <span
              className={cx(
                'inline-flex size-[18px] items-center justify-center rounded-pill text-105 font-semibold',
                now
                  ? 'bg-brand text-white'
                  : done
                    ? 'bg-ok-tint text-success-ink'
                    : 'bg-fill-subtle text-muted',
              )}
            >
              {done ? '✓' : index + 1}
            </span>
            {label}
          </span>
        )
        if (index === 0)
          return (
            <div key={label} className="flex min-w-0 items-center gap-3.5">
              {step}
              <span className="h-px flex-1 bg-fill-muted" />
            </div>
          )
        if (index === 2)
          return (
            <div key={label} className="flex min-w-0 items-center gap-3.5">
              <span className="h-px flex-1 bg-fill-muted" />
              {step}
            </div>
          )
        return step
      })}
    </div>
  )
}

export function FixResend({
  row,
  onClose,
  onResent,
}: {
  row: Row
  onClose: () => void
  onResent: (resend: Resend) => void
}) {
  const me = useMe()
  const refreshMail = useRefreshMail()
  const event = row.events[0]
  const requestId = event?.request_id ?? ''
  // The message whose copy this is: the sent message carrying this source.
  const sent = $api.useQuery(
    'get',
    '/api/mail/messages',
    { params: { query: { bucket: 'all', request_id: requestId, limit: 200 } } },
    { enabled: Boolean(requestId) },
  )
  const original = (sent.data?.items ?? []).find((m) => m.source_id === row.source.id)
  const agencyId = event?.agency_id ?? ''
  const contacts = $api.useQuery(
    'get',
    '/api/records/agencies/{agency_id}/contacts',
    { params: { path: { agency_id: agencyId } } },
    { enabled: Boolean(agencyId) },
  )
  // Addresses the agency has written from on this request (125 "from their Sep 3 reply").
  const replies = $api.useQuery(
    'get',
    '/api/records/inbox',
    { params: { query: { bucket: 'all', request_id: requestId, limit: 50 } } },
    { enabled: Boolean(requestId) },
  )
  const bounced = original
    ? recipientsOf(original, 'to')
        .concat(recipientsOf(original, 'cc'))
        .filter((r) => r.delivery_state === 'bounced' || r.delivery_state === 'complained')
    : []
  const bouncedAddresses = new Set(bounced.map((r) => r.address.toLowerCase()))
  const fromContacts = (contacts.data ?? [])
    .filter((c): c is Contact & { email: string } => Boolean(c.email) && !c.bad_address_at)
    .filter((c) => !bouncedAddresses.has(c.email.toLowerCase()))
    .map((c) => ({ address: c.email, name: c.name, note: 'directory' }))
  const seen = new Set(fromContacts.map((c) => c.address.toLowerCase()))
  const fromReplies: Array<{ address: string; name: string | null; note: string }> = []
  for (const reply of replies.data?.items ?? []) {
    const meta = (reply.source.raw_metadata ?? {}) as { from?: string; from_name?: string }
    const address = meta.from?.trim()
    if (!address || seen.has(address.toLowerCase()) || bouncedAddresses.has(address.toLowerCase()))
      continue
    seen.add(address.toLowerCase())
    fromReplies.push({
      address,
      name: meta.from_name || null,
      note: `from their ${formatShortInstant(reply.source.captured_at, me.timezone)} reply`,
    })
  }
  const options = [...fromContacts, ...fromReplies]
  const [step, setStep] = useState<0 | 1>(0)
  const [choice, setChoice] = useState<string>('')
  const [other, setOther] = useState('')
  const [markBad, setMarkBad] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The copy, once made: a refusal to send it (a director must approve it first) leaves it in
  // Drafts, where it can be put up for review.
  const [made, setMade] = useState<string | null>(null)
  const chosen = choice === 'other' ? other.trim() : choice || options[0]?.address || ''
  const chosenName = options.find((o) => o.address === chosen)?.name ?? null
  const badContacts = (contacts.data ?? []).filter(
    (c) => c.email && bouncedAddresses.has(c.email.toLowerCase()) && !c.bad_address_at,
  )

  async function resend() {
    if (!original || !chosen) return
    setBusy(true)
    setError(null)
    try {
      const copy = copyForResend(original)
      const replaced = copy.to.filter((a) => !bouncedAddresses.has(a.address.toLowerCase()))
      // A second press after a refusal sends the copy already made, not another one.
      const draftId =
        made ??
        unwrap(
          await api.POST('/api/mail/messages', {
            body: {
              ...copy,
              to: [{ address: chosen, name: chosenName }, ...replaced],
              cc: copy.cc.filter((a) => !bouncedAddresses.has(a.address.toLowerCase())),
            },
          }),
        ).id
      setMade(draftId)
      const outcome = await sendOrSubmit(draftId, needsApproval(me))
      const queued = outcome
      if (markBad) {
        for (const contact of badContacts) {
          await api.POST('/api/mail/contacts/{contact_id}/bad-address', {
            params: { path: { contact_id: contact.id } },
            body: { note: `Bounced: ${bounced[0]?.bounce_description ?? 'delivery failed'}` },
          })
        }
      }
      // Dealt with: the bounce leaves Flagged.
      await api.POST('/api/records/sources/{source_id}/reviewed', {
        params: { path: { source_id: row.source.id } },
      })
      await refreshMail()
      onResent({ message: queued, to: chosen, delivered: null })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not resend it.')
    } finally {
      setBusy(false)
    }
  }

  const loading = sent.isPending && Boolean(requestId)
  const missing = !loading && !original

  return (
    <Dialog title="Fix and resend" open bare centered width={520} onClose={onClose}>
      <Stepper at={step} />
      {missing ? (
        <div className="px-6 pt-[18px] pb-5">
          <div className="font-display text-18 font-extrabold tracking-dialog text-ink">
            Fix and resend
          </div>
          <p className="mt-2 text-125 leading-[1.6] text-ink-2">
            The message that bounced is not on a request the portal sent it from, so there is
            nothing to resend from here. Write to the agency from its request instead.
          </p>
          <div className="flex justify-end pt-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-pill px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover"
            >
              Close
            </button>
          </div>
        </div>
      ) : step === 0 ? (
        <>
          <div className="px-6 pt-[18px] pb-1.5">
            <div className="font-display text-18 font-extrabold tracking-dialog text-ink">
              Fix and resend
            </div>
            <div className="mt-[3px] text-125 text-muted">
              {loading ? (
                'Finding the message…'
              ) : (
                <>
                  The {original?.kind === 'filing' ? 'filing' : 'message'} to{' '}
                  <span className="text-ink">
                    {bounced.map((r) => r.address).join(', ') || 'the agency'}
                  </span>{' '}
                  bounced.
                </>
              )}
            </div>
          </div>
          <div
            role="radiogroup"
            aria-label="Send it to"
            className="flex flex-col pt-1.5 pr-6 pl-10"
          >
            {options.map((option, index) => {
              const on = chosen === option.address && choice !== 'other'
              return (
                <label
                  key={option.address}
                  className="flex h-9 cursor-pointer items-center gap-[11px] border-b border-line-row"
                >
                  <input
                    type="radio"
                    className="sr-only"
                    checked={on}
                    onChange={() => setChoice(option.address)}
                    autoFocus={index === 0}
                  />
                  <span
                    aria-hidden
                    className={cx(
                      'box-border size-4 shrink-0 rounded-pill bg-card-white',
                      on ? 'border-[5px] border-brand' : 'border-[1.5px] border-line-control',
                    )}
                  />
                  <span className="min-w-0 truncate text-13 text-ink">{option.address}</span>
                  <span className="ml-auto pl-3 text-115 whitespace-nowrap text-muted">
                    {option.note}
                  </span>
                </label>
              )
            })}
            <label className="flex h-9 cursor-pointer items-center gap-[11px]">
              <input
                type="radio"
                className="sr-only"
                checked={choice === 'other'}
                onChange={() => setChoice('other')}
              />
              <span
                aria-hidden
                className={cx(
                  'box-border size-4 shrink-0 rounded-pill bg-card-white',
                  choice === 'other'
                    ? 'border-[5px] border-brand'
                    : 'border-[1.5px] border-line-control',
                )}
              />
              {choice === 'other' ? (
                <input
                  autoFocus
                  aria-label="Another address"
                  placeholder="name@agency.gov"
                  value={other}
                  onChange={(e) => setOther(e.target.value)}
                  className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-text-placeholder"
                />
              ) : (
                <span className="text-13 text-text-placeholder">Other address…</span>
              )}
            </label>
          </div>
          {badContacts.length > 0 ? (
            <label className="flex cursor-pointer items-center gap-[9px] px-6 pt-[18px] pb-1 text-125 text-ink-3">
              <input
                type="checkbox"
                className="sr-only"
                checked={markBad}
                onChange={(e) => setMarkBad(e.target.checked)}
              />
              <Box checked={markBad} size={16} />
              Mark the old address as bad in Agencies
            </label>
          ) : null}
          <div className="flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">
            <button
              type="button"
              onClick={onClose}
              className="rounded-pill px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!chosen || loading}
              onClick={() => setStep(1)}
              className="rounded-pill bg-brand px-[18px] py-[9px] text-13 font-semibold text-white hover:bg-brand-dark disabled:opacity-55"
            >
              Review →
            </button>
          </div>
        </>
      ) : original ? (
        <>
          <div className="px-6 pt-[18px] pb-1.5">
            <div className="font-display text-18 font-extrabold tracking-dialog text-ink">
              Review before resending
            </div>
          </div>
          <div className="mx-6 overflow-hidden rounded-12 bg-page">
            <div className="grid min-w-0 grid-cols-[52px_minmax(0,1fr)] gap-x-2.5 gap-y-[5px] px-3.5 pt-3 pb-2.5 text-125">
              <span className="text-muted">From</span>
              <span className="min-w-0 [overflow-wrap:anywhere] text-ink">
                {original.from_address}
              </span>
              <span className="text-muted">To</span>
              <span className="min-w-0 [overflow-wrap:anywhere]">
                <span className="rounded-4 bg-green-tint-2 px-[5px] py-px text-ink">{chosen}</span>
                {bounced.map((r) => (
                  <span key={r.id} className="mt-[3px] block text-danger line-through">
                    {r.address}
                  </span>
                ))}
              </span>
              <span className="text-muted">Subject</span>
              <span className="min-w-0 [overflow-wrap:anywhere] text-ink">{original.subject}</span>
            </div>
            <div className="relative mx-2 max-h-28 overflow-hidden rounded-8 bg-card-white px-3.5 pt-3 pb-3.5 text-125 leading-[1.7] whitespace-pre-wrap text-ink-2">
              {original.body_text || '(no text)'}
              <div className="absolute inset-x-0 bottom-0 h-9 bg-linear-to-b from-transparent to-card-white" />
            </div>
            <div className="px-3.5 pt-2.5 pb-3 text-12 text-ink-2">
              {original.attach_letter ? 'The request letter, as a PDF' : null}
              {original.attachment_ids.length > 0
                ? `${original.attach_letter ? ' · ' : ''}${original.attachment_ids.length} ${original.attachment_ids.length === 1 ? 'file' : 'files'}`
                : null}
              {original.attach_letter || original.attachment_ids.length > 0 ? (
                <span className="text-muted"> · unchanged</span>
              ) : (
                <span className="text-muted">No files</span>
              )}
            </div>
          </div>
          {error ? (
            <p className="mx-6 mt-3 text-125 text-danger">
              {error}{' '}
              {made ? (
                <Link to={`/inbox?bucket=drafts&message=${made}`} className="font-medium">
                  The copy is in Drafts.
                </Link>
              ) : null}
            </p>
          ) : null}
          <div className="flex items-center justify-end gap-2.5 px-6 pt-3.5 pb-5">
            <button
              type="button"
              onClick={() => setStep(0)}
              className="rounded-pill px-3 py-[9px] text-13 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
            >
              Back
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void resend()}
              className="inline-flex items-center gap-1.5 rounded-pill bg-brand px-[18px] py-[9px] text-13 font-semibold whitespace-nowrap text-white hover:bg-brand-dark disabled:opacity-55"
            >
              {busy ? <CheckIcon size={12} width={3} stroke="currentColor" /> : null}
              Resend
            </button>
          </div>
        </>
      ) : null}
    </Dialog>
  )
}
