import { useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { useMe } from '../../app/session'
import { Dialog } from '../../components/Dialog'
import { Box, DialogField, RaisedInput, Segmented } from '../../components/Fields'
import { Loading } from '../../components/States'
import { Button, cx } from '../../components/ui'
import { formatShortInstant } from '../../lib/dates'
import { statusWords } from '../../lib/status'
import { fileSize } from '../../lib/vocab'

type Event = Schemas['EventOut']
type Sheet = Schemas['ReviewSheetOut']

const DISPOSITIONS = [
  { value: 'produced', label: 'Produced' },
  { value: 'partly', label: 'Partly' },
  { value: 'withheld', label: 'Withheld' },
  { value: 'not_yet', label: 'Not yet' },
]

interface Row {
  disposition: string
  exemption: string
  where: string
  note: string
}

const LABEL = 'mb-1 text-105 font-semibold tracking-label text-muted uppercase'

/** 079: review a production. The files at the left, ticked when they are records; every item at
 *  the right with its disposition, the records it got, what was cited when withheld; the status
 *  it becomes in the footer before saving. */
export function ReviewProduction({
  requestId,
  event,
  sender,
  onClose,
  onDone,
}: {
  requestId: string
  event: Event
  /** Who it came from (079 "· L. Alvarez"). */
  sender?: string
  onClose: () => void
  onDone: () => void
}) {
  const me = useMe()
  const sheet = $api.useQuery('get', '/api/records/events/{event_id}/review-sheet', {
    params: { path: { event_id: event.id } },
  })
  const files = sheet.data?.files.length
  return sheet.isSuccess ? (
    <Form
      requestId={requestId}
      sheet={sheet.data}
      subtitle={[
        `Received ${formatShortInstant(event.occurred_at, me.timezone)}`,
        files !== undefined ? `${files} ${files === 1 ? 'file' : 'files'}` : null,
        sender,
      ]
        .filter(Boolean)
        .join(' · ')}
      onClose={onClose}
      onDone={onDone}
    />
  ) : (
    <Dialog title="Review production" open onClose={onClose} width={920} top={60}>
      {sheet.isError ? (
        <div className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2">
          {messageOf(sheet.error)}
        </div>
      ) : (
        <Loading what="Loading the files and items" />
      )}
    </Dialog>
  )
}

function Form({
  requestId,
  sheet,
  subtitle,
  onClose,
  onDone,
}: {
  requestId: string
  sheet: Sheet
  subtitle: string
  onClose: () => void
  onDone: () => void
}) {
  const [rows, setRows] = useState<Record<string, Row>>(() =>
    Object.fromEntries(
      sheet.items.map((item) => {
        const standing = sheet.current[item.id]
        return [
          item.id,
          {
            disposition: standing?.disposition ?? '',
            exemption: standing?.exemption_cited ?? '',
            where: standing?.cited_where ?? '',
            note: standing?.note ?? '',
          },
        ]
      }),
    ),
  )
  // A ticked file is a record; its value is the item it answers, '' for the whole request.
  const [files, setFiles] = useState<Record<string, string | null>>(() =>
    Object.fromEntries(
      sheet.files.map((file) => [
        file.id,
        file.id in sheet.linked_files
          ? (sheet.linked_files[file.id] ?? '')
          : file.role === 'produced_record'
            ? ''
            : null,
      ]),
    ),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const set = (itemId: string, patch: Partial<Row>) =>
    setRows((current) => ({ ...current, [itemId]: { ...current[itemId], ...patch } }))
  const undecided = sheet.items.filter((item) => !rows[item.id].disposition)
  const complete =
    sheet.items.length > 0 && sheet.items.every((item) => rows[item.id].disposition === 'produced')
  const becomes = statusWords(complete ? 'production_received' : 'partial_production')
  const records = sheet.files.filter((file) => files[file.id] !== null)

  async function save() {
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/records/requests/{request_id}/reviews', {
      params: { path: { request_id: requestId } },
      body: {
        event_id: sheet.event_id,
        rows: sheet.items.map((item) => ({
          request_item_id: item.id,
          disposition: rows[item.id].disposition,
          exemption_cited: rows[item.id].exemption.trim() || null,
          cited_where: rows[item.id].where.trim() || null,
          note: rows[item.id].note.trim() || null,
        })),
        files: Object.entries(files)
          .filter(([, item]) => item !== null)
          .map(([attachmentId, item]) => ({
            attachment_id: attachmentId,
            request_item_id: item || null,
          })),
        note: null,
      },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    onDone()
  }

  return (
    <Dialog
      title="Review production"
      subtitle={subtitle}
      open
      onClose={onClose}
      width={920}
      top={60}
      footer={
        <>
          <span className="mr-auto text-125 text-ink-2">
            {undecided.length > 0 ? (
              `${undecided.length === 1 ? 'One item' : `${undecided.length} items`} still need a disposition.`
            ) : (
              <>
                Status becomes{' '}
                <span className="text-muted">
                  {becomes.family}
                  {becomes.qualifier ? ' · ' : ''}
                </span>
                <span className="font-medium text-ink">{becomes.qualifier}</span>
                {complete ? ' — you are marking it fully produced' : ''}
              </>
            )}
          </span>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button
            kind="primary"
            pill
            busy={busy}
            disabled={undecided.length > 0}
            onClick={() => void save()}
          >
            Save review
          </Button>
        </>
      }
    >
      {error ? (
        <div
          role="alert"
          className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 leading-[1.5] text-danger-2"
        >
          {error}
        </div>
      ) : null}
      <div className="flex gap-[26px]">
        <div className="flex-[0_0_260px]">
          <div className={LABEL}>Files · tick the records</div>
          {sheet.files.length === 0 ? (
            <p className="py-2 text-125 text-muted">This message has no files.</p>
          ) : null}
          {sheet.files.map((file) => {
            const on = files[file.id] !== null
            return (
              <label
                key={file.id}
                className="flex cursor-pointer items-center gap-2.5 border-b border-line-row-2 py-[9px]"
              >
                <input
                  type="checkbox"
                  className="sr-only"
                  aria-label={`${file.filename} is a record`}
                  checked={on}
                  onChange={(event) =>
                    setFiles((current) => ({
                      ...current,
                      [file.id]: event.target.checked ? '' : null,
                    }))
                  }
                />
                <Box checked={on} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-125 text-ink">{file.filename}</span>
                  <span className="block text-115 text-muted">
                    {fileSize(file.size_bytes)}
                    {file.page_count
                      ? ` · ${file.page_count} ${file.page_count === 1 ? 'page' : 'pages'}`
                      : ''}
                  </span>
                </span>
              </label>
            )
          })}
        </div>
        <div className="min-w-0 flex-1">
          <div className={LABEL}>Items</div>
          {sheet.items.length === 0 ? (
            <p className="py-3 text-125 text-muted">
              This request has no separate items. Saving records the production against the whole
              request; the status becomes a partial production until a person marks it complete.
            </p>
          ) : null}
          {sheet.items.map((item) => {
            const row = rows[item.id]
            const got = row.disposition === 'produced' || row.disposition === 'partly'
            const cited = row.disposition === 'withheld' || row.disposition === 'partly'
            return (
              <div
                key={item.id}
                className="flex flex-col gap-[9px] border-b border-line-row-2 py-3"
              >
                <div className="flex items-start gap-3">
                  <span className="w-4 shrink-0 text-125 text-muted">{item.item_number}</span>
                  <span className="min-w-0 flex-1 text-13 leading-[1.45] text-ink">
                    {item.description}
                  </span>
                  <Segmented
                    label={`Item ${item.item_number}`}
                    options={DISPOSITIONS}
                    value={row.disposition}
                    onChange={(value) => set(item.id, { disposition: value })}
                  />
                </div>
                {row.disposition ? (
                  <div className="ml-7 flex flex-col gap-1.5">
                    {got && records.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {records.map((file) => {
                          const linked = files[file.id] === item.id
                          return (
                            <button
                              key={file.id}
                              type="button"
                              aria-pressed={linked}
                              title={linked ? 'Answers this item' : 'Tie it to this item'}
                              onClick={() =>
                                setFiles((current) => ({
                                  ...current,
                                  [file.id]: linked ? '' : item.id,
                                }))
                              }
                              className={cx(
                                'inline-flex h-6 items-center rounded-6 px-[9px] text-115',
                                linked
                                  ? 'bg-page text-ink-2'
                                  : 'border border-line bg-card-white text-muted',
                              )}
                            >
                              {file.filename}
                            </button>
                          )
                        })}
                      </div>
                    ) : null}
                    {cited ? (
                      <div className="flex gap-3">
                        <div className="min-w-0 flex-1">
                          <DialogField label="Exemption they cited">
                            <RaisedInput
                              compact
                              value={row.exemption}
                              onChange={(event) => set(item.id, { exemption: event.target.value })}
                            />
                          </DialogField>
                        </div>
                        <div className="min-w-0 flex-1">
                          <DialogField label="Page in their letter">
                            <RaisedInput
                              compact
                              value={row.where}
                              onChange={(event) => set(item.id, { where: event.target.value })}
                            />
                          </DialogField>
                        </div>
                      </div>
                    ) : null}
                    {row.disposition !== 'produced' || row.note ? (
                      <input
                        aria-label={`Note on item ${item.item_number}`}
                        placeholder="Add a note"
                        value={row.note}
                        onChange={(event) => set(item.id, { note: event.target.value })}
                        className="bg-transparent text-12 text-ink-2 outline-none placeholder:text-muted-2"
                      />
                    ) : null}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      </div>
    </Dialog>
  )
}
