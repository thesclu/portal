import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useSyncExternalStore } from 'react'
import { useLocation } from 'react-router'

import { api, type Schemas } from '../api/client'

type Presence = Schemas['PresenceOut']

/** Where the presence list lives in the query cache: the heartbeat's answer is the control's
 *  data, so the control never asks for it a second time. */
export const PRESENCE_KEY = ['get', '/api/core/presence'] as const

const HEARTBEAT_EVERY = 60_000

// The page's own name ("Inbox › Flagged"), set by PageHeader, read by the heartbeat. A module
// store rather than context so the Shell, which draws PageHeader's parent, can read what its
// child said without re-rendering every screen.
let currentLabel = ''
const listeners = new Set<() => void>()

export function setPageLabel(label: string): void {
  if (label === currentLabel) return
  currentLabel = label
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function usePageLabel(): string {
  return useSyncExternalStore(subscribe, () => currentLabel)
}

/**
 * Tells the API where this person is: on mount, once a minute, and whenever the route or the
 * page's name changes. The answer is the list of everyone online, kept under PRESENCE_KEY. Runs
 * for everyone signed in; only the control that shows the list is staff-only.
 */
export function usePresence(): void {
  const location = useLocation()
  const label = usePageLabel()
  const client = useQueryClient()
  const path = location.pathname + location.search

  useEffect(() => {
    let gone = false
    async function beat() {
      const { data } = await api.POST('/api/core/presence', { body: { path, label } })
      if (data && !gone) client.setQueryData(PRESENCE_KEY, data)
    }
    // A route change and the new page naming itself land a tick apart; one heartbeat covers both.
    const soon = window.setTimeout(() => void beat(), 50)
    const every = window.setInterval(() => void beat(), HEARTBEAT_EVERY)
    return () => {
      gone = true
      window.clearTimeout(soon)
      window.clearInterval(every)
    }
  }, [path, label, client])
}

/** The presence list as the last heartbeat left it. Never asks on its own: the heartbeat answers
 *  first, and asking too would be a second request for the same thing. */
export function usePresenceList() {
  return useQuery<Presence>({
    queryKey: PRESENCE_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/core/presence')
      if (error) throw error
      return data
    },
    enabled: false,
    staleTime: Infinity,
  })
}
