import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import type { Me } from '../../app/session'
import { Dialog } from '../../components/Dialog'
import { SearchIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { formatDayTime, formatShortInstant } from '../../lib/dates'
import { initialsOf } from '../../lib/presence'
import { isFullStaff, roleShort } from '../../lib/roles'

type Detail = Schemas['RequestDetailOut']
type Access = Schemas['AccessOut']
type Person = Schemas['UserOut']
type Review = Schemas['ApprovalOut']

const first = (name: string) => name.trim().split(/\s+/)[0] ?? name

/** "owner", "owner and Priya", "owner, Priya and Maya". */
export function namesSentence(names: string[]): string {
  if (names.length <= 1) return names[0] ?? ''
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
}

/**
 * Who has access to a request (231, 230): the people it was given to, and before them the
 * organisation's legal director when they see it anyway (an administrator, or staff and staff
 * attorneys on a request that is not privileged), so the line names everyone the design names.
 */
export function usePeopleWithAccess(
  request: Pick<Detail, 'id' | 'sensitivity'>,
  access: Access[],
  people: Person[],
): Array<{ id: string; name: string; sub: string; grant: Access | null }> {
  const settings = $api.useQuery('get', '/api/core/settings')
  const directorId = String(
    (settings.data ?? []).find((s) => s.key === 'org.legal_director')?.value ?? '',
  )
  return useMemo(() => {
    const byId = new Map(people.map((p) => [p.id, p]))
    const rows = access.map((grant) => {
      const person = byId.get(grant.user_id)
      return {
        id: grant.user_id,
        name: grant.user_name,
        sub: person ? roleShort(person.records_role) : '',
        grant,
      }
    })
    const director = byId.get(directorId)
    const seesAnyway =
      director &&
      director.is_active &&
      (director.records_role === 'admin' ||
        (request.sensitivity !== 'privileged' && isFullStaff(director.records_role)))
    if (director && seesAnyway && !rows.some((row) => row.id === director.id)) {
      rows.unshift({ id: director.id, name: director.name, sub: 'Legal director', grant: null })
    }
    return rows
  }, [access, people, directorId, request.sensitivity])
}

/** The 22 px initials, overlapping by 6 px on a 2 px white ring (231). */
function Stack({ names }: { names: string[] }) {
  return (
    <span className="inline-flex">
      {names.map((name, index) => (
        <span
          key={`${name}-${index}`}
          aria-hidden
          className={cx(
            'inline-flex size-[22px] items-center justify-center rounded-pill bg-brand text-9 leading-none font-semibold text-white shadow-ring-avatar',
            index > 0 && '-ml-1.5',
          )}
        >
          {initialsOf(name)}
        </span>
      ))}
    </span>
  )
}

/** The end of the meta row (231): who has access, and Change access for an administrator. */
export function AccessLine({
  rows,
  canChange,
  onChange,
}: {
  rows: Array<{ name: string }>
  canChange: boolean
  onChange: () => void
}) {
  if (rows.length === 0 && !canChange) return null
  const names = rows.map((row) => first(row.name))
  return (
    <span className="ml-auto inline-flex items-center gap-2.5">
      {rows.length > 0 ? (
        <>
          <Stack names={rows.map((row) => row.name)} />
          <span className="text-ink-2">
            {namesSentence(names)} {names.length === 1 ? 'has' : 'have'} access
          </span>
        </>
      ) : null}
      {canChange ? (
        <button
          type="button"
          onClick={onChange}
          className="font-medium text-brand hover:text-brand-dark"
        >
          Change access
        </button>
      ) : null}
    </span>
  )
}

/**
 * Change access (099, 230), an administrator's: the people the request was given to, each with
 * Remove, and "Add an attorney or intern" to give it to someone else (anyone on the team can be
 * found; attorneys and interns first). Nothing changes until Save, which gives and takes each
 * grant in turn. The legal director, who sees the request anyway, is listed without Remove.
 */
export function ChangeAccessDialog({
  request,
  rows,
  people,
  open,
  onClose,
  onSaved,
}: {
  request: Pick<Detail, 'id' | 'tracking_number' | 'agency_name'>
  rows: Array<{ id: string; name: string; sub: string; grant: Access | null }>
  people: Person[]
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  return open ? (
    <ChangeAccessOpen
      request={request}
      rows={rows}
      people={people}
      onClose={onClose}
      onSaved={onSaved}
    />
  ) : null
}

const SCOPED_FIRST: Record<string, number> = {
  limited_attorney: 0,
  intern: 1,
  staff_attorney: 2,
  staff: 3,
  admin: 4,
}

function ChangeAccessOpen({
  request,
  rows,
  people,
  onClose,
  onSaved,
}: {
  request: Pick<Detail, 'id' | 'tracking_number' | 'agency_name'>
  rows: Array<{ id: string; name: string; sub: string; grant: Access | null }>
  people: Person[]
  onClose: () => void
  onSaved: () => void
}) {
  const me = $api.useQuery('get', '/api/core/auth/me')
  const zone = me.data?.timezone ?? 'UTC'
  const [removed, setRemoved] = useState<string[]>([])
  const [added, setAdded] = useState<Person[]>([])
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const listed = new Set([...rows.map((row) => row.id), ...added.map((p) => p.id)])
  const needle = query.trim().toLowerCase()
  const matches = needle
    ? people
        .filter(
          (p) =>
            p.is_active &&
            p.records_role &&
            p.records_role !== 'requester' &&
            !listed.has(p.id) &&
            p.name.toLowerCase().includes(needle),
        )
        .toSorted(
          (a, b) =>
            (SCOPED_FIRST[a.records_role ?? ''] ?? 9) - (SCOPED_FIRST[b.records_role ?? ''] ?? 9) ||
            a.name.localeCompare(b.name),
        )
        .slice(0, 6)
    : []

  async function save() {
    setBusy(true)
    setError(null)
    for (const person of added) {
      const result = await api.POST('/api/records/requests/{request_id}/access', {
        params: { path: { request_id: request.id } },
        body: { user_id: person.id },
      })
      if (result.error) {
        setBusy(false)
        setError(messageOf(result.error))
        return
      }
    }
    for (const row of rows.filter((r) => r.grant && removed.includes(r.id))) {
      const result = await api.POST('/api/records/access/{access_id}/revoke', {
        params: { path: { access_id: row.grant!.id } },
      })
      if (result.error) {
        setBusy(false)
        setError(messageOf(result.error))
        return
      }
    }
    setBusy(false)
    onSaved()
  }

  const shown = [
    ...rows.filter((row) => !removed.includes(row.id)),
    ...added.map((p) => ({ id: p.id, name: p.name, sub: roleShort(p.records_role), grant: null })),
  ]
  return (
    <Dialog title="Change access" open onClose={onClose} width={480} top={90} bare>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
        className="leading-[normal]"
      >
        <div className="px-6 pt-[22px] pb-1">
          <h2 className="font-display text-18 font-extrabold text-ink">Change access</h2>
          <p className="mt-1 text-125 text-muted">
            {request.tracking_number} · {request.agency_name}
          </p>
        </div>
        <div className="relative px-6 pt-3.5">
          <label className="box-border flex h-[38px] items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1 text-13">
            <SearchIcon size={15} width={2} stroke="var(--color-ink-3)" />
            <input
              aria-label="Add an attorney or intern"
              placeholder="Add an attorney or intern"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-ink-3"
            />
          </label>
          {matches.length > 0 ? (
            <div
              role="listbox"
              className="absolute inset-x-6 top-full z-10 mt-1 overflow-hidden rounded-10 bg-card-white py-1 shadow-panel"
            >
              {matches.map((person) => (
                <button
                  key={person.id}
                  type="button"
                  role="option"
                  aria-selected={false}
                  onClick={() => {
                    setAdded((was) => [...was, person])
                    setQuery('')
                  }}
                  className="flex w-full items-center gap-2.5 px-3.5 py-[7px] text-left text-13 text-ink hover:bg-hover"
                >
                  <span className="min-w-0 flex-1 truncate">{person.name}</span>
                  <span className="text-12 text-muted">{roleShort(person.records_role)}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="px-6 pt-2.5">
          {error ? (
            <p
              role="alert"
              className="mb-2 rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 leading-[1.5] text-danger-2"
            >
              {error}
            </p>
          ) : null}
          {shown.length === 0 ? (
            <p className="py-2.5 text-125 text-muted">Nobody has been given this request.</p>
          ) : null}
          {shown.map((row, index) => (
            <div
              key={row.id}
              className={cx(
                'flex items-center gap-[11px] py-2.5',
                index > 0 && 'border-t border-line-row',
              )}
            >
              <span
                aria-hidden
                className="inline-flex size-7 shrink-0 items-center justify-center rounded-pill bg-brand text-105 font-semibold text-white"
              >
                {initialsOf(row.name)}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-13 font-medium text-ink">{row.name}</div>
                <div className="text-12 text-muted">
                  {[
                    row.sub,
                    row.grant ? `given ${formatShortInstant(row.grant.granted_at, zone)}` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </div>
              </div>
              {row.grant || added.some((p) => p.id === row.id) ? (
                <button
                  type="button"
                  onClick={() =>
                    row.grant
                      ? setRemoved((was) => [...was, row.id])
                      : setAdded((was) => was.filter((p) => p.id !== row.id))
                  }
                  className="text-12 text-muted hover:text-ink-2"
                >
                  Remove
                </button>
              ) : null}
            </div>
          ))}
        </div>
        <div className="flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">
          <button
            type="button"
            onClick={onClose}
            className="rounded-pill px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-pill bg-brand px-[18px] py-[9px] text-13 font-semibold text-white hover:bg-brand-dark disabled:opacity-55"
          >
            Save
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/**
 * The strip an intern's letter puts under the meta row (233, 238): waiting for approval since
 * when, with Withdraw for the person who submitted it (and Review for whoever may decide it);
 * or sent back, by whom and when, the note, and Edit letter.
 */
export function ApprovalStrip({
  request,
  me,
  onChanged,
}: {
  request: Pick<Detail, 'id' | 'current_status'>
  me: Me
  onChanged: () => void
}) {
  const client = useQueryClient()
  const status = request.current_status
  const watched = status === 'waiting_for_approval' || status === 'pending_filing'
  const reviews = $api.useQuery(
    'get',
    '/api/core/reviews',
    { params: { query: { request_id: request.id } } },
    { enabled: watched },
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const newest: Review | undefined = (reviews.data ?? [])[0]
  if (!watched || !newest) return null
  const mine = newest.submitted_by_user_id === me.id

  if (status === 'waiting_for_approval' && !newest.decision) {
    async function withdraw() {
      setBusy(true)
      setError(null)
      const result = await api.POST('/api/core/reviews/{review_id}/withdraw', {
        params: { path: { review_id: newest!.id } },
      })
      setBusy(false)
      if (result.error) {
        setError(messageOf(result.error))
        return
      }
      await client.invalidateQueries({ queryKey: ['get', '/api/core/reviews'] })
      onChanged()
    }
    return (
      <div
        role="status"
        className="flex items-center gap-3 rounded-12 bg-brand-tint px-4 py-[11px] text-13 leading-[normal] text-brand"
      >
        <span className="font-semibold">Waiting for approval</span>
        <span className="text-ink-2">
          Submitted {formatDayTime(newest.submitted_at, me.timezone)}
        </span>
        {error ? <span className="text-danger">{error}</span> : null}
        {mine ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void withdraw()}
            className="ml-auto font-medium text-ink-2 hover:text-ink disabled:opacity-55"
          >
            Withdraw
          </button>
        ) : newest.can_decide ? (
          <Link
            to={`/approvals/${newest.id}`}
            className="ml-auto font-medium text-brand hover:text-brand-dark"
          >
            Review
          </Link>
        ) : null}
      </div>
    )
  }
  if (status === 'pending_filing' && newest.decision === 'sent_back') {
    return (
      <div
        role="status"
        className="flex items-start gap-3 rounded-12 bg-amber-tint px-4 py-3 text-13 text-amber"
      >
        <div className="min-w-0 flex-1 leading-[1.5]">
          <span className="font-semibold">
            Sent back by {newest.decided_by_name ?? 'an approver'}
            {newest.decided_at ? ` · ${formatShortInstant(newest.decided_at, me.timezone)}` : ''}
          </span>
          <br />
          <span className="text-ink-2">“{newest.comment}”</span>
        </div>
        {mine && newest.draft_id ? (
          <Link
            to={`/drafts/${newest.draft_id}?stage=draft`}
            className="rounded-9 bg-brand px-3.5 py-2 text-125 leading-[normal] font-semibold whitespace-nowrap text-white hover:bg-brand-dark hover:text-white"
          >
            Edit letter
          </Link>
        ) : null}
      </div>
    )
  }
  return null
}
