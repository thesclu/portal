import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { useMe } from '../../app/session'
import { Dialog } from '../../components/Dialog'
import { RaisedTextarea } from '../../components/Fields'
import { CopyIcon, DownloadIcon, LetterFullScreenIcon } from '../../components/icons'
import { Callout } from '../../components/ui'
import { formatShortInstant, formatSubmitted } from '../../lib/dates'
import { roleShort } from '../../lib/roles'
import { IconAction, IconTool, SectionTitle } from '../newrequest/parts'
import {
  InfoLine,
  LetterFrame,
  isForkLetter,
  letterPdfUrl,
  pdfUrl,
  usePageCount,
} from '../newrequest/stages/ReviewFile'
import { attachmentLabel, pagesLabel } from '../newrequest/stages/stages'

type Review = Schemas['ApprovalOut']

const firstName = (name: string) => name.trim().split(/\s+/)[0] ?? name

/** "from Maya Torres, Intern". */
function fromLine(review: Review): string {
  const role = roleShort(review.submitted_by_role)
  return `from ${review.submitted_by_name}${role ? `, ${role}` : ''}`
}

/**
 * Reviewing an intern's letter (235): the cover message, the letter and how it goes, exactly as
 * they will be sent, and Send back (237) or Approve and send (236). A reply or a follow-up shows
 * its message and its Sending; a letter filed by hand (a web form, the post) shows the letter, and
 * approving it files nothing, so the button is plain Approve (42a). "Open in Drafting ↗" is the
 * drafting engine's and waits for it (P21). Whoever may not decide it (or after it is decided)
 * reads it without the buttons.
 */
export function LetterReview({ review }: { review: Review }) {
  const me = useMe()
  const client = useQueryClient()
  const navigate = useNavigate()
  const message = $api.useQuery(
    'get',
    '/api/mail/messages/{message_id}',
    { params: { path: { message_id: review.subject_id } } },
    { enabled: review.subject_type === 'message' },
  )
  const request = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}',
    { params: { path: { request_id: review.request_id ?? '' } } },
    { enabled: Boolean(review.request_id), retry: false },
  )
  const recipientId = review.recipient_id ?? null
  const draftId = review.draft_id ?? null
  const letter = Boolean(recipientId && draftId)
  const draft = $api.useQuery(
    'get',
    '/api/drafting/drafts/{draft_id}',
    { params: { path: { draft_id: draftId ?? '' } } },
    { enabled: Boolean(draftId) },
  )
  const preview = $api.useQuery(
    'get',
    '/api/drafting/recipients/{recipient_id}/preview',
    { params: { path: { recipient_id: recipientId ?? '' } } },
    { enabled: letter },
  )
  const pages = usePageCount(draftId ?? '', recipientId ?? '', review.request_id ?? 'draft')
  const recipient = draft.data?.recipients.find((r) => r.id === recipientId)
  const [dialog, setDialog] = useState<'approve' | 'back' | null>(null)
  const sends = review.subject_type === 'message'
  const decide = review.can_decide && !review.decision

  const subject = request.data?.subject ?? review.title
  const note = [
    request.data?.agency_name ?? review.agency_name,
    review.tracking_number,
    fromLine(review),
    formatSubmitted(review.submitted_at, me.timezone),
  ]
    .filter(Boolean)
    .join(' · ')
  const dialogSubtitle = [
    review.tracking_number,
    review.agency_short_name ?? review.agency_name,
    fromLine(review),
  ]
    .filter(Boolean)
    .join(' · ')

  async function done() {
    setDialog(null)
    await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/pending'] })
    await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews/{review_id}'] })
    await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews'] })
    void navigate('/tasks?view=approvals')
  }

  const m = message.data
  const to = (m?.recipients ?? []).filter((r) => r.kind === 'to').map((r) => r.address)
  const cc = (m?.recipients ?? []).filter((r) => r.kind === 'cc').map((r) => r.address)
  const attachment = letter
    ? attachmentLabel(recipient?.letter_filename ?? '', pages)
    : (m?.attachment_ids.length ?? 0) > 0
      ? `${m!.attachment_ids.length} ${m!.attachment_ids.length === 1 ? 'file' : 'files'}`
      : null
  // P21c: a letter written in the drafting tool is read as the drafting tool's PDF.
  const fromFork = isForkLetter(draft.data, recipient)
  const pdf = !letter
    ? ''
    : draft.data && recipient
      ? letterPdfUrl(draft.data, recipient)
      : pdfUrl(draftId!, recipientId!)

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader
        title={subject}
        back={{ label: 'Tasks', to: '/tasks?view=approvals' }}
        note={note}
        roomy
      />
      <div className="flex min-w-0 flex-col gap-3 px-2 pb-2">
        {review.decision ? (
          <Callout tone={review.decision === 'approved' ? 'grey' : 'amber'} role="status">
            {review.decision === 'approved'
              ? `Approved by ${review.decided_by_name ?? 'an approver'}`
              : review.decision === 'withdrawn'
                ? `Withdrawn by ${review.submitted_by_name}`
                : `Sent back by ${review.decided_by_name ?? 'an approver'}`}
            {review.decided_at ? ` · ${formatShortInstant(review.decided_at, me.timezone)}` : ''}
            {review.comment ? ` · “${review.comment}”` : ''}
          </Callout>
        ) : null}
        <section className="overflow-hidden rounded-16 bg-card shadow-float">
          {sends ? (
            <div className="flex flex-col gap-3.5 px-6 py-5">
              <div className="flex items-center gap-2.5">
                <SectionTitle>{letter ? 'Cover Message' : 'Message'}</SectionTitle>
                <span className="ml-auto flex items-center gap-1">
                  <IconTool
                    label="Copy message"
                    tone="muted"
                    icon={<CopyIcon size={15} width={1.9} />}
                    disabled={!m?.body_text}
                    copyText={() => m?.body_text}
                  />
                </span>
              </div>
              <div className="rounded-8 bg-surface-soft px-4 py-3.5 text-125 leading-[1.75] whitespace-pre-wrap text-ink">
                {m?.body_text || <span className="text-muted">No message.</span>}
              </div>
            </div>
          ) : null}
          {letter ? (
            <div
              className={
                sends
                  ? 'flex flex-col gap-3 border-t border-hairline px-6 py-5'
                  : 'flex flex-col gap-3 px-6 py-5'
              }
            >
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
                    tone="muted"
                    icon={<CopyIcon size={15} width={1.9} />}
                    disabled={!preview.data}
                    copyText={() => preview.data?.text}
                  />
                  <IconTool
                    label="Download PDF"
                    tone="muted"
                    icon={<DownloadIcon size={15} width={1.9} />}
                    href={pdf}
                    download={recipient?.letter_filename || undefined}
                  />
                </span>
              </div>
              <div className="flex justify-center rounded-10 bg-page p-5">
                <LetterFrame
                  draftId={draftId!}
                  recipientId={recipientId!}
                  forkPdf={fromFork ? pdf : undefined}
                />
              </div>
            </div>
          ) : null}
          {sends ? (
            <div className="flex flex-col gap-[13px] border-t border-hairline px-6 py-5">
              <span className="text-14 font-semibold text-ink">Sending</span>
              <InfoLine label="To">{to.join(', ') || '—'}</InfoLine>
              {cc.length > 0 ? <InfoLine label="Cc">{cc.join(', ')}</InfoLine> : null}
              <InfoLine label="From">{m?.from_address ?? '…'}</InfoLine>
              <InfoLine label="Subject">{m?.subject ?? '…'}</InfoLine>
              {attachment && (letter ? m?.attach_letter !== false : true) ? (
                <InfoLine label="Attached">{attachment}</InfoLine>
              ) : null}
            </div>
          ) : (
            <div className="flex flex-col gap-[13px] border-t border-hairline px-6 py-5">
              <span className="text-14 font-semibold text-ink">Filing</span>
              <InfoLine label="Goes by">
                {recipient?.channel === 'mail'
                  ? 'Post: the intern prints and mails it once you approve'
                  : 'Their web form: the intern submits it once you approve'}
              </InfoLine>
            </div>
          )}
        </section>
        {decide ? (
          <div className="flex items-center justify-end gap-[11px]">
            <button
              type="button"
              onClick={() => setDialog('back')}
              className="rounded-9 px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover"
            >
              Send back
            </button>
            <button
              type="button"
              onClick={() => setDialog('approve')}
              className="rounded-9 border-[1.5px] border-brand bg-brand px-[17px] py-2.5 text-13 font-medium text-white hover:bg-brand-dark"
            >
              {sends ? 'Approve and send' : 'Approve'}
            </button>
          </div>
        ) : null}
      </div>
      <ApproveDialog
        open={dialog === 'approve'}
        review={review}
        title={sends ? 'Approve and send' : 'Approve'}
        subtitle={dialogSubtitle}
        onClose={() => setDialog(null)}
        onDone={() => void done()}
      />
      <SendBackDialog
        open={dialog === 'back'}
        review={review}
        subtitle={dialogSubtitle}
        onClose={() => setDialog(null)}
        onDone={() => void done()}
      />
    </div>
  )
}

function DialogFrame({
  title,
  subtitle,
  open,
  onClose,
  children,
  footer,
}: {
  title: string
  subtitle: string
  open: boolean
  onClose: () => void
  children?: ReactNode
  footer: ReactNode
}) {
  return (
    <Dialog title={title} open={open} onClose={onClose} width={460} centered bare>
      <div className="leading-[normal]">
        <div className="px-6 pt-[22px] pb-1">
          <h2 className="font-display text-18 font-extrabold text-ink">{title}</h2>
          <p className="mt-1 text-125 text-muted">{subtitle}</p>
        </div>
        <div className="px-6 pt-3">{children}</div>
        <div className="flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">{footer}</div>
      </div>
    </Dialog>
  )
}

const CANCEL =
  'rounded-pill px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover disabled:opacity-55'
const PRIMARY =
  'rounded-pill bg-brand px-[18px] py-[9px] text-13 font-semibold text-white hover:bg-brand-dark disabled:opacity-55'

/** Approve and send (236): one press, and the message goes exactly as stage 4 would have sent it;
 *  for a letter filed by hand, Approve releases it to the intern. */
function ApproveDialog({
  open,
  review,
  title,
  subtitle,
  onClose,
  onDone,
}: {
  open: boolean
  review: Review
  title: string
  subtitle: string
  onClose: () => void
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function approve() {
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/core/reviews/{review_id}/decide', {
      params: { path: { review_id: review.id } },
      body: { decision: 'approved', comment: '' },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    onDone()
  }
  return (
    <DialogFrame
      title={title}
      subtitle={subtitle}
      open={open}
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} className={CANCEL}>
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={() => void approve()} className={PRIMARY}>
            {title}
          </button>
        </>
      }
    >
      {error ? (
        <p
          role="alert"
          className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
        >
          {error}
        </p>
      ) : null}
    </DialogFrame>
  )
}

/** Send back (237): the note is required, and the intern reads it on their request (238). */
function SendBackDialog({
  open,
  review,
  subtitle,
  onClose,
  onDone,
}: {
  open: boolean
  review: Review
  subtitle: string
  onClose: () => void
  onDone: () => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const who = firstName(review.submitted_by_name)
  async function sendBack() {
    if (!note.trim()) {
      setError(`Write a note: ${who} reads it to know what to change.`)
      return
    }
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/core/reviews/{review_id}/decide', {
      params: { path: { review_id: review.id } },
      body: { decision: 'sent_back', comment: note.trim() },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    onDone()
  }
  return (
    <DialogFrame
      title={`Send back to ${who}`}
      subtitle={subtitle}
      open={open}
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} className={CANCEL}>
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={() => void sendBack()} className={PRIMARY}>
            Send back
          </button>
        </>
      }
    >
      <label className="flex flex-col gap-1.5">
        <span className="text-12 font-medium text-ink-2">Note for {who}</span>
        <RaisedTextarea
          data-autofocus
          required
          minHeight={68}
          rows={2}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="text-13 leading-[1.55]"
        />
      </label>
      {error ? (
        <p
          role="alert"
          className="mt-2 rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
        >
          {error}
        </p>
      ) : null}
    </DialogFrame>
  )
}
