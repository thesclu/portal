/**
 * Stage 3 of a new request (048, "Confirm agencies"): "Add an agency" as the underlined search,
 * one table of the agencies the letter goes to, grouped "Sent by email" and "You file", each row
 * with its box (ticked: it is filed; unticked: held, with why under the name), the agency, how
 * it files (changed in place until it is reserved), where it sends, and × to take it off. At the
 * right "This will create" with "Review & file N →", and the letter with "Edit letter".
 *
 * "Review & file N →" is where reserving happens (ARCHITECTURE §4.5): every ticked agency not
 * yet reserved gets its tracking number and request address, one call each, and stage 4 opens.
 * Filing stays a separate act, agency by agency, in stage 4. An agency the server will not
 * reserve (a blank nothing fills, a review not yet given) stays unreserved and says why there.
 */
import { useRef, useState } from 'react'

import { api, type Schemas } from '../../../api/client'
import { $api, messageOf } from '../../../api/query'
import { unwrap, type ActionSpec, type Values } from '../../../components/ActionDialog'
import { CheckIcon, ChevronDown, CloseIcon } from '../../../components/icons'
import { Callout, cx } from '../../../components/ui'
import { FlowSearch } from '../parts'
import {
  CHANNELS,
  filesBy,
  groupRecipients,
  itemsLabel,
  sendsTo,
  toReserve,
  willCreate,
  type Agency,
  type Recipient,
  type StepKey,
} from './stages'
import { Stepper } from './Stepper'

type Draft = Schemas['DraftDetailOut']

const text = (values: Values, name: string) => String(values[name] ?? '').trim()
/** The table's columns as 048 draws them, 14 px apart. */
const COLUMNS = '22px 1.5fr 150px 1.3fr 22px'

function holdSpec(recipient: Recipient, name: string): ActionSpec {
  return {
    key: `hold-${recipient.id}`,
    title: `Hold ${name}`,
    intro: 'A held agency is skipped when filing until it is released.',
    submit: 'Hold',
    fields: [{ name: 'reason', label: 'Why', required: true }],
    run: async (values) => {
      unwrap(
        await api.PATCH('/api/drafting/recipients/{recipient_id}', {
          params: { path: { recipient_id: recipient.id } },
          body: { status: 'held', hold_reason: text(values, 'reason') },
        }),
      )
    },
  }
}

/** The blanks the letter still has for this agency, read from its preview. */
function Unresolved({ recipientId }: { recipientId: string }) {
  const preview = $api.useQuery('get', '/api/drafting/recipients/{recipient_id}/preview', {
    params: { path: { recipient_id: recipientId } },
  })
  if (!preview.data || preview.data.unresolved.length === 0) return null
  return (
    <span className="mt-0.5 block truncate text-115 text-warn">
      Not filled in: {preview.data.unresolved.join(', ')}
    </span>
  )
}

export function AgenciesStage({
  draft,
  agencies,
  setSpec,
  refresh,
  onGo,
}: {
  draft: Draft
  agencies: Agency[]
  setSpec: (spec: ActionSpec | null) => void
  refresh: () => Promise<void>
  onGo: (key: StepKey) => void
}) {
  const [query, setQuery] = useState('')
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reserving, setReserving] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)
  const template = $api.useQuery(
    'get',
    '/api/drafting/templates/{template_id}',
    { params: { path: { template_id: draft.source_template_id ?? '' } } },
    { enabled: Boolean(draft.source_template_id) },
  )
  const agencyOf = (id: string) => agencies.find((a) => a.id === id)
  const taken = new Set(draft.recipients.map((r) => r.agency_id))
  const groups = groupRecipients(draft.recipients)
  const creates = willCreate(draft.recipients, agencies)
  const q = query.trim().toLowerCase()
  const matches = agencies
    .filter((agency) => !taken.has(agency.id))
    .filter(
      (agency) =>
        !q ||
        agency.name.toLowerCase().includes(q) ||
        (agency.short_name ?? '').toLowerCase().includes(q),
    )
    .slice(0, 8)

  async function call(result: Promise<{ error?: unknown }>) {
    setError(null)
    const done = await result
    if (done.error) setError(messageOf(done.error))
    await refresh()
  }
  const path = (recipient: Recipient) => ({ params: { path: { recipient_id: recipient.id } } })
  function add(agency: Agency) {
    setQuery('')
    setPicking(false)
    void call(
      api.POST('/api/drafting/drafts/{draft_id}/recipients', {
        params: { path: { draft_id: draft.id } },
        body: { agency_id: agency.id },
      }),
    )
  }
  async function reviewAndFile() {
    setReserving(true)
    setError(null)
    for (const recipient of toReserve(draft.recipients)) {
      // One at a time: each reserve takes the request's lock and its own number. A refusal is
      // read again in stage 4, on that agency, where it can be dealt with.
      await api.POST('/api/drafting/recipients/{recipient_id}/reserve', {
        ...path(recipient),
        body: {},
      })
    }
    await refresh()
    setReserving(false)
    onGo('file')
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
      <Stepper current="agencies" onGo={onGo} />
      <div className="flex flex-1 items-start gap-5">
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div
            className="relative"
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null))
                setPicking(false)
            }}
          >
            <FlowSearch
              inputRef={searchRef}
              placeholder="Add an agency"
              value={query}
              onChange={(value) => {
                setQuery(value)
                setPicking(true)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && matches[0]) {
                  event.preventDefault()
                  add(matches[0])
                } else if (event.key === 'Escape') setPicking(false)
                else if (event.key === 'ArrowDown') setPicking(true)
              }}
            />
            {picking && (q || matches.length > 0) ? (
              <div
                role="listbox"
                aria-label="Agencies to add"
                className="absolute top-full left-0 z-20 mt-1 box-content w-[360px] overflow-hidden rounded-10 border border-line bg-card-white py-0 shadow-menu"
              >
                {matches.length === 0 ? (
                  <p className="px-3.5 py-[7px] text-125 text-muted">
                    No agency by that name. Add it under Agencies first.
                  </p>
                ) : (
                  matches.map((agency) => (
                    <button
                      key={agency.id}
                      type="button"
                      role="option"
                      aria-selected={false}
                      onClick={() => add(agency)}
                      className="flex w-full items-center gap-3 px-3.5 py-[7px] text-left text-125 leading-[normal] text-ink hover:bg-hover"
                    >
                      <span className="min-w-0 flex-1 truncate">{agency.name}</span>
                      <span className="shrink-0 text-115 text-muted">
                        {filesBy(agency.filing_method ?? '', agency)}
                      </span>
                    </button>
                  ))
                )}
              </div>
            ) : null}
          </div>
          {error ? (
            <Callout tone="danger" role="alert">
              {error}
            </Callout>
          ) : null}

          <section
            aria-label="Agencies on this letter"
            className="overflow-hidden rounded-16 bg-card leading-[normal] shadow-float"
          >
            {draft.recipients.length === 0 ? (
              <p className="px-9 py-10 text-center text-125 text-muted">
                No agencies yet. Each one added becomes its own request when it is filed.
              </p>
            ) : (
              <>
                <div
                  style={{ gridTemplateColumns: COLUMNS }}
                  className="grid gap-x-3.5 px-9 py-[9px] text-105 font-semibold tracking-label text-muted uppercase"
                >
                  <span />
                  <span>Agency</span>
                  <span>Files by</span>
                  <span>Sends to</span>
                  <span />
                </div>
                {groups.map((group) => (
                  <div key={group.key} role="rowgroup">
                    <div className="flex items-baseline gap-2 border-y border-hairline bg-surface-row px-[18px] py-[9px]">
                      <span className="text-105 font-semibold tracking-label text-muted uppercase">
                        {group.title}
                      </span>
                      <span className="text-12 text-muted-2">{group.rows.length}</span>
                    </div>
                    {group.rows.map((recipient, index) => (
                      <AgencyRow
                        key={recipient.id}
                        recipient={recipient}
                        agency={agencyOf(recipient.agency_id)}
                        zebra={index % 2 === 1}
                        last={index === group.rows.length - 1}
                        onChannel={(channel) =>
                          void call(
                            api.PATCH('/api/drafting/recipients/{recipient_id}', {
                              ...path(recipient),
                              body: { channel },
                            }),
                          )
                        }
                        onTick={() =>
                          recipient.status === 'held'
                            ? void call(
                                api.PATCH('/api/drafting/recipients/{recipient_id}', {
                                  ...path(recipient),
                                  body: { status: 'pending', hold_reason: null },
                                }),
                              )
                            : setSpec(
                                holdSpec(recipient, agencyOf(recipient.agency_id)?.name ?? ''),
                              )
                        }
                        onRemove={() =>
                          void call(
                            api.DELETE('/api/drafting/recipients/{recipient_id}', path(recipient)),
                          )
                        }
                      />
                    ))}
                  </div>
                ))}
              </>
            )}
          </section>
        </div>

        <div className="flex w-[290px] shrink-0 flex-col gap-2.5 leading-[normal]">
          <div className="rounded-12 border border-transparent px-4 py-2">
            <div className="pb-2.5">
              <span className="text-11 font-semibold tracking-group text-muted uppercase">
                This will create
              </span>
            </div>
            <div className="flex items-baseline gap-2.5 py-1 text-125">
              <span className="flex-1 text-ink">Requests</span>
              <span className="text-muted">{creates.requests}</span>
            </div>
            {creates.rows.map((row) => (
              <div key={row.label} className="flex items-baseline gap-2.5 py-1 text-125">
                <span className="flex-1 text-ink">{row.label}</span>
                <span className="text-muted">{row.count}</span>
              </div>
            ))}
            <button
              type="button"
              disabled={creates.requests === 0 || reserving}
              aria-busy={reserving || undefined}
              title="Reserves a tracking number and a request address for each ticked agency"
              onClick={() => void reviewAndFile()}
              className="mt-3 w-full rounded-pill bg-brand p-[9px] text-center text-13 font-medium text-white hover:bg-brand-dark disabled:opacity-55"
            >
              {reserving
                ? 'Reserving…'
                : `Review & file ${creates.requests > 0 ? `${creates.requests} ` : ''}→`}
            </button>
          </div>
          <div className="rounded-12 border border-transparent px-4 py-2">
            <div className="pb-2.5">
              <span className="text-11 font-semibold tracking-group text-muted uppercase">
                Letter
              </span>
            </div>
            <p className="text-125 leading-[1.45] text-ink">{draft.subject || 'Untitled letter'}</p>
            <p className="mt-1 text-12 text-muted">
              {draft.fork_master_document_id
                ? 'Written in the drafting tool; each agency’s letter is made there when it is reserved'
                : [template.data?.legal_basis, itemsLabel(draft.items.length)]
                    .filter(Boolean)
                    .join(' · ')}
            </p>
            <button
              type="button"
              onClick={() => onGo('draft')}
              className="mt-2.5 text-125 font-medium text-brand hover:text-brand-dark"
            >
              Edit letter
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function AgencyRow({
  recipient,
  agency,
  zebra,
  last,
  onChannel,
  onTick,
  onRemove,
}: {
  recipient: Recipient
  agency: Agency | undefined
  zebra: boolean
  last: boolean
  onChannel: (channel: string) => void
  onTick: () => void
  onRemove: () => void
}) {
  const reserved = Boolean(recipient.request_id)
  const editable = !reserved && recipient.status !== 'filed' && recipient.status !== 'skipped'
  const ticked = recipient.status !== 'held' && recipient.status !== 'skipped'
  const name = agency?.name ?? 'An agency'
  return (
    <div
      role="row"
      style={{ gridTemplateColumns: COLUMNS }}
      className={cx(
        'group grid items-center gap-x-3.5 px-9 py-[11px] text-125',
        !last && 'border-b border-transparent',
        zebra && 'bg-row-alt',
      )}
    >
      <button
        type="button"
        role="checkbox"
        aria-checked={ticked}
        aria-label={ticked ? `Hold ${name}` : `File with ${name}`}
        disabled={!editable}
        onClick={onTick}
        className={cx(
          'box-border inline-flex size-[15px] items-center justify-center rounded-4',
          ticked ? 'bg-brand' : 'border-[1.5px] border-line-control bg-card-white',
          !editable && 'cursor-default',
        )}
      >
        {ticked ? <CheckIcon size={9} width={3.4} stroke="var(--color-card-white)" /> : null}
      </button>
      <span className="min-w-0">
        <span className={cx('block truncate', ticked ? 'text-ink' : 'text-muted')}>
          {name}
          {reserved ? (
            <span className="text-muted">
              {' '}
              · {recipient.tracking_number} {recipient.status === 'filed' ? 'filed' : 'reserved'}
            </span>
          ) : recipient.status === 'skipped' ? (
            <span className="text-muted"> · not sent</span>
          ) : null}
        </span>
        {recipient.status === 'held' ? (
          <span className="mt-0.5 block truncate text-115 text-warn">
            Held: {recipient.hold_reason}
          </span>
        ) : recipient.status === 'pending' && !reserved ? (
          <Unresolved recipientId={recipient.id} />
        ) : null}
      </span>
      {editable ? (
        <span className="relative inline-flex min-w-0 items-center gap-1">
          <select
            aria-label={`How ${name} is filed with`}
            title="Change how it files"
            value={recipient.channel}
            onChange={(event) => onChannel(event.target.value)}
            className="min-w-0 cursor-pointer appearance-none truncate bg-transparent pr-3 text-125 text-ink-2 outline-none hover:text-ink focus-visible:shadow-ring-focus-strong"
          >
            {CHANNELS.map((channel) => (
              <option key={channel.value} value={channel.value}>
                {channel.value === 'portal' ? filesBy('portal', agency) : channel.label}
              </option>
            ))}
          </select>
          <span className="pointer-events-none absolute right-0 opacity-0 group-hover:opacity-100">
            <ChevronDown size={9} width={2.6} stroke="var(--color-muted)" />
          </span>
        </span>
      ) : (
        <span className="truncate text-ink-2">{filesBy(recipient.channel, agency)}</span>
      )}
      <span className="min-w-0 truncate text-muted">{sendsTo(recipient.channel, agency)}</span>
      {editable ? (
        <button
          type="button"
          aria-label={`Take ${name} off this letter`}
          title="Take it off this letter"
          onClick={onRemove}
          className="inline-flex justify-self-end rounded-4 hover:bg-hover"
        >
          <CloseIcon size={12} width={2.2} stroke="var(--color-muted-2)" />
        </button>
      ) : (
        <span />
      )}
    </div>
  )
}
