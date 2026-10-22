import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { needsApproval, useMe } from '../../app/session'
import { unwrap } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { DialogField } from '../../components/Fields'
import { Button } from '../../components/ui'
import { formatShortDate } from '../../lib/dates'
import { sendLabel, sendOrSubmit } from '../../lib/outgoing'
import { statusText } from '../../lib/status'

type Detail = Schemas['RequestDetailOut']

/**
 * 082: follow up on an overdue request. The letter is the one the server renders from the
 * organisation's own follow-up template (Settings → Filing & deadlines), the same draft the
 * Docket's bulk follow-up (034) reads; it is edited here and sent from the request's address.
 */
export function FollowUp({
  request,
  onClose,
  onDone,
}: {
  request: Detail
  onClose: () => void
  onDone: () => void
}) {
  const intern = needsApproval(useMe())
  const draft = useQuery({
    queryKey: ['followup-draft', request.id],
    staleTime: Infinity,
    queryFn: async () =>
      unwrap(
        await api.POST('/api/mail/requests/{request_id}/followup-draft', {
          params: { path: { request_id: request.id } },
        }),
      ),
  })
  const [body, setBody] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const text = body ?? draft.data?.body ?? ''
  const missed = request.deadlines
    .filter((d) => d.status === 'missed')
    .toSorted((a, b) => b.due_at.localeCompare(a.due_at))[0]

  async function send() {
    if (!draft.data) return
    setBusy(true)
    setError(null)
    try {
      const address = (list: string[]) => list.map((a) => ({ address: a }))
      const message = unwrap(
        await api.POST('/api/mail/messages', {
          body: {
            request_id: request.id,
            kind: 'followup',
            subject: draft.data.subject,
            body_text: text,
            to: address(draft.data.to),
            cc: address(draft.data.cc),
            bcc: [],
            attachment_ids: [],
            attach_letter: false,
          },
        }),
      )
      // An intern's follow-up waits for an approver (P20h); everyone else's goes.
      await sendOrSubmit(message.id, intern)
      onDone()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'It was not sent.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      title="Follow up"
      subtitle={`${statusText(request.current_status)}${missed ? ` since ${formatShortDate(missed.due_at)}` : ''}`}
      open
      onClose={onClose}
      width={560}
      top={70}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button
            kind="primary"
            pill
            busy={busy}
            disabled={!draft.data || draft.data.to.length === 0 || !text.trim()}
            onClick={() => void send()}
          >
            {sendLabel(intern)}
          </Button>
        </>
      }
    >
      {draft.isError ? (
        <p role="alert" className="text-13 text-danger">
          Couldn’t load this request’s follow-up.
        </p>
      ) : (
        <textarea
          aria-label="The follow-up"
          value={draft.data ? text : 'Preparing the follow-up…'}
          disabled={!draft.data}
          onChange={(event) => setBody(event.target.value)}
          className="min-h-[140px] resize-none rounded-10 border-[1.5px] border-line px-4 py-3.5 text-13 leading-[1.6] text-ink outline-none [field-sizing:content] focus:border-brand"
        />
      )}
      <DialogField label="To">
        <div className="box-border flex h-[38px] items-center rounded-12 bg-card-white px-3 text-13 text-ink shadow-field">
          <span className="min-w-0 flex-1 truncate">{draft.data?.to.join(', ') || '—'}</span>
        </div>
      </DialogField>
      {error ? (
        <div
          role="alert"
          className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 leading-[1.5] text-danger-2"
        >
          {error}
        </div>
      ) : null}
    </Dialog>
  )
}
