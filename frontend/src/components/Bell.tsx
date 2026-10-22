import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import type { Schemas } from '../api/client'
import { $api, messageOf } from '../api/query'
import { openChat } from '../app/chat'
import { chatLinkTarget } from '../lib/chat'
import { formatAgo } from '../lib/dates'
import { BellIcon } from './icons'
import { IconButton } from './IconButton'
import { cx } from './ui'

type Notification = Schemas['NotificationOut']

const KEY = ['get', '/api/core/notifications'] as const

/**
 * The bell in the top bar (P15b, design 006/107): a 7 px brand dot when anything is unread, and
 * the notifications panel it opens, 340 px wide, 60 px from the top and 120 px from the right as
 * 107 places it. Asks the API once a minute. Only ever drawn for a signed-in person.
 */
export function Bell() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const panelId = useId()
  const wrapper = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const notifications = $api.useQuery(
    'get',
    '/api/core/notifications',
    { params: { query: { limit: 30 } } },
    { refetchInterval: 60_000 },
  )
  const markRead = $api.useMutation('post', '/api/core/notifications/read', {
    onSuccess: () => client.invalidateQueries({ queryKey: KEY }),
  })

  // The panel closes on a click anywhere else, or on Escape.
  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (wrapper.current && !wrapper.current.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const unread = notifications.data?.unread ?? 0
  const items = notifications.data?.items ?? []

  function openOne(item: Notification) {
    setOpen(false)
    if (!item.read_at) markRead.mutate({ body: { ids: [item.id] } })
    // A mention in team chat opens its conversation in the chat panel, over the page (P22).
    const conversation = chatLinkTarget(item.link)
    if (conversation) openChat(conversation)
    else void navigate(item.link)
  }

  return (
    <div ref={wrapper} className="contents">
      <IconButton
        label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        open={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
      >
        <BellIcon size={16} stroke="var(--color-ink-2)" />
        {unread > 0 ? (
          <span
            aria-hidden
            className="absolute top-[5px] right-[5px] size-[7px] rounded-pill bg-brand shadow-[0_0_0_2px_var(--color-card-white)]"
          />
        ) : null}
      </IconButton>
      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label="Notifications"
          className="fixed top-[60px] right-[120px] z-30 w-[340px] overflow-hidden rounded-10 border border-line bg-card-white shadow-popover-2"
        >
          <div className="flex items-center px-4 py-[13px]">
            <span className="text-135 font-semibold text-ink">Notifications</span>
            {unread > 0 ? (
              <button
                type="button"
                disabled={markRead.isPending}
                onClick={() => markRead.mutate({ body: { ids: null } })}
                className="ml-auto text-12 text-brand hover:text-brand-dark"
              >
                Mark all read
              </button>
            ) : null}
          </div>
          {markRead.isError ? (
            <p role="alert" className="px-4 pb-2 text-12 text-danger">
              {messageOf(markRead.error)}
            </p>
          ) : null}
          <ul className="max-h-[420px] overflow-y-auto">
            {notifications.isPending ? (
              <Line>Loading…</Line>
            ) : notifications.isError ? (
              <Line>{messageOf(notifications.error, 'Couldn’t load notifications.')}</Line>
            ) : items.length === 0 ? (
              <Line>Nothing new.</Line>
            ) : (
              items.map((item) => (
                <li key={item.id} className="border-t border-line-row-3">
                  <button
                    type="button"
                    onClick={() => openOne(item)}
                    className="flex w-full gap-[11px] px-4 py-3 text-left hover:bg-page"
                  >
                    <span
                      aria-hidden
                      className={cx(
                        'mt-1.5 size-[7px] shrink-0 rounded-pill',
                        item.read_at ? 'bg-transparent' : 'bg-brand',
                      )}
                    />
                    <span className="min-w-0 flex-1 text-125 leading-[1.5] text-ink-2">
                      <span
                        className={cx('text-ink', item.read_at ? 'font-medium' : 'font-semibold')}
                      >
                        {item.text}
                      </span>
                      <br />
                      <span className="text-115 text-muted">{formatAgo(item.created_at)}</span>
                    </span>
                  </button>
                </li>
              ))
            )}
          </ul>
          <Link
            to="/settings/notifications"
            onClick={() => setOpen(false)}
            className="block border-t border-line-row-3 px-4 py-[11px] text-center text-12 text-brand hover:text-brand-dark"
          >
            See all
          </Link>
        </div>
      ) : null}
    </div>
  )
}

function Line({ children }: { children: React.ReactNode }) {
  return (
    <li className="border-t border-line-row-3 px-4 py-6 text-center text-125 text-muted">
      {children}
    </li>
  )
}
