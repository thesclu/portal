import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { needsApproval, useMe } from '../../app/session'
import { unwrap, type ActionSpec, type Values } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { ChevronDown, SearchIcon } from '../../components/icons'
import { Button, Callout, cx } from '../../components/ui'
import { statusText } from '../../lib/status'
import { useDefinitions, usePeople } from '../../lib/vocab'
import { CheckBox } from './parts'
import { SUMMARY_KEYS } from './stats'
import { sendOrSubmit } from '../../lib/outgoing'

type Row = Schemas['DocketRowOut']

/** The fields of 035 and 036: white, 12 px round, raised by a shadow rather than outlined. */
const RAISED = 'rounded-12 bg-card-white shadow-field'

export function useRefreshDocket() {
  const client = useQueryClient()
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ['get', '/api/records/requests'] }),
      client.invalidateQueries({ queryKey: ['get', '/api/records/requests/{request_id}'] }),
      client.invalidateQueries({ queryKey: ['get', '/api/records/home'] }),
      ...SUMMARY_KEYS.map((queryKey) => client.invalidateQueries({ queryKey: [...queryKey] })),
    ])
}

/** Runs one call per request; a refusal on one does not stop the others. Returns the refusals by
 *  tracking number, in the API's own words. */
async function eachOf(rows: Row[], call: (row: Row) => Promise<void>): Promise<string[]> {
  const failed: string[] = []
  for (const row of rows) {
    try {
      await call(row)
    } catch (caught) {
      failed.push(
        `${row.tracking_number}: ${caught instanceof Error ? caught.message : 'it was refused.'}`,
      )
    }
  }
  return failed
}

function Failures({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null
  return (
    <Callout tone="danger" role="alert">
      {lines.map((line) => (
        <div key={line}>{line}</div>
      ))}
    </Callout>
  )
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** 036: assign several. The person list with each one's open requests; one choice; and
 *  whether to tell them by email as well as in the portal. */
export function AssignDialog({ rows, onClose }: { rows: Row[] | null; onClose: () => void }) {
  return rows ? <AssignForm rows={rows} onClose={onClose} /> : null
}

function AssignForm({ rows, onClose }: { rows: Row[]; onClose: () => void }) {
  const people = usePeople()
  const refresh = useRefreshDocket()
  const [search, setSearch] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  // 036 draws "Tell them by email" ticked.
  const [byEmail, setByEmail] = useState(true)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string[]>([])
  const staff = (people.data ?? []).filter((p) => p.is_active && p.records_role !== 'requester')
  const shown = staff.filter((p) => p.name.toLowerCase().includes(search.trim().toLowerCase()))
  // "4 open": each person's open requests, as you may see them, in one read.
  const open = $api.useQuery('get', '/api/records/requests/assignee-counts')
  const openOf = (id: string) => (open.data ? (open.data[id] ?? 0) : undefined)

  async function assign() {
    setBusy(true)
    const refused = await eachOf(rows, async (row) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/assign', {
          params: { path: { request_id: row.id } },
          body: { user_id: chosen, notify: byEmail },
        }),
      )
    })
    await refresh()
    setBusy(false)
    if (refused.length === 0) onClose()
    else setFailed(refused)
  }

  return (
    <Dialog
      title={
        rows.length === 1 ? `Assign ${rows[0].tracking_number}` : `Assign ${rows.length} requests`
      }
      open
      onClose={onClose}
      width={440}
      top={90}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" pill busy={busy} disabled={!chosen} onClick={() => void assign()}>
            Assign
          </Button>
        </>
      }
    >
      <label className={cx(RAISED, 'mb-2.5 flex h-9 items-center gap-[9px] px-3 text-13')}>
        <SearchIcon size={15} width={2} stroke="var(--color-muted)" />
        <input
          autoFocus
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search people"
          aria-label="Search people"
          className="min-w-0 flex-1 bg-transparent text-ink outline-none placeholder:text-muted-2"
        />
      </label>
      <div role="radiogroup" aria-label="Assign to" className={cx(RAISED, 'overflow-hidden')}>
        {shown.map((person, index) => {
          const picked = chosen === person.id
          return (
            <div
              key={person.id}
              role="radio"
              aria-checked={picked}
              tabIndex={0}
              onClick={() => setChosen(person.id)}
              onKeyDown={(event) => {
                if (event.key === ' ' || event.key === 'Enter') {
                  event.preventDefault()
                  setChosen(person.id)
                }
              }}
              className={cx(
                'flex cursor-pointer items-center gap-2.5 px-3.5 py-2.5 text-125',
                index > 0 && 'border-t border-line-row-2',
                picked && 'bg-blue-tint-3',
              )}
            >
              <CheckBox
                checked={picked}
                label={`Assign to ${person.name}`}
                onChange={() => setChosen(person.id)}
              />
              <span className="min-w-0 flex-1 truncate text-ink">{person.name}</span>
              {openOf(person.id) !== undefined ? (
                <span className="whitespace-nowrap text-muted">{openOf(person.id)} open</span>
              ) : null}
            </div>
          )
        })}
        {shown.length === 0 ? (
          <p className="px-3.5 py-2.5 text-125 text-muted">Nobody by that name.</p>
        ) : null}
      </div>
      <label className="mt-1 flex cursor-pointer items-center gap-2.5 pt-3 pl-3.5 text-13 text-ink">
        <CheckBox
          checked={byEmail}
          label="Tell them by email"
          onChange={() => setByEmail((was) => !was)}
        />
        Tell them by email
      </label>
      <Failures lines={failed} />
    </Dialog>
  )
}

/** 035: change the status of several, with the reason recorded on each. */
export function ChangeStatusDialog({ rows, onClose }: { rows: Row[] | null; onClose: () => void }) {
  return rows ? <ChangeStatusForm rows={rows} onClose={onClose} /> : null
}

function ChangeStatusForm({ rows, onClose }: { rows: Row[]; onClose: () => void }) {
  const definitions = useDefinitions()
  const refresh = useRefreshDocket()
  const [status, setStatus] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string[]>([])
  const codes = (definitions.data?.statuses ?? []).map((s) => s.code)

  async function change() {
    setBusy(true)
    const refused = await eachOf(rows, async (row) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/status', {
          params: { path: { request_id: row.id } },
          body: { status, note: reason.trim() },
        }),
      )
    })
    await refresh()
    setBusy(false)
    if (refused.length === 0) onClose()
    else setFailed(refused)
  }

  return (
    <Dialog
      title={`Change status of ${plural(rows.length, 'request', 'requests')}`}
      open
      onClose={onClose}
      top={90}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button
            kind="primary"
            pill
            busy={busy}
            disabled={!status || !reason.trim()}
            onClick={() => void change()}
          >
            Change {rows.length}
          </Button>
        </>
      }
    >
      <label className="flex min-w-0 flex-col gap-1.5">
        <span className="text-125 text-ink-2">New status</span>
        <span className={cx(RAISED, 'relative flex h-[38px] items-center')}>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="h-full w-full appearance-none bg-transparent pr-8 pl-3 text-13 text-ink outline-none"
          >
            <option value="">Choose a status</option>
            {codes.map((code) => (
              <option key={code} value={code}>
                {statusText(code)}
              </option>
            ))}
          </select>
          <span className="pointer-events-none absolute right-3">
            <ChevronDown size={10} width={2.4} stroke="var(--color-ink-3)" />
          </span>
        </span>
      </label>
      {status === 'production_received' ? (
        <p className="text-12 text-ink-2">
          Only a person can mark a request fully produced. This is recorded as a manual override.
        </p>
      ) : null}
      <label className="flex min-w-0 flex-col gap-1.5">
        <span className="text-125 text-ink-2">
          Reason<span className="ml-1 text-danger">required</span>
        </span>
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Type reason"
          rows={1}
          className={cx(
            RAISED,
            'resize-none px-3.5 py-3 text-13 leading-body text-ink outline-none placeholder:text-muted-2',
          )}
        />
      </label>
      <Failures lines={failed} />
    </Dialog>
  )
}

/** A note on each request (the Notes tab's own kind of entry). */
export function noteSpec(rows: Row[], refresh: () => Promise<unknown>): ActionSpec {
  return {
    key: 'note',
    title: rows.length === 1 ? 'Add a note' : `Add a note to ${rows.length} requests`,
    subtitle: rows.length === 1 ? rows[0].tracking_number : undefined,
    submit: 'Add note',
    fields: [{ name: 'body', label: 'Note', type: 'textarea', required: true }],
    run: async (values: Values) => {
      const body = String(values.body ?? '').trim()
      const refused = await eachOf(rows, async (row) => {
        unwrap(
          await api.POST('/api/records/requests/{request_id}/events', {
            params: { path: { request_id: row.id } },
            body: {
              event_type: 'note',
              channel: 'system',
              direction: 'internal',
              occurred_at: new Date().toISOString(),
              display_body: body,
            },
          }),
        )
      })
      await refresh()
      if (refused.length > 0) throw new Error(refused.join(' '))
    },
  }
}

interface Draft {
  to: string[]
  cc: string[]
  bcc: string[]
  subject: string
  body: string
}

/** The follow-up for one request, rendered by the server from the organisation's own templates
 *  (Settings → Filing & deadlines) the way it renders an automatic one. Reading it saves nothing. */
function useDraftFor(row: Row): { draft: Draft | null; error: string | null } {
  const draft = useQuery({
    queryKey: ['followup-draft', row.id],
    staleTime: Infinity,
    queryFn: async () =>
      unwrap(
        await api.POST('/api/mail/requests/{request_id}/followup-draft', {
          params: { path: { request_id: row.id } },
        }),
      ),
  })
  if (draft.error) return { draft: null, error: 'Couldn’t load this request’s details.' }
  if (!draft.data) return { draft: null, error: null }
  return { error: null, draft: { ...draft.data, bcc: [] } }
}

/** 034: follow up on several, one agency at a time: send this one, or skip it, then the next. */
export function FollowUpDialog({ rows, onClose }: { rows: Row[] | null; onClose: () => void }) {
  return rows && rows.length > 0 ? <FollowUpForm rows={rows} onClose={onClose} /> : null
}

function FollowUpForm({ rows, onClose }: { rows: Row[]; onClose: () => void }) {
  const intern = needsApproval(useMe())
  const [index, setIndex] = useState(0)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const refresh = useRefreshDocket()
  const row = rows[index]
  const value = drafts[row.id] ?? null
  const allOverdue = rows.every((r) => (r.days_to_next_deadline ?? 0) < 0)
  const setDraft = (id: string, draft: Draft) => setDrafts((all) => ({ ...all, [id]: draft }))

  const next = () => {
    setError(null)
    if (index + 1 >= rows.length) {
      void refresh()
      onClose()
    } else setIndex(index + 1)
  }

  async function send() {
    if (!value) return
    setBusy(true)
    setError(null)
    try {
      const address = (list: string[]) => list.map((a) => ({ address: a }))
      const message = unwrap(
        await api.POST('/api/mail/messages', {
          body: {
            request_id: row.id,
            kind: 'followup',
            subject: value.subject,
            body_text: value.body,
            to: address(value.to),
            cc: address(value.cc),
            bcc: address(value.bcc),
            attachment_ids: [],
            attach_letter: false,
          },
        }),
      )
      // An intern's follow-up waits for an approver (P20h).
      await sendOrSubmit(message.id, intern)
      next()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'It was not sent.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      title={
        allOverdue
          ? `Follow up on ${rows.length} overdue`
          : `Follow up on ${plural(rows.length, 'request', 'requests')}`
      }
      open
      onClose={onClose}
      width={760}
      top={90}
      footer={
        <>
          <span className="mr-auto text-125 text-muted">
            {index + 1} of {rows.length}
          </span>
          <Button kind="text" onClick={next}>
            Skip
          </Button>
          <Button
            kind="primary"
            pill
            busy={busy}
            disabled={!value || value.to.length === 0 || !value.subject.trim()}
            onClick={() => void send()}
          >
            {intern ? 'Submit and next' : 'Send and next'}
          </Button>
        </>
      }
    >
      <div className="flex gap-[18px]">
        <div className="flex flex-[0_0_200px] flex-col gap-0.5">
          {rows.map((r, i) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setIndex(i)}
              className={cx(
                'rounded-8 px-3 py-[9px] text-left text-125',
                i === index ? 'bg-hover font-medium text-ink' : 'text-ink-2 hover:bg-page',
              )}
            >
              {r.agency_name}
              <div className="text-115 font-normal text-muted">
                {(r.days_to_next_deadline ?? 0) < 0
                  ? `${plural(-(r.days_to_next_deadline ?? 0), 'day', 'days')} overdue`
                  : r.tracking_number}
              </div>
            </button>
          ))}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <FollowUpComposer
            key={row.id}
            row={row}
            value={value}
            onChange={(draft) => setDraft(row.id, draft)}
          />
        </div>
      </div>
      {error ? (
        <Callout tone="danger" role="alert">
          {error}
        </Callout>
      ) : null}
    </Dialog>
  )
}

function FollowUpComposer({
  row,
  value,
  onChange,
}: {
  row: Row
  value: Draft | null
  onChange: (draft: Draft) => void
}) {
  const { draft, error: loadError } = useDraftFor(row)
  const [copies, setCopies] = useState(false)
  useEffect(() => {
    if (draft && !value) onChange(draft)
  }, [draft, value, onChange])
  const set = (patch: Partial<Draft>) => value && onChange({ ...value, ...patch })

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-12 bg-card-white shadow-field">
      <div className="flex flex-wrap items-center gap-2 border-b border-hairline px-4 py-2.5">
        <span className="w-[22px] text-125 text-muted">To</span>
        {(value?.to ?? []).map((address) => (
          <span
            key={address}
            className="inline-flex h-6 items-center gap-1.5 rounded-pill bg-hover pr-1.5 pl-2.5 text-12 whitespace-nowrap text-ink"
          >
            {address}
            <button
              type="button"
              aria-label={`Remove ${address}`}
              onClick={() => set({ to: (value?.to ?? []).filter((a) => a !== address) })}
              className="text-13 leading-none text-muted hover:text-ink"
            >
              ×
            </button>
          </span>
        ))}
        {value && value.to.length === 0 ? (
          <input
            aria-label="To"
            placeholder="An address, then Enter"
            onKeyDown={(event) => {
              const typed = event.currentTarget.value.trim()
              if (event.key === 'Enter' && typed) {
                event.preventDefault()
                set({ to: [typed] })
              }
            }}
            className="min-w-0 flex-1 bg-transparent text-12 text-ink outline-none placeholder:text-muted-2"
          />
        ) : null}
        <span className="ml-auto flex gap-3 text-12 text-muted">
          <button type="button" onClick={() => setCopies((was) => !was)} className="hover:text-ink">
            Cc
          </button>
          <button type="button" onClick={() => setCopies((was) => !was)} className="hover:text-ink">
            Bcc
          </button>
        </span>
      </div>
      {copies && value ? (
        <div className="flex flex-col gap-1 border-b border-hairline px-4 py-2 text-12">
          {(['cc', 'bcc'] as const).map((field) => (
            <label key={field} className="flex items-center gap-2">
              <span className="w-[22px] text-muted capitalize">{field}</span>
              <input
                value={value[field].join(', ')}
                onChange={(event) =>
                  set({
                    [field]: event.target.value
                      .split(',')
                      .map((a) => a.trim())
                      .filter(Boolean),
                  })
                }
                className="min-w-0 flex-1 bg-transparent text-ink outline-none"
              />
            </label>
          ))}
        </div>
      ) : null}
      {loadError ? (
        <p role="alert" className="px-4 py-3.5 text-13 text-danger">
          {loadError}
        </p>
      ) : value ? (
        <textarea
          aria-label="Message"
          value={value.body}
          onChange={(event) => set({ body: event.target.value })}
          // As tall as its text, as 034 draws it.
          className="max-h-[360px] min-h-[64px] flex-1 resize-none px-4 py-3.5 text-13 leading-prose text-ink outline-none [field-sizing:content]"
        />
      ) : (
        <p className="px-4 py-3.5 text-13 text-muted">Preparing the follow-up…</p>
      )}
    </div>
  )
}

/** The context the request page's own actions take (`request/actions.tsx`), for the row menu. */
export function useActionContext() {
  const me = useMe()
  const definitions = useDefinitions()
  const people = usePeople()
  return async (id: string) => {
    const request = unwrap(
      await api.GET('/api/records/requests/{request_id}', {
        params: { path: { request_id: id } },
      }),
    )
    if (!definitions.data) throw new Error('Still loading the portal’s vocabulary; try again.')
    return {
      request,
      definitions: definitions.data,
      people: people.data ?? [],
      me,
      attachments: [],
    }
  }
}
