/**
 * Every action the request page offers, described as data for <ActionDialog>, each drawn as its
 * v4 screen draws it (070–104): the title, the subtitle, the width and distance from the top, the
 * fields in their order and rows, the button's words.
 *
 * Each `run` is one or a few API calls and nothing else; what a person may do is the server's to
 * decide, and a refusal comes back in the server's own words. Nothing here states a rule of law:
 * dates are entered by a person or computed by the server from the rules file. A field the
 * design draws and the API cannot take is left out (HANDOFF lists them); a field the API needs
 * and the design does not draw (a reason the ledger keeps) is kept.
 *
 * Reviewing a production and following up are their own dialogs (`ReviewProduction.tsx`,
 * `FollowUp.tsx`): each is a form over many rows, not a list of fields.
 */
import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import {
  instantOf,
  instantOfParts,
  nowLocal,
  unwrap,
  type ActionSpec,
  type FieldSpec,
  type Values,
} from '../../components/ActionDialog'
import { DialogField, RaisedTextarea } from '../../components/Fields'
import { civilDayOf, formatDayTime, formatShortDate } from '../../lib/dates'
import { findRequest } from '../../lib/requests'
import { statusText } from '../../lib/status'
import { CHANNEL, humanize, money } from '../../lib/vocab'
import { AgencyPicker, RequestPicker, RequestSelect } from './pickers'

type Detail = Schemas['RequestDetailOut']
type Definitions = Schemas['DefinitionsOut']
type Person = Schemas['UserOut']
type Me = Schemas['MeOut']
type Event = Schemas['EventOut']
type Deadline = Schemas['DeadlineOut']
type Fee = Schemas['FeeOut']
type Attachment = Schemas['AttachmentOut']
type Contact = Schemas['ContactOut']

/** What the page does after an action that hands over to something else: the composer, or the
 *  new-request flow. */
export type Compose =
  | { kind: 'new' }
  | { kind: 'reply'; sourceId: string; forward: boolean }
  | { kind: 'message'; messageId: string }

export interface Context {
  request: Detail
  definitions: Definitions
  people: Person[]
  me: Me
  attachments: Attachment[]
  contacts?: Contact[]
  /** Open the composer on the page (withdraw → tell the agency; narrow → reply; a challenge's
   *  letter). Absent where the caller has no composer (the Docket's row menu). */
  compose?: (compose: Compose) => void
  navigate?: (to: string) => void
  /** The draft a reserved request was made from (084 "Print letter"). */
  madeFrom?: { draft_id: string; recipient_id: string } | null
}

const text = (values: Values, name: string) => String(values[name] ?? '').trim()
const checked = (values: Values, name: string) => values[name] === true
const path = (request: Detail) => ({ params: { path: { request_id: request.id } } })
/** "REQ-2026-000042 · Agency": the line under most titles (070). */
const onWhat = (request: Detail) => `${request.tracking_number} · ${request.agency_name}`
const staffOptions = (people: Person[]) =>
  people
    .filter((p) => p.is_active && p.records_role !== 'requester')
    .map((p) => ({ value: p.id, label: p.name }))
const today = () => nowLocal().slice(0, 10)
const timeNow = () => nowLocal().slice(11, 16)

/** The ledger's reason: required wherever the API keeps one. */
const reason = (minHeight = 60, label = 'Reason'): FieldSpec => ({
  name: 'note',
  label,
  type: 'textarea',
  required: true,
  minHeight,
})

/** "Change status to" (102, 103): what the event means, as the status it moves the request to. */
function meaningOptions(definitions: Definitions) {
  const counts = new Map<string, number>()
  for (const kind of definitions.classifications)
    if (kind.status) counts.set(kind.status, (counts.get(kind.status) ?? 0) + 1)
  return [
    { value: '', label: 'Leave as it is' },
    ...definitions.classifications.map((kind) => ({
      value: kind.kind,
      label: kind.status && counts.get(kind.status) === 1 ? statusText(kind.status) : kind.label,
    })),
  ]
}

async function edit(
  request: Detail,
  entity: string,
  id: string,
  field: string,
  value: unknown,
  note: string,
) {
  unwrap(
    await api.POST('/api/records/requests/{request_id}/edits', {
      ...path(request),
      body: { entity_type: entity, entity_id: id, field, new_value: value, note },
    }),
  )
}

async function postStatus(request: Detail, status: string, note: string) {
  unwrap(
    await api.POST('/api/records/requests/{request_id}/status', {
      ...path(request),
      body: { status, note },
    }),
  )
}

/** The multipart body of "add a document". The generated type describes its fields. */
async function postDocument(
  request: Detail,
  kind: string,
  when: string,
  body: string,
  files: File[],
  contactId = '',
) {
  const form = new FormData()
  form.set('source_type', kind)
  form.set('received_at', when)
  form.set('display_body', body)
  // P23b (102 "From"): who at the agency sent it.
  if (contactId) form.set('contact_id', contactId)
  for (const file of files) form.append('files', file)
  return unwrap(
    await api.POST('/api/records/requests/{request_id}/documents', {
      ...path(request),
      body: form as never,
      bodySerializer: (payload) => payload as unknown as FormData,
    }),
  )
}

async function classify(eventId: string, kind: string, note: string | null, extra = {}) {
  unwrap(
    await api.POST('/api/records/events/{event_id}/classify', {
      params: { path: { event_id: eventId } },
      body: { kind, note, record_attachment_ids: [], ...extra },
    }),
  )
}

// ---- the request as a whole --------------------------------------------------------------------

/** 083: the request has gone out. 084 (`byPost`): logging the mailing of a postal filing. */
export function markFiled({ request, madeFrom, definitions }: Context, byPost = false): ActionSpec {
  const own = request.items.filter((item) => item.provision)
  return {
    secondary:
      byPost && madeFrom
        ? {
            label: 'Print letter',
            run: () =>
              window.open(
                `/api/drafting/drafts/${madeFrom.draft_id}/pdf?recipient_id=${madeFrom.recipient_id}`,
                '_blank',
              ),
          }
        : undefined,
    key: byPost ? 'log-mailing' : 'mark-filed',
    title: byPost ? 'Log the mailing' : 'Mark as filed',
    subtitle: onWhat(request),
    width: 520,
    top: 70,
    compact: true,
    submit: byPost ? 'Log mailing' : 'Mark as filed',
    fields: [
      {
        name: 'date',
        label: byPost ? 'Mailed on' : 'Filed on',
        type: 'date',
        required: true,
        initial: today(),
        row: 'when',
      },
      { name: 'time', label: 'Time', type: 'time', initial: timeNow(), row: 'when' },
      ...(byPost
        ? []
        : [
            {
              name: 'channel',
              label: 'How',
              type: 'select' as const,
              required: true,
              initial: 'email',
              options: ['email', 'portal', 'mail'].map((value) => ({
                value,
                label: CHANNEL[value],
              })),
            },
            { name: 'reference', label: 'Their reference number', autoFocus: true },
          ]),
      ...(own.length > 0
        ? [
            {
              name: 'items',
              label: 'Items with their own deadline',
              type: 'checks' as const,
              initial: own.map((item) => item.id),
              options: own.map((item) => ({
                value: item.id,
                label: `Item ${item.item_number} — ${item.provision}`,
              })),
            },
          ]
        : []),
      // P23b (083): the deadlines filing on this date would open, from the rules, before filing.
      {
        name: 'clocks',
        label: 'The deadlines this opens',
        type: 'custom' as const,
        render: (_value, _onChange, values) => (
          <FilingClocks
            request={request}
            filedAt={instantOfParts(text(values, 'date'), text(values, 'time') || '12:00')}
            itemProvisions={
              own.length > 0 && ((values.items as string[] | undefined) ?? []).length < own.length
                ? Object.fromEntries(
                    own
                      .filter((item) => ((values.items as string[]) ?? []).includes(item.id))
                      .map((item) => [item.id, [item.provision as string]]),
                  )
                : undefined
            }
            kinds={definitions.deadline_kinds}
          />
        ),
      },
    ],
    run: async (values) => {
      const ticked = (values.items as string[] | undefined) ?? []
      // Every item ticked is what filing does anyway; a narrower choice is passed whole.
      const narrowed = own.length > 0 && ticked.length < own.length
      unwrap(
        await api.POST('/api/records/requests/{request_id}/file', {
          ...path(request),
          body: {
            filed_at: instantOfParts(text(values, 'date'), text(values, 'time')),
            channel: byPost ? 'mail' : text(values, 'channel'),
            item_provisions: narrowed
              ? Object.fromEntries(
                  own
                    .filter((item) => ticked.includes(item.id))
                    .map((item) => [item.id, [item.provision as string]]),
                )
              : undefined,
          },
        }),
      )
      if (text(values, 'reference')) {
        await edit(
          request,
          'requests',
          request.id,
          'agency_reference_number',
          text(values, 'reference'),
          'Recorded when the request was filed.',
        )
      }
    },
  }
}

/** 070. */
export function changeStatus({ request, definitions }: Context): ActionSpec {
  return {
    key: 'change-status',
    title: 'Change status',
    subtitle: onWhat(request),
    intro: (values) =>
      values.status === 'production_received'
        ? 'Only a person can mark a request fully produced. This is recorded as a manual override.'
        : null,
    submit: 'Change status',
    fields: [
      { name: 'now', label: 'Now', type: 'readonly', initial: statusText(request.current_status) },
      {
        name: 'status',
        label: 'New status',
        type: 'select',
        required: true,
        options: definitions.statuses
          .filter((status) => status.code !== request.current_status)
          .map((status) => ({ value: status.code, label: statusText(status.code) })),
      },
      reason(76),
    ],
    run: (values) => postStatus(request, text(values, 'status'), text(values, 'note')),
  }
}

/** 090. "Tell the agency" opens a message to it once the request is withdrawn. */
export function withdraw({ request, compose }: Context): ActionSpec {
  return {
    key: 'withdraw',
    title: 'Withdraw this request?',
    subtitle: onWhat(request),
    top: 100,
    submit: 'Withdraw',
    danger: true,
    fields: [
      reason(52),
      ...(compose && request.inbound_alias_email
        ? [{ name: 'tell', label: 'Tell the agency', type: 'checkbox' as const, initial: true }]
        : []),
    ],
    run: async (values) => {
      await postStatus(request, 'withdrawn', text(values, 'note'))
      if (checked(values, 'tell')) compose?.({ kind: 'new' })
    },
  }
}

export function close({ request }: Context): ActionSpec {
  return {
    key: 'close',
    title: 'Close this request?',
    subtitle: onWhat(request),
    top: 100,
    intro: 'Closing says the request is finished. It can be reopened.',
    submit: 'Close request',
    danger: true,
    fields: [reason(52)],
    run: (values) => postStatus(request, 'closed', text(values, 'note')),
  }
}

/** 101. */
export function acceptNoRecords({ request }: Context): ActionSpec {
  return {
    key: 'accept-no-records',
    title: 'Accept no records?',
    subtitle: onWhat(request),
    top: 90,
    submit: 'Accept and close',
    fields: [reason(50, 'Note')],
    run: (values) => postStatus(request, 'closed', text(values, 'note')),
  }
}

/** 098. */
export function reopen({ request, definitions }: Context): ActionSpec {
  return {
    key: 'reopen',
    title: 'Reopen this request',
    subtitle: onWhat(request),
    top: 90,
    compact: true,
    submit: 'Reopen',
    fields: [
      {
        name: 'status',
        label: 'New status',
        type: 'select',
        required: true,
        options: definitions.statuses
          .filter((status) => !status.is_terminal)
          .map((status) => ({ value: status.code, label: statusText(status.code) })),
      },
      reason(52),
    ],
    run: (values) => postStatus(request, text(values, 'status'), text(values, 'note')),
  }
}

/** 071: one field at a time, its value now beside the new one, and why. */
export function editRequest({ request }: Context): ActionSpec {
  const now = (field: string) =>
    field === 'reference' ? (request.agency_reference_number ?? '') : request.subject
  return {
    key: 'edit-request',
    title: 'Edit request',
    subtitle: onWhat(request),
    submit: 'Save',
    fields: [
      {
        name: 'field',
        label: 'Field',
        type: 'select',
        initial: 'subject',
        options: [
          { value: 'subject', label: 'Subject' },
          { value: 'reference', label: 'Agency reference number' },
        ],
      },
      {
        name: 'now_subject',
        label: 'Now',
        type: 'readonly',
        initial: now('subject'),
        when: (values) => values.field !== 'reference',
      },
      {
        name: 'now_reference',
        label: 'Now',
        type: 'readonly',
        initial: now('reference') || 'None',
        when: (values) => values.field === 'reference',
      },
      {
        name: 'subject',
        label: 'New',
        required: true,
        autoFocus: true,
        initial: request.subject,
        when: (values) => values.field !== 'reference',
      },
      {
        name: 'reference',
        label: 'New',
        required: true,
        initial: request.agency_reference_number ?? '',
        when: (values) => values.field === 'reference',
      },
      reason(60),
    ],
    run: async (values) => {
      const field = text(values, 'field') === 'reference' ? 'reference' : 'subject'
      const wanted = text(values, field)
      if (wanted === now(field)) throw new Error('Nothing was changed.')
      await edit(
        request,
        'requests',
        request.id,
        field === 'reference' ? 'agency_reference_number' : 'subject',
        wanted,
        text(values, 'note'),
      )
    },
  }
}

/** "Set agency reference number" (069). */
export function setReference({ request }: Context): ActionSpec {
  return {
    key: 'set-reference',
    title: 'Set agency reference number',
    subtitle: onWhat(request),
    intro:
      'The number the agency uses for this request. Mail that quotes it can then be recognised.',
    submit: 'Save',
    fields: [
      {
        name: 'reference',
        label: 'Reference number',
        required: true,
        initial: request.agency_reference_number ?? '',
      },
      reason(60),
    ],
    run: (values) =>
      edit(
        request,
        'requests',
        request.id,
        'agency_reference_number',
        text(values, 'reference'),
        text(values, 'note'),
      ),
  }
}

/** Making a request privileged, or visible to the team again (staff, P20h): who may see it is
 *  then its access list (099/230, an administrator's). The person sealing it goes on the list. */
export function changeSensitivity({ request }: Context, seal: boolean): ActionSpec {
  return {
    key: `sensitivity-${seal}`,
    title: seal ? 'Make it privileged' : 'Make it visible to the team',
    subtitle: onWhat(request),
    intro: seal
      ? 'Only you, the people it is given to and admins will see it.'
      : 'Everyone on staff will see it; people who were given it keep it.',
    submit: seal ? 'Make it privileged' : 'Make it visible',
    fields: [reason(52)],
    run: async (values) => {
      await edit(
        request,
        'requests',
        request.id,
        'sensitivity',
        seal ? 'privileged' : 'internal',
        text(values, 'note'),
      )
    },
  }
}

export function assign({ request, people }: Context): ActionSpec {
  return {
    key: 'assign',
    title: 'Assign to',
    subtitle: onWhat(request),
    submit: 'Assign',
    fields: [
      {
        name: 'user',
        label: 'Who is working this',
        type: 'select',
        initial: request.assigned_to_user_id ?? '',
        options: [{ value: '', label: 'Nobody' }, ...staffOptions(people)],
        hint:
          request.sensitivity === 'privileged'
            ? 'This request is privileged: it can only be assigned to someone it is shared with.'
            : undefined,
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/assign', {
          ...path(request),
          // As before P20b2: the new assignee is emailed when their own preference says so. The
          // request page draws no "Tell them by email" box (DECISIONS 35f).
          body: { user_id: text(values, 'user') || null, notify: true },
        }),
      )
    },
  }
}

export function createTask({ request, definitions, people }: Context, from?: Event): ActionSpec {
  return {
    key: `create-task-${from?.id ?? 'request'}`,
    title: 'Create task',
    subtitle: onWhat(request),
    submit: 'Create task',
    fields: [
      {
        name: 'description',
        label: 'What needs doing',
        type: 'textarea',
        required: true,
        initial: from
          ? `About the ${humanize(from.event_type).toLowerCase()} of ${formatShortDate(from.occurred_at)}`
          : '',
      },
      {
        name: 'type',
        label: 'Kind',
        type: 'select',
        initial: 'manual',
        options: definitions.task_types
          // A rule is entered in the rules file, never as a to-do someone ticks off here.
          .filter((type) => type.code !== 'enter_deadline_rules')
          .map((type) => ({ value: type.code, label: humanize(type.code) })),
      },
      { name: 'due', label: 'Due', type: 'date', row: 'who' },
      {
        name: 'user',
        label: 'Assign to',
        type: 'select',
        initial: '',
        row: 'who',
        options: [{ value: '', label: 'Nobody yet' }, ...staffOptions(people)],
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/tasks', {
          body: {
            request_id: request.id,
            task_type: text(values, 'type') || 'manual',
            description: text(values, 'description'),
            due_at: text(values, 'due') ? instantOfParts(text(values, 'due'), '17:00') : null,
            assigned_to_user_id: text(values, 'user') || null,
            // P23b: a task made from a message is about that message.
            source_id: from?.source_id ?? null,
          },
        }),
      )
    },
  }
}

// ---- recording what happened ---------------------------------------------------------------------

const contactOptions = (contacts: Contact[] | undefined, agency: string) => [
  { value: '', label: agency },
  ...(contacts ?? []).map((c) => ({
    value: c.id,
    label: c.title ? `${c.name} · ${c.title}` : c.name,
  })),
]

/** 072 (a call) and the meeting beside it: who, when, what was said, and optionally what it
 *  means for the request. Only something the agency said can move the status. */
export function logCall(
  { request, definitions, contacts, me }: Context,
  meeting = false,
): ActionSpec {
  const kindOf = (values: Values) =>
    definitions.classifications.find((k) => k.kind === text(values, 'meaning'))
  return {
    key: meeting ? 'log-meeting' : 'log-call',
    title: meeting ? 'Log a meeting' : 'Log a call',
    subtitle: onWhat(request),
    top: 80,
    submit: meeting ? 'Log meeting' : 'Log call',
    fields: [
      { name: 'date', label: 'Date', type: 'date', required: true, initial: today(), row: 'when' },
      { name: 'time', label: 'Time', type: 'time', initial: timeNow(), row: 'when' },
      {
        name: 'contact',
        label: 'With',
        type: 'select',
        initial: '',
        options: contactOptions(contacts, request.agency_name),
      },
      {
        name: 'direction',
        label: meeting ? 'Who asked for it' : 'Who called',
        type: 'segmented',
        initial: 'outbound',
        options: [
          { value: 'outbound', label: meeting ? 'We did' : 'We called' },
          { value: 'inbound', label: meeting ? 'They did' : 'They called' },
        ],
      },
      { name: 'body', label: 'Summary', type: 'textarea', required: true, minHeight: 76 },
      { name: 'also', label: 'Also change status', type: 'checkbox' },
      {
        name: 'meaning',
        label: 'Change status to',
        type: 'select',
        initial: '',
        options: meaningOptions(definitions),
        when: (values) => values.direction === 'inbound' && checked(values, 'also'),
      },
      {
        // Something we said moves nothing by itself: the status is set by hand, the summary
        // as the reason (070's door).
        name: 'status',
        label: 'Change status to',
        type: 'select',
        required: true,
        options: definitions.statuses
          .filter((status) => status.code !== request.current_status)
          .map((status) => ({ value: status.code, label: statusText(status.code) })),
        when: (values) => values.direction !== 'inbound' && checked(values, 'also'),
      },
      {
        name: 'meaning_note',
        label: 'Note',
        required: true,
        when: (values) =>
          values.direction === 'inbound' &&
          checked(values, 'also') &&
          Boolean(kindOf(values)?.note_required),
      },
    ],
    run: async (values) => {
      const inbound = values.direction === 'inbound'
      const event = unwrap(
        await api.POST('/api/records/requests/{request_id}/events', {
          ...path(request),
          body: {
            event_type: 'correspondence',
            channel: meeting ? 'in_person' : 'call',
            direction: inbound ? 'inbound' : 'outbound',
            occurred_at: instantOfParts(text(values, 'date'), text(values, 'time')),
            display_body: text(values, 'body'),
            // Who spoke: the agency's contact when they called, the person logging it when we did.
            actor_agency_contact_id: text(values, 'contact') || null,
            actor_user_id: inbound ? null : me.id,
          },
        }),
      )
      if (inbound && checked(values, 'also') && text(values, 'meaning')) {
        await classify(event.id, text(values, 'meaning'), text(values, 'meaning_note') || null)
      }
      if (!inbound && checked(values, 'also') && text(values, 'status')) {
        await postStatus(request, text(values, 'status'), text(values, 'body'))
      }
    },
  }
}

/** 102 (a letter by post) and 103 (a portal response): when, what it says, the file, and what
 *  it means for the request. */
export function logDocument(
  { request, definitions, contacts }: Context,
  kind: 'scanned_mail' | 'portal_capture',
): ActionSpec {
  const letter = kind === 'scanned_mail'
  const kindOf = (values: Values) =>
    definitions.classifications.find((k) => k.kind === text(values, 'meaning'))
  return {
    key: letter ? 'log-letter' : 'log-portal',
    title: letter ? 'Log a letter' : 'Log a portal response',
    subtitle: letter ? 'Arrived by post' : request.agency_name,
    width: 500,
    top: 90,
    compact: true,
    submit: letter ? 'Log letter' : 'Log response',
    fields: [
      {
        name: 'date',
        label: letter ? 'Received on' : 'Posted on',
        type: 'date',
        required: true,
        initial: today(),
        row: 'when',
      },
      // A letter's sender (P23b, 102 "From"): one of the agency's contacts, or the agency. A
      // portal response's reference number goes through the classification or the audited edit.
      ...(letter
        ? [
            {
              name: 'contact',
              label: 'From',
              type: 'select' as const,
              initial: '',
              row: 'when',
              options: contactOptions(contacts, request.agency_name),
            },
          ]
        : [
            {
              name: 'reference',
              label: 'Their reference',
              initial: request.agency_reference_number ?? '',
              row: 'when',
            },
          ]),
      ...(letter
        ? [{ name: 'files', label: 'The scan', type: 'files' as const, hideLabel: true }]
        : []),
      { name: 'body', label: 'What it says', type: 'textarea', required: true, minHeight: 50 },
      {
        name: 'meaning',
        label: 'Change status to',
        type: 'select',
        initial: '',
        options: meaningOptions(definitions),
      },
      {
        name: 'meaning_note',
        label: 'Note',
        required: true,
        when: (values) => Boolean(kindOf(values)?.note_required),
      },
      ...(letter
        ? []
        : [
            { name: 'shot', label: 'Attach a screenshot', type: 'checkbox' as const },
            {
              name: 'files',
              label: 'The screenshot',
              type: 'files' as const,
              hideLabel: true,
              when: (values: Values) => checked(values, 'shot'),
            },
          ]),
    ],
    run: async (values) => {
      const added = await postDocument(
        request,
        kind,
        instantOfParts(text(values, 'date'), '12:00'),
        text(values, 'body'),
        (values.files as File[]) ?? [],
        letter ? text(values, 'contact') : '',
      )
      const reference = text(values, 'reference')
      if (text(values, 'meaning')) {
        await classify(
          added.event.id,
          text(values, 'meaning'),
          text(values, 'meaning_note') || null,
          {
            agency_reference_number:
              reference && reference !== request.agency_reference_number ? reference : null,
          },
        )
      } else if (reference && reference !== (request.agency_reference_number ?? '')) {
        await edit(
          request,
          'requests',
          request.id,
          'agency_reference_number',
          reference,
          'Read from the portal response.',
        )
      }
    },
  }
}

const HOW = [
  { value: 'scanned_mail', label: 'Letter or scan' },
  { value: 'portal_capture', label: 'Portal' },
  { value: 'call', label: 'Phone call' },
  { value: 'in_person', label: 'Meeting' },
]
const isDocument = (values: Values) =>
  ['scanned_mail', 'portal_capture'].includes(text(values, 'how'))

/** "Log a response" (068): something the agency sent that is not already on the thread, and what
 *  it is (`kind`, one of the classifications). The event is recorded, then classified. */
export function logResponse({ request, definitions }: Context, kind: string): ActionSpec {
  const meaning = definitions.classifications.find((k) => k.kind === kind)
  return {
    key: `log-response-${kind}`,
    title: meaning?.label ?? 'Log a response',
    subtitle: onWhat(request),
    width: 500,
    top: 90,
    compact: true,
    intro: meaning?.through_override
      ? 'Only a person can mark a request fully produced. This is recorded as a manual override.'
      : meaning?.status
        ? `Status becomes ${statusText(meaning.status)}.`
        : 'The request’s status stays as it is.',
    submit: 'Record',
    fields: [
      {
        name: 'how',
        label: 'How it came',
        type: 'segmented',
        initial: 'scanned_mail',
        options: HOW,
      },
      {
        name: 'date',
        label: 'Received on',
        type: 'date',
        required: true,
        initial: today(),
        row: 'when',
      },
      { name: 'time', label: 'Time', type: 'time', initial: timeNow(), row: 'when' },
      { name: 'files', label: 'The document', type: 'files', when: isDocument },
      { name: 'body', label: 'What it says', type: 'textarea', required: true, minHeight: 50 },
      ...(meaning?.takes_fee
        ? [{ name: 'amount', label: 'Amount quoted, in dollars', type: 'number' as const }]
        : []),
      ...(meaning?.note_required ? [{ name: 'meaning_note', label: 'Note', required: true }] : []),
    ],
    run: async (values) => {
      const when = instantOfParts(text(values, 'date'), text(values, 'time'))
      const eventId = isDocument(values)
        ? (
            await postDocument(
              request,
              text(values, 'how'),
              when,
              text(values, 'body'),
              (values.files as File[]) ?? [],
            )
          ).event.id
        : unwrap(
            await api.POST('/api/records/requests/{request_id}/events', {
              ...path(request),
              body: {
                event_type: 'correspondence',
                channel: text(values, 'how'),
                direction: 'inbound',
                occurred_at: when,
                display_body: text(values, 'body'),
              },
            }),
          ).id
      const amount = text(values, 'amount')
      await classify(eventId, kind, text(values, 'meaning_note') || null, {
        fee:
          meaning?.takes_fee && amount ? { amount_cents: Math.round(Number(amount) * 100) } : null,
      })
    },
  }
}

/** The general "Record event" (the split button's own face; the Docket's row menu). */
export function recordEvent(context: Context): ActionSpec {
  return logCall(context)
}

/** 104: a note on the request, for the team only. */
export function addNote({ request, me }: Context): ActionSpec {
  return {
    key: 'add-note',
    title: 'Add a note',
    subtitle: onWhat(request),
    top: 90,
    submit: 'Add note',
    fields: [
      {
        name: 'body',
        label: 'Note',
        type: 'custom',
        render: (value, onChange) => (
          <RaisedTextarea
            aria-label="Note"
            required
            autoFocus
            minHeight={80}
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => onChange(event.target.value)}
          />
        ),
      },
    ],
    run: async (values) => {
      if (!text(values, 'body')) throw new Error('Write the note first.')
      unwrap(
        await api.POST('/api/records/requests/{request_id}/events', {
          ...path(request),
          body: {
            event_type: 'note',
            channel: 'system',
            direction: 'internal',
            occurred_at: new Date().toISOString(),
            display_body: text(values, 'body'),
            // The note is the writer's own.
            actor_user_id: me.id,
          },
        }),
      )
    },
  }
}

export function classifyEvent({ definitions, attachments }: Context, event: Event): ActionSpec {
  const files = attachments.filter((file) => file.source_id === event.source_id)
  const kindOf = (values: Values) =>
    definitions.classifications.find((k) => k.kind === text(values, 'kind'))
  return {
    key: `classify-${event.id}`,
    title: 'Record as…',
    subtitle: `${humanize(event.event_type)} · ${formatShortDate(event.occurred_at)}`,
    intro: (values) => {
      const kind = kindOf(values)
      if (!kind) return 'Say what this message means for the request.'
      if (kind.through_override)
        return 'Only a person can mark a request fully produced. This is recorded as a manual override.'
      return kind.status
        ? `Status becomes ${statusText(kind.status)}.`
        : 'The request’s status stays as it is.'
    },
    submit: 'Record',
    fields: [
      {
        name: 'kind',
        label: 'This is',
        type: 'select',
        required: true,
        options: definitions.classifications.map((kind) => ({
          value: kind.kind,
          label: kind.label,
        })),
      },
      {
        name: 'note',
        label: 'Note',
        when: (values) => Boolean(kindOf(values)),
        required: false,
        hint: 'Required for some of these.',
      },
      {
        name: 'amount',
        label: 'Amount quoted, in dollars',
        type: 'number',
        when: (values) => Boolean(kindOf(values)?.takes_fee),
      },
      {
        name: 'records',
        label: 'Which files are the records',
        type: 'checks',
        options: files.map((file) => ({ value: file.id, label: file.filename })),
        when: (values) => Boolean(kindOf(values)?.takes_records),
      },
      {
        name: 'reference',
        label: 'Their reference number',
        hint: 'If this message gives one.',
        when: (values) => Boolean(kindOf(values)),
      },
    ],
    run: async (values) => {
      const amount = text(values, 'amount')
      unwrap(
        await api.POST('/api/records/events/{event_id}/classify', {
          params: { path: { event_id: event.id } },
          body: {
            kind: text(values, 'kind'),
            note: text(values, 'note') || null,
            fee:
              kindOf(values)?.takes_fee && amount
                ? { amount_cents: Math.round(Number(amount) * 100) }
                : null,
            record_attachment_ids: (values.records as string[]) ?? [],
            agency_reference_number: text(values, 'reference') || null,
          },
        }),
      )
    },
  }
}

/** 074: the event as it stands, struck through, and why. */
export function voidEvent({ me, people }: Context, event: Event): ActionSpec {
  const who = people.find((p) => p.id === event.actor_user_id)?.name
  return {
    key: `void-${event.id}`,
    title: 'Void this event?',
    subtitle: [
      CHANNEL[event.channel] ?? humanize(event.event_type),
      formatDayTime(event.occurred_at, me.timezone),
      who,
    ]
      .filter(Boolean)
      .join(' · '),
    intro: (
      <div className="rounded-10 bg-page px-3.5 py-3 text-13 leading-body whitespace-pre-wrap text-muted line-through">
        {event.display_body}
      </div>
    ),
    submit: 'Void event',
    danger: true,
    fields: [reason(60)],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/events/{event_id}/void', {
          params: { path: { event_id: event.id } },
          body: { reason: text(values, 'note') },
        }),
      )
    },
  }
}

/** 075: put this message on another request too, or move it there. */
export function attachElsewhere({ request }: Context, event: Event): ActionSpec {
  return {
    key: `attach-${event.id}`,
    title: 'Attach to another request',
    subtitle: `Already on ${request.tracking_number}`,
    width: 480,
    compact: true,
    submit: (values) => (values.mode === 'belongs_here_instead' ? 'Move' : 'Add to both'),
    fields: [
      {
        name: 'target',
        label: 'The other request',
        required: true,
        placeholder: 'Its tracking number',
      },
      {
        name: 'mode',
        label: 'How',
        type: 'cards',
        initial: 'also_applies',
        options: [
          {
            value: 'also_applies',
            label: 'Add to both',
            hint: `Stays on ${request.tracking_number} and appears on the other request`,
          },
          {
            value: 'belongs_here_instead',
            label: 'Move it',
            hint: `Crossed out on ${request.tracking_number} with your reason, added to the other request`,
          },
        ],
      },
      // P23b (075): why it moves, written where it is crossed out.
      {
        name: 'reason',
        label: 'Reason',
        type: 'textarea',
        minHeight: 56,
        required: true,
        marked: true,
        when: (values) => values.mode === 'belongs_here_instead',
      },
    ],
    run: async (values) => {
      if (!event.source_id) throw new Error('This event has no message to attach.')
      const moving = values.mode === 'belongs_here_instead'
      if (moving && !text(values, 'reason')) throw new Error('Say why it moves.')
      const target = await findRequest(text(values, 'target'))
      if (target.id === request.id) throw new Error('That is this request.')
      unwrap(
        await api.POST('/api/records/sources/{source_id}/assign', {
          params: { path: { source_id: event.source_id } },
          body: {
            request_id: target.id,
            mode: text(values, 'mode') as 'also_applies',
            from_event_id: event.id,
            reason: moving ? text(values, 'reason') : null,
          },
        }),
      )
    },
  }
}

/** The person-plus on a message (062): who is dealing with it. */
export function assignSource({ request, people }: Context, event: Event): ActionSpec {
  return {
    key: `assign-source-${event.id}`,
    title: 'Assign this message',
    subtitle: onWhat(request),
    submit: 'Assign',
    fields: [
      {
        name: 'user',
        label: 'Who is dealing with it',
        type: 'select',
        initial: '',
        options: [{ value: '', label: 'Nobody' }, ...staffOptions(people)],
      },
    ],
    run: async (values) => {
      if (!event.source_id) throw new Error('This event has no message behind it.')
      unwrap(
        await api.POST('/api/records/sources/{source_id}/assignee', {
          params: { path: { source_id: event.source_id } },
          body: { user_id: text(values, 'user') || null },
        }),
      )
    },
  }
}

export function markRecords({ request, attachments }: Context, event?: Event): ActionSpec {
  const files = attachments.filter((file) => !event || file.source_id === event.source_id)
  return {
    key: `records-${event?.id ?? 'all'}`,
    title: 'Mark files as records',
    subtitle: onWhat(request),
    intro:
      'Tick the files that are the records the agency produced. A file left unticked is an ordinary attachment.',
    submit: 'Save',
    fields: [
      {
        name: 'records',
        label: event ? 'Files of this message' : 'Files on this request',
        type: 'checks',
        initial: files.filter((file) => file.role === 'produced_record').map((file) => file.id),
        options: files.map((file) => ({ value: file.id, label: file.filename })),
      },
    ],
    run: async (values) => {
      const chosen = (values.records as string[]) ?? []
      const sources = [...new Set(files.map((file) => file.source_id))]
      if (sources.length === 0) throw new Error('There are no files to mark.')
      for (const source of sources) {
        const own = files.filter((file) => file.source_id === source).map((file) => file.id)
        unwrap(
          await api.POST('/api/records/sources/{source_id}/records', {
            params: { path: { source_id: source } },
            body: { record_attachment_ids: chosen.filter((id) => own.includes(id)) },
          }),
        )
      }
    },
  }
}

// ---- deadlines ------------------------------------------------------------------------------------

/** How a message that can carry a date is named (085 "letter of Sep 5"). */
const CAME_BY: Record<string, string> = {
  mail: 'letter',
  email: 'email',
  portal: 'portal update',
  call: 'call',
  in_person: 'meeting',
}

/** 085. "Where the date comes from" (P23b) names the message it came from when there is one: the
 *  request's inbound messages, newest first, as "Their commitment · letter of Sep 5", then their
 *  commitment with no message, then the law worked out by hand. */
export function addDeadline({ request, definitions, me }: Context): ActionSpec {
  const messages = request.events
    .filter((e) => e.direction === 'inbound' && !e.voided_at)
    .toSorted((a, b) => b.occurred_at.localeCompare(a.occurred_at))
  const sourceOptions = messages.map((e) => ({
    value: `agency_commitment:${e.id}`,
    label: `Their commitment · ${CAME_BY[e.channel] ?? 'message'} of ${formatShortDate(
      civilDayOf(e.occurred_at, me.timezone),
    )}`,
  }))
  return {
    key: 'add-deadline',
    title: 'Add a deadline',
    subtitle: onWhat(request),
    width: 500,
    top: 70,
    compact: true,
    submit: 'Add deadline',
    fields: [
      {
        name: 'kind',
        label: 'Type',
        type: 'segmented',
        required: true,
        initial: definitions.deadline_kinds[0]?.code ?? '',
        // A challenge's follow-up date is set on the challenge ("Follow up by"), not here.
        options: definitions.deadline_kinds
          .filter((kind) => kind.code !== 'challenge_response')
          .map((kind) => ({
            value: kind.code,
            label: kind.label,
          })),
      },
      { name: 'due', label: 'Due', type: 'date', required: true, row: 'when' },
      {
        name: 'item',
        label: 'Applies to',
        type: 'select',
        initial: '',
        row: 'when',
        options: [
          { value: '', label: 'Whole request' },
          ...request.items.map((item) => ({ value: item.id, label: `Item ${item.item_number}` })),
        ],
      },
      {
        name: 'basis',
        label: 'Where the date comes from',
        type: 'select',
        initial: sourceOptions[0]?.value ?? 'agency_commitment',
        options: [
          ...sourceOptions,
          { value: 'agency_commitment', label: 'Their commitment' },
          { value: 'statutory', label: 'The law, worked out by hand' },
        ],
      },
      { name: 'note', label: 'Note', type: 'textarea', minHeight: 50 },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/deadlines', {
          ...path(request),
          body: {
            deadline_type: text(values, 'kind'),
            due_at: text(values, 'due'),
            basis: text(values, 'basis').split(':')[0],
            source_event_id: text(values, 'basis').split(':')[1] || null,
            request_item_id: text(values, 'item') || null,
            note: text(values, 'note') || null,
          },
        }),
      )
    },
  }
}

export function updateDeadline({ me, request }: Context, deadline: Deadline): ActionSpec {
  return {
    key: `update-${deadline.id}`,
    title: 'Update deadline',
    subtitle: onWhat(request),
    intro: 'The old date is kept on the record as superseded by the new one.',
    submit: 'Update deadline',
    // P20h: the retired Director mark's door is an administrator's (30l, 42d).
    refused:
      me.records_role === 'admin'
        ? undefined
        : 'Only admins can do this. Changing an existing deadline’s date is an admin’s to do.',
    fields: [
      { name: 'due', label: 'New date', type: 'date', required: true, initial: deadline.due_at },
      reason(60),
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/deadlines/{deadline_id}/supersede', {
          params: { path: { deadline_id: deadline.id } },
          body: {
            deadline_type: deadline.deadline_type,
            due_at: text(values, 'due'),
            basis: 'agency_commitment',
            request_item_id: deadline.request_item_id,
            note: text(values, 'note'),
          },
        }),
      )
    },
  }
}

/** "Update deadline" in the More menu (069): the date to change is chosen first. */
export function updateAnyDeadline(context: Context): ActionSpec {
  const active = context.request.deadlines.filter((d) => d.status === 'active')
  const kind = (code: string) =>
    context.definitions.deadline_kinds.find((k) => k.code === code)?.label ?? humanize(code)
  const base = active[0] ? updateDeadline(context, active[0]) : null
  return {
    key: 'update-any-deadline',
    title: 'Update deadline',
    subtitle: onWhat(context.request),
    intro: 'The old date is kept on the record as superseded by the new one.',
    submit: 'Update deadline',
    refused:
      active.length === 0
        ? 'There is no deadline running to update.'
        : (base?.refused ?? undefined),
    fields: [
      {
        name: 'which',
        label: 'Deadline',
        type: 'select',
        required: true,
        initial: active[0]?.id ?? '',
        options: active.map((d) => ({
          value: d.id,
          label: `${kind(d.deadline_type)} · ${formatShortDate(d.due_at)}`,
        })),
      },
      { name: 'due', label: 'New date', type: 'date', required: true },
      reason(60),
    ],
    run: async (values) => {
      const deadline = active.find((d) => d.id === text(values, 'which'))
      if (!deadline) throw new Error('Choose the deadline to update.')
      await updateDeadline(context, deadline).run(values)
    },
  }
}

const basisWords = (deadline: Deadline) =>
  deadline.legal_basis ??
  deadline.provision ??
  (deadline.basis === 'agency_commitment'
    ? 'Their commitment'
    : deadline.basis === 'internal'
      ? 'Our own date'
      : 'From the rules file')

/** 073: two dates on file for one thing. Track one (the other is kept as superseded by it), mark
 *  the thing met, or waive it. */
export function resolveDeadlines(
  { request, definitions, me }: Context,
  pair: Deadline[],
): ActionSpec {
  const [a, b] = pair
  const kind = definitions.deadline_kinds.find((k) => k.code === a.deadline_type)?.label
  const choices = [
    { value: `keep:${a.id}`, label: `Track ${formatShortDate(a.due_at)}`, hint: basisWords(a) },
    { value: `keep:${b.id}`, label: `Track ${formatShortDate(b.due_at)}`, hint: basisWords(b) },
    { value: 'met', label: 'Mark as met', hint: 'It has happened' },
    { value: 'waive', label: 'Waive', hint: 'It stops being checked' },
  ]
  return {
    key: `resolve-${pair.map((d) => d.id).join('-')}`,
    title: 'Resolve due date',
    subtitle: `${kind ?? humanize(a.deadline_type)} · two dates on file`,
    width: 480,
    top: 70,
    submit: (values) => choices.find((c) => c.value === values.choice)?.label ?? 'Resolve',
    fields: [
      {
        name: 'choice',
        label: 'What to do',
        type: 'cards',
        initial: `keep:${a.id}`,
        options: choices,
      },
      {
        name: 'event',
        label: 'Met by',
        type: 'select',
        required: true,
        under: 'choice:met',
        options: request.events
          .filter((event) => !event.voided_at)
          .map((event) => ({
            value: event.id,
            label: `${formatDayTime(event.occurred_at, me.timezone)} · ${event.display_body.slice(0, 50)}`,
          })),
      },
      reason(56),
    ],
    run: async (values) => {
      const choice = text(values, 'choice')
      const note = text(values, 'note')
      if (choice.startsWith('keep:')) {
        const keep = choice.slice(5)
        const drop = pair.find((d) => d.id !== keep)
        if (!drop) throw new Error('Choose one of the two dates.')
        unwrap(
          await api.POST('/api/records/deadlines/{deadline_id}/resolve', {
            params: { path: { deadline_id: keep } },
            body: { drop_deadline_id: drop.id, note },
          }),
        )
        return
      }
      for (const deadline of pair) {
        if (choice === 'met') {
          unwrap(
            await api.POST('/api/records/deadlines/{deadline_id}/satisfy', {
              params: { path: { deadline_id: deadline.id } },
              body: { satisfied_by_event_id: text(values, 'event') },
            }),
          )
        } else {
          unwrap(
            await api.POST('/api/records/deadlines/{deadline_id}/waive', {
              params: { path: { deadline_id: deadline.id } },
              body: { note },
            }),
          )
        }
      }
    },
  }
}

export function satisfyDeadline({ request, me }: Context, deadline: Deadline): ActionSpec {
  return {
    key: `satisfy-${deadline.id}`,
    title: 'Mark deadline met',
    subtitle: onWhat(request),
    intro: 'Choose the message or event that met it.',
    submit: 'Mark met',
    fields: [
      {
        name: 'event',
        label: 'Met by',
        type: 'select',
        required: true,
        options: request.events
          .filter((event) => !event.voided_at)
          .map((event) => ({
            value: event.id,
            label: `${formatDayTime(event.occurred_at, me.timezone)} · ${event.display_body.slice(0, 60)}`,
          })),
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/deadlines/{deadline_id}/satisfy', {
          params: { path: { deadline_id: deadline.id } },
          body: { satisfied_by_event_id: text(values, 'event') },
        }),
      )
    },
  }
}

export function waiveDeadline({ request }: Context, deadline: Deadline): ActionSpec {
  return {
    key: `waive-${deadline.id}`,
    title: 'Waive deadline?',
    subtitle: onWhat(request),
    intro: 'The deadline stops being checked. It stays on the record as waived, with your reason.',
    submit: 'Waive',
    danger: true,
    fields: [reason(60)],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/deadlines/{deadline_id}/waive', {
          params: { path: { deadline_id: deadline.id } },
          body: { note: text(values, 'note') },
        }),
      )
    },
  }
}

// ---- items, fees ----------------------------------------------------------------------------------

/** 080: new wording for the items changed, the agency's ask quoted above; optionally a reply to it
 *  with the new wording. */
export function narrow({ request, compose }: Context): ActionSpec {
  const ask = request.events.findLast(
    (event) => event.event_type === 'clarification' && !event.voided_at,
  )
  return {
    key: 'narrow',
    title: 'Narrow the request',
    subtitle: onWhat(request),
    width: 560,
    top: 70,
    intro: ask ? (
      <div className="rounded-10 bg-page px-3.5 py-3 text-125 leading-body text-ink-2">
        “{ask.display_body}” — {formatShortDate(ask.occurred_at)}
      </div>
    ) : null,
    submit: (values) => (checked(values, 'reply') ? 'Save and reply' : 'Save'),
    fields: [
      ...request.items.map((item, index): FieldSpec => ({
        name: `item:${item.id}`,
        label: `Item ${item.item_number}`,
        type: 'custom',
        initial: item.description,
        render: (value, onChange) => (
          <div className="flex items-start gap-2.5">
            <span className="w-4 pt-2.5 text-125 text-muted">{item.item_number}</span>
            <RaisedTextarea
              aria-label={`Item ${item.item_number}`}
              autoFocus={index === 0}
              minHeight={58}
              value={typeof value === 'string' ? value : ''}
              onChange={(event) => onChange(event.target.value)}
            />
          </div>
        ),
      })),
      reason(52),
      ...(compose && request.inbound_alias_email
        ? [
            {
              name: 'reply',
              label: ask ? 'Reply to the agency with the narrowed wording' : 'Tell the agency',
              type: 'checkbox' as const,
              initial: true,
            },
          ]
        : []),
    ],
    run: async (values) => {
      const edits: Record<string, string> = {}
      for (const item of request.items) {
        const wording = text(values, `item:${item.id}`)
        if (wording && wording !== item.description) edits[item.id] = wording
      }
      if (Object.keys(edits).length === 0) throw new Error('No wording was changed.')
      unwrap(
        await api.POST('/api/records/requests/{request_id}/narrow', {
          ...path(request),
          body: { edits, note: text(values, 'note') },
        }),
      )
      if (checked(values, 'reply'))
        compose?.(
          ask?.source_id
            ? { kind: 'reply', sourceId: ask.source_id, forward: false }
            : { kind: 'new' },
        )
    },
  }
}

export function addItem({ request }: Context): ActionSpec {
  return {
    key: 'add-item',
    title: 'Add a request item',
    subtitle: onWhat(request),
    submit: 'Add item',
    fields: [
      {
        name: 'number',
        label: 'Number',
        required: true,
        initial: String(request.items.length + 1),
      },
      { name: 'description', label: 'What is asked for', type: 'textarea', required: true },
      reason(52),
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/items', {
          ...path(request),
          body: {
            item_number: text(values, 'number'),
            description: text(values, 'description'),
            note: text(values, 'note'),
            sort_order: request.items.length,
          },
        }),
      )
    },
  }
}

export const FEE_STATUS = [
  { value: 'assessed', label: 'unpaid' },
  { value: 'waiting_on_payment', label: 'paying' },
  { value: 'paid', label: 'paid' },
  { value: 'waived', label: 'waived' },
  { value: 'disputed', label: 'contested' },
]

/** 086. */
export function recordFee({ request, me }: Context): ActionSpec {
  const notices = request.events.filter((e) => e.direction === 'inbound' && !e.voided_at)
  return {
    key: 'record-fee',
    title: 'Record a fee',
    subtitle: onWhat(request),
    width: 480,
    top: 90,
    compact: true,
    submit: 'Record fee',
    fields: [
      { name: 'amount', label: 'Amount', type: 'number', required: true, row: 'money' },
      { name: 'due', label: 'Pay by', type: 'date', row: 'money' },
      { name: 'description', label: 'For', autoFocus: true },
      {
        name: 'from',
        label: 'From',
        type: 'select',
        initial: '',
        options: [
          { value: '', label: 'Not from a message' },
          ...notices.map((event) => ({
            value: event.id,
            label: `${humanize(event.event_type)} · ${formatDayTime(event.occurred_at, me.timezone)}`,
          })),
        ],
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/fees', {
          ...path(request),
          body: {
            status: 'assessed',
            amount_cents: Math.round(Number(text(values, 'amount')) * 100),
            description: text(values, 'description') || null,
            due_at: text(values, 'due') || null,
            source_event_id: text(values, 'from') || null,
          },
        }),
      )
    },
  }
}

/** 087: paid (when, how), waived (why) or contested, and a note. */
export function settleFee({ request }: Context, fee: Fee, preset = 'paid'): ActionSpec {
  return {
    key: `fee-${fee.id}-${preset}`,
    title: `${money(fee.amount_cents)}${fee.description ? ` · ${fee.description.toLowerCase()}` : ''}`,
    subtitle: onWhat(request),
    width: 480,
    top: 60,
    compact: true,
    submit: 'Save',
    fields: [
      {
        name: 'status',
        label: 'This fee is now',
        type: 'cards',
        tight: true,
        initial: preset,
        options: [
          { value: 'paid', label: 'Paid' },
          { value: 'waived', label: 'Waived' },
          { value: 'disputed', label: 'Contested' },
        ],
      },
      {
        name: 'paid_on',
        label: 'Paid on',
        type: 'date',
        initial: today(),
        under: 'status:paid',
        row: 'paid',
      },
      { name: 'method', label: 'Method', under: 'status:paid', row: 'paid' },
      { name: 'waiver', label: 'Why it was waived', under: 'status:waived' },
      reason(46, 'Note'),
    ],
    run: async (values) => {
      const status = text(values, 'status')
      unwrap(
        await api.PATCH('/api/records/fees/{fee_id}', {
          params: { path: { fee_id: fee.id } },
          body: {
            status,
            note: text(values, 'note'),
            ...(status === 'paid'
              ? {
                  paid_at: instantOfParts(text(values, 'paid_on') || today(), '12:00'),
                  payment_method: text(values, 'method') || null,
                }
              : {}),
            ...(status === 'waived' ? { waiver_reason: text(values, 'waiver') || null } : {}),
          },
        }),
      )
    },
  }
}

/** "Pay the fee" (062): the fee is ours to pay; it waits on our payment until it is recorded paid. */
export function payFee({ request }: Context, fee: Fee): ActionSpec {
  return {
    key: `pay-${fee.id}`,
    title: 'Pay the fee',
    subtitle: onWhat(request),
    intro: `${money(fee.amount_cents)}${fee.description ? ` · ${fee.description}` : ''}. It is marked as waiting on our payment until the payment is recorded.`,
    submit: 'Mark as paying',
    fields: [reason(46, 'Note')],
    run: async (values) => {
      unwrap(
        await api.PATCH('/api/records/fees/{fee_id}', {
          params: { path: { fee_id: fee.id } },
          body: { status: 'waiting_on_payment', note: text(values, 'note') },
        }),
      )
    },
  }
}

// ---- links, copies ------------------------------------------------------------------------------

/** 069 "Ask a teammate" (P27d): a question about the whole request, put to one person who can
 *  open it (the server refuses anyone else, in words), answered on its page in Approvals. */
export function askTeammate({ request, people, me }: Context): ActionSpec {
  return {
    key: 'ask-teammate',
    title: 'Ask a teammate',
    subtitle: onWhat(request),
    submit: 'Send for review',
    fields: [
      {
        name: 'user',
        label: 'Reviewer',
        type: 'select',
        required: true,
        options: staffOptions(people).filter((p) => p.value !== me.id),
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
            subject_type: 'request',
            subject_id: request.id,
            kind: 'question',
            note: text(values, 'note'),
            assigned_to_user_id: text(values, 'user'),
          },
        }),
      )
    },
  }
}

/** 089: requests ticked from a search, and how they relate. The link keeps a reason. */
export function linkRelated({ request }: Context): ActionSpec {
  return {
    key: 'link-related',
    title: 'Link a related request',
    subtitle: onWhat(request),
    width: 500,
    top: 70,
    compact: true,
    submit: 'Link',
    fields: [
      {
        name: 'targets',
        label: 'Requests',
        type: 'custom',
        initial: [],
        render: (value, onChange) => (
          <RequestPicker
            exclude={request.id}
            chosen={Array.isArray(value) ? (value as string[]) : []}
            onChange={onChange}
          />
        ),
      },
      {
        name: 'kind',
        label: 'How they relate',
        type: 'segmented',
        initial: 'follow_on',
        options: [
          { value: 'follow_on', label: 'Follow-on' },
          { value: 'duplicate', label: 'Duplicate' },
        ],
      },
      reason(50),
    ],
    run: async (values) => {
      const targets = (values.targets as string[]) ?? []
      if (targets.length === 0) throw new Error('Tick the request to link.')
      for (const target of targets) {
        unwrap(
          await api.POST('/api/records/requests/{request_id}/links', {
            ...path(request),
            body: {
              to_request_id: target,
              kind: text(values, 'kind'),
              reason: text(values, 'note'),
            },
          }),
        )
      }
    },
  }
}

/** 095. */
export function markDuplicate({ request }: Context): ActionSpec {
  return {
    key: 'mark-duplicate',
    title: 'Mark as a duplicate',
    subtitle: onWhat(request),
    width: 500,
    top: 90,
    compact: true,
    submit: 'Mark as duplicate',
    fields: [
      {
        name: 'target',
        label: 'Duplicate of',
        type: 'custom',
        render: (value, onChange) => (
          <DialogField label="Duplicate of">
            <RequestSelect
              exclude={request.id}
              value={typeof value === 'string' ? value : ''}
              onChange={onChange}
            />
          </DialogField>
        ),
      },
      reason(50),
    ],
    run: async (values) => {
      const target = text(values, 'target')
      if (!target) throw new Error('Choose the request this duplicates.')
      unwrap(
        await api.POST('/api/records/requests/{request_id}/links', {
          ...path(request),
          body: { to_request_id: target, kind: 'duplicate', reason: text(values, 'note') },
        }),
      )
    },
  }
}

export function voidLink({ request }: Context, link: Schemas['LinkOut']): ActionSpec {
  return {
    key: `void-link-${link.id}`,
    title: 'Remove this link?',
    subtitle: onWhat(request),
    intro: `${link.from_tracking_number} → ${link.to_tracking_number}. The link is kept in the history as removed.`,
    submit: 'Remove link',
    danger: true,
    fields: [reason(52)],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/links/{link_id}/void', {
          params: { path: { link_id: link.id } },
          body: { reason: text(values, 'note') },
        }),
      )
    },
  }
}

/** A draft made from this request's letter, with these agencies, opened at review and file.
 *  `refileReason` (097) is written on this request as a note, in the same call. */
async function draftFor(request: Detail, agencies: string[], refileReason?: string) {
  const draft = unwrap(
    await api.POST('/api/drafting/drafts', {
      body: { from_request_id: request.id, refile_reason: refileReason ?? null },
    }),
  )
  for (const agency of agencies) {
    unwrap(
      await api.POST('/api/drafting/drafts/{draft_id}/recipients', {
        params: { path: { draft_id: draft.id } },
        body: { agency_id: agency },
      }),
    )
  }
  return draft
}

/** 096: the same letter to other agencies, as new requests reviewed before they are filed. */
export function copyToAgencies({ request, navigate }: Context): ActionSpec {
  return {
    key: 'copy-to-agencies',
    title: 'Copy to other agencies',
    subtitle: 'Same letter, new requests',
    width: 520,
    top: 90,
    submit: (values) => {
      const n = ((values.agencies as string[]) ?? []).length
      return n > 0 ? `Review & file ${n}` : 'Review & file'
    },
    fields: [
      {
        name: 'agencies',
        label: 'Agencies',
        type: 'custom',
        initial: [],
        render: (value, onChange) => (
          <AgencyPicker
            own={request.agency_id}
            chosen={Array.isArray(value) ? (value as string[]) : []}
            onChange={onChange}
          />
        ),
      },
    ],
    run: async (values) => {
      const agencies = (values.agencies as string[]) ?? []
      if (agencies.length === 0) throw new Error('Tick at least one agency.')
      const draft = await draftFor(request, agencies)
      navigate?.(`/drafts/${draft.id}?stage=file`)
    },
  }
}

/** 097: the same letter to the same agency again, reviewed before it is filed. */
/** 097's "Why" (P23b): the drawn reason first, then the others a person most often has; "Another
 *  reason" asks for the words. The choice is written as a note on this request. */
export const REFILE_REASONS = [
  'Their system lost the first one',
  'It went to the wrong address',
  'They asked us to send it again',
]
const ANOTHER_REASON = 'another'

export function fileAgain({ request, navigate }: Context): ActionSpec {
  return {
    key: 'file-again',
    title: 'File again?',
    subtitle: onWhat(request),
    top: 90,
    compact: true,
    submit: 'Continue to review',
    fields: [
      {
        name: 'why',
        label: 'Why',
        type: 'select',
        initial: REFILE_REASONS[0],
        options: [
          ...REFILE_REASONS.map((reason) => ({ value: reason, label: reason })),
          { value: ANOTHER_REASON, label: 'Another reason…' },
        ],
      },
      {
        name: 'other',
        label: 'Reason',
        required: true,
        marked: true,
        autoFocus: true,
        when: (values) => values.why === ANOTHER_REASON,
      },
    ],
    run: async (values) => {
      const why =
        text(values, 'why') === ANOTHER_REASON ? text(values, 'other') : text(values, 'why')
      if (!why) throw new Error('Say why it is filed again.')
      // A new draft from this request's letter, to the same agency; nothing is filed until it is
      // filed at review. The reason is written on this request as a note.
      const draft = await draftFor(request, [request.agency_id], why)
      navigate?.(`/drafts/${draft.id}?stage=file`)
    },
  }
}

// ---- appeals and search challenges -------------------------------------------------------------

type Challenge = Schemas['ChallengeOut']

/** 081 (appeal a withholding) and 100 (challenge the search). The letter is started as a mail
 *  draft to the agency and opened for the person to write; nothing is prefilled as text that
 *  would go out in the organisation's name. */
export function openChallenge(
  { request, compose }: Context,
  kind: 'appeal' | 'search_challenge',
  scope: { request_item_id?: string; review_item_id?: string; label?: string } = {},
): ActionSpec {
  const appeal = kind === 'appeal'
  const noRecords = request.status_history.findLast(
    (change) => change.status === 'no_records_claimed' && !change.voided_at,
  )
  return {
    key: `challenge-${kind}-${scope.review_item_id ?? scope.request_item_id ?? 'request'}`,
    title: appeal ? 'Appeal the withholding' : 'Challenge the search',
    subtitle: appeal
      ? `${scope.label ?? 'The request'} · withheld`
      : `They say no records exist${noRecords ? ` · ${formatShortDate(noRecords.effective_at)}` : ''}`,
    width: 560,
    top: appeal ? 60 : 90,
    compact: true,
    submit: 'Open in editor',
    fields: [
      {
        name: 'basis',
        label: 'Why',
        type: 'textarea',
        required: true,
        minHeight: 76,
        hint: 'Kept with the challenge; the letter is written next.',
      },
      { name: 'follow_up', label: 'Follow up if no answer by', type: 'date' },
    ],
    run: async (values) => {
      const challenge = unwrap(
        await api.POST('/api/records/requests/{request_id}/challenges', {
          ...path(request),
          body: {
            kind,
            basis: text(values, 'basis'),
            request_item_id: scope.request_item_id ?? null,
            review_item_id: scope.review_item_id ?? null,
            follow_up_by: text(values, 'follow_up') || null,
            with_letter: true,
          },
        }),
      )
      if (challenge.message_id) compose?.({ kind: 'message', messageId: challenge.message_id })
    },
  }
}

export function challengeSent({ request }: Context, challenge: Challenge): ActionSpec {
  return {
    key: `challenge-sent-${challenge.id}`,
    title: 'The letter went out another way',
    subtitle: onWhat(request),
    intro: 'Record that it was sent outside the portal. The follow-up date becomes a deadline.',
    submit: 'Mark sent',
    fields: [
      { name: 'at', label: 'Sent', type: 'datetime', required: true, initial: nowLocal() },
      {
        name: 'channel',
        label: 'How',
        type: 'select',
        initial: 'mail',
        options: [
          { value: 'mail', label: 'Post' },
          { value: 'portal', label: 'Portal' },
          { value: 'email', label: 'Email (outside the portal)' },
          { value: 'in_person', label: 'Handed over' },
        ],
      },
      { name: 'note', label: 'Note' },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/challenges/{challenge_id}/sent', {
          params: { path: { challenge_id: challenge.id } },
          body: {
            sent_at: instantOf(text(values, 'at')),
            channel: text(values, 'channel'),
            note: text(values, 'note'),
          },
        }),
      )
    },
  }
}

export function challengeFollowUp({ request }: Context, challenge: Challenge): ActionSpec {
  return {
    key: `challenge-follow-${challenge.id}`,
    title: 'Follow up by',
    subtitle: onWhat(request),
    submit: 'Save',
    fields: [{ name: 'date', label: 'Date', type: 'date', initial: challenge.follow_up_by ?? '' }],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/challenges/{challenge_id}/follow-up', {
          params: { path: { challenge_id: challenge.id } },
          body: { follow_up_by: text(values, 'date') || null },
        }),
      )
    },
  }
}

export const CHALLENGE_OUTCOME = [
  { value: 'reversed', label: 'Reversed: they released it' },
  { value: 'partly_reversed', label: 'Partly reversed' },
  { value: 'upheld', label: 'Upheld: they stood by it' },
  { value: 'withdrawn', label: 'Withdrawn by us' },
]

export function challengeOutcome({ request }: Context, challenge: Challenge): ActionSpec {
  return {
    key: `challenge-outcome-${challenge.id}`,
    title: 'Record the outcome',
    subtitle: onWhat(request),
    intro: 'What the agency did. The status is yours to change afterwards if it should move.',
    submit: 'Record',
    fields: [
      {
        name: 'outcome',
        label: 'Outcome',
        type: 'select',
        required: true,
        options: CHALLENGE_OUTCOME,
      },
      { name: 'note', label: 'Note' },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/challenges/{challenge_id}/outcome', {
          params: { path: { challenge_id: challenge.id } },
          body: { outcome: text(values, 'outcome'), note: text(values, 'note') },
        }),
      )
    },
  }
}

/**
 * 083's footnote (P23b): "Determination due **Sep 18** · TESTLAW § 1", one line per deadline filing
 * on the chosen date would open, asked of the same rules service filing uses (`/file/preview`,
 * which writes nothing). Says so when no rule covers the agency's law; says nothing while asking.
 */
function FilingClocks({
  request,
  filedAt,
  itemProvisions,
  kinds,
}: {
  request: Detail
  filedAt: string
  itemProvisions: Record<string, string[]> | undefined
  kinds: Definitions['deadline_kinds']
}) {
  const valid = !Number.isNaN(new Date(filedAt).getTime())
  const preview = $api.useQuery(
    'post',
    '/api/records/requests/{request_id}/file/preview',
    {
      params: { path: { request_id: request.id } },
      body: { filed_at: filedAt, item_provisions: itemProvisions ?? null },
    },
    { enabled: valid, placeholderData: (previous) => previous },
  )
  if (!valid || !preview.data) return null
  const label = (code: string) => kinds.find((k) => k.code === code)?.label ?? humanize(code)
  if (preview.data.problem)
    return (
      <div className="text-125 text-ink-2">
        No deadline can be worked out yet: {preview.data.problem}
      </div>
    )
  return (
    <div className="flex flex-col gap-1">
      {preview.data.deadlines
        .toSorted((a, b) => a.due_at.localeCompare(b.due_at))
        .map((d) => (
          <div
            key={`${d.deadline_type}-${d.request_item_id ?? 'all'}-${d.due_at}`}
            className="text-125 text-ink-2"
          >
            {label(d.deadline_type)} due{' '}
            <span className="font-medium text-ink">{formatShortDate(d.due_at)}</span>
            {d.citation || d.legal_basis ? ` · ${d.citation || d.legal_basis}` : ''}
            {d.request_item_id
              ? ` · item ${request.items.find((i) => i.id === d.request_item_id)?.item_number ?? ''}`
              : ''}
          </div>
        ))}
      {preview.data.unresolved.map((gap) => (
        <div key={`${gap.provision}-${gap.request_item_id}`} className="text-125 text-ink-2">
          No rule yet for {gap.provision}: a task to enter it is opened
        </div>
      ))}
    </div>
  )
}
