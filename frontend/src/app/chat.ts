import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect, useSyncExternalStore } from 'react'

import { api, type Schemas } from '../api/client'
import { mergeMessages, notificationFor } from '../lib/chat'

type Message = Schemas['ChatMessageOut']
type Page = Schemas['ChatPageOut']
type Overview = Schemas['ChatOverviewOut']

/**
 * Team chat's live connection (P22): one websocket per tab, opened when the shell mounts, with
 * the HTTP route as its fallback.
 *
 * The socket (`/api/core/chat/socket`, `portal/core/chat/consumers.py`) pushes each new, edited
 * or deleted message in the person's conversations, already resolved for them (a request link a
 * reader may not see arrives as "a request"), and "your conversations changed". Each push is
 * folded into the query cache: the open conversation's page, and the panel's list (refetched).
 * While the socket is down it is retried with a growing wait, and meanwhile the client asks
 * `/api/core/chat/updates` every few seconds for what changed since its last answer.
 *
 * Browser notifications (owner, 2026-10-17): the Notification API while the portal is open, asked
 * for from a button (`askToNotify`), never on load; a direct message or a mention of the reader
 * that arrives while its conversation is not on screen shows the sender and the conversation,
 * never the words.
 */

// The panel asks with an empty init (`$api.useQuery('get', '/api/core/chat', {})`), which is part of
// its key: this must be the same key for `setQueryData` and an exact invalidation to reach it.
export const OVERVIEW_KEY = ['get', '/api/core/chat', {}] as const

export function messagesKey(channelId: string) {
  return [
    'get',
    '/api/core/chat/channels/{channel_id}/messages',
    { params: { path: { channel_id: channelId } } },
  ] as const
}

const POLL_EVERY = 5_000
const PING_EVERY = 30_000
const RETRY_WAITS = [1_000, 2_000, 5_000, 10_000, 15_000]
const NOTIFY_KEY = 'sclu.chat.notify'

// ---- a small store: is the socket up, which conversation is on screen, open requests -----------

type State = { connected: boolean; showing: string | null }
let state: State = { connected: false, showing: null }
const listeners = new Set<() => void>()

function set(next: Partial<State>) {
  state = { ...state, ...next }
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useChatConnected(): boolean {
  return useSyncExternalStore(subscribe, () => state.connected)
}

/** The conversation the panel has open and on screen, so its messages are not notified. */
export function setShowing(channelId: string | null): void {
  if (state.showing !== channelId) set({ showing: channelId })
}

const openers = new Set<(channelId: string) => void>()

/** Open the panel on a conversation (the bell, a browser notification, a `?chat=` link). */
export function openChat(channelId: string): void {
  for (const open of openers) open(channelId)
}

export function onOpenChat(open: (channelId: string) => void): () => void {
  openers.add(open)
  return () => openers.delete(open)
}

// ---- browser notifications ----------------------------------------------------------------------

export type NotifyState = 'unsupported' | 'default' | 'denied' | 'on' | 'off'

export function notifyState(): NotifyState {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  if (Notification.permission === 'default') return 'default'
  try {
    return localStorage.getItem(NOTIFY_KEY) === 'off' ? 'off' : 'on'
  } catch {
    return 'on'
  }
}

/** Asked from a button, never on load. Turning it off again is this browser's choice. */
export async function askToNotify(): Promise<NotifyState> {
  if (notifyState() === 'unsupported') return 'unsupported'
  try {
    localStorage.setItem(NOTIFY_KEY, 'on')
  } catch {
    /* a private window: the permission alone decides */
  }
  if (Notification.permission === 'default') await Notification.requestPermission()
  return notifyState()
}

export function stopNotifying(): void {
  try {
    localStorage.setItem(NOTIFY_KEY, 'off')
  } catch {
    /* nothing to remember it in */
  }
}

function notify(client: QueryClient, message: Message): void {
  if (notifyState() !== 'on' || message.mine || message.deleted) return
  const overview = client.getQueryData<Overview>(OVERVIEW_KEY)
  const channel = [...(overview?.channels ?? []), ...(overview?.direct ?? [])].find(
    (c) => c.id === message.channel_id,
  )
  const direct = channel?.kind === 'dm'
  if (!direct && !message.mentions_me) return
  if (state.showing === message.channel_id && document.visibilityState === 'visible') return
  const { title, body } = notificationFor(message.author.name, channel)
  try {
    const shown = new Notification(title, { body, tag: message.channel_id })
    shown.onclick = () => {
      window.focus()
      openChat(message.channel_id)
      shown.close()
    }
  } catch {
    /* some browsers only notify from a service worker; the panel still shows it */
  }
}

// ---- folding messages into the cache --------------------------------------------------------------

const seen = new Set<string>()

/** A message from the socket, the fallback, or our own send: into the open page, and the list. */
export function receive(client: QueryClient, message: Message, fresh: boolean): void {
  client.setQueryData<Page>(messagesKey(message.channel_id), (page) =>
    page ? { ...page, messages: mergeMessages(page.messages, [message]) } : page,
  )
  if (fresh && !seen.has(message.id)) {
    seen.add(message.id)
    notify(client, message)
  }
  void client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true })
}

function socketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/core/chat/socket`
}

/**
 * Holds the socket open for as long as the shell is mounted, and polls while it is not. Mounted
 * once, by the top bar's chat button.
 */
export function useChatLive(): void {
  const client = useQueryClient()

  useEffect(() => {
    let socket: WebSocket | null = null
    let stopped = false
    let attempt = 0
    let retry: number | undefined
    let ping: number | undefined
    let poll: number | undefined
    let since = new Date().toISOString()

    function startPolling() {
      if (poll !== undefined) return
      poll = window.setInterval(() => void catchUp(), POLL_EVERY)
    }

    function stopPolling() {
      if (poll !== undefined) window.clearInterval(poll)
      poll = undefined
    }

    async function catchUp() {
      // A little overlap with the last answer, so a message written as it was read is not lost;
      // the merge drops what is already held.
      const from = new Date(new Date(since).getTime() - 5_000).toISOString()
      const { data } = await api.GET('/api/core/chat/updates', {
        params: { query: { since: from } },
      })
      if (!data || stopped) return
      since = data.now
      for (const message of data.messages) {
        const known = seen.has(message.id)
        receive(client, message, !known && message.created_at >= from)
      }
      client.setQueryData<Overview>(OVERVIEW_KEY, (overview) =>
        overview ? { ...overview, unread: data.unread } : overview,
      )
    }

    function connect() {
      if (stopped) return
      try {
        socket = new WebSocket(socketUrl())
      } catch {
        scheduleRetry()
        return
      }
      socket.onopen = () => {
        attempt = 0
        set({ connected: true })
        stopPolling()
        // Whatever was missed while it was down.
        void catchUp()
        ping = window.setInterval(() => socket?.send(JSON.stringify({ type: 'ping' })), PING_EVERY)
      }
      socket.onmessage = (event) => {
        let note: { type?: string; event?: string; message?: Message }
        try {
          note = JSON.parse(String(event.data))
        } catch {
          return
        }
        if (note.type === 'message' && note.message) {
          receive(client, note.message, note.event === 'new')
        } else if (note.type === 'channels') {
          void client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true })
        }
      }
      socket.onclose = () => {
        if (ping !== undefined) window.clearInterval(ping)
        ping = undefined
        set({ connected: false })
        if (!stopped) {
          startPolling()
          scheduleRetry()
        }
      }
    }

    function scheduleRetry() {
      const wait = RETRY_WAITS[Math.min(attempt, RETRY_WAITS.length - 1)]
      attempt += 1
      retry = window.setTimeout(connect, wait)
    }

    connect()
    return () => {
      stopped = true
      window.clearTimeout(retry)
      if (ping !== undefined) window.clearInterval(ping)
      stopPolling()
      socket?.close()
      set({ connected: false })
    }
  }, [client])
}
