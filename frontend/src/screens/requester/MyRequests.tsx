import { $api } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { ListCard, ListHead, ListRow } from '../../components/ListTable'
import { LoadFailed } from '../../components/States'
import { StatusText } from '../../components/ui'
import { statusWords } from '../../lib/status'

/** A suggestion's state, in the requester's words. */
export function suggestionState(status: string): { family: string; qualifier: string } {
  if (status === 'started') return { family: 'Suggested', qualifier: 'taken up' }
  if (status === 'declined') return { family: 'Suggested', qualifier: 'declined' }
  return { family: 'Suggested', qualifier: 'waiting for staff' }
}

const COLUMNS = '150px 1.2fr 1.5fr 1.2fr'

/**
 * My requests (189): what the SCLU has filed for this person and what they suggested, in one
 * list. A request opens its page (194, read only); a suggestion has no tracking number until
 * staff file one, and a declined one carries the reason staff gave. The API already scopes a
 * requester to their own requests and suggestions.
 */
export function MyRequests() {
  const requests = $api.useQuery('get', '/api/records/requests', {
    params: { query: { sort: 'newest', limit: 200 } },
  })
  const suggestions = $api.useQuery('get', '/api/core/suggestions')
  const rows = requests.data?.items ?? []
  const suggested = suggestions.data ?? []
  const failed = requests.isError ? requests : suggestions.isError ? suggestions : null

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="My requests" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        <ListCard aria-label="My requests">
          <ListHead columns={COLUMNS}>
            <span>Tracking no.</span>
            <span>Agency</span>
            <span>Subject</span>
            <span>Status</span>
          </ListHead>
          {requests.isPending || suggestions.isPending ? (
            Array.from({ length: 3 }, (_, i) => (
              <div
                key={i}
                aria-hidden
                style={{ gridTemplateColumns: COLUMNS, opacity: 1 - i * 0.2 }}
                className="grid items-center gap-x-3.5 px-9 py-[15px]"
              >
                {['80%', '70%', '85%', '60%'].map((width, j) => (
                  <span
                    key={j}
                    className="block h-[11px] rounded-4 bg-fill-skeleton"
                    style={{ width }}
                  />
                ))}
              </div>
            ))
          ) : failed ? (
            <LoadFailed
              what="your requests"
              error={failed.error}
              onRetry={() => void Promise.all([requests.refetch(), suggestions.refetch()])}
            />
          ) : rows.length === 0 && suggested.length === 0 ? (
            <div className="flex flex-col items-center gap-[9px] px-5 py-[44px]">
              <div className="font-display text-lg font-extrabold text-ink">No requests yet</div>
              <div className="max-w-[44ch] text-center text-125 leading-[1.6] text-muted">
                When a request is filed for you it appears here. You can suggest one.
              </div>
            </div>
          ) : (
            <>
              {rows.map((row, index) => {
                const words = statusWords(row.current_status)
                return (
                  <ListRow
                    key={row.id}
                    columns={COLUMNS}
                    index={index}
                    zebra={false}
                    to={`/requests/${row.id}`}
                  >
                    <span className="min-w-0 truncate">{row.tracking_number}</span>
                    <span className="min-w-0 truncate">{row.agency_name}</span>
                    <span className="min-w-0 truncate">{row.subject}</span>
                    <StatusText family={words.family} qualifier={words.qualifier} />
                  </ListRow>
                )
              })}
              {suggested.map((row, index) => {
                const state = suggestionState(row.status)
                return (
                  <ListRow key={row.id} columns={COLUMNS} index={rows.length + index} zebra={false}>
                    <span className="min-w-0 truncate">—</span>
                    <span className="min-w-0 truncate">{row.agency_hint || '—'}</span>
                    <span className="min-w-0 truncate" title={row.what}>
                      {row.what}
                    </span>
                    <span
                      className="min-w-0 truncate text-muted"
                      title={row.status === 'declined' ? row.decline_note : undefined}
                    >
                      {state.family} · {state.qualifier}
                      {row.status === 'declined' && row.decline_note
                        ? ` · ${row.decline_note}`
                        : ''}
                    </span>
                  </ListRow>
                )
              })}
            </>
          )}
        </ListCard>
      </div>
    </div>
  )
}
