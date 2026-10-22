import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import { $api } from '../api/query'
import { destinationsFor, isBuilt, isRequester } from '../app/nav'
import { useMe } from '../app/session'
import { Dialog } from './Dialog'
import { SearchIcon } from './icons'
import { Kbd, cx } from './ui'

interface Hit {
  key: string
  label: string
  detail: string
  to: string
  section: 'Requests' | 'Agencies' | 'Actions' | 'Pages'
}

const SECTIONS: Hit['section'][] = ['Requests', 'Agencies', 'Actions', 'Pages']

/**
 * The quick-jump palette (design 059): 620 px, 18 px radius, 80 px from the top over the scrim;
 * the search row, then uppercase section labels and rows with the hover fill and the ↵ hint on
 * the active row. Requests by number, subject or reference; agencies by name; the actions; and
 * the destinations themselves. For staff the action searches all mail for the words (059).
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  // Mounted only while open, so each opening starts with an empty box and nothing selected.
  return open ? <Palette onClose={onClose} /> : null
}

function Palette({ onClose }: { onClose: () => void }) {
  const me = useMe()
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [active, setActive] = useState(0)
  const query = text.trim()
  const requester = isRequester(me)

  const requests = $api.useQuery(
    'get',
    '/api/records/requests',
    { params: { query: { q: query, limit: 6 } } },
    { enabled: query.length > 1 && isBuilt('docket') },
  )
  const agencies = $api.useQuery(
    'get',
    '/api/records/agencies',
    {},
    { enabled: query.length > 1 && isBuilt('agencies') && !requester, staleTime: 60_000 },
  )

  const hits = useMemo<Hit[]>(() => {
    const lowered = query.toLowerCase()
    const pages: Hit[] = destinationsFor(me)
      .filter((d) => d.label.toLowerCase().includes(lowered))
      .map((d) => ({
        key: `page:${d.key}`,
        label: d.label,
        detail: '',
        to: d.to,
        section: 'Pages',
      }))
    if (query.length < 2) return pages
    // 059's actions. ("New request to <agency>" is drawn too; the new-request flow cannot be
    // started for an agency yet, so it is not offered: HANDOFF, open questions.)
    const actions: Hit[] =
      requester || !isBuilt('inbox')
        ? []
        : [
            {
              key: 'mail',
              label: `Search all mail for “${query}”`,
              detail: '',
              to: `/inbox?bucket=all&q=${encodeURIComponent(query)}`,
              section: 'Actions',
            },
          ]
    const found: Hit[] = (requests.data?.items ?? []).map((r) => ({
      key: `request:${r.id}`,
      label: `${r.tracking_number} · ${r.subject}`,
      detail: r.agency_name,
      to: `/requests/${r.id}`,
      section: 'Requests',
    }))
    const named: Hit[] = (agencies.data ?? [])
      .filter((a) => a.name.toLowerCase().includes(lowered))
      .slice(0, 5)
      .map((a) => ({
        key: `agency:${a.id}`,
        label: a.name,
        detail: a.jurisdiction,
        to: `/agencies/${a.id}`,
        section: 'Agencies',
      }))
    return [...found, ...named, ...actions, ...pages]
  }, [query, requests.data, agencies.data, me, requester])

  function go(hit: Hit | undefined) {
    if (!hit) return
    onClose()
    void navigate(hit.to)
  }

  // Each row's place in the one keyboard order, section by section.
  const sections = SECTIONS.map((section) => ({
    section,
    rows: hits.filter((hit) => hit.section === section),
  })).filter((group) => group.rows.length > 0)
  const position = new Map(hits.map((hit, i) => [hit.key, i]))

  return (
    <Dialog title="Search" open onClose={onClose} bare width={620} top={80}>
      <div className="flex items-center gap-2.5 px-[18px] py-3.5">
        <SearchIcon size={16} width={2} stroke="var(--color-ink)" />
        <input
          autoFocus
          value={text}
          placeholder="Search requests, agencies, records"
          aria-label="Search"
          aria-controls="palette-results"
          className="min-w-0 flex-1 bg-transparent text-14 text-ink outline-none placeholder:text-muted"
          onChange={(event) => {
            setText(event.target.value)
            setActive(0)
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setActive((i) => Math.min(i + 1, hits.length - 1))
            } else if (event.key === 'ArrowUp') {
              event.preventDefault()
              setActive((i) => Math.max(i - 1, 0))
            } else if (event.key === 'Enter') {
              event.preventDefault()
              go(hits[active])
            }
          }}
        />
        <Kbd>esc</Kbd>
      </div>
      <div id="palette-results" role="listbox" aria-label="Results">
        {sections.map(({ section, rows }) => (
          <div key={section}>
            <div className="px-[18px] pt-2.5 pb-1 text-105 leading-none font-semibold tracking-label text-muted uppercase">
              {section}
            </div>
            {rows.map((hit) => {
              const at = position.get(hit.key) ?? 0
              const isActive = at === active
              return (
                <div
                  key={hit.key}
                  role="option"
                  aria-selected={isActive}
                  tabIndex={-1}
                  onMouseEnter={() => setActive(at)}
                  onClick={() => go(hit)}
                  className={cx(
                    'flex cursor-pointer gap-3 px-[18px] py-[9px] text-13',
                    isActive && 'bg-hover',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate text-ink">{hit.label}</span>
                  {hit.detail ? <span className="text-125 text-muted">{hit.detail}</span> : null}
                  {isActive ? <span className="text-11 text-muted">↵</span> : null}
                </div>
              )
            })}
          </div>
        ))}
        {hits.length === 0 ? (
          <p className="px-[18px] py-4 text-center text-13 text-muted">
            {requests.isFetching ? 'Searching…' : 'Nothing matches.'}
          </p>
        ) : null}
        <div className="h-2" />
      </div>
    </Dialog>
  )
}
