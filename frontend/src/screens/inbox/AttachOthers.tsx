/**
 * Attach to other requests (130): a message on one request that answers several. The requests
 * it names (by their address or the agency's own number) are ticked; the agency's other open
 * requests can be ticked too. Each tick puts the same message on that request as well; nothing is
 * moved off the request it is on.
 */
import { useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { unwrap } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { CheckIcon, SearchIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { senderOf, subjectOf, type Row } from './model'

type Candidate = Schemas['CandidateOut']

function Check({ on, locked = false }: { on: boolean; locked?: boolean }) {
  return (
    <span
      aria-hidden
      className={cx(
        'box-border inline-flex size-3.5 shrink-0 items-center justify-center rounded-4',
        locked
          ? 'bg-line-control'
          : on
            ? 'bg-brand'
            : 'border-[1.5px] border-line-control bg-card-white',
      )}
    >
      {on || locked ? <CheckIcon size={9} width={3.2} stroke="var(--color-card-white)" /> : null}
    </span>
  )
}

export function AttachOthers({
  row,
  candidates,
  currentSubject,
  onClose,
  onDone,
}: {
  row: Row
  candidates: Candidate[]
  /** The subject of the request it is already on. */
  currentSubject?: string
  onClose: () => void
  onDone: (count: number) => void
}) {
  const landed = row.events[0]
  const agencyId = landed?.agency_id ?? ''
  const [query, setQuery] = useState('')
  const open = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { agency_id: agencyId, open: true, limit: 50, sort: 'newest' } } },
    { enabled: Boolean(agencyId) },
  )
  const on = new Set(row.events.map((e) => e.request_id))
  const named = candidates.filter((c) => !on.has(c.request_id))
  const namedIds = new Set(named.map((c) => c.request_id))
  const [chosen, setChosen] = useState<string[]>(() => named.map((c) => c.request_id))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const needle = query.trim().toLowerCase()
  const matches = (words: string) => !needle || words.toLowerCase().includes(needle)
  const otherOpen = (open.data?.items ?? []).filter(
    (r) => !on.has(r.id) && !namedIds.has(r.id) && matches(`${r.subject} ${r.tracking_number}`),
  )
  const toggle = (id: string) =>
    setChosen((was) => (was.includes(id) ? was.filter((x) => x !== id) : [...was, id]))

  async function attach() {
    if (!landed) return
    setBusy(true)
    setError(null)
    try {
      for (const id of chosen) {
        unwrap(
          await api.POST('/api/records/sources/{source_id}/assign', {
            params: { path: { source_id: row.source.id } },
            body: { request_id: id, mode: 'also_applies', from_event_id: landed.id },
          }),
        )
      }
      unwrap(
        await api.POST('/api/records/sources/{source_id}/reviewed', {
          params: { path: { source_id: row.source.id } },
        }),
      )
      onDone(chosen.length)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not attach it.')
    } finally {
      setBusy(false)
    }
  }

  const group = (label: string) => (
    <div className="pt-3 pb-0.5 text-11 font-semibold tracking-group text-muted uppercase">
      {label}
    </div>
  )
  const line = (
    key: string,
    subject: string,
    meta: string,
    state: 'on' | 'named' | 'other',
    last: boolean,
  ) => {
    const locked = state === 'on'
    const ticked = locked || chosen.includes(key)
    return (
      <label
        key={key}
        className={cx(
          'flex h-[46px] items-center gap-3',
          !last && 'border-b border-line-row',
          locked ? 'cursor-default' : 'cursor-pointer',
        )}
      >
        <input
          type="checkbox"
          className="sr-only"
          checked={ticked}
          disabled={locked}
          onChange={() => toggle(key)}
        />
        <Check on={ticked} locked={locked} />
        <div className="min-w-0 flex-1">
          <div className={cx('truncate text-13', locked ? 'text-muted' : 'text-ink')}>
            {subject}
          </div>
          <div className="mt-px text-115 text-muted">{meta}</div>
        </div>
        {state === 'on' ? (
          <span className="text-115 whitespace-nowrap text-muted">Already attached</span>
        ) : state === 'named' ? (
          <span className="text-115 whitespace-nowrap text-warn">Named in letter</span>
        ) : null}
      </label>
    )
  }

  return (
    <Dialog title="Attach to other requests" open bare centered width={560} onClose={onClose}>
      <div className="px-6 pt-[22px] pb-1">
        <div className="font-display text-18 font-extrabold tracking-dialog text-ink">
          Attach to other requests
        </div>
        <div className="mt-[3px] text-125 text-muted">
          {subjectOf(row)}
          {landed ? ` · ${landed.agency_name}` : ` · ${senderOf(row)}`}
        </div>
      </div>
      <div className="px-6 pt-3">
        <label className="flex h-[34px] items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1 text-13 text-ink-3">
          <SearchIcon size={15} width={2} stroke="var(--color-ink-3)" />
          <input
            aria-label="Find a request"
            placeholder={landed?.agency_name ?? 'Find a request'}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-ink-3"
          />
        </label>
      </div>
      <div className="max-h-[360px] overflow-y-auto px-6">
        {group('Named in the letter')}
        {landed
          ? line(
              landed.request_id,
              currentSubject ?? landed.tracking_number,
              landed.tracking_number,
              'on',
              named.length === 0,
            )
          : null}
        {named
          .filter((c) =>
            matches(`${c.subject} ${c.tracking_number} ${c.agency_reference_number ?? ''}`),
          )
          .map((c, index, all) =>
            line(
              c.request_id,
              c.subject,
              [c.tracking_number, c.agency_reference_number].filter(Boolean).join(' · '),
              'named',
              index === all.length - 1,
            ),
          )}
        {otherOpen.length > 0 ? (
          <>
            {group(`Other open requests${landed ? ` · ${landed.agency_name}` : ''}`)}
            {otherOpen.map((r, index) =>
              line(r.id, r.subject, r.tracking_number, 'other', index === otherOpen.length - 1),
            )}
          </>
        ) : null}
      </div>
      {error ? <p className="mx-6 mt-2 text-125 text-danger">{error}</p> : null}
      <div className="flex items-center justify-end gap-2.5 px-6 pt-3.5 pb-5">
        <span className="mr-auto text-12 text-muted">
          Creates an event on each · nothing is moved
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-pill px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy || chosen.length === 0}
          onClick={() => void attach()}
          className="rounded-pill bg-brand px-[18px] py-[9px] text-13 font-semibold text-white hover:bg-brand-dark disabled:opacity-55"
        >
          Attach to {chosen.length} more
        </button>
      </div>
    </Dialog>
  )
}
