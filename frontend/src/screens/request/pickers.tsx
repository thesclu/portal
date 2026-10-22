import { useState } from 'react'

import { $api } from '../../api/query'
import { Checklist, RaisedSelect, SearchLine } from '../../components/Fields'

/** 089: requests to tick, found by a search over the ones this person may see. */
export function RequestPicker({
  exclude,
  chosen,
  onChange,
  single = false,
}: {
  exclude: string
  chosen: string[]
  onChange: (ids: string[]) => void
  /** One request only (095 "Duplicate of"). */
  single?: boolean
}) {
  const [query, setQuery] = useState('')
  const q = query.trim()
  const found = $api.useQuery('get', '/api/records/requests', {
    params: { query: { q: q || undefined, limit: 3, sort: 'newest' } },
  })
  const rows = (found.data?.items ?? []).filter((row) => row.id !== exclude)
  return (
    <div className="flex flex-col gap-3.5">
      <SearchLine placeholder="Search requests" value={query} onChange={setQuery} />
      <Checklist
        rows={rows.map((row) => ({
          value: row.id,
          lead: row.tracking_number,
          label: `${row.subject} · ${row.agency_name}`,
        }))}
        chosen={chosen}
        onToggle={(id) =>
          onChange(
            single
              ? chosen.includes(id)
                ? []
                : [id]
              : chosen.includes(id)
                ? chosen.filter((c) => c !== id)
                : [...chosen, id],
          )
        }
        empty={found.isPending ? 'Searching…' : 'No request matches.'}
      />
    </div>
  )
}

/** 095 "Duplicate of": one request, from the newest the person may see. */
export function RequestSelect({
  exclude,
  value,
  onChange,
}: {
  exclude: string
  value: string
  onChange: (id: string) => void
}) {
  const found = $api.useQuery('get', '/api/records/requests', {
    params: { query: { limit: 50, sort: 'newest' } },
  })
  const rows = (found.data?.items ?? []).filter((row) => row.id !== exclude)
  return (
    <RaisedSelect compact required value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{found.isPending ? 'Loading…' : 'Choose…'}</option>
      {rows.map((row) => (
        <option key={row.id} value={row.id}>
          {row.tracking_number} · {row.subject}
        </option>
      ))}
    </RaisedSelect>
  )
}

const METHOD: Record<string, string> = { email: 'Email', portal: 'Portal', mail: 'Post' }

/** 096: agencies to tick; the request's own agency is shown as already having one. */
export function AgencyPicker({
  own,
  chosen,
  onChange,
}: {
  own: string
  chosen: string[]
  onChange: (ids: string[]) => void
}) {
  const [query, setQuery] = useState('')
  const agencies = $api.useQuery('get', '/api/records/agencies')
  const q = query.trim().toLowerCase()
  const rows = (agencies.data ?? [])
    .filter((agency) => !q || agency.name.toLowerCase().includes(q))
    .slice(0, 8)
  return (
    <div className="flex flex-col gap-3.5">
      <SearchLine placeholder="Search agencies" value={query} onChange={setQuery} />
      <Checklist
        rows={rows.map((agency) => ({
          value: agency.id,
          label: agency.name,
          meta:
            agency.id === own
              ? 'Already has one'
              : (METHOD[agency.filing_method ?? ''] ?? 'No filing method'),
          disabled: agency.id === own,
        }))}
        chosen={chosen}
        onToggle={(id) =>
          onChange(chosen.includes(id) ? chosen.filter((c) => c !== id) : [...chosen, id])
        }
        empty={agencies.isPending ? 'Loading…' : 'No agency matches.'}
      />
    </div>
  )
}
