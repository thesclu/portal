import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'

import { api } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { instantOfParts, unwrap } from '../../components/ActionDialog'
import { ChevronDown } from '../../components/icons'
import { cx } from '../../components/ui'
import { formatShortDate } from '../../lib/dates'
import { usePeople } from '../../lib/vocab'

const BOX =
  'box-border flex w-full items-center gap-2 rounded-12 border-[1.5px] border-transparent bg-card-white px-[10.5px] py-[7.5px] text-125 leading-[normal] text-ink shadow-field outline-none focus-within:rounded-9 focus-within:border-brand focus-within:shadow-ring-focus'

function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-[5px]">
      <span className="text-12 leading-[normal] font-medium text-ink-2">{label}</span>
      {children}
    </label>
  )
}

/** A native select drawn as the popover's raised box with its 10 px chevron. */
function PickBox({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  children: ReactNode
}) {
  return (
    <span className={cx(BOX, 'relative')}>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-w-0 flex-1 appearance-none truncate bg-transparent outline-none"
      >
        {children}
      </select>
      <ChevronDown size={10} width={2.4} stroke="var(--color-muted)" />
    </span>
  )
}

/**
 * The New task popover (183): 360 px under "＋ New task", what needs doing, the request, the due
 * date and who it is for, Cancel and Add task on a grey band. A task a person adds is theirs to
 * describe (`manual`); the portal raises the other kinds itself.
 */
export function NewTaskPopover({
  requestId,
  onClose,
  onAdded,
}: {
  /** The request the list is showing (180), chosen for the person. */
  requestId?: string | null
  onClose: () => void
  onAdded: () => void
}) {
  const me = useMe()
  const people = usePeople()
  const box = useRef<HTMLDivElement>(null)
  const dateInput = useRef<HTMLInputElement>(null)
  const requests = $api.useQuery('get', '/api/records/requests', {
    params: { query: { open: true, sort: 'deadline', limit: 500 } },
  })
  const [what, setWhat] = useState('')
  const [request, setRequest] = useState(requestId ?? '')
  const [due, setDue] = useState('')
  const [who, setWho] = useState(me.id)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    function away(event: PointerEvent) {
      if (box.current && !box.current.contains(event.target as Node)) onClose()
    }
    function key(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', key)
    }
  }, [onClose])

  const staff = (people.data ?? []).filter(
    (p) => p.is_active && p.records_role !== 'requester' && p.id !== me.id,
  )
  const rows = requests.data?.items ?? []

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (!request) throw new Error('Choose the request this is for.')
      unwrap(
        await api.POST('/api/records/tasks', {
          body: {
            request_id: request,
            task_type: 'manual',
            description: what.trim(),
            due_at: due ? instantOfParts(due, '17:00') : null,
            assigned_to_user_id: who || null,
          },
        }),
      )
      onAdded()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The task was not added.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      ref={box}
      role="dialog"
      aria-label="New task"
      className="absolute top-[42px] right-0 z-20 w-[360px] overflow-hidden rounded-16 bg-card leading-[normal] shadow-popover-3"
    >
      <div className="px-[18px] pt-4 pb-1 font-display text-md-lg font-extrabold text-ink">
        New task
      </div>
      <form id="new-task" onSubmit={(event) => void submit(event)}>
        <div className="flex flex-col gap-3 px-[18px] pt-3 pb-4">
          <Labelled label="What needs doing">
            <span className={BOX}>
              <input
                required
                autoFocus
                value={what}
                onChange={(event) => setWhat(event.target.value)}
                className="min-w-0 flex-1 bg-transparent outline-none"
              />
            </span>
          </Labelled>
          <Labelled label="Request">
            <PickBox label="Request" value={request} onChange={setRequest}>
              <option value="">{requests.isPending ? 'Loading…' : 'Choose a request…'}</option>
              {rows.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.tracking_number} · {row.agency_name}
                </option>
              ))}
            </PickBox>
          </Labelled>
          <div className="flex gap-2.5">
            <div className="min-w-0 flex-1">
              <Labelled label="Due">
                <span className={cx(BOX, 'relative cursor-pointer')}>
                  <span className={cx('min-w-0 flex-1', !due && 'text-muted')}>
                    {due ? formatShortDate(due) : 'No date'}
                  </span>
                  <ChevronDown size={10} width={2.4} stroke="var(--color-muted)" />
                  <input
                    ref={dateInput}
                    type="date"
                    aria-label="Due"
                    value={due}
                    onChange={(event) => setDue(event.target.value)}
                    onClick={() => dateInput.current?.showPicker?.()}
                    className="absolute inset-0 cursor-pointer opacity-0"
                  />
                </span>
              </Labelled>
            </div>
            <div className="min-w-0 flex-1">
              <Labelled label="Assign to">
                <PickBox label="Assign to" value={who} onChange={setWho}>
                  <option value={me.id}>Me</option>
                  {staff.map((person) => (
                    <option key={person.id} value={person.id}>
                      {person.name}
                    </option>
                  ))}
                  <option value="">Nobody</option>
                </PickBox>
              </Labelled>
            </div>
          </div>
          {error ? (
            <p role="alert" className="text-12 text-danger">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-hairline bg-row-alt px-[18px] py-3">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 items-center rounded-9 px-3.5 text-125 font-medium text-ink-2 hover:bg-hover"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="inline-flex h-8 items-center rounded-9 bg-brand px-3.5 text-125 font-medium text-white hover:bg-brand-dark disabled:opacity-55"
          >
            Add task
          </button>
        </div>
      </form>
    </div>
  )
}
