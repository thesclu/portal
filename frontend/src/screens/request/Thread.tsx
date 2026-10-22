import { useState, type ReactNode } from 'react'

import type { Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import type { ActionSpec } from '../../components/ActionDialog'
import {
  AssignIcon,
  ChatIcon,
  DownloadIcon,
  FilePlusIcon,
  ForwardIcon,
  MoreIcon,
  PaperclipIcon,
  PlusIcon,
  ReturnIcon,
  SentIcon,
  TaskBoxIcon,
} from '../../components/icons'
import { Menu, type MenuItem } from '../../components/Menu'
import { Avatar, cx } from '../../components/ui'
import { formatDayTime, formatInstant, formatShortDate, formatShortInstant } from '../../lib/dates'
import { initialsOf } from '../../lib/presence'
import { isTeam } from '../../lib/roles'
import { fileMeta, fileTag, foldThread, senderName } from '../../lib/requestView'
import { statusText } from '../../lib/status'
import { CHANNEL, fileSize, humanize, useNameOf } from '../../lib/vocab'
import * as actions from './actions'

type Detail = Schemas['RequestDetailOut']
type Event = Schemas['EventOut']
type Attachment = Schemas['AttachmentOut']
type SourceNote = Schemas['SourceNoteOut']
type Act = (make: (context: actions.Context) => ActionSpec) => void

/** The white card every tab sits in (062): 16 px round, the float shadow. */
const CARD = 'overflow-hidden rounded-16 shadow-float'

const download = (file: Attachment) => `/api/records/attachments/${file.id}/download`

/** Who an event is from, in a word: the person who logged it, the agency, or the system. */
function useWho(request: Detail) {
  const nameOf = useNameOf()
  // P23b: something the agency sent names who sent it when that was recorded (102's "From", a
  // call's contact); otherwise the agency.
  const me = useMe()
  const contacts = $api.useQuery(
    'get',
    '/api/records/agencies/{agency_id}/contacts',
    { params: { path: { agency_id: request.agency_id } } },
    // The same read the page makes (a requester reads no directory).
    { enabled: isTeam(me.records_role) },
  )
  return (event: Event) =>
    event.direction === 'inbound'
      ? (event.actor_agency_contact_id &&
          contacts.data?.find((c) => c.id === event.actor_agency_contact_id)?.name) ||
        request.agency_name
      : (nameOf(event.actor_user_id) ?? (event.direction === 'internal' ? 'System' : 'Your team'))
}

/* ── Correspondence (062) ───────────────────────────────────────────────── */

export function Correspondence({
  request,
  attachments,
  sourceNotes,
  canAct,
  act,
  onReview,
  onReply,
  onNotes,
  composer,
  empty,
}: {
  request: Detail
  attachments: Attachment[]
  sourceNotes: SourceNote[]
  canAct: boolean
  act: Act
  onReview: (event: Event) => void
  onReply: (compose: actions.Compose) => void
  onNotes: () => void
  /** The composer or the just-sent banner, drawn where the Reply bar is. */
  composer: ReactNode
  /** What the card says when nothing has been sent or received (076). */
  empty: ReactNode
}) {
  const events = request.events
    .filter((event) => event.event_type !== 'note')
    .toSorted((a, b) => a.occurred_at.localeCompare(b.occurred_at))
  const [expanded, setExpanded] = useState(false)
  const [opened, setOpened] = useState<Set<string>>(new Set())
  const [closed, setClosed] = useState<Set<string>>(new Set())
  if (events.length === 0)
    return empty ? <div className={cx(CARD, 'bg-card-white')}>{empty}</div> : null
  const newest = events.at(-1)
  const isOpen = (event: Event) =>
    opened.has(event.id) || (event.id === newest?.id && !closed.has(event.id))
  const toggle = (event: Event) => {
    const open = isOpen(event)
    setOpened((set) => {
      const next = new Set(set)
      if (open) next.delete(event.id)
      else next.add(event.id)
      return next
    })
    setClosed((set) => {
      const next = new Set(set)
      if (open) next.add(event.id)
      else next.delete(event.id)
      return next
    })
  }
  const replyTo = [...events].reverse().find((e) => e.direction === 'inbound' && e.source_id)
  const pieces = foldThread(events.length, expanded)
  return (
    <div className={cx(CARD, 'flex flex-col bg-card-white')}>
      {pieces.map((piece, index) => {
        if (piece.kind === 'more') {
          const hidden = piece.indexes.map((i) => events[i])
          return (
            <button
              key="more"
              type="button"
              title={`${hidden.length} more · ${formatShortDate(hidden[0].occurred_at)} – ${formatShortDate(hidden.at(-1)?.occurred_at)}`}
              onClick={() => setExpanded(true)}
              className="relative h-[30px] bg-surface-soft"
            >
              <span className="absolute inset-x-0 top-[13px] h-px bg-fill-muted" />
              <span className="absolute inset-x-0 top-4 h-px bg-fill-muted" />
              <span className="absolute top-0.5 left-[22px] box-border inline-flex size-[26px] items-center justify-center rounded-pill border border-line bg-card-white text-115 font-semibold text-ink-2 shadow-menu">
                {hidden.length}
              </span>
            </button>
          )
        }
        const event = events[piece.index]
        const previousOpen =
          index > 0 && pieces[index - 1].kind === 'message'
            ? isOpen(events[(pieces[index - 1] as { index: number }).index])
            : false
        return isOpen(event) ? (
          <OpenMessage
            key={event.id}
            request={request}
            event={event}
            files={attachments.filter((f) => f.source_id && f.source_id === event.source_id)}
            notes={
              sourceNotes.filter((n) => n.source_id === event.source_id && !n.voided_at).length
            }
            first={index === 0}
            canAct={canAct}
            act={act}
            onReview={onReview}
            onReply={onReply}
            onNotes={onNotes}
            onCollapse={() => toggle(event)}
          />
        ) : (
          <CollapsedMessage
            key={event.id}
            request={request}
            event={event}
            files={attachments.filter((f) => f.source_id && f.source_id === event.source_id)}
            ruled={previousOpen}
            onOpen={() => toggle(event)}
          />
        )
      })}
      <div className="px-4 pt-2 pb-[18px]">
        {composer ??
          (canAct && request.inbound_alias_email ? (
            <button
              type="button"
              onClick={() =>
                onReply(
                  replyTo?.source_id
                    ? { kind: 'reply', sourceId: replyTo.source_id, forward: false }
                    : { kind: 'new' },
                )
              }
              className="flex w-full items-center gap-3 rounded-18 bg-card-white px-5 py-3.5 text-left shadow-float"
            >
              <span className="inline-flex size-[22px] shrink-0 items-center justify-center">
                <PlusIcon size={15} width={1.8} stroke="var(--color-ink-2)" />
              </span>
              <span className="flex-1 text-135 leading-[1.3] text-text-placeholder">
                {replyTo ? 'Reply…' : 'Write to the agency…'}
              </span>
              <span className="inline-flex size-[22px] shrink-0 items-center justify-center">
                <ReturnIcon size={16} width={1.8} stroke="var(--color-muted-2)" />
              </span>
            </button>
          ) : null)}
      </div>
    </div>
  )
}

function CollapsedMessage({
  request,
  event,
  files,
  ruled,
  onOpen,
}: {
  request: Detail
  event: Event
  files: Attachment[]
  ruled: boolean
  onOpen: () => void
}) {
  const me = useMe()
  const who = useWho(request)
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cx(
        'flex items-center gap-3 bg-surface-soft px-[22px] py-3 text-left',
        ruled && 'border-t border-hairline',
      )}
    >
      <span
        className={cx(
          'max-w-[150px] flex-[0_0_auto] truncate text-125 font-medium text-ink-soft',
          event.voided_at && 'line-through',
        )}
      >
        {who(event)}
      </span>
      <span className="min-w-0 flex-1 truncate text-12 text-muted" />
      <span className="mr-0.5 inline-flex items-center gap-[9px]">
        {event.direction === 'outbound' ? (
          <span title="Sent by your team" className="inline-flex shrink-0 items-center">
            <SentIcon size={13} width={2} stroke="var(--color-muted)" />
          </span>
        ) : null}
        {files.length > 0 ? (
          <span
            title={`${files.length} ${files.length === 1 ? 'attachment' : 'attachments'} · ${files.map((f) => f.filename).join(', ')}`}
            className="inline-flex shrink-0 items-center"
          >
            <PaperclipIcon size={13} width={2} stroke="var(--color-muted)" />
          </span>
        ) : null}
      </span>
      <span
        title={formatInstant(event.occurred_at, me.timezone)}
        className="text-11 whitespace-nowrap text-muted"
      >
        {formatShortInstant(event.occurred_at, me.timezone)}
      </span>
    </button>
  )
}

/** A toolbar button of a message (062): 32 × 30, the 13 px icon in the tool grey. */
function Tool({
  label,
  onClick,
  divided,
  children,
}: {
  label: string
  onClick: () => void
  divided?: boolean
  children: ReactNode
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className={cx(
        'inline-flex h-[30px] w-8 items-center justify-center hover:bg-hover',
        divided && 'border-l border-[rgba(11,31,58,0.09)]',
      )}
    >
      {children}
    </button>
  )
}

function OpenMessage({
  request,
  event,
  files,
  notes,
  first,
  canAct,
  act,
  onReview,
  onReply,
  onNotes,
  onCollapse,
}: {
  request: Detail
  event: Event
  files: Attachment[]
  notes: number
  first: boolean
  canAct: boolean
  act: Act
  onReview: (event: Event) => void
  onReply: (compose: actions.Compose) => void
  onNotes: () => void
  onCollapse: () => void
}) {
  const me = useMe()
  const who = useWho(request)
  const message = $api.useQuery(
    'get',
    '/api/records/sources/{source_id}/message',
    { params: { path: { source_id: event.source_id ?? '' } } },
    { enabled: Boolean(event.source_id), staleTime: 5 * 60_000 },
  )
  const mail = message.data?.readable ? message.data : null
  const from = mail ? senderName(mail.from_addr) : { name: who(event), email: '' }
  const to = mail ? [...mail.to, ...mail.cc] : []
  const live = canAct && !event.voided_at
  const more: MenuItem[] = [
    ...(event.direction === 'inbound'
      ? [{ label: 'Record as…', onPick: () => act((c) => actions.classifyEvent(c, event)) }]
      : []),
    ...(event.event_type === 'production'
      ? [{ label: 'Review production', onPick: () => onReview(event) }]
      : []),
    ...(files.length > 0
      ? [
          {
            label: 'Mark files as records',
            onPick: () => act((c) => actions.markRecords(c, event)),
          },
        ]
      : []),
    ...(event.source_id
      ? [
          {
            label: 'View original',
            separator: true,
            onPick: () => window.open(`/api/records/sources/${event.source_id}/raw`, '_blank'),
          },
        ]
      : []),
    {
      label: 'Void…',
      separator: true,
      danger: true,
      onPick: () => act((c) => actions.voidEvent(c, event)),
    },
  ]
  return (
    <div className={cx(!first && 'border-t border-hairline')}>
      <div className="border-b border-hairline px-[22px] pt-3.5 pb-3">
        <div className="grid grid-cols-[44px_1fr_auto] items-baseline gap-x-2.5 gap-y-0.5 text-12">
          <button type="button" onClick={onCollapse} className="text-left font-semibold text-ink">
            From
          </button>
          <span className="min-w-0 truncate">
            <span className="font-medium text-ink">{from.name}</span>
            {from.email ? <span className="text-ink-2"> &lt;{from.email}&gt;</span> : null}
            {!mail ? (
              <span className="text-muted">
                {' '}
                · {CHANNEL[event.channel] ?? event.channel} ·{' '}
                {humanize(event.event_type).toLowerCase()}
              </span>
            ) : null}
          </span>
          <span
            title={formatInstant(event.occurred_at, me.timezone)}
            className="inline-flex items-center text-12 whitespace-nowrap text-muted"
          >
            {notes > 0 ? (
              <button
                type="button"
                title={`${notes} ${notes === 1 ? 'note' : 'notes'} on this email`}
                onClick={onNotes}
                className="mr-2.5 inline-flex items-center gap-1 text-muted"
              >
                <ChatIcon size={13} width={1.9} stroke="var(--color-muted)" />
                {notes}
              </button>
            ) : null}
            {formatShortInstant(event.occurred_at, me.timezone)}
          </span>
          {to.length > 0 ? (
            <>
              <span className="font-semibold text-ink">To</span>
              <span
                className="min-w-0 truncate text-ink-2"
                title={`To: ${mail?.to.join(', ')}${mail?.cc.length ? `\nCc: ${mail.cc.join(', ')}` : ''}`}
              >
                {to.map((address) => senderName(address).name).join(', ')}
              </span>
              <span />
            </>
          ) : null}
        </div>
        {live ? (
          <div className="mt-3 flex items-center">
            <span className="inline-flex items-stretch overflow-hidden rounded-9 shadow-ring-hairline">
              {event.source_id ? (
                <Tool
                  label="Attach to a request"
                  onClick={() => act((c) => actions.attachElsewhere(c, event))}
                >
                  <FilePlusIcon size={13} width={1.8} stroke="var(--color-icon)" />
                </Tool>
              ) : null}
              {event.source_id ? (
                <Tool
                  label="Assign to a teammate"
                  divided
                  onClick={() => act((c) => actions.assignSource(c, event))}
                >
                  <AssignIcon size={13} width={1.8} stroke="var(--color-icon)" />
                </Tool>
              ) : null}
              <Tool
                label="Create a task from it"
                divided={Boolean(event.source_id)}
                onClick={() => act((c) => actions.createTask(c, event))}
              >
                <TaskBoxIcon size={13} width={1.8} stroke="var(--color-icon)" />
              </Tool>
              <Menu
                label="More on this message"
                items={more}
                align="left"
                width={200}
                buttonClassName="rounded-none"
              >
                <span className="inline-flex h-[30px] w-8 items-center justify-center border-l border-[rgba(11,31,58,0.09)] hover:bg-hover">
                  <MoreIcon size={13} stroke="var(--color-ink-2)" />
                </span>
              </Menu>
            </span>
            {event.direction === 'inbound' && event.source_id ? (
              <button
                type="button"
                title="Forward"
                aria-label="Forward"
                onClick={() =>
                  onReply({ kind: 'reply', sourceId: event.source_id as string, forward: true })
                }
                className="ml-auto inline-flex h-[30px] w-8 items-center justify-center rounded-9 shadow-ring-hairline hover:bg-hover"
              >
                <ForwardIcon size={13} width={1.8} stroke="var(--color-icon)" />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
      <div
        className={cx(
          'px-[22px] pt-4 text-125 leading-[1.8] whitespace-pre-wrap text-ink',
          event.voided_at && 'text-muted line-through',
        )}
      >
        {mail?.body || event.display_body}
      </div>
      {event.voided_at ? (
        <div className="px-[22px] pt-2 text-12 text-muted">Voided: {event.void_reason}</div>
      ) : null}
      {files.length > 0 ? (
        <div className="flex flex-wrap gap-2 px-[22px] pt-3.5 pb-[18px]">
          {files.map((file) => (
            <a
              key={file.id}
              href={download(file)}
              className="inline-flex items-center gap-[9px] rounded-10 bg-brand-tint px-[11px] py-[7px] text-115 text-ink hover:text-ink"
            >
              <span className="box-border inline-flex h-[26px] w-[22px] items-end justify-center rounded-[3px] bg-red-tint-2 pb-[3px] text-[7.5px] font-bold text-danger">
                {fileTag(file.filename)}
              </span>
              <span className="flex flex-col leading-[1.3]">
                <span>{file.filename}</span>
                <span className="text-105 text-muted">{fileSize(file.size_bytes)}</span>
              </span>
            </a>
          ))}
        </div>
      ) : (
        <div className="pb-[18px]" />
      )}
    </div>
  )
}

/* ── Timeline (065) ─────────────────────────────────────────────────────── */

type Row = {
  at: string
  key: string
  dot: 'brand' | 'hollow' | 'grey' | 'orange'
  kind: 'card' | 'line' | 'note'
  who?: string | null
  meta?: ReactNode
  body: ReactNode
  voided?: boolean
  reason?: string | null
}

export function Timeline({ request, attachments }: { request: Detail; attachments: Attachment[] }) {
  const me = useMe()
  const nameOf = useNameOf()
  const who = useWho(request)
  const eventById = new Map(request.events.map((event) => [event.id, event]))
  const deadlineFrom = new Map(
    request.deadlines.filter((d) => d.source_event_id).map((d) => [d.source_event_id, d]),
  )
  const rows: Row[] = [
    ...request.events.map((event): Row => {
      const files = attachments.filter((f) => f.source_id && f.source_id === event.source_id)
      if (event.event_type === 'note')
        return {
          at: event.occurred_at,
          key: `e${event.id}`,
          dot: 'grey',
          kind: 'note',
          who: nameOf(event.actor_user_id) ?? 'Someone',
          body: event.display_body,
          voided: Boolean(event.voided_at),
          reason: event.void_reason,
        }
      if (event.event_type === 'submission')
        return {
          at: event.occurred_at,
          key: `e${event.id}`,
          dot: 'hollow',
          kind: 'line',
          body: [
            `Filed by ${(CHANNEL[event.channel] ?? event.channel).toLowerCase()}`,
            nameOf(event.actor_user_id),
          ]
            .filter(Boolean)
            .join(' · '),
          voided: Boolean(event.voided_at),
          reason: event.void_reason,
        }
      const opened = deadlineFrom.get(event.id)
      const conflicts =
        opened &&
        request.deadlines.find(
          (d) =>
            d.id !== opened.id &&
            d.status === 'active' &&
            opened.status === 'active' &&
            d.deadline_type === opened.deadline_type &&
            d.request_item_id === opened.request_item_id,
        )
      return {
        at: event.occurred_at,
        key: `e${event.id}`,
        dot: conflicts ? 'orange' : 'brand',
        kind: 'card',
        who: who(event),
        meta: conflicts ? (
          <span className="text-warn">Conflicts with {formatShortDate(conflicts.due_at)}</span>
        ) : files.length > 0 ? (
          <span className="inline-flex items-center gap-[5px] text-muted">
            <PaperclipIcon size={13} width={2} stroke="var(--color-muted)" />
            {files.length}
          </span>
        ) : event.direction === 'outbound' ? (
          <span className="text-muted">Sent</span>
        ) : null,
        body: event.display_body,
        voided: Boolean(event.voided_at),
        reason: event.void_reason,
      }
    }),
    ...request.status_history.map((change): Row => {
      const trigger = change.triggering_event_id ? eventById.get(change.triggering_event_id) : null
      return {
        at: change.effective_at,
        key: `s${change.id}`,
        dot: 'hollow',
        kind: 'line',
        body: (
          <>
            Status → <span className="font-medium text-ink">{statusText(change.status)}</span>
            {change.reason === 'deadline_expired'
              ? ' · a deadline passed'
              : trigger?.actor_user_id
                ? ` · ${nameOf(trigger.actor_user_id)}`
                : ''}
          </>
        ),
        voided: Boolean(change.voided_at),
        reason: trigger?.void_reason ?? null,
      }
    }),
    ...request.deadlines
      .filter((d) => d.status === 'satisfied' && d.satisfied_by_event_id)
      .map((d): Row => ({
        at: eventById.get(d.satisfied_by_event_id ?? '')?.occurred_at ?? d.due_at,
        key: `d${d.id}`,
        dot: 'hollow',
        kind: 'line',
        body: `${humanize(d.deadline_type)} deadline met`,
      })),
  ].toSorted((a, b) => b.at.localeCompare(a.at))
  if (rows.length === 0)
    return (
      <div className={cx(CARD, 'bg-card px-5 py-14 text-center text-125 text-muted')}>
        Nothing has happened yet.
      </div>
    )
  return (
    <div className={cx(CARD, 'bg-card px-5 pt-5 pb-1')}>
      {rows.map((row, index) => (
        <div key={row.key} className="grid grid-cols-[52px_14px_1fr] items-start gap-x-2.5">
          <span className="pt-0.5 text-left text-12 text-muted">
            {formatShortInstant(row.at, me.timezone)}
          </span>
          <div className="flex flex-col items-center self-stretch">
            <span
              className={cx(
                'mt-[5px] box-border size-[11px] rounded-pill',
                row.dot === 'brand' && 'bg-brand',
                row.dot === 'grey' && 'bg-line-control',
                row.dot === 'orange' && 'bg-attention',
                row.dot === 'hollow' && 'border-2 border-line-control bg-card-white',
              )}
            />
            {index < rows.length - 1 ? <span className="mt-1 w-[1.5px] flex-1 bg-line" /> : null}
          </div>
          <div className={cx('min-w-0', index === rows.length - 1 ? 'pb-3.5' : 'pb-[18px]')}>
            {row.kind === 'card' ? (
              <div className="rounded-16 bg-card px-4 py-3 shadow-float">
                <div className="flex items-baseline gap-2">
                  <span
                    className={cx('text-13 font-semibold text-ink', row.voided && 'line-through')}
                  >
                    {row.who}
                  </span>
                  {row.meta ? <span className="ml-auto text-12">{row.meta}</span> : null}
                </div>
                <div
                  className={cx(
                    'mt-1 line-clamp-3 text-125 leading-body text-ink-2',
                    row.voided && 'line-through',
                  )}
                >
                  {row.body}
                </div>
                {row.voided ? (
                  <div className="mt-1 text-12 text-danger">
                    Voided{row.reason ? `: ${row.reason}` : ''}
                  </div>
                ) : null}
              </div>
            ) : row.kind === 'note' ? (
              <div
                className={cx(
                  'rounded-10 bg-page px-3.5 py-2.5 text-125 whitespace-pre-wrap text-ink-2',
                  row.voided && 'line-through',
                )}
              >
                <span className="font-semibold text-ink">{row.who}</span> · internal note
                {'\n'}
                {row.body}
              </div>
            ) : (
              <div
                className={cx('pt-px text-125 text-ink-2', row.voided && 'text-muted line-through')}
              >
                {row.body}
                {row.voided ? (
                  <span className="ml-1 text-12 text-danger no-underline">
                    Voided{row.reason ? `: ${row.reason}` : ''}
                  </span>
                ) : null}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

/* ── Notes (066) ────────────────────────────────────────────────────────── */

export function Notes({
  request,
  sourceNotes,
  composer,
}: {
  request: Detail
  sourceNotes: SourceNote[]
  /** The note writer (066 "Write a note…"). */
  composer: ReactNode
}) {
  const me = useMe()
  const nameOf = useNameOf()
  const who = useWho(request)
  const eventOf = (sourceId: string) =>
    request.events.find((event) => event.source_id === sourceId && event.event_type !== 'note')
  const bySource = new Map<string, SourceNote[]>()
  for (const note of sourceNotes.toSorted((a, b) => a.created_at.localeCompare(b.created_at))) {
    bySource.set(note.source_id, [...(bySource.get(note.source_id) ?? []), note])
  }
  const own = request.events
    .filter((event) => event.event_type === 'note')
    .toSorted((a, b) => a.occurred_at.localeCompare(b.occurred_at))
  const entry = (key: string, author: string, at: string, body: string, voided: boolean) => (
    <div key={key} className="flex items-start gap-3 py-3">
      <Avatar initials={initialsOf(author)} size={26} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-13 font-semibold text-ink">{author}</span>
          <span className="text-12 text-muted">{formatDayTime(at, me.timezone)}</span>
        </div>
        <div
          className={cx(
            'mt-[3px] text-13 leading-[1.6] whitespace-pre-wrap text-ink-2',
            voided && 'text-muted line-through',
          )}
        >
          {body}
        </div>
      </div>
    </div>
  )
  const groups = [...bySource.entries()]
  return (
    <div className={cx(CARD, 'flex flex-col bg-card')}>
      <div className="px-5 pt-1.5">
        {groups.length === 0 && own.length === 0 ? (
          <p className="py-10 text-center text-125 text-muted">No notes yet.</p>
        ) : null}
        {groups.map(([sourceId, notes]) => {
          const event = eventOf(sourceId)
          return (
            <div key={sourceId}>
              <div className="flex items-center gap-[7px] pt-3.5 text-12 text-muted">
                <ChatIcon size={13} width={1.9} stroke="var(--color-muted)" />
                <span>
                  On{' '}
                  <span className="text-ink-2 underline decoration-line-control underline-offset-[3px]">
                    {event
                      ? `${who(event)}’s ${event.channel === 'email' ? 'email' : 'message'} · ${formatShortInstant(event.occurred_at, me.timezone)}`
                      : 'a message'}
                  </span>
                </span>
              </div>
              <div className="my-2 mr-0 mb-1 ml-1.5 border-l-[1.5px] border-line pl-[18px]">
                {notes.map((note) =>
                  entry(
                    note.id,
                    nameOf(note.author_user_id) ?? 'Someone',
                    note.created_at,
                    note.body,
                    Boolean(note.voided_at),
                  ),
                )}
              </div>
            </div>
          )
        })}
        {groups.length > 0 && own.length > 0 ? <div className="my-1.5 h-px bg-line-row" /> : null}
        {own.map((event) =>
          entry(
            event.id,
            nameOf(event.actor_user_id) ?? 'Someone',
            event.occurred_at,
            event.display_body,
            Boolean(event.voided_at),
          ),
        )}
      </div>
      <div className="mt-auto px-5 pt-2.5 pb-[18px]">{composer}</div>
    </div>
  )
}

/** "Write a note…" (066): a note on the request, for the team only. */
export function NoteWriter({ onWrite }: { onWrite: () => void }) {
  return (
    <button
      type="button"
      onClick={onWrite}
      className="flex w-full items-center gap-3 rounded-18 bg-card-white px-5 py-3.5 text-left shadow-float"
    >
      <span className="inline-flex size-[22px] shrink-0 items-center justify-center">
        <PlusIcon size={15} width={1.8} stroke="var(--color-ink-2)" />
      </span>
      <span className="flex-1 text-135 leading-[1.3] text-text-placeholder">Write a note…</span>
      <span className="inline-flex size-[22px] shrink-0 items-center justify-center">
        <ReturnIcon size={16} width={1.8} stroke="var(--color-muted-2)" />
      </span>
    </button>
  )
}

/* ── Files (067) ────────────────────────────────────────────────────────── */

/** P21c: the request's letter in the drafting tool, at the top of Files: its revision, when it
 *  last changed, open review comments and the latest citation check (from the tool's webhooks),
 *  and its PDF and .docx fetched from the tool as the person reading. */
function ForkLetters({ requestId }: { requestId: string }) {
  const me = useMe()
  const letters = $api.useQuery(
    'get',
    '/api/drafting/requests/{request_id}/fork/letter',
    { params: { path: { request_id: requestId } } },
    { enabled: isTeam(me.records_role), retry: false },
  )
  if (!letters.data || letters.data.letters.length === 0) return null
  return (
    <div className={cx(CARD, 'bg-card')}>
      <div className="flex items-center gap-2 border-b border-hairline px-5 py-3">
        <span className="text-125 text-ink-2">The letter in the drafting tool</span>
      </div>
      {letters.data.letters.map((letter, index) => {
        const base = `/api/drafting/drafts/${letter.draft_id}/fork/file?recipient_id=${encodeURIComponent(letter.recipient_id)}`
        const check = letter.verification as { checked?: number; verified?: number } | null
        const meta = [
          letter.version_number != null ? `rev ${letter.version_number}` : null,
          letter.revised_at
            ? `changed ${formatShortInstant(letter.revised_at, me.timezone)}`
            : null,
          letter.open_suggestions > 0
            ? `${letter.open_suggestions} ${letter.open_suggestions === 1 ? 'suggestion' : 'suggestions'}`
            : null,
          check && typeof check.checked === 'number'
            ? `${check.verified ?? 0} of ${check.checked} citations verified`
            : null,
          letter.filed ? 'filed' : null,
        ].filter(Boolean)
        return (
          <div
            key={letter.recipient_id}
            className={cx(
              'flex items-center gap-3.5 px-5 py-3',
              index > 0 && 'border-t border-line-row',
            )}
          >
            <span className="box-border inline-flex h-9 w-[30px] shrink-0 items-end justify-center rounded-5 border border-line bg-page pb-[5px] text-[8.5px] font-bold tracking-[0.02em] text-danger">
              DOC
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-13 text-ink">{letter.filename}</div>
              <div className="mt-0.5 text-12 text-muted">{meta.join(' · ')}</div>
            </div>
            {letters.data.in_use ? (
              <span className="flex shrink-0 items-center gap-3 text-12 font-medium">
                <a href={base} className="text-brand">
                  PDF
                </a>
                <a href={`${base}&format=docx`} className="text-brand">
                  .docx
                </a>
              </span>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

export function Files({ request, attachments }: { request: Detail; attachments: Attachment[] }) {
  const me = useMe()
  const who = useWho(request)
  if (attachments.length === 0)
    return (
      <div className="flex flex-col gap-3.5">
        <ForkLetters requestId={request.id} />
        <div className={cx(CARD, 'bg-card px-5 py-14 text-center text-125 text-muted')}>
          No files on this request.
        </div>
      </div>
    )
  const groups = new Map<string, Attachment[]>()
  for (const file of attachments)
    groups.set(file.source_id, [...(groups.get(file.source_id) ?? []), file])
  return (
    <div className="flex flex-col gap-3.5">
      <ForkLetters requestId={request.id} />
      {[...groups.entries()].map(([sourceId, files]) => {
        const event = request.events.find(
          (e) => e.source_id === sourceId && e.event_type !== 'note',
        )
        return (
          <div key={sourceId} className={cx(CARD, 'bg-card')}>
            <div className="flex items-center gap-2 border-b border-hairline px-5 py-3">
              <span className="text-125 text-ink-2">
                {event ? (
                  <>
                    From <span className="font-medium text-ink">{who(event)}</span> ·{' '}
                    {formatShortInstant(event.occurred_at, me.timezone)} ·{' '}
                    {humanize(event.event_type)}
                  </>
                ) : (
                  'Added to the request'
                )}
              </span>
              {files.length > 1 ? (
                <a
                  href={`/api/records/produced/set?ids=${files.map((f) => f.id).join(',')}`}
                  className="ml-auto text-12 font-medium text-brand"
                >
                  Download all
                </a>
              ) : null}
            </div>
            {files.map((file, index) => (
              <div
                key={file.id}
                className={cx(
                  'flex items-center gap-3.5 px-5 py-3',
                  index > 0 && 'border-t border-line-row',
                )}
              >
                <span className="box-border inline-flex h-9 w-[30px] shrink-0 items-end justify-center rounded-kbd border border-line bg-page pb-[5px] text-[8.5px] font-bold tracking-[0.02em] text-danger">
                  {fileTag(file.filename)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-13 text-ink">{file.filename}</div>
                  <div className="mt-0.5 text-12 text-muted">
                    {fileMeta(file.page_count, fileSize(file.size_bytes))}
                    {file.role === 'produced_record' ? ' · record' : ''}
                  </div>
                </div>
                <a
                  href={download(file)}
                  title="Download"
                  aria-label={`Download ${file.filename}`}
                  className="inline-flex size-[30px] items-center justify-center rounded-8 hover:bg-hover"
                >
                  <DownloadIcon size={15} width={1.9} stroke="var(--color-ink-3)" />
                </a>
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}
