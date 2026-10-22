import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, errorOf } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { NEW_REQUEST_BUILT, isBuilt } from '../../app/nav'
import { setPageLabel, usePresenceList } from '../../app/presence'
import { isFullStaff, isRecordsAdmin, isRequester, needsApproval, useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { AddDocument } from '../../components/AddDocument'
import { ChevronDown, ChevronLeft, LockIcon, MoreIcon } from '../../components/icons'
import { Menu, type MenuItem } from '../../components/Menu'
import { LoadFailed, Loading } from '../../components/States'
import { Notice, cx } from '../../components/ui'
import { formatShortInstant } from '../../lib/dates'
import { pageLabel, viewersOf } from '../../lib/presence'
import { isPreFiling } from '../../lib/status'
import { useDefinitions, useNameOf, usePeople } from '../../lib/vocab'
import { NoAccess, NotFound } from '../ErrorPages'
import { Composer } from '../inbox/Composer'
import { QueuedBanner, useLiveMessage } from '../inbox/OutboundPane'
import { AccessLine, ApprovalStrip, ChangeAccessDialog, usePeopleWithAccess } from './Access'
import * as actions from './actions'
import { FollowUp } from './FollowUp'
import { ReviewProduction } from './ReviewProduction'
import { Side } from './Side'
import { Correspondence, Files, NoteWriter, Notes, Timeline } from './Thread'

type Event = Schemas['EventOut']
type MailMessage = Schemas['MailMessageOut']

const TABS = ['correspondence', 'timeline', 'notes', 'files'] as const
type Tab = (typeof TABS)[number]
const TAB_LABEL: Record<Tab, string> = {
  correspondence: 'Correspondence',
  timeline: 'Timeline',
  notes: 'Notes',
  files: 'Files',
}

/**
 * The request page (062–078, 088–094): the title with its More menu, the agency and the tracking
 * number, a banner for the state the request is in, the four tabs with Record event, and the
 * right column of obligations (status steps, items, tasks, fees). Everything on it comes from the
 * request's own read, its files, the notes on its messages and the agency's contacts.
 */
export function RequestPage() {
  const { requestId = '' } = useParams()
  const me = useMe()
  const client = useQueryClient()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab = (
    TABS.includes(params.get('tab') as Tab) ? params.get('tab') : 'correspondence'
  ) as Tab
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [reviewing, setReviewing] = useState<Event | null>(null)
  const [followingUp, setFollowingUp] = useState(false)
  // "Upload a file" (069) opens "Add a document" (061) with this request chosen.
  const [addingDocument, setAddingDocument] = useState(false)
  const [composing, setComposing] = useState<actions.Compose | null>(null)
  const [sentMessage, setSentMessage] = useState<MailMessage | null>(null)

  const path = { params: { path: { request_id: requestId } } }
  const detail = $api.useQuery('get', '/api/records/requests/{request_id}', path, { retry: false })
  const loaded = detail.isSuccess
  const files = $api.useQuery('get', '/api/records/requests/{request_id}/attachments', path, {
    enabled: loaded,
  })
  const sourceNotes = $api.useQuery(
    'get',
    '/api/records/requests/{request_id}/source-notes',
    path,
    { enabled: loaded },
  )
  const privileged = detail.data?.sensitivity === 'privileged'
  // P20h: every request may have been given to someone (231), not only a privileged one.
  const access = $api.useQuery('get', '/api/records/requests/{request_id}/access', path, {
    enabled: loaded && !isRequester(me),
  })
  const [changingAccess, setChangingAccess] = useState(false)
  const agencyId = detail.data?.agency_id ?? ''
  const contacts = $api.useQuery(
    'get',
    '/api/records/agencies/{agency_id}/contacts',
    { params: { path: { agency_id: agencyId } } },
    { enabled: Boolean(agencyId) && !isRequester(me) },
  )
  const agency = $api.useQuery(
    'get',
    '/api/records/agencies/{agency_id}',
    { params: { path: { agency_id: agencyId } } },
    { enabled: Boolean(agencyId) && !isRequester(me) },
  )
  // The draft a reserved request was made from, for "Review & file" (076).
  const madeFrom = $api.useQuery('get', '/api/drafting/requests/{request_id}/draft', path, {
    enabled: isPreFiling(detail.data?.current_status ?? '') && !isRequester(me),
    retry: false,
  })
  const definitions = useDefinitions()
  const people = usePeople()
  const nameOf = useNameOf()
  const withAccess = usePeopleWithAccess(
    { id: requestId, sensitivity: detail.data?.sensitivity ?? 'internal' },
    access.data ?? [],
    people.data ?? [],
  )
  // P20h: an intern's letters leave the portal only through approval, so nothing on the page
  // files or sends by hand for them (233, 238); an administrator gives and takes access (230).
  const intern = needsApproval(me)
  const admin = isRecordsAdmin(me)
  // The newest approval on an intern's request (the strip reads the same query): sent back, the
  // strip carries Edit letter and nothing else is drawn under the tabs (238).
  const approvals = $api.useQuery(
    'get',
    '/api/core/reviews',
    { params: { query: { request_id: requestId } } },
    { enabled: loaded && intern && isPreFiling(detail.data?.current_status ?? '') },
  )
  const sentBack = approvals.data?.[0]?.decision === 'sent_back'

  const subject = detail.data?.subject ?? 'Request'
  useEffect(() => {
    document.title = `${subject} · SCLU Portal`
    setPageLabel(pageLabel(subject, ['Docket']))
    return () => setPageLabel('')
  }, [subject])

  if (detail.isPending) {
    return (
      <>
        <PageHeader title="Request" trail={['Docket']} />
        <Loading what="Opening the request" />
      </>
    )
  }
  if (detail.isError) {
    const refusal = errorOf(detail.error)
    if (refusal.code === 'privileged')
      return <NoAccess trackingNumber={refusal.tracking_number} requestId={requestId} />
    // A request a limited-scope attorney or an intern was not given reads as one that does not
    // exist: no banner, no explanation (239–241).
    if (refusal.detail?.startsWith('No request') || refusal.detail?.startsWith('Not allowed'))
      return <NotFound />
    return (
      <>
        <PageHeader title="Request" trail={['Docket']} />
        <LoadFailed
          what="this request"
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </>
    )
  }

  const request = detail.data
  const attachments = files.data ?? []
  // A requester reads their request; only staff act on it (P13).
  const canAct = !isRequester(me)
  const compose = (next: actions.Compose) => {
    setSentMessage(null)
    setComposing(next)
    if (tab !== 'correspondence') setParams({}, { replace: true })
  }
  const context: actions.Context | null =
    definitions.data && people.data
      ? {
          request,
          definitions: definitions.data,
          people: people.data,
          me,
          attachments,
          contacts: contacts.data ?? [],
          compose,
          navigate: (to) => void navigate(to),
          madeFrom: madeFrom.data ?? null,
        }
      : null
  const act = (make: (context: actions.Context) => ActionSpec) => {
    if (context) setSpec(make(context))
  }
  const refresh = async () => {
    setSpec(null)
    await Promise.all(
      [
        '/api/records/requests/{request_id}',
        '/api/records/requests/{request_id}/attachments',
        '/api/records/requests/{request_id}/source-notes',
        '/api/records/requests/{request_id}/access',
        '/api/records/requests',
      ].map((key) => client.invalidateQueries({ queryKey: ['get', key] })),
    )
  }
  const kindLabel = (code: string) =>
    definitions.data?.deadline_kinds.find((k) => k.code === code)?.label ?? code

  const status = request.current_status
  const terminal = definitions.data?.statuses.find((s) => s.code === status)?.is_terminal ?? false
  const pending = isPreFiling(status)
  const overdue = status.endsWith('_overdue')
  const activeDeadlines = request.deadlines.filter((d) => d.status === 'active')
  const noDeadline = !pending && !terminal && activeDeadlines.length === 0
  const missingRule = request.tasks.find(
    (t) => t.task_type === 'enter_deadline_rules' && !t.completed_at,
  )
  const byPost = agency.data?.filing_method === 'mail'

  // ---- the menus ---------------------------------------------------------------------------
  const moreItems: MenuItem[] = [
    { label: 'Edit request', hint: 'E', onPick: () => act(actions.editRequest) },
    ...(intern && pending
      ? []
      : [{ label: 'Change status…', onPick: () => act(actions.changeStatus) }]),
    { label: 'Set agency reference number', onPick: () => act(actions.setReference) },
    { label: 'Update deadline', onPick: () => act(actions.updateAnyDeadline) },
    { label: 'Add a request item', separator: true, onPick: () => act(actions.addItem) },
    ...(attachments.length > 0
      ? [{ label: 'Mark files as records', onPick: () => act((c) => actions.markRecords(c)) }]
      : []),
    { label: 'Upload a file', onPick: () => setAddingDocument(true) },
    { label: 'Assign to…', separator: true, onPick: () => act(actions.assign) },
    // P27d: a question about this request, put to a teammate (069).
    { label: 'Ask a teammate', onPick: () => act(actions.askTeammate) },
    { label: 'Create task…', onPick: () => act(actions.createTask) },
    ...(NEW_REQUEST_BUILT
      ? [
          {
            label: 'File with another agency',
            separator: true,
            onPick: () => act(actions.copyToAgencies),
          },
          {
            label: 'Duplicate as a new request',
            onPick: () => void navigate(`/new?from=${request.id}`),
          },
        ]
      : []),
    {
      label: 'Link a related request',
      separator: !NEW_REQUEST_BUILT,
      onPick: () => act(actions.linkRelated),
    },
    // P23b: the record as one PDF (header, items, deadlines, status history, messages).
    {
      label: 'Export the record as PDF',
      separator: true,
      onPick: () => downloadFile(`/api/records/requests/${request.id}/record.pdf`),
    },
    {
      label: 'Copy link',
      onPick: () => void navigator.clipboard?.writeText(window.location.href),
    },
    { label: 'Print', onPick: () => window.print() },
    // Whether a request is privileged is staff's to change (P20h); drawn nowhere since v6 099
    // became the access list, kept so sealing and unsealing stay possible.
    ...(isFullStaff(me)
      ? [
          {
            label: privileged ? 'Make it visible to the team…' : 'Make it privileged…',
            onPick: () => act((c) => actions.changeSensitivity(c, !privileged)),
          },
        ]
      : []),
    ...(terminal
      ? [{ label: 'Reopen request', separator: true, onPick: () => act(actions.reopen) }]
      : [
          { label: 'Withdraw request', separator: true, onPick: () => act(actions.withdraw) },
          { label: 'Close request', danger: true, onPick: () => act(actions.close) },
        ]),
  ]
  const stateItems: MenuItem[] = [
    ...(pending
      ? [
          { label: 'Mark as filed…', onPick: () => act((c) => actions.markFiled(c)) },
          { label: 'Log the mailing…', onPick: () => act((c) => actions.markFiled(c, true)) },
        ]
      : []),
    { label: 'Change status…', onPick: () => act(actions.changeStatus) },
    { label: 'Add a deadline…', onPick: () => act(actions.addDeadline) },
    { label: 'Record a fee…', onPick: () => act(actions.recordFee) },
    ...(overdue && request.inbound_alias_email
      ? [{ label: 'Follow up…', onPick: () => setFollowingUp(true) }]
      : []),
    {
      label: request.auto_followups_enabled
        ? 'Turn off automatic follow-ups'
        : 'Turn on automatic follow-ups',
      onPick: () => act((c) => followUps(c, !request.auto_followups_enabled)),
    },
    ...(NEW_REQUEST_BUILT && !pending
      ? [
          { label: 'File again', separator: true, onPick: () => act(actions.fileAgain) },
          { label: 'Copy to another agency…', onPick: () => act(actions.copyToAgencies) },
        ]
      : []),
    {
      label: 'Link a related request…',
      separator: !NEW_REQUEST_BUILT || pending,
      onPick: () => act(actions.linkRelated),
    },
    { label: 'Mark as duplicate of…', onPick: () => act(actions.markDuplicate) },
    ...(terminal
      ? [{ label: 'Reopen…', separator: true, onPick: () => act(actions.reopen) }]
      : [
          {
            label: 'Withdraw…',
            separator: true,
            danger: true,
            onPick: () => act(actions.withdraw),
          },
        ]),
  ]
  const recordItems: MenuItem[] = [
    ...(definitions.data?.classifications ?? []).map((kind, index) => ({
      label: kind.label,
      heading: index === 0 ? 'Log a response' : undefined,
      onPick: () => act((c) => actions.logResponse(c, kind.kind)),
    })),
    {
      label: 'Phone call',
      heading: 'Log communication',
      separator: true,
      onPick: () => act((c) => actions.logCall(c)),
    },
    { label: 'Letter or scan', onPick: () => act((c) => actions.logDocument(c, 'scanned_mail')) },
    {
      label: 'Portal update',
      onPick: () => act((c) => actions.logDocument(c, 'portal_capture')),
    },
    { label: 'Meeting or visit', onPick: () => act((c) => actions.logCall(c, true)) },
  ]

  // ---- the state banner --------------------------------------------------------------------
  const banners: ReactNode[] = []
  if (terminal) {
    const change = request.status_history.findLast((c) => c.status === status && !c.voided_at)
    banners.push(
      <Banner
        key="terminal"
        title={`${status === 'withdrawn' ? 'Withdrawn' : status === 'denied' ? 'Denied' : 'Closed'}${change ? ` ${formatShortInstant(change.effective_at, me.timezone)}` : ''}`}
        detail={change?.note ?? null}
        links={
          canAct
            ? [
                ...(status === 'withdrawn' && request.inbound_alias_email
                  ? [{ label: 'Tell the agency', onClick: () => compose({ kind: 'new' }) }]
                  : []),
                { label: 'Reopen', onClick: () => act(actions.reopen) },
              ]
            : []
        }
      />,
    )
  }
  if (status === 'no_records_claimed') {
    const change = request.status_history.findLast((c) => c.status === status && !c.voided_at)
    const task = request.tasks.find(
      (t) => t.task_type === 'review_no_records_claim' && !t.completed_at,
    )
    const who = nameOf(task?.assigned_to_user_id)
    banners.push(
      <Banner
        key="no-records"
        title="They say no records exist"
        detail={[
          change ? formatShortInstant(change.effective_at, me.timezone) : null,
          task ? `review task ${who ? `assigned to ${who}` : 'open'}` : null,
        ]
          .filter(Boolean)
          .join(' · ')}
        links={
          canAct
            ? [
                { label: 'Accept', onClick: () => act(actions.acceptNoRecords) },
                {
                  label: 'Challenge the search',
                  onClick: () => act((c) => actions.openChallenge(c, 'search_challenge')),
                },
              ]
            : []
        }
      />,
    )
  }
  if (noDeadline && status !== 'no_records_claimed') {
    banners.push(
      <Banner
        key="no-deadline"
        title="No deadline yet"
        detail={
          missingRule
            ? (missingRule.description.split('\n')[0] ?? 'No rule is entered for it')
            : 'Nothing is being tracked'
        }
        links={
          canAct
            ? [
                { label: 'Add a deadline', onClick: () => act(actions.addDeadline) },
                ...(missingRule && isBuilt('settings')
                  ? [{ label: 'Enter the rule', onClick: () => void navigate('/settings/rules') }]
                  : []),
              ]
            : []
        }
      />,
    )
  }
  if (privileged) {
    const names = (access.data ?? []).map((row) => row.user_name)
    banners.push(
      <Banner
        key="privileged"
        lock
        title="Privileged"
        detail={
          names.length > 0
            ? `Only ${names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`} can see this request`
            : 'Only the people it is shared with can see this request'
        }
        links={admin ? [{ label: 'Change access', onClick: () => setChangingAccess(true) }] : []}
      />,
    )
  }

  const counts: Partial<Record<Tab, number>> = {
    // 233, 238: no count beside Correspondence while there is nothing in it.
    correspondence: request.events.filter((e) => e.event_type !== 'note').length || undefined,
    notes:
      request.events.filter((e) => e.event_type === 'note').length +
      (sourceNotes.data ?? []).length,
    files: attachments.length,
  }

  const composer =
    canAct && composing ? (
      <Composer
        {...(composing.kind === 'new'
          ? { requestId: request.id }
          : composing.kind === 'message'
            ? { messageId: composing.messageId }
            : { inReplyToSourceId: composing.sourceId, forward: composing.forward })}
        onClose={() => setComposing(null)}
        onSent={(message) => {
          setComposing(null)
          setSentMessage(message)
        }}
      />
    ) : sentMessage ? (
      <SentBanner
        message={sentMessage}
        onChanged={(message) => {
          setSentMessage(message)
          if (message.status === 'sent' || message.status === 'failed') void refresh()
          if (message.status === 'draft') {
            setSentMessage(null)
            setComposing(null)
          }
        }}
      />
    ) : null

  const filedHow = agency.data?.filing_method
  // An intern's letter on its way to an approver, or back from one: the strip above says it all
  // (233, 238 draw nothing under the tabs).
  const strip = intern && (status === 'waiting_for_approval' || (pending && sentBack))
  const emptyThread = strip ? null : pending ? (
    <div className="flex flex-col items-center gap-2.5 px-[22px] py-14">
      <div className="text-md-lg font-semibold text-ink">Not filed yet</div>
      <div className="text-125 text-muted">
        Reserved {formatShortInstant(request.created_at, me.timezone)}
        {filedHow === 'email' && agency.data?.default_request_email
          ? ` · goes by email to ${agency.data.default_request_email}`
          : filedHow === 'mail'
            ? ' · goes by post'
            : filedHow === 'portal'
              ? ' · goes through their portal'
              : ''}
      </div>
      {canAct && status !== 'waiting_for_approval' ? (
        <div className="mt-2 flex gap-2">
          {intern ? null : (
            <button
              type="button"
              onClick={() => act((c) => actions.markFiled(c, byPost))}
              className={cx(
                'inline-flex h-[34px] items-center px-4 text-13 font-medium whitespace-nowrap',
                madeFrom.data
                  ? 'rounded-9 text-ink-2 hover:bg-hover'
                  : 'rounded-pill bg-brand text-white hover:bg-brand-dark',
              )}
            >
              {byPost ? 'Log the mailing' : 'Mark as filed'}
            </button>
          )}
          {madeFrom.data ? (
            <Link
              to={`/drafts/${madeFrom.data.draft_id}?stage=file`}
              className="inline-flex h-[34px] items-center rounded-pill bg-brand px-4 text-13 font-medium whitespace-nowrap text-white hover:bg-brand-dark hover:text-white"
            >
              Review &amp; file
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  ) : (
    <p className="px-[22px] py-14 text-center text-125 text-muted">
      Nothing has been sent or received yet.
    </p>
  )

  return (
    <div className="flex min-h-0 flex-1 items-start gap-5 leading-[normal]">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-start gap-5 px-2 pt-1.5 pb-5">
          <div className="min-w-0 flex-initial">
            {/* A requester's way back is their own list (194); the Docket is staff's. */}
            <Link
              to={canAct ? '/docket' : '/my-requests'}
              className="mb-1 flex items-center gap-1.5 text-125 text-ink-2 hover:text-ink"
            >
              <ChevronLeft size={13} width={2} stroke="var(--color-ink-2)" />
              {canAct ? 'Docket' : 'My requests'}
            </Link>
            <div className="flex min-w-0 items-center gap-2">
              <h1 className="min-w-0 truncate font-display text-32 leading-tight font-extrabold tracking-title text-ink">
                {request.subject}
              </h1>
              {canAct ? (
                <Menu
                  label="More actions"
                  items={moreItems}
                  align="left"
                  width={270}
                  panelClassName="mt-1.5 pb-1 z-[7]"
                  buttonClassName="rounded-6"
                >
                  {(open) => (
                    <span
                      className={cx(
                        'box-border inline-flex size-7 shrink-0 items-center justify-center rounded-6 hover:bg-hover',
                        open && 'bg-hover',
                      )}
                    >
                      <MoreIcon size={16} stroke="var(--color-muted)" />
                    </span>
                  )}
                </Menu>
              ) : null}
            </div>
          </div>
        </header>
        <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2">
          <div className="-mt-2 flex flex-wrap items-center gap-2.5 text-125 text-ink-2">
            {isBuilt('agencies') && canAct ? (
              <Link to={`/agencies/${request.agency_id}`} className="text-ink-2 hover:text-ink">
                {request.agency_name}
              </Link>
            ) : (
              <span>{request.agency_name}</span>
            )}
            <span className="text-line-control">·</span>
            <span>{request.tracking_number}</span>
            <ViewingNow requestId={request.id} />
            {canAct ? (
              <AccessLine
                rows={withAccess}
                canChange={admin}
                onChange={() => setChangingAccess(true)}
              />
            ) : null}
          </div>
          {canAct ? (
            <ApprovalStrip request={request} me={me} onChanged={() => void refresh()} />
          ) : null}
          <div className="flex min-w-0 flex-1 flex-col gap-3.5">
            {banners}
            <div className="flex items-end gap-[22px] border-b border-line">
              {TABS.map((name) => (
                <button
                  key={name}
                  type="button"
                  role="tab"
                  aria-selected={tab === name}
                  onClick={() =>
                    setParams(name === 'correspondence' ? {} : { tab: name }, { replace: true })
                  }
                  className={cx(
                    'pb-2.5 text-13',
                    tab === name
                      ? '-mb-px border-b-2 border-ink font-semibold text-ink'
                      : 'font-medium text-ink-3 hover:text-ink',
                  )}
                >
                  {TAB_LABEL[name]}
                  {counts[name] !== undefined ? (
                    <span className="font-medium text-muted-2"> {counts[name]}</span>
                  ) : null}
                </button>
              ))}
              {canAct ? (
                <span className="mb-[7px] ml-auto flex items-center gap-1.5">
                  <Menu
                    label="Record event"
                    items={recordItems}
                    width={250}
                    panelClassName="mt-1.5 pb-1 z-[7]"
                  >
                    {(open) => (
                      <span
                        className={cx(
                          'box-border inline-flex h-[30px] shrink-0 overflow-hidden rounded-8 border-[1.5px] border-brand',
                          open ? 'bg-brand-tint' : 'bg-card-white',
                        )}
                      >
                        <span className="inline-flex items-center px-3 text-125 font-medium whitespace-nowrap text-brand">
                          Record event
                        </span>
                        <span
                          className={cx(
                            'inline-flex items-center border-l px-2',
                            open ? 'border-blue-line-2' : 'border-blue-line-45',
                          )}
                        >
                          <ChevronDown size={11} width={2.4} stroke="var(--color-brand)" />
                        </span>
                      </span>
                    )}
                  </Menu>
                  {intern ? null : (
                    <Menu
                      label="More on this request"
                      items={stateItems}
                      width={240}
                      panelClassName="mt-1.5 z-[6]"
                      buttonClassName="rounded-chip"
                    >
                      {(open) => (
                        <span
                          className={cx(
                            'box-border inline-flex size-7 shrink-0 items-center justify-center rounded-chip border-[1.5px] border-line hover:bg-hover',
                            open ? 'bg-hover' : 'bg-card-white',
                          )}
                        >
                          <MoreIcon size={14} stroke="var(--color-ink-2)" />
                        </span>
                      )}
                    </Menu>
                  )}
                </span>
              ) : null}
            </div>
            {tab === 'correspondence' ? (
              <Correspondence
                request={request}
                attachments={attachments}
                sourceNotes={sourceNotes.data ?? []}
                canAct={canAct}
                act={act}
                onReview={setReviewing}
                onReply={compose}
                onNotes={() => setParams({ tab: 'notes' }, { replace: true })}
                composer={composer}
                empty={emptyThread}
              />
            ) : tab === 'timeline' ? (
              <Timeline request={request} attachments={attachments} />
            ) : tab === 'notes' ? (
              <Notes
                request={request}
                sourceNotes={sourceNotes.data ?? []}
                composer={canAct ? <NoteWriter onWrite={() => act(actions.addNote)} /> : null}
              />
            ) : (
              <Files request={request} attachments={attachments} />
            )}
          </div>
        </div>
      </div>
      <Side
        request={request}
        attachments={attachments}
        canAct={canAct}
        act={act}
        onChanged={() => void refresh()}
        kindLabel={kindLabel}
        onRespond={(item) =>
          act((c) =>
            actions.openChallenge(c, 'appeal', {
              request_item_id: item.id,
              label: `Item ${item.item_number}`,
            }),
          )
        }
      />
      <ActionDialog spec={spec} onClose={() => setSpec(null)} onDone={() => void refresh()} />
      <ChangeAccessDialog
        request={request}
        rows={withAccess}
        people={people.data ?? []}
        open={changingAccess}
        onClose={() => setChangingAccess(false)}
        onSaved={() => {
          setChangingAccess(false)
          void refresh()
        }}
      />
      {reviewing ? (
        <ReviewProduction
          requestId={request.id}
          event={reviewing}
          sender={reviewing.direction === 'inbound' ? request.agency_name : undefined}
          onClose={() => setReviewing(null)}
          onDone={() => {
            setReviewing(null)
            void refresh()
          }}
        />
      ) : null}
      {followingUp ? (
        <FollowUp
          request={request}
          onClose={() => setFollowingUp(false)}
          onDone={() => {
            setFollowingUp(false)
            void refresh()
          }}
        />
      ) : null}
      <AddDocument
        open={addingDocument}
        request={{ id: request.id, label: `${request.tracking_number} · ${request.agency_name}` }}
        onClose={() => setAddingDocument(false)}
        onAdded={() => void refresh()}
      />
    </div>
  )
}

/** "Turn off automatic follow-ups" (088), through the audited edit door. */
function followUps({ request }: actions.Context, on: boolean): ActionSpec {
  return {
    key: `follow-ups-${on}`,
    title: on ? 'Turn on automatic follow-ups' : 'Turn off automatic follow-ups',
    subtitle: `${request.tracking_number} · ${request.agency_name}`,
    intro: on
      ? 'Follow-ups go out again on the organisation’s schedule while a deadline is overdue.'
      : 'No automatic follow-up is sent for this request. You can still follow up by hand.',
    submit: on ? 'Turn on' : 'Turn off',
    fields: [{ name: 'note', label: 'Reason', type: 'textarea', required: true, minHeight: 52 }],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/edits', {
          params: { path: { request_id: request.id } },
          body: {
            entity_type: 'requests',
            entity_id: request.id,
            field: 'auto_followups_enabled',
            new_value: on,
            note: String(values.note ?? '').trim(),
          },
        }),
      )
    },
  }
}

/** A state banner (078, 091–093): what the request is, a muted line, and what to do. */
function Banner({
  title,
  detail,
  links,
  lock = false,
}: {
  title: string
  detail: string | null
  links: Array<{ label: string; onClick: () => void }>
  lock?: boolean
}) {
  return (
    <div className="flex items-center gap-2.5 rounded-10 border border-line-chip bg-surface-row px-3.5 py-2.5 text-125 text-ink">
      {lock ? <LockIcon size={14} width={2} stroke="var(--color-ink)" /> : null}
      <span className="font-medium whitespace-nowrap">{title}</span>
      {detail ? <span className="min-w-0 truncate text-muted">{detail}</span> : null}
      {links.length > 0 ? (
        <span className="ml-auto flex gap-3">
          {links.map((link) => (
            <button
              key={link.label}
              type="button"
              onClick={link.onClick}
              className="text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark"
            >
              {link.label}
            </button>
          ))}
        </span>
      ) : null}
    </div>
  )
}

/** The message just sent from this page: its undo window, then what became of it. */
function SentBanner({
  message,
  onChanged,
}: {
  message: MailMessage
  onChanged: (message: MailMessage) => void
}) {
  const live = useLiveMessage(message.id, message)
  const current = live.data ?? message
  // The poll stops once the message settles; the page is told then, so the Correspondence
  // tab shows the sent copy without a reload.
  const settled = current.status === 'sent' || current.status === 'failed'
  useEffect(() => {
    if (settled) onChanged(current)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when it settles
  }, [settled])
  if (current.status === 'sent')
    return (
      <Notice tone="ok">
        Sent{current.test_send ? ' (test mode: nothing was delivered)' : ''}. It is on the thread
        above and under Sent in the Inbox.
      </Notice>
    )
  if (current.status === 'failed')
    return (
      <Notice tone="danger">Could not send: {current.error ?? 'the provider refused it.'}</Notice>
    )
  return <QueuedBanner message={current} onChanged={onChanged} />
}

/** Who else has this request open right now: nothing when nobody. */
function ViewingNow({ requestId }: { requestId: string }) {
  const me = useMe()
  const presence = usePresenceList()
  const viewers = viewersOf(presence.data?.people ?? [], requestId, me.id)
  if (viewers.length === 0) return null
  return (
    <>
      <span className="text-line-control">·</span>
      <span className="text-muted">Viewing now: {viewers.map((p) => p.name).join(', ')}</span>
    </>
  )
}

/** A file the server hands back as an attachment, saved by the browser without leaving the page. */
function downloadFile(href: string) {
  const link = document.createElement('a')
  link.href = href
  link.download = ''
  document.body.append(link)
  link.click()
  link.remove()
}
