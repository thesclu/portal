import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { useMe } from '../../app/session'
import type { ActionSpec } from '../../components/ActionDialog'
import {
  CheckIcon,
  ChevronDown,
  FeeIcon,
  MinusCircleIcon,
  PlusIcon,
  WarningIcon,
} from '../../components/icons'
import { Menu, MenuPanel, type MenuItem } from '../../components/Menu'
import { Avatar, cx } from '../../components/ui'
import { civilDayOf, formatShortDate, formatShortInstant } from '../../lib/dates'
import { initialsOf } from '../../lib/presence'
import { isPreFiling } from '../../lib/status'
import { itemState, statusSteps, taskDue, type Step } from '../../lib/requestView'
import { useNow } from '../../lib/tasks'
import { CHANNEL, humanize, money, useNameOf } from '../../lib/vocab'
import * as actions from './actions'

type Detail = Schemas['RequestDetailOut']
type Deadline = Schemas['DeadlineOut']
type Attachment = Schemas['AttachmentOut']
type Act = (make: (context: actions.Context) => ActionSpec) => void

/** A block of the right column (062): the small-capitals title and its rows. */
function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-12 border border-transparent px-4 py-2">
      <h2 className="flex items-baseline pb-2 text-11 leading-[normal] font-semibold tracking-group text-muted uppercase">
        {title}
      </h2>
      {children}
    </section>
  )
}

/** The hover card under a row (064): a 12 px line and an optional muted one. */
function Hint({
  title,
  sub,
  className = 'top-[calc(100%+4px)] left-6',
}: {
  title: string
  sub?: string | null
  className?: string
}) {
  return (
    <span
      role="tooltip"
      className={cx(
        'pointer-events-none absolute z-[6] hidden flex-col gap-0.5 rounded-10 border border-line bg-card-white px-2.5 py-[7px] whitespace-nowrap shadow-menu group-hover:flex',
        className,
      )}
    >
      <span className="text-12 leading-[normal] font-medium text-ink">{title}</span>
      {sub ? <span className="text-115 leading-[normal] text-muted">{sub}</span> : null}
    </span>
  )
}

/** A row that lights on hover (063): the fill reaches 6 px past the column, the words stay put. */
const ROW =
  'group relative flex gap-2.5 rounded-6 px-0.5 py-[5px] hover:-mx-1.5 hover:bg-hover hover:px-2'

const LINK = 'text-12 font-medium whitespace-nowrap text-brand hover:text-brand-dark'

/** "＋ Add task" (062). */
function AddRow({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1.5 pt-1.5 pb-1 text-125 font-medium text-brand hover:text-brand-dark"
    >
      <PlusIcon size={14} width={2} stroke="var(--color-brand)" />
      {label}
    </button>
  )
}

export function Side({
  request,
  attachments,
  canAct,
  act,
  onChanged,
  kindLabel,
  onRespond,
}: {
  request: Detail
  attachments: Attachment[]
  canAct: boolean
  act: Act
  onChanged: () => void
  kindLabel: (code: string) => string
  /** "Respond" on a withheld item: appeal it (081). */
  onRespond: (item: Detail['items'][number]) => void
}) {
  const pending = isPreFiling(request.current_status)
  const openTasks = request.tasks.filter((task) => !task.completed_at)
  return (
    <aside
      aria-label="Obligations"
      className="mt-[22px] box-content flex min-w-0 flex-[0_0_290px] flex-col gap-1.5 pr-2"
    >
      <StatusBlock request={request} canAct={canAct} act={act} kindLabel={kindLabel} />
      {request.items.length > 0 ? (
        <Block title="Request items">
          {request.items.map((item, index) => (
            <ItemRow
              key={item.id}
              item={item}
              last={index === request.items.length - 1}
              files={attachments}
              links={request.file_links}
              canAct={
                canAct &&
                !request.challenges.some((c) => c.request_item_id === item.id && !c.resolved_at)
              }
              onRespond={() => onRespond(item)}
            />
          ))}
        </Block>
      ) : null}
      {pending && openTasks.length === 0 ? null : (
        <Block title="Tasks">
          {openTasks.length === 0 ? (
            <div className="py-1 text-125 text-muted">No open tasks</div>
          ) : (
            openTasks.map((task) => (
              <TaskRow key={task.id} task={task} canAct={canAct} onChanged={onChanged} />
            ))
          )}
          {canAct ? <AddRow label="Add task" onClick={() => act(actions.createTask)} /> : null}
        </Block>
      )}
      {request.fees.length > 0 ? (
        <Block title="Fees">
          {request.fees.map((fee, index) => (
            <FeeRow
              key={fee.id}
              fee={fee}
              last={index === request.fees.length - 1}
              canAct={canAct}
              act={act}
            />
          ))}
          {canAct ? <AddRow label="Add fee" onClick={() => act(actions.recordFee)} /> : null}
        </Block>
      ) : null}
      {request.challenges.length > 0 ? (
        <Block title="Appeals and challenges">
          <Challenges request={request} canAct={canAct} act={act} />
        </Block>
      ) : null}
      {request.links.length > 0 ? (
        <Block title="Related requests">
          <Related request={request} canAct={canAct} act={act} />
        </Block>
      ) : null}
    </aside>
  )
}

/* ── Status ─────────────────────────────────────────────────────────────── */

function StatusBlock({
  request,
  canAct,
  act,
  kindLabel,
}: {
  request: Detail
  canAct: boolean
  act: Act
  kindLabel: (code: string) => string
}) {
  const me = useMe()
  const filing = request.events.find((e) => e.event_type === 'submission' && !e.voided_at)
  const steps = statusSteps(request, {
    filedBy: filing ? (CHANNEL[filing.channel] ?? filing.channel).toLowerCase() : null,
  })
  return (
    <Block title="Status">
      <div className="pt-0.5">
        {steps.map((step, index) => (
          <StepRow
            key={step.key}
            step={step}
            next={steps[index + 1]}
            when={step.at ? formatShortInstant(step.at, me.timezone) : null}
            canAct={canAct}
            act={act}
            kindLabel={kindLabel}
          />
        ))}
      </div>
    </Block>
  )
}

function Dot({ state }: { state: Step['state'] }) {
  if (state === 'done')
    return (
      <span className="box-border inline-flex size-3.5 shrink-0 items-center justify-center rounded-pill bg-brand">
        <CheckIcon size={8} width={4} stroke="var(--color-card-white)" />
      </span>
    )
  return (
    <span
      className={cx(
        'box-border inline-flex size-3.5 shrink-0 rounded-pill bg-card-white',
        state === 'current' ? 'border-2 border-brand' : 'border-[1.5px] border-line-control',
      )}
    />
  )
}

function StepRow({
  step,
  next,
  when,
  canAct,
  act,
  kindLabel,
}: {
  step: Step
  next: Step | undefined
  when: string | null
  canAct: boolean
  act: Act
  kindLabel: (code: string) => string
}) {
  const hover = step.state === 'done' && when
  return (
    <div
      className={cx(
        'relative grid grid-cols-[14px_minmax(0,1fr)] gap-x-3',
        hover &&
          'group rounded-6 hover:-mx-2 hover:-mt-1.5 hover:-mb-1 hover:bg-hover hover:px-2 hover:pt-1.5 hover:pb-1',
      )}
    >
      <div className="flex flex-col items-center">
        <Dot state={step.state} />
        {next ? (
          <span
            className={cx(
              'my-[3px] min-h-[18px] w-0.5 flex-1',
              step.state === 'done' ? 'bg-brand' : 'bg-line',
            )}
          />
        ) : null}
      </div>
      <div className={cx('-mt-0.5 pb-3', hover && 'group-hover:mt-0')}>
        <div className="flex items-center gap-1.5">
          <span
            className={cx(
              'text-125',
              step.state === 'current' ? 'font-semibold text-ink' : 'font-medium',
              step.state === 'done' && 'text-ink',
              step.state === 'future' && 'text-muted',
            )}
          >
            {step.label}
          </span>
          {step.conflict ? (
            <>
              <WarningIcon size={12} width={2.2} stroke="var(--color-attention)" />
              {canAct ? (
                <button
                  type="button"
                  onClick={() =>
                    act((c) => actions.resolveDeadlines(c, step.conflict as Deadline[]))
                  }
                  className="text-12 whitespace-nowrap text-warn underline decoration-current underline-offset-[3px]"
                >
                  Resolve due date
                </button>
              ) : null}
            </>
          ) : null}
        </div>
        {step.sub ? <div className="mt-0.5 text-12 text-muted">{step.sub}</div> : null}
        {step.deadlines && step.deadlines.length > 0 ? (
          <div className="mt-1.5">
            {step.deadlines.map(({ deadline, tone }) => (
              <DeadlineRow
                key={deadline.id}
                deadline={deadline as Deadline}
                tone={tone}
                canAct={canAct}
                act={act}
                kindLabel={kindLabel}
              />
            ))}
          </div>
        ) : null}
      </div>
      {hover ? (
        <Hint title={when} sub={step.detail} className="top-[calc(100%-8px)] left-[30px]" />
      ) : null}
    </div>
  )
}

const BASIS: Record<string, string> = {
  statutory: 'From the rules file',
  agency_commitment: 'Their commitment',
  internal: 'Our own date',
}

/** One date the current step waits on (062): the date, and where it comes from. A click opens
 *  what can be done with it. */
function DeadlineRow({
  deadline,
  tone,
  canAct,
  act,
  kindLabel,
}: {
  deadline: Deadline
  tone: 'ink' | 'warn' | 'danger'
  canAct: boolean
  act: Act
  kindLabel: (code: string) => string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])
  const today = useNow().toISOString().slice(0, 10)
  // An active date already behind us reads as overdue before the hourly sweep marks it missed.
  const shown = deadline.status === 'active' && deadline.due_at < today ? 'danger' : tone
  const words =
    deadline.legal_basis ?? deadline.provision ?? BASIS[deadline.basis] ?? humanize(deadline.basis)
  const items: MenuItem[] = [
    { label: 'Mark as met…', onPick: () => act((c) => actions.satisfyDeadline(c, deadline)) },
    { label: 'Update the date…', onPick: () => act((c) => actions.updateDeadline(c, deadline)) },
    { label: 'Waive…', onPick: () => act((c) => actions.waiveDeadline(c, deadline)) },
  ]
  const body = (
    <>
      <span
        className={cx(
          'flex-[0_0_46px] text-left font-medium',
          shown === 'danger' ? 'text-danger' : shown === 'warn' ? 'text-warn' : 'text-ink',
        )}
      >
        {formatShortDate(deadline.due_at)}
      </span>
      <span className="min-w-0 truncate text-left text-muted">
        {deadline.deadline_type === 'production' ||
        deadline.deadline_type === 'statutory_determination'
          ? words
          : `${kindLabel(deadline.deadline_type)} · ${words}`}
      </span>
    </>
  )
  if (deadline.status !== 'active' || !canAct)
    return <div className="flex items-baseline gap-2.5 py-[3px] text-12">{body}</div>
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className="flex w-full items-baseline gap-2.5 py-[3px] text-12"
      >
        {body}
      </button>
      {open ? (
        <MenuPanel
          items={items}
          width={200}
          onPicked={() => setOpen(false)}
          className="absolute top-full left-0 z-20 mt-1"
        />
      ) : null}
    </div>
  )
}

/* ── Items, tasks, fees ─────────────────────────────────────────────────── */

function ItemRow({
  item,
  last,
  files,
  links,
  canAct,
  onRespond,
}: {
  item: Detail['items'][number]
  last: boolean
  files: Attachment[]
  links: Detail['file_links']
  canAct: boolean
  onRespond: () => void
}) {
  const state = itemState(item.disposition)
  const linked = links
    .filter((link) => link.request_item_id === item.id)
    .map((link) => files.find((file) => file.id === link.attachment_id)?.filename)
    .filter(Boolean)
  const hint =
    state === 'produced'
      ? {
          title: item.disposition_at
            ? `Produced ${formatShortDate(item.disposition_at)}`
            : 'Produced',
          sub: linked.join(', ') || item.disposition_note,
        }
      : state === 'withheld'
        ? {
            title: item.disposition === 'partly' ? 'Partly produced' : 'Withheld',
            sub:
              item.disposition_note ??
              (item.exemption_cited ? `Under ${item.exemption_cited}` : null),
          }
        : item.provision
          ? { title: 'Its own deadline', sub: item.provision }
          : null
  // Hovering the icon alone rings it and leaves the row unlit (064, third board).
  const icon = 'mt-0.5 inline-flex rounded-pill hover:bg-hover hover:shadow-ring-grey'
  return (
    <div
      className={cx(
        ROW,
        'items-start has-[[data-icon]:hover]:mx-0 has-[[data-icon]:hover]:bg-transparent has-[[data-icon]:hover]:px-0.5',
        last && 'pb-[9px]',
      )}
    >
      {state === 'produced' ? (
        <span data-icon className={icon}>
          <CheckIcon size={14} width={2.4} stroke="var(--color-ok)" />
        </span>
      ) : state === 'withheld' ? (
        <span data-icon className={icon}>
          <MinusCircleIcon size={14} width={2.2} stroke="var(--color-attention)" />
        </span>
      ) : (
        <span className="mt-0.5 box-border inline-block size-3.5 shrink-0 rounded-pill border-[1.5px] border-line-control" />
      )}
      <div className="min-w-0 flex-1 text-125 leading-[1.45]">
        <div className="flex items-baseline gap-2.5">
          <span className="min-w-0 flex-1">
            <span className="mr-1.5 text-muted">{item.item_number}</span>
            <span className="text-ink">{item.description}</span>
          </span>
          {state === 'withheld' && canAct ? (
            <button type="button" onClick={onRespond} className={LINK}>
              Respond
            </button>
          ) : null}
        </div>
      </div>
      {hint ? <Hint title={hint.title} sub={hint.sub} /> : null}
    </div>
  )
}

function TaskRow({
  task,
  canAct,
  onChanged,
}: {
  task: Detail['tasks'][number]
  canAct: boolean
  onChanged: () => void
}) {
  const me = useMe()
  const nameOf = useNameOf()
  const who = nameOf(task.assigned_to_user_id)
  const today = civilDayOf(useNow().toISOString(), me.timezone)
  const due = task.due_at ? civilDayOf(task.due_at, me.timezone) : null
  const word = taskDue(due, today)
  // Rules are entered in the rules file, never ticked off here.
  const tickable = canAct && task.task_type !== 'enter_deadline_rules'
  return (
    <div className={cx(ROW, 'items-center text-125')}>
      <button
        type="button"
        role="checkbox"
        aria-checked={false}
        aria-label={`Mark “${task.description.split('\n')[0]}” done`}
        disabled={!tickable}
        onClick={() =>
          void api
            .POST('/api/records/tasks/{task_id}/complete', {
              params: { path: { task_id: task.id } },
            })
            .then(onChanged)
        }
        className="box-border size-3.5 shrink-0 rounded-4 border-[1.5px] border-line-control hover:border-ink-3 disabled:cursor-default disabled:hover:border-line-control"
      />
      <span className="min-w-0 flex-[0_1_auto] truncate text-ink">
        {task.description.split('\n')[0]}
      </span>
      {who ? <Avatar initials={initialsOf(who)} size={20} /> : null}
      <span
        className={cx(
          'ml-auto text-right text-12 whitespace-nowrap',
          word.late ? 'text-danger' : 'text-muted',
        )}
      >
        {word.text ?? (due ? formatShortDate(due) : '')}
      </span>
      <Hint
        title={due ? `Due ${formatShortDate(due)}` : 'No due date'}
        sub={
          who
            ? `Assigned to ${who}`
            : task.created_by_system
              ? 'Opened automatically'
              : 'Nobody has it yet'
        }
      />
    </div>
  )
}

const FEE_WORDS: Record<string, string> = Object.fromEntries(
  actions.FEE_STATUS.map((s) => [s.value, s.label]),
)

function FeeRow({
  fee,
  last,
  canAct,
  act,
}: {
  fee: Detail['fees'][number]
  last: boolean
  canAct: boolean
  act: Act
}) {
  const me = useMe()
  const open = fee.status === 'assessed' || fee.status === 'waiting_on_payment'
  const line =
    fee.status === 'paid' && fee.paid_at
      ? `${money(fee.amount_cents)} · paid ${formatShortInstant(fee.paid_at, me.timezone)}`
      : `${money(fee.amount_cents)} · ${FEE_WORDS[fee.status] ?? humanize(fee.status)}`
  const items: MenuItem[] = [
    ...(fee.status === 'assessed'
      ? [{ label: 'Pay the fee', onPick: () => act((c) => actions.payFee(c, fee)) }]
      : []),
    { label: 'Contest the fee', onPick: () => act((c) => actions.settleFee(c, fee, 'disputed')) },
    { label: 'Ask to narrow the request', onPick: () => act(actions.narrow) },
    {
      label: 'Record a payment made',
      separator: true,
      onPick: () => act((c) => actions.settleFee(c, fee, 'paid')),
    },
  ]
  return (
    <div className={cx(ROW, 'items-start', last && 'pb-[9px]')}>
      <span className="mt-0.5">
        <FeeIcon size={14} width={2} stroke="var(--color-attention)" />
      </span>
      <div className="min-w-0 flex-1 text-125 leading-[1.45]">
        <div className="flex items-baseline gap-2.5">
          <span className="min-w-0 flex-1 text-ink">{fee.description || 'Fee'}</span>
          {canAct && open ? (
            <Menu
              label="Fee actions"
              width={220}
              items={items}
              panelClassName="mt-1.5 z-[6]"
              buttonClassName="rounded-4"
            >
              <span className="inline-flex items-center gap-1 text-12 font-medium whitespace-nowrap text-brand">
                Pay
                <ChevronDown size={10} width={2.6} stroke="var(--color-brand)" />
              </span>
            </Menu>
          ) : null}
        </div>
        <div className={cx('mt-[3px] text-12', open ? 'text-warn' : 'text-muted')}>{line}</div>
      </div>
      <Hint
        title={`Noticed ${formatShortInstant(fee.created_at, me.timezone)}`}
        sub={fee.due_at ? `Pay by ${formatShortDate(fee.due_at)}` : fee.payment_method}
      />
    </div>
  )
}

/* ── Appeals, related requests (not drawn in v4; kept so they stay reachable) ───────── */

function Challenges({ request, canAct, act }: { request: Detail; canAct: boolean; act: Act }) {
  const me = useMe()
  return (
    <div className="flex flex-col gap-2">
      {request.challenges.map((row) => {
        const item = request.items.find((i) => i.id === row.request_item_id)
        const outcome = actions.CHALLENGE_OUTCOME.find((o) => o.value === row.outcome)
        return (
          <div key={row.id} className="text-125 leading-[1.45]">
            <div className="font-medium text-ink">
              {row.kind === 'appeal' ? 'Appeal' : 'Search challenge'}
              {item ? ` · item ${item.item_number}` : ''}
            </div>
            <div className="text-12 text-muted">
              {row.resolved_at
                ? `${outcome?.label ?? humanize(row.outcome ?? '')} · ${formatShortInstant(row.resolved_at, me.timezone)}`
                : row.sent_at
                  ? `Sent ${formatShortInstant(row.sent_at, me.timezone)}${row.follow_up_by ? ` · follow up by ${formatShortDate(row.follow_up_by)}` : ''}`
                  : `Letter not sent yet${row.follow_up_by ? ` · follow up by ${formatShortDate(row.follow_up_by)}` : ''}`}
            </div>
            {canAct && !row.resolved_at ? (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {!row.sent_at ? (
                  <>
                    <button
                      type="button"
                      className={LINK}
                      onClick={() => act((c) => actions.challengeSent(c, row))}
                    >
                      Sent another way
                    </button>
                    <button
                      type="button"
                      className={LINK}
                      onClick={() => act((c) => actions.challengeFollowUp(c, row))}
                    >
                      Follow up by
                    </button>
                  </>
                ) : null}
                <button
                  type="button"
                  className={LINK}
                  onClick={() => act((c) => actions.challengeOutcome(c, row))}
                >
                  Record the outcome
                </button>
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

function Related({ request, canAct, act }: { request: Detail; canAct: boolean; act: Act }) {
  return (
    <div className="flex flex-col gap-2">
      {request.links.map((link) => {
        const outgoing = link.from_request_id === request.id
        const otherId = outgoing ? link.to_request_id : link.from_request_id
        const other = outgoing ? link.to_tracking_number : link.from_tracking_number
        const subject = outgoing ? link.to_subject : link.from_subject
        const words =
          link.kind === 'duplicate'
            ? outgoing
              ? 'Duplicate of'
              : 'Duplicated by'
            : outgoing
              ? 'Follows on from'
              : 'Followed by'
        return (
          <div key={link.id} className="text-125 leading-[1.45]">
            <span className="text-muted">{words} </span>
            <Link className="text-brand" to={`/requests/${otherId}`}>
              {subject}
            </Link>
            <div className="truncate text-12 text-muted" title={link.reason}>
              {other} · {link.reason}
            </div>
            {canAct ? (
              <button
                type="button"
                className="text-12 text-muted hover:text-ink"
                onClick={() => act((c) => actions.voidLink(c, link))}
              >
                Remove
              </button>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
