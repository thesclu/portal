/**
 * The reading pane for a message that arrived (106, 133): the header with the subject, the reason
 * or where it landed, and the one next step; the thread's earlier messages folded above (110,
 * 136); who it is from and to with the message's toolbar; the words and the files; and under it
 * the internal-notes lane (Flagged) or the reply (New, All). The notes lane and the reply are
 * never the same field: a note is the team's, a reply goes to the agency from the request's own
 * address.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { useMe } from '../../app/session'
import { ActionDialog, type ActionSpec } from '../../components/ActionDialog'
import {
  AssignIcon,
  ChatIcon,
  CheckIcon,
  ChevronRight,
  DotsIcon,
  FilePlusIcon,
  FlagReasonIcon,
  ForwardIcon,
  LockIcon,
  PaperclipIcon,
  SendingIcon,
  SentIcon,
  TaskBoxIcon,
} from '../../components/icons'
import { MenuPanel, type MenuItem } from '../../components/Menu'
import { Avatar, cx } from '../../components/ui'
import { formatDayTime, formatInstant, formatListTime, formatShortDate } from '../../lib/dates'
import { initialsOf } from '../../lib/presence'
import { senderName } from '../../lib/requestView'
import { useDefinitions, useNameOf, usePeople } from '../../lib/vocab'
import * as requestActions from '../request/actions'
import * as inboxActions from './actions'
import { AttachOthers } from './AttachOthers'
import { Composer } from './Composer'
import { FixResend, type Resend } from './FixResend'
import { useNow, useRefreshMail, type Message } from './mail'
import {
  NEXT_WORDS,
  chainOf,
  flagWords,
  foldChain,
  nextStep,
  senderOf,
  subjectOf,
  type Bucket,
  type RequestEvent,
  type Row,
} from './model'
import { Original } from './Original'
import {
  ComposerPill,
  FileChip,
  MoreFiles,
  NoteRow,
  PaneCard,
  QuietButton,
  SplitButton,
  Toast,
  Tool,
  ToolGroup,
} from './parts'
import { SentToast } from './OutboundPane'

/** What just happened to this message, by this person, while it stays on screen (127, 128, 132). */
export type Afterwards =
  { kind: 'handled'; words: string; undoable: boolean } | { kind: 'resent'; resend: Resend }

export function Reader({
  row,
  bucket,
  afterwards,
  onAfterwards,
  onOpenFile,
  onNext,
  onClose,
  composeRequest,
  noteRequest,
  originalRequest,
  attachRequest,
  className,
}: {
  /** A fixed width beside an open attachment (162). */
  className?: string
  /** A: open "Attach to a request". */
  attachRequest?: number
  row: Row
  bucket: Bucket
  afterwards: Afterwards | null
  onAfterwards: (next: Afterwards | null) => void
  onOpenFile: (attachmentId: string) => void
  /** Open the next message in the list (Next flagged, Reviewed and open next). */
  onNext: () => void
  onClose: () => void
  /** Keys pressed in the list (R, F): open the reply or the forward here. */
  composeRequest?: { kind: 'reply' | 'forward'; at: number } | null
  /** N: focus the note field. */
  noteRequest?: number
  /** Show original from the message menu. */
  originalRequest?: number
}) {
  const me = useMe()
  const client = useQueryClient()
  const navigate = useNavigate()
  const definitions = useDefinitions()
  const people = usePeople()

  const nameOf = useNameOf()
  const refreshMail = useRefreshMail()
  const id = row.source.id
  const path = { params: { path: { source_id: id } } }
  const landed = row.events[0]
  const flagged = row.source.flagged_for_review
  const message = $api.useQuery('get', '/api/records/sources/{source_id}/message', path, {
    staleTime: 5 * 60_000,
  })
  const notes = $api.useQuery('get', '/api/records/sources/{source_id}/notes', path, {
    enabled: flagged,
  })
  const candidates = $api.useQuery('get', '/api/records/sources/{source_id}/candidates', path)
  const detail = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}',
    { params: { path: { request_id: landed?.request_id ?? '' } } },
    { enabled: Boolean(landed) },
  )
  const files = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}/attachments',
    { params: { path: { request_id: landed?.request_id ?? '' } } },
    { enabled: Boolean(landed) },
  )
  // "Maya Torres is also writing a reply" (146): someone else's draft that answers this message.
  const drafts = $api.useQuery(
    'get',
    '/api/mail/messages',
    {
      params: {
        query: { bucket: 'drafts', request_id: landed?.request_id ?? undefined, limit: 50 },
      },
    },
    { enabled: Boolean(landed) },
  )

  const [menu, setMenu] = useState<'next' | 'more' | 'record' | null>(null)
  // Read only once the next-step menu is open: it is for "Start a new request from this".
  const agencies = $api.useQuery(
    'get',
    '/api/records/agencies',
    {},
    { staleTime: 5 * 60_000, enabled: menu === 'next' && !landed },
  )
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [composing, setComposing] = useState<
    { kind: 'reply' | 'forward' } | { draftId: string } | null
  >(null)
  const [sent, setSent] = useState<Message | null>(null)
  const [writingNote, setWritingNote] = useState(false)
  const [showOriginal, setShowOriginal] = useState(false)
  const [chainOpen, setChainOpen] = useState(false)
  const [allFiles, setAllFiles] = useState(false)
  const [dialog, setDialog] = useState<'fix' | 'others' | null>(null)
  const [scrolled, setScrolled] = useState(false)
  const [pane, setPane] = useState<HTMLElement | null>(null)
  const noteRef = useRef<HTMLTextAreaElement>(null)
  const candidatesRef = useRef<Schemas['CandidateOut'][]>([])
  candidatesRef.current = candidates.data ?? []

  // Keys from the list: R and F open the reply or forward; N the note field.
  useEffect(() => {
    if (composeRequest && landed) setComposing({ kind: composeRequest.kind })
  }, [composeRequest, landed])
  useEffect(() => {
    if (noteRequest && flagged) {
      setWritingNote(true)
      window.requestAnimationFrame(() => noteRef.current?.focus())
    }
  }, [noteRequest, flagged])
  useEffect(() => {
    if (originalRequest) setShowOriginal(true)
  }, [originalRequest])
  useEffect(() => {
    if (attachRequest) setSpec(inboxActions.attachTo(row, candidatesRef.current))
    // Only the key press opens it, not a change of the candidates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachRequest])

  // A menu closes on a click anywhere outside it.
  useEffect(() => {
    if (!menu) return
    function away(event: PointerEvent) {
      const target = event.target as HTMLElement | null
      if (
        !target?.closest(
          '[role="menu"], [aria-haspopup="menu"], [aria-label="More on this message"]',
        )
      )
        setMenu(null)
    }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [menu])

  const refresh = async () => {
    setSpec(null)
    await Promise.all(
      [
        '/api/records/inbox',
        '/api/records/sources/{source_id}/notes',
        '/api/records/home',
        '/api/records/requests/{request_id}',
      ].map((key) => client.invalidateQueries({ queryKey: ['get', key] })),
    )
  }

  const mail = message.data?.readable ? message.data : null
  const subject = mail?.subject || subjectOf(row)
  const meta = (row.source.raw_metadata ?? {}) as { from?: string; to?: string; cc?: string }
  // A copy of mail we sent (a bounce, 124) is read from what was kept when it went.
  const sentCopy = row.source.source_type === 'sent_email'
  const from = sentCopy
    ? { name: 'Your team', email: meta.from ?? '' }
    : mail && (mail.from_name || mail.from_addr)
      ? { name: mail.from_name || senderName(mail.from_addr).name, email: mail.from_addr }
      : { name: senderOf(row), email: '' }
  const toLine = sentCopy ? (meta.to ?? '') : (mail?.to.join(', ') ?? '')
  const ccLine = sentCopy ? (meta.cc ?? '') : (mail?.cc.join(', ') ?? '')
  const step = nextStep(row)
  const reviewed = Boolean(row.source.reviewed_at)
  const context: requestActions.Context | null =
    detail.data && definitions.data && people.data
      ? {
          request: detail.data,
          definitions: definitions.data,
          people: people.data,
          me,
          attachments: files.data ?? [],
        }
      : null
  const event =
    landed && detail.data ? detail.data.events.find((e) => e.id === landed.id) : undefined
  const others = (drafts.data?.items ?? []).filter(
    (draft) =>
      draft.in_reply_to_source_id === id &&
      draft.created_by_user_id !== me.id &&
      draft.kind === 'reply',
  )
  const mine = (drafts.data?.items ?? []).find(
    (draft) =>
      draft.in_reply_to_source_id === id &&
      draft.created_by_user_id === me.id &&
      draft.kind === 'reply',
  )

  async function markReviewed(next = false) {
    await api.POST('/api/records/sources/{source_id}/reviewed', path)
    onAfterwards({
      kind: 'handled',
      words: `${flagged ? 'Handled' : 'Reviewed'} · moved to All mail`,
      undoable: true,
    })
    await refresh()
    if (next) onNext()
  }
  async function unreview() {
    await api.POST('/api/records/sources/{source_id}/unreview', path)
    // Undoing "Mark handled" on a flagged message puts it back in Flagged as it was: taking
    // back the review alone would leave a message on a request in New instead.
    if (flagged && row.source.flag_reason)
      await api.POST('/api/records/sources/{source_id}/flag', {
        ...path,
        body: {
          reason: row.source.flag_reason,
          note: row.source.flag_note || flagWords(row.source.flag_reason).long,
        },
      })
    onAfterwards(null)
    await refresh()
  }

  const ask = (make: () => ActionSpec | null) => {
    setMenu(null)
    const made = make()
    if (made) setSpec(made)
  }

  function runStep() {
    if (step === 'fix_resend') setDialog('fix')
    else if (step === 'attach_others') setDialog('others')
    else if (step === 'attach') ask(() => inboxActions.attachTo(row, candidates.data ?? []))
    else void markReviewed()
  }

  /* ── The menus ── */
  const definitionsData = definitions.data
  const peopleData = people.data ?? []
  // P20h: moving a deadline's date is an administrator's (the retired Director mark, 30l, 42d).
  const director = me.records_role === 'admin'
  const copyLink = {
    label: 'Copy link',
    onPick: () =>
      void navigator.clipboard?.writeText(
        `${window.location.origin}/inbox?bucket=${bucket}&message=${id}`,
      ),
  }

  // 108: what a flagged message offers, its primary next step lit at the top.
  const nextItems: MenuItem[] = definitionsData
    ? [
        ...(landed
          ? [
              {
                label: NEXT_WORDS[step],
                current: true,
                onPick: () => {
                  setMenu(null)
                  runStep()
                },
              },
              {
                label: 'Attach to another request',
                onPick: () => ask(() => inboxActions.attachTo(row, candidates.data ?? [])),
              },
            ]
          : [
              {
                label: 'Attach to a request',
                current: step === 'attach',
                onPick: () => ask(() => inboxActions.attachTo(row, candidates.data ?? [])),
              },
              {
                label: 'Start a new request from this',
                onPick: () => ask(() => inboxActions.startRequest(row, agencies.data ?? [], me.id)),
              },
            ]),
        ...(step === 'fix_resend'
          ? [{ label: 'Fix and resend', current: true, onPick: () => setDialog('fix') }]
          : []),
        {
          label: 'Handle & reply',
          disabled: !landed,
          onPick: () => {
            void markReviewed()
            setComposing({ kind: 'reply' })
          },
        },
        { label: 'Mark as handled', disabled: !landed, onPick: () => void markReviewed() },
        {
          label: 'Assign to…',
          separator: true,
          onPick: () => ask(() => inboxActions.assignTo(row, peopleData)),
        },
        {
          label: 'Ask a teammate',
          onPick: () => ask(() => inboxActions.askTeammate(row, peopleData, me.id)),
        },
        {
          label: 'Create task…',
          onPick: () =>
            ask(() =>
              context
                ? requestActions.createTask(context, event)
                : inboxActions.taskNeedsRequest(row),
            ),
        },
        ...(!landed
          ? [
              {
                label: 'Mark as automated reply',
                separator: true,
                onPick: () =>
                  ask(() => inboxActions.resolve(row, definitionsData, 'automated_reply')),
              },
              copyLink,
              {
                label: 'Mark handled without attaching',
                separator: true,
                danger: true,
                onPick: () => ask(() => inboxActions.resolve(row, definitionsData)),
              },
              {
                label: 'Report as suspicious',
                danger: true,
                onPick: () => ask(() => inboxActions.resolve(row, definitionsData, 'suspicious')),
              },
            ]
          : [{ ...copyLink, separator: true }]),
      ]
    : []

  // 134: what a message on a request offers; 135: a door that needs a director, locked.
  const reviewItems: MenuItem[] = definitionsData
    ? [
        reviewed
          ? { label: 'Mark not reviewed', current: true, onPick: () => void unreview() }
          : { label: 'Reviewed', current: true, onPick: () => void markReviewed() },
        ...(!reviewed
          ? [{ label: 'Reviewed and open next', onPick: () => void markReviewed(true) }]
          : []),
        {
          label: 'Record as…',
          separator: true,
          trailing: <ChevronRight size={10} width={2.6} stroke="var(--color-muted)" />,
          onPick: () => setMenu('record'),
        },
        {
          label: 'Set agency reference number',
          onPick: () =>
            ask(() =>
              context
                ? { ...requestActions.setReference(context), look: 'inbox', width: 420 }
                : null,
            ),
        },
        director
          ? {
              label: 'Update deadline',
              onPick: () =>
                ask(() =>
                  context ? { ...requestActions.updateAnyDeadline(context), look: 'inbox' } : null,
                ),
            }
          : {
              // 135: a door that is an admin's (30l reads 135's "Director" as admin), shown
              // locked; the row asks a teammate.
              label: 'Update deadline',
              muted: true,
              sub: (
                <>
                  Admins only · <span className="text-brand">Ask a teammate</span>
                </>
              ),
              trailing: <LockIcon size={12} width={2} stroke="var(--color-muted-2)" />,
              onPick: () => ask(() => inboxActions.askTeammate(row, peopleData, me.id)),
            },
        {
          label: 'Mark attachments as records',
          disabled: row.attachments.length === 0,
          onPick: () => ask(() => inboxActions.markRecords(row)),
        },
        {
          label: 'Attach to another request',
          separator: true,
          onPick: () => ask(() => inboxActions.attachTo(row, candidates.data ?? [])),
        },
        {
          label: 'Belongs to a different request',
          onPick: () => ask(() => inboxActions.attachTo(row, candidates.data ?? [], true)),
        },
        ...(!flagged && !reviewed
          ? [
              {
                label: 'Move to Flagged',
                onPick: () => ask(() => inboxActions.flag(row, definitionsData)),
              },
            ]
          : []),
        {
          label: 'Assign to…',
          separator: true,
          onPick: () => ask(() => inboxActions.assignTo(row, peopleData)),
        },
        {
          label: 'Ask a teammate',
          onPick: () => ask(() => inboxActions.askTeammate(row, peopleData, me.id)),
        },
        {
          label: 'Create task…',
          onPick: () => ask(() => (context ? requestActions.createTask(context, event) : null)),
        },
        { ...copyLink, separator: true },
      ]
    : []

  // 158: the kinds of reply a message can be recorded as.
  const recordItems: MenuItem[] = (definitionsData?.classifications ?? []).map((kind, index) => ({
    label: kind.label,
    separator: index === 3,
    onPick: () =>
      ask(() => (definitionsData ? inboxActions.recordAs(row, definitionsData, kind.kind) : null)),
  }))

  // 107: the message's own menu.
  const moreItems: MenuItem[] = [
    { label: 'View original', onPick: () => (setMenu(null), setShowOriginal(true)) },
    {
      label: 'Download original',
      onPick: () => window.open(`/api/records/sources/${id}/raw`, '_blank'),
    },
    { ...copyLink, separator: true },
    { label: 'Print', onPick: () => window.print() },
    // P23b: the message on a page of its own.
    { label: 'Open full page', onPick: () => void navigate(`/inbox/message/${id}`) },
  ]

  /* ── The header ── */
  const resent = afterwards?.kind === 'resent' ? afterwards.resend : null
  const handled = afterwards?.kind === 'handled' ? afterwards : null
  const primary = resent ? (
    resent.message.status === 'sent' ? null : (
      <QuietButton disabled>Fix and resend</QuietButton>
    )
  ) : handled ? (
    handled.undoable ? (
      <QuietButton onClick={() => void unreview()}>Undo</QuietButton>
    ) : null
  ) : flagged ? (
    <SplitButton
      label={NEXT_WORDS[step]}
      onClick={runStep}
      onMenu={() => setMenu(menu === 'next' ? null : 'next')}
      menuOpen={menu === 'next'}
      filled={scrolled}
    />
  ) : landed ? (
    <SplitButton
      label={reviewed ? 'Mark not reviewed' : 'Reviewed'}
      quiet={reviewed}
      onClick={() => void (reviewed ? unreview() : markReviewed())}
      onMenu={() => setMenu(menu === 'next' || menu === 'record' ? null : 'next')}
      menuOpen={menu === 'next' || menu === 'record'}
      filled={scrolled && !reviewed}
    />
  ) : null

  const agencyLink = landed ? (
    <Link
      to={`/agencies/${landed.agency_id}`}
      className={cx(
        'shrink-0 whitespace-nowrap text-ink-2 underline decoration-line-control underline-offset-[3px] hover:text-ink',
        scrolled ? 'text-13' : 'text-125',
      )}
    >
      {landed.agency_name}
    </Link>
  ) : null

  const metaLine = resent ? (
    resent.message.status === 'sent' && resent.delivered ? (
      <span className="inline-flex items-center gap-1.5 text-125 font-medium whitespace-nowrap text-ok">
        <CheckIcon size={13} width={2.4} stroke="var(--color-success-ink-2)" />
        Delivered to {resent.to} · {formatDayTime(resent.delivered, me.timezone)}
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 text-125 font-medium whitespace-nowrap text-ink-2">
        <SendingIcon size={13} width={2.4} stroke="var(--color-brand)" />
        Resent to {resent.to} · waiting for delivery
      </span>
    )
  ) : handled ? (
    <span className="inline-flex items-center gap-1.5 text-125 font-medium whitespace-nowrap">
      <CheckIcon size={13} width={2.2} stroke="var(--color-ok)" />
      <span className="text-ok">
        {`Handled by ${me.name}`}
        {row.events.length > 0
          ? ` · attached to ${row.events.length} ${row.events.length === 1 ? 'request' : 'requests'}`
          : ''}
      </span>
    </span>
  ) : flagged ? (
    <span className="inline-flex items-center gap-1.5 text-125 font-medium whitespace-nowrap text-warn">
      <FlagReasonIcon size={13} width={2.2} stroke="var(--color-warn)" />
      {flagWords(row.source.flag_reason).long}
    </span>
  ) : null

  /* ── The chain ── */
  const chain = detail.data ? chainOf(detail.data.events, id, landed?.occurred_at) : []
  const pieces = foldChain(chain.length, chainOpen)
  const who = (e: RequestEvent) =>
    e.direction === 'inbound'
      ? (detail.data?.agency_name ?? 'The agency')
      : (nameOf(e.actor_user_id) ?? 'Your team')
  const filesOf = (e: RequestEvent) =>
    (files.data ?? []).filter((f) => f.source_id && f.source_id === e.source_id)

  const body = mail?.body || landed?.display_body || ''
  const shownFiles = allFiles ? row.attachments : row.attachments.slice(0, 2)

  return (
    <PaneCard label={subject} paneRef={setPane} className={className}>
      <header
        className={cx(
          'relative flex items-start gap-3.5 border-b',
          scrolled
            ? 'border-line bg-card-white px-[22px] py-3'
            : 'border-hairline px-[22px] pt-[18px] pb-3.5',
        )}
      >
        <div className={cx('flex min-w-0 flex-1 flex-col', scrolled ? 'gap-[5px]' : 'gap-[3px]')}>
          <span className="flex min-w-0 items-baseline gap-3 overflow-hidden whitespace-nowrap">
            <h2
              className={cx(
                'min-w-0 truncate font-display text-xl font-extrabold tracking-dialog text-ink',
                !scrolled && 'leading-[1.3]',
              )}
            >
              {subject}
            </h2>
            {scrolled ? agencyLink : null}
          </span>
          {!scrolled ? (
            // The "·" after a flagged reason is the page's 16 px (106), so the line is 21 px tall
            // even when there is no agency to name after it.
            <span
              className={cx(
                'flex min-w-0 items-center gap-2 whitespace-nowrap',
                metaLine && 'min-h-[21px]',
              )}
            >
              {metaLine}
              {metaLine && landed ? (
                <span className="text-16 leading-[normal] text-line-2">·</span>
              ) : null}
              {flagged || handled || resent ? agencyLink : null}
              {!flagged && !handled && !resent && landed ? (
                <>
                  {agencyLink}
                  <span className="m-0 shrink-0 text-125 text-line-control">·</span>
                  <Link
                    to={`/requests/${landed.request_id}`}
                    className="shrink-0 text-125 text-ink-2 underline decoration-line-control underline-offset-[3px] hover:text-ink"
                  >
                    {landed.tracking_number}
                  </Link>
                </>
              ) : null}
            </span>
          ) : null}
        </div>
        <span
          className={cx(
            'flex shrink-0 items-center gap-1.5',
            scrolled ? '-mt-1 gap-[7px]' : 'ml-auto -mt-0.5',
          )}
        >
          {primary}
        </span>
        {menu === 'next' ? (
          <MenuPanel
            aria-label="Next steps"
            width={250}
            tail
            className="absolute top-[56px] right-[22px] z-[3]"
            items={flagged ? nextItems : reviewItems}
            onPicked={() => undefined}
          />
        ) : null}
        {menu === 'record' ? (
          <>
            <MenuPanel
              aria-label="Next steps"
              width={250}
              tail
              className="absolute top-[58px] right-[22px] z-[3]"
              items={reviewItems.map((item) => ({ ...item, current: item.label === 'Record as…' }))}
            />
            <MenuPanel
              aria-label="Record as"
              width={230}
              tail
              className="absolute top-[118px] right-[276px] z-[4]"
              items={recordItems}
            />
          </>
        ) : null}
      </header>

      {showOriginal ? (
        <Original sourceId={id} onBack={() => setShowOriginal(false)} />
      ) : (
        <div
          className="flex min-h-0 flex-1 flex-col overflow-y-auto"
          onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 48)}
        >
          {/* The thread's earlier messages (110, 136, 142). */}
          {pieces.map((piece) =>
            piece.kind === 'more' ? (
              <button
                key="more"
                type="button"
                title={`${piece.indexes.length} more · ${formatShortDate(chain[piece.indexes[0]].occurred_at)} – ${formatShortDate(chain[piece.indexes.at(-1) ?? 0].occurred_at)}`}
                onClick={() => setChainOpen(true)}
                className="relative h-[30px] shrink-0 bg-surface-soft"
              >
                <span className="absolute inset-x-0 top-[13px] h-px bg-fill-muted" />
                <span className="absolute inset-x-0 top-4 h-px bg-fill-muted" />
                <span className="absolute top-0.5 left-[22px] box-border inline-flex size-[26px] items-center justify-center rounded-pill border border-line bg-card-white text-115 font-semibold text-ink-2 shadow-menu">
                  {piece.indexes.length}
                </span>
              </button>
            ) : (
              <ChainRow
                key={chain[piece.index].id}
                who={who(chain[piece.index])}
                event={chain[piece.index]}
                files={filesOf(chain[piece.index])}
                folded={chain.length > 3 && !chainOpen}
                place={
                  piece.index === 0 ? 'first' : piece.index === chain.length - 1 ? 'last' : 'middle'
                }
                flagged={flagged}
                onOpen={() => void navigate(`/requests/${chain[piece.index].request_id}`)}
              />
            ),
          )}

          {/* Who it is from and to, and the toolbar (106, 133). */}
          <div
            className={cx(
              'relative px-[22px] pb-1.5',
              chain.length > 0 && !flagged ? 'border-t border-hairline pt-3.5' : 'pt-4',
            )}
          >
            <div className="grid grid-cols-[44px_1fr_auto] items-baseline gap-x-2.5 gap-y-0.5 text-12">
              <span className="font-semibold text-ink">From</span>
              <span className="min-w-0 truncate">
                <span className={cx('text-ink', flagged && 'font-medium')}>{from.name}</span>
                {from.email && from.email !== from.name ? (
                  <span className="text-ink-2"> &lt;{from.email}&gt;</span>
                ) : null}
              </span>
              <span
                title={formatInstant(mail?.date ?? row.source.captured_at, me.timezone)}
                className="text-12 whitespace-nowrap text-muted"
              >
                {formatListTime(mail?.date ?? row.source.captured_at, me.timezone)}
              </span>
              {toLine ? (
                <>
                  <span className="font-semibold text-ink">To</span>
                  <span className="min-w-0 truncate text-ink-2">{toLine}</span>
                  <span />
                </>
              ) : null}
              {ccLine ? (
                <>
                  <span className="font-semibold text-ink">Cc</span>
                  <span className="min-w-0 truncate text-ink-2">{ccLine}</span>
                  <span />
                </>
              ) : null}
            </div>
            <div className="mt-3 flex items-center">
              <ToolGroup>
                {!flagged ? (
                  <Tool
                    first
                    label="Attach to a request"
                    onClick={() => ask(() => inboxActions.attachTo(row, candidates.data ?? []))}
                  >
                    <FilePlusIcon size={13} width={1.8} stroke="var(--color-icon)" />
                  </Tool>
                ) : null}
                <Tool
                  first={flagged}
                  label="Assign to a teammate"
                  onClick={() => ask(() => inboxActions.assignTo(row, peopleData))}
                >
                  <AssignIcon size={13} width={1.8} stroke="var(--color-icon)" />
                </Tool>
                <Tool
                  label="Create a task from it"
                  onClick={() =>
                    ask(() =>
                      context
                        ? requestActions.createTask(context, event)
                        : inboxActions.taskNeedsRequest(row),
                    )
                  }
                >
                  <TaskBoxIcon size={13} width={1.8} stroke="var(--color-icon)" />
                </Tool>
                {!flagged && landed ? (
                  <Tool
                    label="Internal notes — opens the request page"
                    badge={row.note_count || undefined}
                    onClick={() => void navigate(`/requests/${landed.request_id}?tab=notes`)}
                  >
                    <ChatIcon size={13} width={1.8} stroke="var(--color-icon)" />
                  </Tool>
                ) : null}
                <Tool
                  label="More on this message"
                  onClick={() => setMenu(menu === 'more' ? null : 'more')}
                >
                  <DotsIcon size={13} fill="var(--color-ink-2)" stroke="var(--color-icon)" />
                </Tool>
              </ToolGroup>
              {landed || flagged ? (
                <button
                  type="button"
                  title="Forward"
                  aria-label="Forward"
                  onClick={() =>
                    landed
                      ? setComposing({ kind: 'forward' })
                      : setSpec({
                          key: `forward-${id}`,
                          look: 'inbox',
                          title: 'Forward',
                          subtitle: subject,
                          refused:
                            'A message is forwarded from its request’s own address. Attach it to its request first.',
                          submit: 'Forward',
                          fields: [],
                          run: async () => undefined,
                        })
                  }
                  className="ml-auto inline-flex h-[30px] w-8 items-center justify-center rounded-9 shadow-ring-hairline hover:bg-hover"
                >
                  <ForwardIcon size={13} width={1.8} stroke="var(--color-icon)" />
                </button>
              ) : null}
            </div>
            {menu === 'more' ? (
              <MenuPanel
                aria-label="More on this message"
                width={190}
                tail
                className="absolute top-[94px] left-[90px] z-[3]"
                items={moreItems}
                onPicked={() => setMenu(null)}
              />
            ) : null}
          </div>

          {/* The words, and what the portal noticed about them (129, 131, 143). */}
          <div className="flex-1 px-[22px] py-4 text-125 leading-[1.8] text-ink">
            <Noticed
              row={row}
              candidates={candidates.data ?? []}
              recorded={recordedAs(row, definitionsData)}
              recordedBy={
                row.recorded_by && row.recorded_at
                  ? `by ${row.recorded_by} · ${formatListTime(row.recorded_at, me.timezone)}`
                  : null
              }
              onChange={() => setMenu('record')}
            />
            <div className="whitespace-pre-wrap">
              {body || 'This message has no text. Its files are below.'}
            </div>
            {row.attachments.length > 0 ? (
              <div className="mt-3.5 flex flex-wrap gap-2">
                {shownFiles.map((file) => (
                  <FileChip
                    key={file.id}
                    name={file.filename}
                    bytes={file.size_bytes}
                    onOpen={() => onOpenFile(file.id)}
                  />
                ))}
                {!allFiles && row.attachments.length > 2 ? (
                  <MoreFiles count={row.attachments.length - 2} onClick={() => setAllFiles(true)} />
                ) : null}
              </div>
            ) : null}
          </div>

          {/* The reply or forward being written (144–150), from the request's own address. */}
          {composing && landed && !resent ? (
            <Composer
              key={'draftId' in composing ? composing.draftId : composing.kind}
              {...('draftId' in composing
                ? { messageId: composing.draftId }
                : { inReplyToSourceId: id, forward: composing.kind === 'forward' })}
              frame="pane"
              overlayHost={pane}
              autoFocus
              notice={
                others.length > 0 ? (
                  <AlsoWriting draft={others[0]} name={nameOf(others[0].created_by_user_id)} />
                ) : null
              }
              onClose={() => setComposing(null)}
              onSent={(queued) => {
                setComposing(null)
                setSent(queued)
                void refreshMail()
              }}
            />
          ) : null}
          {/* Flagged: the team's lane (106). On a request: the reply (133). */}
          {flagged || handled ? (
            <NotesLane
              sourceId={id}
              notes={(notes.data ?? []).filter((n) => !n.voided_at)}
              writing={writingNote}
              onWrite={() => setWritingNote(true)}
              onDone={() => {
                setWritingNote(false)
                void refresh()
              }}
              noteRef={noteRef}
            />
          ) : landed && !resent && !composing && !sent ? (
            <div className="px-4 pt-2 pb-[18px]">
              <ComposerPill
                placeholder="Reply…"
                onClick={() => setComposing(mine ? { draftId: mine.id } : { kind: 'reply' })}
              />
            </div>
          ) : null}
        </div>
      )}

      {/* What just happened (127, 128, 132, 151). */}
      {sent ? (
        <LiveSent
          id={sent.id}
          placeholder={sent}
          to={landed?.agency_name ?? 'the agency'}
          onUndone={(draft) => {
            setSent(null)
            setComposing({ draftId: draft.id })
          }}
          onGone={() => setSent(null)}
        />
      ) : null}
      {handled ? (
        <Toast width={360} done action="Next flagged" onAction={onNext} progress={0.62}>
          {handled.words}
        </Toast>
      ) : null}
      {resent ? (
        <ResendWatch
          resend={resent}
          onChange={(next) => onAfterwards({ kind: 'resent', resend: next })}
          onUndo={async () => {
            // Taken back inside its undo window: the copy is discarded and the bounce goes
            // back to Flagged with what it said before.
            await api.POST('/api/mail/messages/{message_id}/cancel', {
              params: { path: { message_id: resent.message.id } },
            })
            await api.DELETE('/api/mail/messages/{message_id}', {
              params: { path: { message_id: resent.message.id } },
            })
            await api.POST('/api/records/sources/{source_id}/unreview', path)
            await api.POST('/api/records/sources/{source_id}/flag', {
              ...path,
              body: {
                reason: 'delivery_failed',
                note: row.source.flag_note || 'Delivery failed.',
              },
            })
            onAfterwards(null)
            await refresh()
          }}
          onNext={onNext}
        />
      ) : null}

      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          const title = spec?.title ?? ''
          void refresh()
          if (/^Attach|^Belongs|handled|automated|suspicious/i.test(title))
            onAfterwards({ kind: 'handled', words: 'Handled · moved to All mail', undoable: false })
        }}
      />
      {dialog === 'fix' ? (
        <FixResend
          row={row}
          onClose={() => setDialog(null)}
          onResent={(resend) => {
            setDialog(null)
            onAfterwards({ kind: 'resent', resend })
            void refresh()
          }}
        />
      ) : null}
      {dialog === 'others' && landed ? (
        <AttachOthers
          row={row}
          candidates={candidates.data ?? []}
          currentSubject={detail.data?.subject}
          onClose={() => setDialog(null)}
          onDone={(count) => {
            setDialog(null)
            onAfterwards({
              kind: 'handled',
              words: `Attached to ${count} more · moved to All mail`,
              undoable: false,
            })
            void refresh()
          }}
        />
      ) : null}
      {/* Escape closes the pane when nothing else is open. */}
      <EscapeCloses
        enabled={!menu && !spec && !dialog && !composing && !showOriginal}
        onEscape={menu ? () => setMenu(null) : onClose}
      />
    </PaneCard>
  )
}

/** A recorded classification (143): the event no longer reads as plain correspondence. */
function recordedAs(row: Row, definitions: Schemas['DefinitionsOut'] | undefined): string | null {
  const event = row.events[0]
  if (!event || event.event_type === 'correspondence' || !definitions) return null
  const kinds = definitions.classifications.filter((k) => k.event_type === event.event_type)
  if (kinds.length === 1) return kinds[0].label
  return definitions.event_types.find((t) => t.code === event.event_type)?.label ?? null
}

/** The boxes at the top of the words: what the portal noticed (129, 131) or what it was recorded
 *  as (143). */
function Noticed({
  row,
  candidates,
  recorded,
  recordedBy,
  onChange,
}: {
  row: Row
  candidates: Schemas['CandidateOut'][]
  recorded: string | null
  /** "by Dana Nolan · 8:31 AM" (143): who recorded it and when (P23b). */
  recordedBy: string | null
  /** "Change" (143): record it as something else. */
  onChange: () => void
}) {
  const reason = row.source.flag_reason
  const landed = row.events[0]
  const box =
    'mb-3.5 rounded-10 border-[1.5px] border-line bg-row-alt px-3.5 py-3 text-125 leading-[1.6] text-ink'
  if (row.source.flagged_for_review && reason === 'may_cover_others' && candidates.length > 0) {
    return (
      <div className={box}>
        <div className="mb-1.5 font-semibold">
          {landed ? (
            <>
              Attached to{' '}
              <Link
                to={`/requests/${landed.request_id}`}
                className="text-ink underline decoration-line-control underline-offset-[3px]"
              >
                {landed.tracking_number}
              </Link>
              . Also mentions:
            </>
          ) : (
            'Mentions:'
          )}
        </div>
        <div className="flex flex-col gap-[5px]">
          {candidates.map((c) => (
            <span key={c.request_id}>
              {c.agency_reference_number ? (
                <>
                  <span className="text-ink-2">{c.agency_reference_number}</span> →{' '}
                </>
              ) : null}
              <Link
                to={`/requests/${c.request_id}`}
                className="text-ink underline decoration-line-control underline-offset-[3px]"
              >
                {c.tracking_number}
              </Link>{' '}
              · {c.subject}
            </span>
          ))}
        </div>
      </div>
    )
  }
  if (row.source.flagged_for_review && reason === 'reply_to_ignored') {
    const first = candidates[0]
    return (
      <div className={box}>
        Sent to another address instead of the request’s own.
        {first ? (
          <>
            {' '}
            It answers mail sent on{' '}
            <Link
              to={`/requests/${first.request_id}`}
              className="text-ink underline decoration-line-control underline-offset-[3px]"
            >
              {first.tracking_number}
            </Link>
            .
          </>
        ) : null}
      </div>
    )
  }
  // The reasons whose plain words say it all draw no box; these carry the system's own sentence.
  if (
    row.source.flagged_for_review &&
    row.source.flag_note &&
    (reason === 'other' ||
      reason === 'assistant' ||
      reason === 'attachment_too_large' ||
      reason === 'delivery_failed')
  ) {
    return <div className={box}>{row.source.flag_note}</div>
  }
  if (recorded) {
    return (
      <div className="mb-3 flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-6 bg-ok-tint px-[9px] py-[3px] text-115 font-medium text-ok">
          <CheckIcon size={11} width={3} stroke="var(--color-ok)" />
          Recorded as {recorded.toLowerCase()}
        </span>
        {recordedBy ? <span className="text-115 text-muted">{recordedBy}</span> : null}
        <button
          type="button"
          onClick={onChange}
          className="ml-auto text-115 font-medium text-brand hover:text-brand-dark"
        >
          Change
        </button>
      </div>
    )
  }
  return null
}

/** One earlier message of the thread (110, 136, 142): who, the paperclip, the date. */
function ChainRow({
  who,
  event,
  files,
  folded,
  place,
  flagged,
  onOpen,
}: {
  who: string
  event: RequestEvent
  files: Schemas['AttachmentOut'][]
  folded: boolean
  place: 'first' | 'middle' | 'last'
  flagged: boolean
  onOpen: () => void
}) {
  const me = useMe()
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cx(
        'flex w-full shrink-0 items-center gap-3 px-[22px] text-left',
        folded && !flagged
          ? cx('bg-card-white', place === 'first' ? 'border-t border-hairline pt-3.5' : 'pb-3.5')
          : flagged
            ? 'border-b border-hairline bg-surface-soft py-[7px]'
            : 'border-t border-hairline bg-surface-soft py-[7px]',
      )}
    >
      <span className="max-w-[150px] flex-[0_0_auto] truncate text-125 font-medium text-ink-soft">
        {who}
      </span>
      <span className="min-w-0 flex-1" />
      <span className="mr-0.5 inline-flex items-center gap-[9px]">
        {event.direction === 'outbound' ? (
          <span title="Sent by your team" className="inline-flex">
            <SentIcon size={13} width={2} stroke="var(--color-muted)" />
          </span>
        ) : null}
        {files.length > 0 ? (
          <span
            title={`${files.length} ${files.length === 1 ? 'attachment' : 'attachments'} · ${files.map((f) => f.filename).join(', ')}`}
            className="inline-flex"
          >
            <PaperclipIcon size={13} width={2} stroke="var(--color-muted)" />
          </span>
        ) : null}
      </span>
      <span
        title={formatInstant(event.occurred_at, me.timezone)}
        className="text-11 whitespace-nowrap text-muted"
      >
        {formatListTime(event.occurred_at, me.timezone)}
      </span>
    </button>
  )
}

/** "Maya Torres is also writing a reply · View her draft" (146). */
function AlsoWriting({ draft, name }: { draft: Message; name: string | null }) {
  return (
    <div className="mb-3 flex items-center gap-[9px] text-12 text-ink-2">
      <Avatar initials={initialsOf(name ?? '?')} size={20} />
      <span>
        <span className="font-medium text-ink">{name ?? 'Someone'}</span> is also writing a reply
      </span>
      <Link
        to={`/inbox?bucket=drafts&message=${draft.id}`}
        className="ml-auto font-medium text-brand"
      >
        View the draft
      </Link>
    </div>
  )
}

/** The reply on its way, as a toast over the pane (151). */
function LiveSent({
  id,
  placeholder,
  to,
  onUndone,
  onGone,
}: {
  id: string
  placeholder: Message
  to: string
  onUndone: (draft: Message) => void
  onGone: () => void
}) {
  const live = $api.useQuery(
    'get',
    '/api/mail/messages/{message_id}',
    { params: { path: { message_id: id } } },
    {
      placeholderData: placeholder,
      refetchInterval: (query) =>
        ['queued', 'sending'].includes(query.state.data?.status ?? '') ? 3000 : false,
    },
  )
  const message = live.data ?? placeholder
  useEffect(() => {
    if (message.status === 'sent' || message.status === 'failed') {
      const timer = window.setTimeout(onGone, 4000)
      return () => window.clearTimeout(timer)
    }
  }, [message.status, onGone])
  return <SentToast message={message} to={to} onUndone={onUndone} />
}

/** The internal-notes lane (106, 109, 112): the notes, then the field to add one. */
export function NotesLane({
  sourceId,
  notes,
  writing,
  onWrite,
  onDone,
  noteRef,
  tight = false,
}: {
  sourceId: string
  notes: Schemas['SourceNoteOut'][]
  writing: boolean
  onWrite: () => void
  onDone: () => void
  noteRef: React.RefObject<HTMLTextAreaElement | null>
  /** Beside an open attachment (111): 18 px in, the field at the foot of the card. */
  tight?: boolean
}) {
  const me = useMe()
  const nameOf = useNameOf()
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function add() {
    if (!body.trim()) return
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/records/sources/{source_id}/notes', {
      params: { path: { source_id: sourceId } },
      body: { body: body.trim() },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    setBody('')
    onDone()
  }
  return (
    <>
      <div
        className={cx(
          'border-t border-line bg-surface-row pt-3.5 text-105 font-semibold tracking-group text-muted uppercase',
          tight ? 'px-[18px]' : 'px-[22px]',
          notes.length > 0 || tight ? 'pb-2' : '',
        )}
      >
        Internal notes{notes.length > 0 ? ` · ${notes.length}` : ''}
      </div>
      {notes.length === 0 ? (
        tight ? null : (
          <div className="bg-surface-row px-[22px] pt-2.5 pb-1.5 text-12 text-muted-2">
            No notes yet
          </div>
        )
      ) : (
        notes.map((note) => {
          const name = nameOf(note.author_user_id) ?? 'Someone'
          return (
            <NoteRow
              key={note.id}
              initials={initialsOf(name)}
              name={name}
              when={formatListTime(note.created_at, me.timezone)}
              className={tight ? 'px-[18px]' : undefined}
            >
              {note.body}
            </NoteRow>
          )
        })
      )}
      <div
        className={cx(
          'sticky bottom-0 bg-surface-row py-3.5',
          tight ? 'mt-auto px-[18px]' : 'px-[22px]',
        )}
      >
        {writing ? (
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void add()
            }}
            className="flex items-end gap-3 rounded-18 bg-card-white px-5 py-3 shadow-float"
          >
            <textarea
              ref={noteRef}
              aria-label="A note for the team. The agency never sees it."
              placeholder="Write a note…"
              rows={2}
              value={body}
              onChange={(event) => setBody(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void add()
                }
                if (event.key === 'Escape' && !body.trim()) {
                  event.stopPropagation()
                  onDone()
                }
              }}
              className="min-w-0 flex-1 resize-none bg-transparent text-135 leading-[1.4] text-ink outline-none placeholder:text-text-placeholder"
            />
            <button
              type="submit"
              disabled={busy || !body.trim()}
              className="rounded-pill bg-brand px-3.5 py-1.5 text-12 font-medium text-white disabled:opacity-55"
            >
              Add note
            </button>
          </form>
        ) : (
          <ComposerPill placeholder="Write a note…" onClick={onWrite} />
        )}
        {error ? <p className="mt-2 text-12 text-danger">{error}</p> : null}
      </div>
    </>
  )
}

function EscapeCloses({ enabled, onEscape }: { enabled: boolean; onEscape: () => void }) {
  useEffect(() => {
    if (!enabled) return
    function key(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      if (event.key === 'Escape') onEscape()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [enabled, onEscape])
  return null
}

/** The resend on its way (127) and once delivered (128): its state, read again every few
 *  seconds until the provider says it was delivered. */
function ResendWatch({
  resend,
  onChange,
  onUndo,
  onNext,
}: {
  resend: Resend
  onChange: (next: Resend) => void
  onUndo: () => Promise<void>
  onNext: () => void
}) {
  const sending = useSendingSeconds()
  const live = $api.useQuery(
    'get',
    '/api/mail/messages/{message_id}',
    { params: { path: { message_id: resend.message.id } } },
    { refetchInterval: resend.delivered ? false : 3000 },
  )
  const [busy, setBusy] = useState(false)
  const message = live.data ?? resend.message
  const delivered =
    message.recipients.find(
      (r) => r.address.toLowerCase() === resend.to.toLowerCase() && r.delivered_at,
    )?.delivered_at ?? null
  useEffect(() => {
    if (message.status !== resend.message.status || delivered !== resend.delivered)
      onChange({ ...resend, message, delivered })
  }, [message, delivered, resend, onChange])
  const queued = message.status === 'queued' || message.status === 'sending'
  const now = useNow(queued)
  const left = message.send_after ? Math.max(0, new Date(message.send_after).getTime() - now) : 0
  if (queued)
    return (
      <Toast
        width={380}
        action={message.status === 'queued' && !busy ? 'Undo' : undefined}
        onAction={() => {
          setBusy(true)
          void onUndo().finally(() => setBusy(false))
        }}
        progress={1 - left / 1000 / Math.max(1, sending)}
      >
        Resent · waiting for delivery
      </Toast>
    )
  if (message.status === 'sent')
    return (
      <Toast width={360} done action="Next flagged" onAction={onNext} progress={0.62}>
        {delivered ? 'Delivered · removed from Flagged' : 'Sent · removed from Flagged'}
      </Toast>
    )
  return null
}

function useSendingSeconds(): number {
  const sending = $api.useQuery('get', '/api/mail/sending', {}, { staleTime: 5 * 60_000 })
  return sending.data?.undo_send_seconds ?? 30
}
