/**
 * "Add a document" (061): a scan, a portal download or an email file onto a request. 520 px,
 * 70 px from the top; the drop zone (dashed, on the cell grey: "Drop files or browse", "PDF,
 * images, .eml or .msg"), each file chosen as a raised row with its size and ×, "What it is" and
 * "Received on" side by side, and "Attach to" (the request, by tracking number and agency). Add
 * posts the files to the request's documents door (P8b), which makes one source, its files and
 * one inbound event; saying what it means is "Record as…" afterwards, on the request.
 *
 * Opened from the Inbox (061 draws it there) it asks which request; opened from a request page
 * ("Upload a file", 069) the request is already chosen. "3 pages" on a file is not shown: the
 * browser does not count a PDF's pages.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState, type DragEvent } from 'react'

import { api } from '../api/client'
import { $api, messageOf } from '../api/query'
import { instantOfParts, unwrap } from './ActionDialog'
import { Dialog } from './Dialog'
import { DialogField, RaisedInput, RaisedSelect, ReadonlyBox } from './Fields'
import { CloseIcon } from './icons'
import { Button, Callout, cx } from './ui'
import { fileSize } from '../lib/vocab'

export const DOCUMENT_KINDS = [
  { value: 'scanned_mail', label: 'Scanned mail' },
  { value: 'portal_capture', label: 'Portal download' },
  { value: 'imported_eml', label: 'Email file' },
] as const

/** Today in the reader's own calendar, the shape a date field wants. */
function today(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export function AddDocument({
  open,
  onClose,
  onAdded,
  request,
}: {
  open: boolean
  onClose: () => void
  onAdded?: (requestId: string) => void
  /** The request it goes on, when the dialog opens from one; else a picker is drawn. */
  request?: { id: string; label: string }
}) {
  return (
    <Dialog
      title="Add a document"
      subtitle="Scans, portal downloads and email files"
      open={open}
      onClose={onClose}
      width={520}
      top={70}
    >
      {open ? <AddDocumentBody request={request} onClose={onClose} onAdded={onAdded} /> : null}
    </Dialog>
  )
}

function AddDocumentBody({
  request,
  onClose,
  onAdded,
}: {
  request?: { id: string; label: string }
  onClose: () => void
  onAdded?: (requestId: string) => void
}) {
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [kind, setKind] = useState<string>('scanned_mail')
  const [on, setOn] = useState(today)
  const [target, setTarget] = useState(request?.id ?? '')
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requests = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { limit: 200, sort: 'newest' } } },
    { enabled: !request },
  )
  const take = (list: FileList | null) => {
    if (!list) return
    const incoming = [...list]
    setFiles((was) => [
      ...was,
      ...incoming.filter((f) => !was.some((w) => w.name === f.name && w.size === f.size)),
    ])
  }
  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setOver(false)
    take(event.dataTransfer.files)
  }
  async function add() {
    if (!target || files.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.set('source_type', kind)
      form.set('received_at', instantOfParts(on, '12:00'))
      form.set('display_body', '')
      for (const file of files) form.append('files', file)
      unwrap(
        await api.POST('/api/records/requests/{request_id}/documents', {
          params: { path: { request_id: target } },
          body: form as never,
          bodySerializer: (payload) => payload as unknown as FormData,
        }),
      )
      await client.invalidateQueries({
        predicate: (query) => {
          const path = String(query.queryKey[1] ?? '')
          return (
            path.startsWith('/api/records/requests/{request_id}') || path === '/api/records/inbox'
          )
        },
      })
      onAdded?.(target)
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : messageOf(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {error ? (
        <Callout tone="danger" role="alert">
          {error}
        </Callout>
      ) : null}
      <div
        onDragOver={(event) => {
          event.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={cx(
          'flex flex-col items-center gap-1.5 rounded-12 border-[1.5px] border-dashed p-[22px] leading-[normal]',
          over ? 'border-brand bg-blue-tint-3' : 'border-line-control bg-surface-row',
        )}
      >
        <span className="text-13 text-ink">
          Drop files or{' '}
          <button
            type="button"
            className="font-medium text-brand hover:text-brand-dark"
            onClick={() => input.current?.click()}
          >
            browse
          </button>
        </span>
        <span className="text-12 text-muted">PDF, images, .eml or .msg</span>
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,image/*,.eml,.msg,application/pdf,message/rfc822"
          className="sr-only"
          aria-label="Files to add"
          onChange={(event) => {
            take(event.target.files)
            event.target.value = ''
          }}
        />
      </div>
      {files.map((file) => (
        <div
          key={`${file.name}-${file.size}`}
          className="flex items-center gap-2.5 rounded-12 bg-card-white px-3 py-2.5 text-125 leading-[normal] shadow-field"
        >
          <span className="min-w-0 flex-1 truncate text-ink">{file.name}</span>
          <span className="shrink-0 text-muted">{fileSize(file.size)}</span>
          <button
            type="button"
            aria-label={`Take ${file.name} off`}
            onClick={() => setFiles((was) => was.filter((f) => f !== file))}
            className="inline-flex rounded-4 hover:bg-hover"
          >
            <CloseIcon size={14} width={2.2} stroke="var(--color-muted)" />
          </button>
        </div>
      ))}
      <div className="flex gap-3">
        <div className="min-w-0 flex-1">
          <DialogField label="What it is">
            <RaisedSelect compact value={kind} onChange={(event) => setKind(event.target.value)}>
              {DOCUMENT_KINDS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </RaisedSelect>
          </DialogField>
        </div>
        <div className="min-w-0 flex-1">
          <DialogField label="Received on">
            <RaisedInput
              compact
              type="date"
              required
              value={on}
              max={today()}
              onChange={(event) => setOn(event.target.value)}
            />
          </DialogField>
        </div>
      </div>
      <DialogField label="Attach to">
        {request ? (
          <ReadonlyBox compact>{request.label}</ReadonlyBox>
        ) : (
          <RaisedSelect
            compact
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            aria-invalid={error && !target ? true : undefined}
          >
            <option value="">
              {requests.isPending ? 'Loading requests…' : 'Choose a request…'}
            </option>
            {(requests.data?.items ?? []).map((row) => (
              <option key={row.id} value={row.id}>
                {row.tracking_number} · {row.agency_name}
              </option>
            ))}
          </RaisedSelect>
        )}
      </DialogField>
      <div className="-mx-[22px] mt-1.5 -mb-5 flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">
        <Button kind="text" pill onClick={onClose}>
          Cancel
        </Button>
        <Button
          kind="primary"
          pill
          busy={busy}
          disabled={!target || files.length === 0 || !on}
          onClick={() => void add()}
        >
          Add
        </Button>
      </div>
    </>
  )
}
