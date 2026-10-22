import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { useMe, isFullStaff } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { ListCard } from '../../components/ListTable'
import { LoadFailed } from '../../components/States'
import { Avatar, Callout, cx } from '../../components/ui'
import { formatSubmitted } from '../../lib/dates'
import { initialsOf } from '../../lib/presence'

type Suggestion = Schemas['SuggestionOut']

/** The requesters' open suggestions: staff's to take up or decline (P20h: admins, staff and staff
 *  attorneys). Nobody else asks. */
export function usePendingSuggestions() {
  const me = useMe()
  return $api.useQuery(
    'get',
    '/api/core/suggestions',
    { params: { query: { pending: true } } },
    { enabled: isFullStaff(me) },
  )
}

const firstName = (name: string) => name.trim().split(/\s+/)[0] || name
const short = (text: string) => (text.length > 60 ? `${text.slice(0, 57).trimEnd()}…` : text)

/** Declining a suggestion (193): the requester reads what is written. */
function declineSpec(row: Suggestion): ActionSpec {
  const first = firstName(row.requester_name)
  return {
    key: `decline-suggestion-${row.id}`,
    title: `Decline ${first}’s suggestion`,
    subtitle: [short(row.what), row.agency_hint].filter(Boolean).join(' · '),
    width: 460,
    top: 90,
    submit: 'Decline',
    fields: [
      {
        name: 'note',
        label: `Tell ${first} why`,
        type: 'textarea',
        required: true,
        minHeight: 52,
        autoFocus: true,
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/core/suggestions/{suggestion_id}/decline', {
          params: { path: { suggestion_id: row.id } },
          body: { note: String(values.note ?? '').trim() },
        }),
      )
    },
  }
}

/**
 * Tasks → Suggestions (192): what requesters asked staff to look into, newest last. "Start a
 * request" makes a letter from one and opens it; Decline asks for the reason the requester will
 * read (193).
 */
export function Suggestions() {
  const me = useMe()
  const client = useQueryClient()
  const navigate = useNavigate()
  const suggestions = usePendingSuggestions()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [refused, setRefused] = useState<string | null>(null)

  const refresh = async () => {
    setSpec(null)
    await client.invalidateQueries({ queryKey: ['get', '/api/core/suggestions'] })
  }
  async function start(row: Suggestion) {
    setBusy(row.id)
    setRefused(null)
    try {
      const started = unwrap(
        await api.POST('/api/core/suggestions/{suggestion_id}/start', {
          params: { path: { suggestion_id: row.id } },
        }),
      )
      await refresh()
      if (started.draft_id) navigate(`/drafts/${started.draft_id}`)
    } catch (caught) {
      setRefused(caught instanceof Error ? caught.message : 'The server refused this.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      {refused ? (
        <Callout tone="danger" role="alert">
          {refused}
        </Callout>
      ) : null}
      <ListCard aria-label="Suggestions">
        {suggestions.isPending ? (
          <div aria-hidden className="flex items-center gap-3.5 px-5 py-3.5">
            <span className="size-[26px] rounded-pill bg-fill-skeleton" />
            <span className="block h-[11px] w-1/2 rounded-4 bg-fill-skeleton" />
          </div>
        ) : suggestions.isError ? (
          <LoadFailed
            what="the suggestions"
            error={suggestions.error}
            onRetry={() => void suggestions.refetch()}
          />
        ) : suggestions.data.length === 0 ? (
          <div className="flex flex-col items-center gap-[9px] px-5 py-[44px] leading-[normal]">
            <div className="font-display text-lg font-extrabold text-ink">
              No suggestions waiting
            </div>
            <div className="text-center text-125 leading-[1.6] text-muted">
              Requesters’ suggestions arrive here.
            </div>
          </div>
        ) : (
          suggestions.data.map((row, index) => (
            <div
              key={row.id}
              className={cx(
                'flex items-center gap-3.5 px-5 py-3.5',
                index > 0 && 'border-t border-line-row',
              )}
            >
              <Avatar initials={initialsOf(row.requester_name)} size={26} />
              <div className="min-w-0 flex-1" title={row.why || undefined}>
                <div className="text-13 text-ink">{row.what}</div>
                <div className="mt-0.5 text-12 text-muted">
                  {[
                    row.requester_name,
                    'Requester',
                    row.agency_hint || 'no agency named',
                    formatSubmitted(row.created_at, me.timezone).toLowerCase(),
                  ].join(' · ')}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSpec(declineSpec(row))}
                className="box-border inline-flex h-8 items-center gap-1.5 rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
              >
                Decline
              </button>
              <button
                type="button"
                disabled={busy === row.id}
                aria-busy={busy === row.id || undefined}
                onClick={() => void start(row)}
                className="box-border inline-flex h-8 items-center gap-1.5 rounded-pill bg-brand px-3.5 text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark disabled:opacity-55"
              >
                Start a request
              </button>
            </div>
          ))
        )}
      </ListCard>
      <ActionDialog spec={spec} onClose={() => setSpec(null)} onDone={() => void refresh()} />
    </>
  )
}
