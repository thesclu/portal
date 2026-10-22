/**
 * What can be done to a message from the Inbox, each described as data for <ActionDialog> and
 * drawn in the Inbox's dialog look (159): attach it, mark it handled without attaching, report it,
 * move it to Flagged, ask a teammate, hand it to someone, say what it is ("Record as…"), mark its
 * files as records. A message that landed on a request also gets the request page's own dialogs
 * (`request/actions.tsx`): a task, the agency's reference number, a deadline.
 *
 * Each `run` is one or a few API calls; the server decides who may, and says why not.
 */
import { useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { unwrap, type ActionSpec, type Values } from '../../components/ActionDialog'
import { SearchIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { statusText } from '../../lib/status'
import { humanize } from '../../lib/vocab'
import { senderOf, subjectOf, type Row } from './model'

type Definitions = Schemas['DefinitionsOut']
type Person = Schemas['UserOut']
type Candidate = Schemas['CandidateOut']

const text = (values: Values, name: string) => String(values[name] ?? '').trim()
const staff = (people: Person[]) =>
  people
    .filter((p) => p.is_active && p.records_role !== 'requester')
    .map((p) => ({ value: p.id, label: p.name }))

async function reviewed(sourceId: string) {
  unwrap(
    await api.POST('/api/records/sources/{source_id}/reviewed', {
      params: { path: { source_id: sourceId } },
    }),
  )
}

/* ── Attach to a request (159 · 2) ─────────────────────────────────────── */

/** The picker of 159 · 2: a search, the requests the message may be about first, then others. */
function RequestChooser({
  value,
  onChange,
  suggested,
  exclude,
}: {
  value: string
  onChange: (id: string) => void
  suggested: Candidate[]
  exclude: string[]
}) {
  const [query, setQuery] = useState('')
  const q = query.trim()
  const found = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { q: q || undefined, limit: 5, sort: 'newest' } } },
    { enabled: q.length > 0 },
  )
  const suggestedIds = new Set(suggested.map((c) => c.request_id))
  const others = (found.data?.items ?? []).filter(
    (row) => !suggestedIds.has(row.id) && !exclude.includes(row.id),
  )
  const rows = [
    ...suggested
      .filter((c) => !exclude.includes(c.request_id))
      .map((c) => ({
        id: c.request_id,
        subject: c.subject,
        meta: `${c.agency_name} · ${c.tracking_number}`,
        suggested: true,
      })),
    ...others.map((row) => ({
      id: row.id,
      subject: row.subject,
      meta: `${row.agency_name} · ${row.tracking_number}`,
      suggested: false,
    })),
  ]
  const firstOther = rows.findIndex((row) => !row.suggested)
  return (
    <div className="flex flex-col gap-[11px]">
      <label className="box-border flex h-[38px] items-center gap-[9px] rounded-10 border-[1.5px] border-line-2 bg-card-white px-3 focus-within:border-brand focus-within:shadow-ring-focus">
        <SearchIcon size={14} width={2} stroke="var(--color-muted)" />
        <input
          aria-label="Find a request"
          placeholder="A tracking number, a subject or an agency"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-muted-2"
        />
      </label>
      {rows.length === 0 ? (
        <p className="text-125 text-muted">
          {q ? (found.isPending ? 'Searching…' : 'No request matches.') : 'Find the request above.'}
        </p>
      ) : (
        <div className="overflow-hidden rounded-10 border-[1.5px] border-line">
          {suggested.length > 0 ? (
            <div className="bg-row-alt px-3.5 py-2 text-105 font-semibold tracking-label text-muted uppercase">
              Suggested
            </div>
          ) : null}
          {rows.map((row, index) => (
            <div key={row.id}>
              {index === firstOther && firstOther > 0 ? (
                <div className="border-t border-line-row-3 bg-surface-row px-3.5 py-2 text-105 font-semibold tracking-label text-muted uppercase">
                  Other matches
                </div>
              ) : null}
              <label
                className={cx(
                  'flex cursor-pointer items-center gap-[11px] border-t border-line-row-3 px-3.5 py-2.5',
                  index === 0 && suggested.length === 0 && 'border-t-0',
                  value === row.id && 'bg-blue-tint-4',
                )}
              >
                <input
                  type="radio"
                  name="attach-target"
                  className="sr-only"
                  checked={value === row.id}
                  onChange={() => onChange(row.id)}
                />
                <span
                  aria-hidden
                  className={cx(
                    'box-border size-[15px] shrink-0 rounded-pill',
                    value === row.id
                      ? 'border-[4.5px] border-brand'
                      : 'border-[1.5px] border-line-control',
                  )}
                />
                <span className="flex min-w-0 flex-1 flex-col gap-px">
                  <span className="truncate text-125 text-ink">{row.subject}</span>
                  <span className="text-115 text-muted">{row.meta}</span>
                </span>
                {row.suggested ? (
                  <span className="text-11 font-medium text-ok">Named in the message</span>
                ) : null}
              </label>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function attachTo(row: Row, candidates: Candidate[], move = false): ActionSpec {
  const event = row.events[0]
  return {
    key: `attach-${row.source.id}-${move ? 'move' : 'add'}`,
    look: 'inbox',
    width: 500,
    title: event
      ? move
        ? 'Belongs to a different request'
        : 'Attach to another request'
      : 'Attach to a request',
    subtitle: `${subjectOf(row)} · ${senderOf(row)}`,
    submit: move ? 'Move it' : 'Attach',
    fields: [
      {
        name: 'target',
        label: 'Request',
        type: 'custom',
        required: true,
        initial: candidates.find((c) => !row.events.some((e) => e.request_id === c.request_id))
          ?.request_id,
        render: (value, onChange) => (
          <RequestChooser
            value={typeof value === 'string' ? value : ''}
            onChange={onChange}
            suggested={candidates}
            exclude={row.events.map((e) => e.request_id)}
          />
        ),
      },
      // P23b (075): a move says why, written where the message is crossed out.
      ...(move && event
        ? [
            {
              name: 'reason',
              label: 'Reason',
              type: 'textarea' as const,
              minHeight: 56,
              required: true,
              marked: true,
            },
          ]
        : []),
    ],
    footnote: () =>
      event
        ? move
          ? `Crossed out on ${event.tracking_number}, added to the one chosen`
          : `Stays on ${event.tracking_number} too`
        : null,
    run: async (values) => {
      const target = text(values, 'target')
      if (!target) throw new Error('Choose the request this message is about.')
      if (event) {
        unwrap(
          await api.POST('/api/records/sources/{source_id}/assign', {
            params: { path: { source_id: row.source.id } },
            body: {
              request_id: target,
              mode: move ? 'belongs_here_instead' : 'also_applies',
              from_event_id: event.id,
              reason: move ? text(values, 'reason') || null : null,
            },
          }),
        )
        return
      }
      // Nothing to copy from: it goes on the request as correspondence that arrived by email.
      unwrap(
        await api.POST('/api/records/requests/{request_id}/events', {
          params: { path: { request_id: target } },
          body: {
            event_type: 'correspondence',
            channel: 'email',
            direction: 'inbound',
            occurred_at: row.source.captured_at,
            display_body: subjectOf(row),
            source_id: row.source.id,
          },
        }),
      )
      await reviewed(row.source.id)
    },
  }
}

/* ── Handled without attaching, suspicious, automated (159 · 9, 10) ──────── */

export function resolve(
  row: Row,
  definitions: Definitions,
  preset?: 'automated_reply' | 'suspicious',
): ActionSpec {
  const suspicious = preset === 'suspicious'
  return {
    key: `resolve-${row.source.id}-${preset ?? ''}`,
    look: 'inbox',
    title: suspicious
      ? 'Report as suspicious'
      : preset === 'automated_reply'
        ? 'Mark as automated reply'
        : 'Mark handled without attaching',
    subtitle: subjectOf(row),
    submit: suspicious ? 'Report' : 'Mark handled',
    danger: !preset || suspicious,
    fields: [
      {
        name: 'resolution',
        label: suspicious ? 'What looks wrong' : 'Reason',
        type: 'select',
        required: true,
        initial: preset ?? '',
        options: definitions.resolutions.map((value) => ({ value, label: humanize(value) })),
      },
      {
        name: 'note',
        label: 'Details',
        type: 'textarea',
        minHeight: 44,
        hint: 'Required when the reason is Other.',
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/sources/{source_id}/resolve', {
          params: { path: { source_id: row.source.id } },
          body: { resolution: text(values, 'resolution'), note: text(values, 'note') || null },
        }),
      )
    },
  }
}

/* ── Move to Flagged ─────────────────────────────────────────────────── */

export function flag(row: Row, definitions: Definitions): ActionSpec {
  return {
    key: `flag-${row.source.id}`,
    look: 'inbox',
    title: 'Move to Flagged',
    subtitle: subjectOf(row),
    submit: 'Move to Flagged',
    fields: [
      {
        name: 'reason',
        label: 'Why',
        type: 'select',
        initial: 'other',
        options: definitions.flag_reasons.map((value) => ({ value, label: humanize(value) })),
      },
      {
        name: 'note',
        label: 'What needs a decision',
        type: 'textarea',
        required: true,
        minHeight: 52,
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/sources/{source_id}/flag', {
          params: { path: { source_id: row.source.id } },
          body: { reason: text(values, 'reason'), note: text(values, 'note') },
        }),
      )
    },
  }
}

/* ── Ask a teammate (159 · 7), assign (159 · 3) ──────────────────────── */

export function askTeammate(row: Row, people: Person[], me: string): ActionSpec {
  return {
    key: `ask-${row.source.id}`,
    look: 'inbox',
    title: 'Ask a teammate',
    subtitle: subjectOf(row),
    submit: 'Send for review',
    fields: [
      {
        name: 'user',
        label: 'Reviewer',
        type: 'select',
        required: true,
        options: staff(people).filter((p) => p.value !== me),
      },
      {
        name: 'note',
        label: 'What should they look at?',
        type: 'textarea',
        required: true,
        minHeight: 64,
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/core/reviews', {
          body: {
            subject_type: 'source',
            subject_id: row.source.id,
            kind: 'question',
            note: text(values, 'note'),
            assigned_to_user_id: text(values, 'user'),
          },
        }),
      )
    },
  }
}

export function assignTo(row: Row, people: Person[]): ActionSpec {
  return {
    key: `assignee-${row.source.id}`,
    look: 'inbox',
    width: 420,
    title: 'Assign to…',
    subtitle: subjectOf(row),
    submit: 'Assign',
    fields: [
      {
        name: 'user',
        label: 'Who is dealing with this message',
        type: 'select',
        initial: row.source.assigned_to_user_id ?? '',
        options: [{ value: '', label: 'Nobody (unassign)' }, ...staff(people)],
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/sources/{source_id}/assignee', {
          params: { path: { source_id: row.source.id } },
          body: { user_id: text(values, 'user') || null },
        }),
      )
    },
  }
}

/** A task belongs to a request: for a message on none, the dialog says so (159 · 4). */
export function taskNeedsRequest(row: Row): ActionSpec {
  return {
    key: `task-${row.source.id}`,
    look: 'inbox',
    title: 'Create task',
    subtitle: `From: ${subjectOf(row)}`,
    submit: 'Create task',
    refused: 'A task belongs to a request. Attach this message to its request first.',
    fields: [],
    run: async () => undefined,
  }
}

/* ── Record as… (158, 159 · 1, 11, 12) ───────────────────────────────── */

/** What recording it this way would change, asked of the server before anything is written. */
function RecordPreview({ eventId, kind }: { eventId: string; kind: string }) {
  const preview = $api.useQuery('get', '/api/records/events/{event_id}/classify', {
    params: { path: { event_id: eventId }, query: { kind } },
  })
  if (preview.isPending) return <p className="text-125 text-muted">Working out what it changes…</p>
  if (preview.isError) return <p className="text-125 text-danger">{messageOf(preview.error)}</p>
  const p = preview.data
  const rows: Array<[string, React.ReactNode]> = []
  if (p.status_to && p.status_to !== p.status_from)
    rows.push([
      'Status',
      <>
        <span className="text-muted line-through">{statusText(p.status_from)}</span> →{' '}
        {statusText(p.status_to)}
      </>,
    ])
  else rows.push(['Status', 'Stays as it is'])
  if (p.task) rows.push(['Task', p.task.description])
  if (p.status_reason === 'manual_override')
    rows.push(['Note', 'Only a person can mark a request fully produced; a note is required.'])
  return (
    <div>
      <p className="mb-1 text-125 text-ink-2">This will change the request:</p>
      {rows.map(([label, value]) => (
        <div
          key={label}
          className="grid grid-cols-[78px_1fr] gap-x-3 gap-y-1 border-t border-line-row-3 py-2.5 text-125"
        >
          <span className="text-muted">{label}</span>
          <span className="text-ink">{value}</span>
        </div>
      ))}
    </div>
  )
}

export function recordAs(row: Row, definitions: Definitions, kind: string): ActionSpec {
  const event = row.events[0]
  const entry = definitions.classifications.find((k) => k.kind === kind)
  const label = entry?.label ?? humanize(kind)
  return {
    key: `record-${row.source.id}-${kind}`,
    look: 'inbox',
    width: entry?.takes_records ? 500 : 460,
    title: `Record as ${label.toLowerCase()}`,
    subtitle: event ? `${event.tracking_number} · ${event.agency_name}` : subjectOf(row),
    intro: event ? <RecordPreview eventId={event.id} kind={kind} /> : undefined,
    submit: 'Record',
    fields: [
      ...(entry?.takes_records
        ? [
            {
              name: 'records',
              label: 'Which attachments are records?',
              type: 'checks' as const,
              initial: row.attachments.map((file) => file.id),
              options: row.attachments.map((file) => ({ value: file.id, label: file.filename })),
            },
          ]
        : []),
      ...(entry?.takes_fee
        ? [{ name: 'amount', label: 'Amount quoted, in dollars', type: 'number' as const }]
        : []),
      {
        name: 'note',
        label: entry?.note_required ? 'Note' : 'Note (optional)',
        type: 'textarea',
        required: Boolean(entry?.note_required || entry?.through_override),
        minHeight: 44,
        placeholder: 'Add a note for the request history',
      },
    ],
    run: async (values) => {
      if (!event) throw new Error('Attach this message to its request first.')
      const amount = text(values, 'amount')
      unwrap(
        await api.POST('/api/records/events/{event_id}/classify', {
          params: { path: { event_id: event.id } },
          body: {
            kind,
            note: text(values, 'note') || null,
            fee:
              entry?.takes_fee && amount
                ? { amount_cents: Math.round(Number(amount) * 100) }
                : null,
            record_attachment_ids: (values.records as string[]) ?? [],
            agency_reference_number: null,
          },
        }),
      )
      await reviewed(row.source.id)
    },
  }
}

export function markRecords(row: Row): ActionSpec {
  return {
    key: `records-${row.source.id}`,
    look: 'inbox',
    title: 'Mark attachments as records',
    subtitle: subjectOf(row),
    intro: 'Tick the files that are the records the agency produced.',
    submit: 'Save',
    fields: [
      {
        name: 'records',
        label: 'Files',
        type: 'checks',
        initial: row.attachments.filter((f) => f.role === 'produced_record').map((f) => f.id),
        options: row.attachments.map((file) => ({ value: file.id, label: file.filename })),
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/sources/{source_id}/records', {
          params: { path: { source_id: row.source.id } },
          body: { record_attachment_ids: (values.records as string[]) ?? [] },
        }),
      )
    },
  }
}

/* ── Start a new request from this (159 · 8) ─────────────────────────── */

const FILED_BY = [
  { value: 'in_person', label: 'On paper, in person' },
  { value: 'mail', label: 'By post' },
  { value: 'portal', label: 'Through their portal' },
  { value: 'email', label: 'By email' },
]

/** Mail about a request the team made outside the portal: record that request (reserved, then
 *  filed on the date given), and put this message on it. "What was asked" is kept because the
 *  request's own text is required, though 159 does not draw it. */
export function startRequest(row: Row, agencies: Schemas['AgencyOut'][], me: string): ActionSpec {
  const subject = subjectOf(row).replace(/^(re|fwd?):\s*/i, '')
  return {
    key: `start-${row.source.id}`,
    look: 'inbox',
    title: 'Start a new request',
    subtitle: `From: ${subjectOf(row)}`,
    submit: 'Create request',
    fields: [
      {
        name: 'agency',
        label: 'Agency',
        type: 'select',
        required: true,
        row: 'top',
        options: agencies.map((a) => ({ value: a.id, label: a.name })),
      },
      { name: 'filed', label: 'Filed', type: 'date', required: true, row: 'top' },
      { name: 'subject', label: 'Subject', required: true, initial: subject },
      {
        name: 'text',
        label: 'What was asked',
        type: 'textarea',
        required: true,
        minHeight: 52,
      },
      {
        name: 'channel',
        label: 'How it was filed',
        type: 'select',
        required: true,
        initial: 'in_person',
        options: FILED_BY,
      },
    ],
    run: async (values) => {
      const made = unwrap(
        await api.POST('/api/records/requests', {
          body: {
            agency_id: text(values, 'agency'),
            requester_user_id: me,
            subject: text(values, 'subject'),
            request_text: text(values, 'text'),
            items: [],
            sensitivity: 'internal',
          },
        }),
      )
      unwrap(
        await api.POST('/api/records/requests/{request_id}/file', {
          params: { path: { request_id: made.id } },
          body: {
            filed_at: new Date(`${text(values, 'filed')}T12:00`).toISOString(),
            channel: text(values, 'channel'),
          },
        }),
      )
      unwrap(
        await api.POST('/api/records/requests/{request_id}/events', {
          params: { path: { request_id: made.id } },
          body: {
            event_type: 'correspondence',
            channel: 'email',
            direction: 'inbound',
            occurred_at: row.source.captured_at,
            display_body: subjectOf(row),
            source_id: row.source.id,
          },
        }),
      )
      await reviewed(row.source.id)
    },
  }
}
