import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import type { Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { ActionDialog, type ActionSpec } from '../../components/ActionDialog'
import { ClockIcon, CloseIcon, ExpandIcon, MoreIcon, ShrinkIcon } from '../../components/icons'
import { Menu, type MenuItem } from '../../components/Menu'
import { LoadFailed, Loading } from '../../components/States'
import { cx } from '../../components/ui'
import { isOverdueStatus, statusText } from '../../lib/status'
import { useNow } from '../../lib/tasks'
import {
  CHANNEL,
  SENSITIVITY,
  daysFigure,
  humanize,
  useDefinitions,
  useNameOf,
  usePeople,
} from '../../lib/vocab'
import { addDeadline, editRequest, recordEvent, type Context } from '../request/actions'
import { useRefreshDocket } from './BulkDialogs'
import { StatusGlyph } from './parts'

type Row = Schemas['DocketRowOut']
type Detail = Schemas['RequestDetailOut']

const MONTH_DAY = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})
const short = (civil: string | null | undefined) =>
  civil ? MONTH_DAY.format(new Date(`${civil.slice(0, 10)}T00:00:00Z`)) : '—'

/** The 30 px icon buttons of the panel's corner (032). */
function CornerButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className="inline-flex size-[30px] shrink-0 items-center justify-center rounded-8 hover:bg-hover"
    >
      {children}
    </button>
  )
}

function Section({
  title,
  action,
  children,
}: {
  title: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex h-[18px] items-center">
        <span className="text-12 font-medium text-ink">{title}</span>
        {action ? <span className="ml-auto">{action}</span> : null}
      </div>
      {children}
    </div>
  )
}

const LINK = 'text-12 font-medium text-brand hover:text-brand-dark'

/**
 * A request opened beside the Docket (032), or filling the page (033, `expanded`): the summary,
 * the tabs, and the Overview (deadlines, the request's facts, the latest correspondence; in the
 * expanded view also its items). The other tabs are the request page's own, so they open it.
 */
export function DetailPanel({
  id,
  row,
  expanded,
  onExpand,
  onClose,
  moreItems,
}: {
  id: string
  row: Row | undefined
  expanded: boolean
  onExpand: (expanded: boolean) => void
  onClose: () => void
  /** The row menu's actions (029), under "More". */
  moreItems: MenuItem[]
}) {
  const me = useMe()
  const definitions = useDefinitions()
  const people = usePeople()
  const nameOf = useNameOf()
  const refresh = useRefreshDocket()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const detail = $api.useQuery('get', '/api/records/requests/{request_id}', {
    params: { path: { request_id: id } },
  })
  const request = detail.data

  const context = (): Context | null =>
    request && definitions.data
      ? { request, definitions: definitions.data, people: people.data ?? [], me, attachments: [] }
      : null
  const open = (make: (ctx: Context) => ActionSpec) => {
    const ctx = context()
    if (ctx) setSpec(make(ctx))
  }

  const body = !request ? (
    detail.isError ? (
      <LoadFailed what="the request" error={detail.error} onRetry={() => void detail.refetch()} />
    ) : (
      <Loading what="Opening the request" />
    )
  ) : (
    <Overview
      request={request}
      expanded={expanded}
      kindLabel={(code) =>
        definitions.data?.deadline_kinds.find((k) => k.code === code)?.label ?? code
      }
      assignee={nameOf(request.assigned_to_user_id)}
      onAddDeadline={() => open(addDeadline)}
      onEdit={() => open(editRequest)}
    />
  )

  const status = request?.current_status ?? row?.current_status ?? ''
  const late = isOverdueStatus(status)
  const days = daysFigure(row?.days_to_next_deadline)
  const tabs: Array<{ label: string; count?: number; tab?: string }> = request
    ? [
        {
          label: 'Correspondence',
          count: request.events.filter((e) => e.event_type !== 'note').length,
          tab: 'correspondence',
        },
        { label: 'Items', count: request.items.length },
        {
          label: 'Notes',
          count: request.events.filter((e) => e.event_type === 'note').length,
          tab: 'notes',
        },
        { label: 'Timeline', tab: 'timeline' },
        { label: 'Files', tab: 'files' },
        { label: 'Audit' },
      ]
    : []

  return (
    <section
      aria-label="The request"
      className={cx(
        'flex min-h-0 min-w-0 flex-col overflow-hidden rounded-16 bg-card-white shadow-card',
        expanded ? 'flex-1' : 'flex-[0_0_520px]',
      )}
    >
      <div className="flex shrink-0 items-start gap-3 px-5 pt-[18px] pb-3">
        <div className="min-w-0 flex-1">
          <div className="text-12 text-muted">
            {row?.tracking_number ?? request?.tracking_number} ·{' '}
            {row?.agency_name ?? request?.agency_name}
          </div>
          <div className="mt-1 text-16 leading-[1.3] font-medium text-ink">
            {row?.subject ?? request?.subject}
          </div>
          {status ? (
            <div className="mt-2 flex items-center gap-2 text-125 whitespace-nowrap">
              {late ? (
                <ClockIcon size={15} stroke="var(--color-danger)" />
              ) : (
                <StatusGlyph code={status} />
              )}
              <span className={late ? 'font-medium text-danger' : 'font-medium text-ink'}>
                {statusText(status)}
              </span>
              {row?.next_deadline_at ? (
                <span className="text-muted">
                  · {short(row.next_deadline_at)} ·{' '}
                  <span className={days.overdue ? 'font-semibold text-danger' : ''}>
                    {days.text}
                  </span>
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2.5">
          <div className="flex items-center gap-0.5">
            <Menu label="More" width={230} items={moreItems}>
              <span className="inline-flex size-[30px] shrink-0 items-center justify-center rounded-8 hover:bg-hover">
                <MoreIcon size={15} stroke="var(--color-ink-3)" />
              </span>
            </Menu>
            {expanded ? (
              <CornerButton label="Back to split" onClick={() => onExpand(false)}>
                <ShrinkIcon size={15} stroke="var(--color-ink-3)" />
              </CornerButton>
            ) : (
              <CornerButton label="Open full" onClick={() => onExpand(true)}>
                <ExpandIcon size={15} stroke="var(--color-ink-3)" />
              </CornerButton>
            )}
            <CornerButton label="Close" onClick={onClose}>
              <CloseIcon size={15} stroke="var(--color-ink-3)" />
            </CornerButton>
          </div>
          <button
            type="button"
            disabled={!request}
            onClick={() => open(recordEvent)}
            className="inline-flex h-[30px] items-center rounded-9 border-[1.5px] border-line bg-card-white px-3 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-page"
          >
            Record event
          </button>
        </div>
      </div>
      <div className="flex shrink-0 gap-[18px] overflow-hidden border-b border-hairline px-5">
        <span className="inline-flex h-[38px] shrink-0 items-baseline gap-1.5 text-125 font-medium whitespace-nowrap text-ink shadow-[inset_0_-2px_0_var(--color-ink)]">
          <span className="self-center">Overview</span>
        </span>
        {tabs.map((tab) => (
          <Link
            key={tab.label}
            to={`/requests/${id}${tab.tab ? `?tab=${tab.tab}` : ''}`}
            className="inline-flex h-[38px] shrink-0 items-center gap-1.5 text-125 whitespace-nowrap text-muted hover:text-ink"
          >
            {tab.label}
            {tab.count ? <span className="text-115 text-muted-2">{tab.count}</span> : null}
          </Link>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">{body}</div>
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          void refresh()
        }}
      />
    </section>
  )
}

function Overview({
  request,
  expanded,
  kindLabel,
  assignee,
  onAddDeadline,
  onEdit,
}: {
  request: Detail
  expanded: boolean
  kindLabel: (code: string) => string
  assignee: string | null
  onAddDeadline: () => void
  onEdit: () => void
}) {
  const deadlines = request.deadlines
    .filter((d) => d.status === 'active' || d.status === 'satisfied' || d.status === 'missed')
    .toSorted((a, b) => a.due_at.localeCompare(b.due_at))
  const filing = request.events.find((e) => e.event_type === 'submission' && !e.voided_at)
  const latest = request.events
    .filter((e) => e.event_type !== 'note' && !e.voided_at)
    .toSorted((a, b) => b.occurred_at.localeCompare(a.occurred_at))
  const today = useNow().toISOString().slice(0, 10)

  const deadlinesBlock = (
    <Section
      title="Deadlines"
      action={
        <button type="button" onClick={onAddDeadline} className={LINK}>
          Add
        </button>
      }
    >
      {deadlines.length === 0 ? (
        <span className="text-125 text-muted">No deadline running.</span>
      ) : (
        deadlines.map((deadline) => {
          const done = deadline.status === 'satisfied'
          const late = !done && (deadline.status === 'missed' || deadline.due_at < today)
          return (
            <div key={deadline.id} className="flex h-[22px] items-center gap-2.5 text-125">
              <span
                className={cx(
                  'size-[7px] shrink-0 rounded-pill',
                  late ? 'bg-danger' : done ? 'bg-ok-dot' : 'bg-line-control',
                )}
              />
              <span className="flex-1 truncate text-ink">{kindLabel(deadline.deadline_type)}</span>
              {deadline.provision || deadline.legal_basis ? (
                <span className="text-115 whitespace-nowrap text-muted">
                  {deadline.provision ?? deadline.legal_basis}
                </span>
              ) : null}
              <span
                className={cx(
                  'w-[52px] text-right whitespace-nowrap',
                  late
                    ? 'font-medium text-danger'
                    : done
                      ? 'font-medium text-ok-dot'
                      : 'text-ink-2',
                )}
              >
                {short(deadline.due_at)}
              </span>
            </div>
          )
        })
      )}
    </Section>
  )

  const facts: Array<[string, string]> = [
    [
      'Filed',
      request.filed_at
        ? `${short(request.filed_at)}${filing ? ` · ${(CHANNEL[filing.channel] ?? filing.channel).toLowerCase()}` : ''}`
        : 'Not yet',
    ],
    // 032 draws the agency's own number under Filed; shown only when there is one.
    ...(request.agency_reference_number
      ? [['Agency ref', request.agency_reference_number] as [string, string]]
      : []),
    ['Assigned to', assignee ?? 'Nobody'],
    ['Sensitivity', SENSITIVITY[request.sensitivity] ?? request.sensitivity],
  ]
  const requestBlock = (
    <Section
      title="Request"
      action={
        <button type="button" onClick={onEdit} className={LINK}>
          Edit
        </button>
      }
    >
      {facts.map(([label, value]) => (
        <div key={label} className="flex gap-3.5 text-125 leading-[1.5]">
          <span className="w-24 shrink-0 text-muted">{label}</span>
          <span className="min-w-0 flex-1 truncate text-ink">{value}</span>
        </div>
      ))}
    </Section>
  )

  const latestBlock = (
    <Section
      title="Latest"
      action={
        latest.length > 0 ? (
          <Link to={`/requests/${request.id}?tab=correspondence`} className={LINK}>
            All {latest.length}
          </Link>
        ) : undefined
      }
    >
      {latest.length === 0 ? (
        <span className="text-125 text-muted">Nothing yet.</span>
      ) : (
        latest.slice(0, 2).map((event, index) => (
          <div
            key={event.id}
            className={cx(
              'flex flex-col gap-1.5 rounded-14 px-3.5 py-3',
              index === 0 ? 'bg-surface-row shadow-field-soft' : 'bg-card-white shadow-field',
            )}
          >
            <div className="flex items-baseline gap-2.5 text-125">
              <span className="min-w-0 flex-1 truncate font-medium text-ink">
                {event.direction === 'inbound'
                  ? request.agency_name
                  : (CHANNEL[event.channel] ?? event.channel)}
              </span>
              <span className="text-115 whitespace-nowrap text-muted">
                {short(event.occurred_at)}
              </span>
            </div>
            <div className="line-clamp-3 text-125 leading-[1.6] text-ink-2">
              {event.display_body}
            </div>
          </div>
        ))
      )}
    </Section>
  )

  if (!expanded) {
    return (
      <div className="flex flex-col gap-[22px] px-5 py-[18px]">
        {deadlinesBlock}
        {requestBlock}
        {latestBlock}
      </div>
    )
  }
  return (
    <div className="flex items-start gap-10 p-5">
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {deadlinesBlock}
        {requestBlock}
        <Section title="Items">
          {request.items.length === 0 ? (
            <span className="text-125 text-muted">No items.</span>
          ) : (
            request.items
              .toSorted((a, b) => a.sort_order - b.sort_order)
              .map((item) => (
                <div key={item.id} className="flex gap-2 text-125 leading-[1.5]">
                  <span className="w-4 shrink-0 text-muted">{item.item_number}</span>
                  <span className="flex-1 text-ink">{item.description}</span>
                  <span className="whitespace-nowrap text-muted">
                    {item.disposition ? humanize(item.disposition) : 'Open'}
                  </span>
                </div>
              ))
          )}
        </Section>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-6">{latestBlock}</div>
    </div>
  )
}
