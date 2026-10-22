import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router'

import type { Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import {
  askToNotify,
  notifyState,
  onOpenChat,
  OVERVIEW_KEY,
  stopNotifying,
  useChatConnected,
  useChatLive,
  type NotifyState,
} from '../../app/chat'
import { isRequester, useMe } from '../../app/session'
import { ChatIcon, ChevronRight, PencilIcon } from '../../components/icons'
import { IconButton } from '../../components/IconButton'
import { Menu, type MenuItem } from '../../components/Menu'
import { cx } from '../../components/ui'
import { listTime, onlineSummary, seenLine } from '../../lib/chat'
import { Conversation } from './Conversation'
import { ChatAvatar, PanelButton, SectionLabel, ViewHeader } from './parts'

type Channel = Schemas['ChatChannelOut']
type Seen = Schemas['ChatSeenOut']
type Person = Schemas['ChatPersonOut']

type View =
  | { kind: 'list' }
  | { kind: 'people' }
  | { kind: 'channel'; id: string }
  | { kind: 'members'; id: string }
  | { kind: 'new-channel' }
  | { kind: 'new-message' }
  | { kind: 'browse' }

/**
 * Team chat (P22; DECISIONS 32): the top bar's chat bubble and the 400 px panel under it (v6
 * 018–022, ARCHITECTURE "Top-bar panels"). The list (018): "Chat" with the pencil (new message,
 * new channel, browse channels, browser notifications), the who's-online strip, the channels the
 * person is in and their direct messages. The strip opens who's online (021: online, then away,
 * each with where they are; "Message" on the row under the pointer). A conversation opens in
 * place (019, 020), 560 px tall. With nothing yet, "No conversations yet" and New message (022).
 * The socket and its polling fallback are held for as long as the shell is mounted
 * (`app/chat.ts`), whether the panel is open or not, so the bubble's dot and browser
 * notifications work with it closed.
 */
export function ChatPanel() {
  const me = useMe()
  const client = useQueryClient()
  const panelId = useId()
  const wrapper = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<View>({ kind: 'list' })
  const location = useLocation()
  const navigate = useNavigate()
  const connected = useChatConnected()
  useChatLive()

  const overview = $api.useQuery(
    'get',
    '/api/core/chat',
    {},
    // The socket keeps it current; without one, it is read again now and then.
    { refetchInterval: connected ? false : 30_000 },
  )
  const requester = isRequester(me)
  const online = $api.useQuery(
    'get',
    '/api/core/chat/online',
    {},
    { enabled: open && !requester, refetchInterval: open ? 30_000 : false },
  )

  // A notification, the bell or a `?chat=<id>` link opens the panel on a conversation.
  useEffect(
    () =>
      onOpenChat((id) => {
        setOpen(true)
        setView({ kind: 'channel', id })
      }),
    [],
  )
  // The link's conversation is opened while rendering (React's way to follow a changed input
  // without an effect that sets state), then the address loses its `chat=`.
  const linked = new URLSearchParams(location.search).get('chat')
  const [followed, setFollowed] = useState<string | null>(null)
  if (linked !== followed) {
    setFollowed(linked)
    if (linked) {
      setOpen(true)
      setView({ kind: 'channel', id: linked })
    }
  }
  useEffect(() => {
    if (!linked) return
    const params = new URLSearchParams(location.search)
    params.delete('chat')
    const rest = params.toString()
    void navigate(`${location.pathname}${rest ? `?${rest}` : ''}`, { replace: true })
  }, [linked, location.search, location.pathname, navigate])

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

  const unread = overview.data?.unread ?? 0
  const seen = online.data?.people ?? []
  // A conversation and the lists in place of one are 560 px (019); the short form is its own height.
  const tall = view.kind !== 'list' && view.kind !== 'people' && view.kind !== 'new-channel'
  const openChannel = (id: string) => setView({ kind: 'channel', id })
  const direct = $api.useMutation('post', '/api/core/chat/direct', {
    onSuccess: (detail) => {
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true })
      openChannel(detail.channel.id)
    },
  })
  const message = (userId: string) => direct.mutate({ body: { user_ids: [userId] } })

  return (
    <div ref={wrapper} className="contents">
      <IconButton
        label={unread > 0 ? `Team chat, ${unread} unread` : 'Team chat'}
        open={open}
        aria-controls={panelId}
        onClick={() => {
          setOpen((was) => !was)
          if (!open && view.kind !== 'channel') setView({ kind: 'list' })
        }}
        className={open ? 'bg-fill-subtle-3 hover:bg-fill-subtle-3' : undefined}
      >
        <ChatIcon size={16} stroke={open ? 'var(--color-ink)' : 'var(--color-ink-2)'} />
        {unread > 0 && !open ? (
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
          aria-label="Team chat"
          className={cx(
            'fixed top-[52px] right-[154px] z-20 flex w-[402px] flex-col overflow-hidden rounded-14 border border-line bg-card-white leading-[normal] shadow-panel',
            tall && 'h-[562px]',
          )}
        >
          {view.kind === 'channel' ? (
            <Conversation
              key={view.id}
              channelId={view.id}
              seen={seen}
              onBack={() => setView({ kind: 'list' })}
              onMembers={() => setView({ kind: 'members', id: view.id })}
              onLeft={() => setView({ kind: 'list' })}
            />
          ) : view.kind === 'members' ? (
            <Members
              channelId={view.id}
              seen={seen}
              onBack={() => openChannel(view.id)}
              onMessage={message}
            />
          ) : view.kind === 'new-channel' ? (
            <NewChannel onBack={() => setView({ kind: 'list' })} onMade={openChannel} />
          ) : view.kind === 'new-message' ? (
            <NewMessage
              onBack={() => setView({ kind: 'list' })}
              onPick={message}
              busy={direct.isPending}
              error={direct.isError ? messageOf(direct.error) : null}
            />
          ) : view.kind === 'browse' ? (
            <Browse onBack={() => setView({ kind: 'list' })} onOpen={openChannel} />
          ) : (
            <Home
              people={view.kind === 'people'}
              onPeople={(show) => setView({ kind: show ? 'people' : 'list' })}
              channels={overview.data?.channels ?? []}
              direct={overview.data?.direct ?? []}
              loading={overview.isPending}
              error={overview.isError ? messageOf(overview.error) : null}
              seen={seen}
              showSeen={!requester}
              onOpen={openChannel}
              onMessage={message}
              onNewChannel={() => setView({ kind: 'new-channel' })}
              onNewMessage={() => setView({ kind: 'new-message' })}
              onBrowse={() => setView({ kind: 'browse' })}
            />
          )}
        </div>
      ) : null}
    </div>
  )
}

function Home({
  people,
  onPeople,
  channels,
  direct,
  loading,
  error,
  seen,
  showSeen,
  onOpen,
  onMessage,
  onNewChannel,
  onNewMessage,
  onBrowse,
}: {
  people: boolean
  onPeople: (show: boolean) => void
  channels: Channel[]
  direct: Channel[]
  loading: boolean
  error: string | null
  seen: Seen[]
  showSeen: boolean
  onOpen: (id: string) => void
  onMessage: (userId: string) => void
  onNewChannel: () => void
  onNewMessage: () => void
  onBrowse: () => void
}) {
  const me = useMe()
  const [notify, setNotify] = useState<NotifyState>(notifyState)
  const here = seen.filter((p) => p.state === 'online')
  const away = seen.filter((p) => p.state === 'away')
  const empty = !loading && !error && channels.length === 0 && direct.length === 0
  const items: MenuItem[] = [
    { label: 'New message', onPick: onNewMessage },
    { label: 'New channel', onPick: onNewChannel },
    { label: 'Browse channels', onPick: onBrowse },
  ]
  if (notify === 'default' || notify === 'off') {
    items.push({
      label: 'Notify me in this browser',
      separator: true,
      onPick: () => void askToNotify().then(setNotify),
    })
  } else if (notify === 'on') {
    items.push({
      label: 'Stop notifying this browser',
      separator: true,
      onPick: () => {
        stopNotifying()
        setNotify(notifyState())
      },
    })
  } else if (notify === 'denied') {
    items.push({
      label: 'Notifications are blocked in this browser',
      separator: true,
      disabled: true,
      onPick: () => {},
    })
  }
  const stateOf = (userId: string) => seen.find((p) => p.user_id === userId)?.state ?? null

  return (
    <>
      <div className="flex items-center gap-2.5 pt-4 pr-3 pb-2.5 pl-[18px]">
        <span className="font-display text-16 font-extrabold tracking-dialog text-ink">Chat</span>
        <span className="ml-auto flex gap-0.5">
          <Menu label="New conversation" items={items} width={220}>
            <span className="inline-flex size-[30px] items-center justify-center rounded-8 hover:bg-hover">
              <PencilIcon size={16} width={1.9} stroke="var(--color-ink-3)" />
            </span>
          </Menu>
        </span>
      </div>
      {!showSeen ? null : seen.length === 0 ? (
        <div className="px-[18px] pt-0.5 pb-2 text-12 text-muted">No one else online</div>
      ) : people ? (
        <button
          type="button"
          aria-expanded
          onClick={() => onPeople(false)}
          className="flex items-center gap-2 px-[18px] pt-0.5 pb-1 text-left"
        >
          <span className="text-12 font-medium text-ink">
            {onlineSummary(here.length, away.length)}
          </span>
          <span className="inline-flex rotate-90">
            <ChevronRight size={13} width={1.9} stroke="var(--color-muted)" />
          </span>
        </button>
      ) : (
        <button
          type="button"
          aria-expanded={false}
          onClick={() => onPeople(true)}
          className="flex items-center gap-2 px-[18px] pt-0.5 pb-2 text-left"
        >
          {[...here, ...away].slice(0, 3).map((person) => (
            <ChatAvatar
              key={person.user_id}
              id={person.user_id}
              initials={person.initials}
              size={24}
              state={person.state}
            />
          ))}
          <span className="ml-1 text-12 text-ink-2">{onlineSummary(here.length, away.length)}</span>
          <ChevronRight size={13} width={1.9} stroke="var(--color-muted-2)" />
        </button>
      )}
      {people ? (
        <>
          {here.length > 0 ? <SectionLabel>Online</SectionLabel> : null}
          {here.map((person) => (
            <SeenRow key={person.user_id} person={person} onMessage={onMessage} />
          ))}
          {away.length > 0 ? <SectionLabel>Away</SectionLabel> : null}
          {away.map((person) => (
            <SeenRow key={person.user_id} person={person} onMessage={onMessage} />
          ))}
          <div className="h-2.5" />
        </>
      ) : error ? (
        <p role="alert" className="px-[18px] pt-2 pb-5 text-12 text-danger">
          {error}
        </p>
      ) : loading ? (
        <p className="px-[18px] pt-2 pb-5 text-12 text-muted">Loading…</p>
      ) : empty ? (
        <div className="flex flex-col items-center gap-3 px-[18px] pt-10 pb-11">
          <span className="text-14 font-semibold text-ink">No conversations yet</span>
          <PanelButton onClick={onNewMessage}>New message</PanelButton>
        </div>
      ) : (
        <div className="max-h-[640px] overflow-y-auto">
          {channels.length > 0 ? <SectionLabel>Channels</SectionLabel> : null}
          {channels.map((channel) => (
            <button
              key={channel.id}
              type="button"
              onClick={() => onOpen(channel.id)}
              className="flex w-full items-center gap-2.5 px-[18px] py-[7px] text-left text-13 hover:bg-row-alt"
            >
              <span className="w-[18px] text-center text-14 text-muted">#</span>
              <span
                className={cx(
                  'min-w-0 flex-1 truncate text-ink',
                  channel.unread > 0 ? 'font-semibold' : 'font-normal',
                )}
              >
                {channel.name}
              </span>
              {channel.unread > 0 ? (
                <span className="rounded-pill bg-blue-tint-6 px-[7px] py-px text-11 font-semibold text-brand">
                  {channel.unread}
                </span>
              ) : null}
            </button>
          ))}
          {direct.length > 0 ? <SectionLabel>Direct messages</SectionLabel> : null}
          {direct.map((channel) => {
            const other = channel.people[0]
            const bold = channel.unread > 0
            return (
              <button
                key={channel.id}
                type="button"
                onClick={() => onOpen(channel.id)}
                className="flex w-full items-center gap-2.5 px-[18px] py-[7px] text-left hover:bg-row-alt"
              >
                {other ? (
                  <ChatAvatar
                    id={other.id}
                    initials={other.initials}
                    state={channel.people.length === 1 ? stateOf(other.id) : null}
                  />
                ) : (
                  <ChatAvatar id={me.id} initials="—" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span
                      className={cx(
                        'min-w-0 truncate text-13 text-ink',
                        bold ? 'font-semibold' : 'font-medium',
                      )}
                    >
                      {channel.people.map((p) => p.name).join(', ') || 'Just you'}
                    </span>
                    {channel.last_message_at ? (
                      <span className="ml-auto text-11 whitespace-nowrap text-muted">
                        {listTime(channel.last_message_at, me.timezone)}
                      </span>
                    ) : null}
                  </div>
                  {channel.preview ? (
                    <div className={cx('mt-px truncate text-12', bold ? 'text-ink' : 'text-muted')}>
                      {channel.preview}
                    </div>
                  ) : null}
                </div>
              </button>
            )
          })}
          <div className="h-2.5" />
        </div>
      )}
    </>
  )
}

/** One person in who's online (021): where they are (the destination only, unless the reader
 *  is an administrator), and "Message" on the row under the pointer. */
function SeenRow({ person, onMessage }: { person: Seen; onMessage: (userId: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onMessage(person.user_id)}
      className="group flex w-full items-center gap-2.5 px-[18px] py-2 text-left hover:bg-row-alt focus-visible:bg-row-alt"
    >
      <ChatAvatar id={person.user_id} initials={person.initials} state={person.state} />
      <div className="min-w-0 flex-1">
        <div className="text-13 font-medium text-ink">{person.name}</div>
        <div className="truncate text-115 text-muted">
          {person.state === 'online' && person.request_label ? (
            <>
              <span className="underline decoration-line-control underline-offset-[3px]">
                {person.request_label}
              </span>
              {person.where.slice(person.request_label.length)}
            </>
          ) : (
            seenLine(person)
          )}
        </div>
      </div>
      <span className="hidden text-12 font-medium text-brand group-hover:inline group-focus-visible:inline">
        Message
      </span>
    </button>
  )
}

function Members({
  channelId,
  seen,
  onBack,
  onMessage,
}: {
  channelId: string
  seen: Seen[]
  onBack: () => void
  onMessage: (userId: string) => void
}) {
  const me = useMe()
  const client = useQueryClient()
  const [adding, setAdding] = useState(false)
  const detail = $api.useQuery('get', '/api/core/chat/channels/{channel_id}', {
    params: { path: { channel_id: channelId } },
  })
  const add = $api.useMutation('post', '/api/core/chat/channels/{channel_id}/members', {
    onSuccess: (saved) => {
      client.setQueryData(
        [
          'get',
          '/api/core/chat/channels/{channel_id}',
          { params: { path: { channel_id: channelId } } },
        ],
        saved,
      )
      setAdding(false)
    },
  })
  const channel = detail.data?.channel
  const members = detail.data?.members ?? []
  const stateOf = (userId: string) => seen.find((p) => p.user_id === userId)?.state ?? null
  const canAdd = channel?.kind === 'channel' && channel.is_member && !channel.archived

  if (adding) {
    return (
      <PeoplePicker
        title="Add people"
        exclude={members.map((m) => m.id)}
        onBack={() => setAdding(false)}
        onPick={(userId) =>
          add.mutate({ params: { path: { channel_id: channelId } }, body: { user_ids: [userId] } })
        }
        busy={add.isPending}
        error={add.isError ? messageOf(add.error) : null}
      />
    )
  }
  return (
    <>
      <ViewHeader onBack={onBack}>
        <span className="text-14 font-semibold text-ink">
          {channel?.kind === 'dm' ? 'People' : `# ${channel?.name ?? ''}`}
        </span>
        <span className="text-12 text-muted">{members.length || ''}</span>
      </ViewHeader>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {members.map((person) => (
          <button
            key={person.id}
            type="button"
            disabled={person.id === me.id}
            onClick={() => onMessage(person.id)}
            className="group flex w-full items-center gap-2.5 px-[18px] py-2 text-left enabled:hover:bg-row-alt"
          >
            <ChatAvatar id={person.id} initials={person.initials} state={stateOf(person.id)} />
            <span className="min-w-0 flex-1 truncate text-13 font-medium text-ink">
              {person.name}
              {person.id === me.id ? <span className="font-normal text-muted"> (you)</span> : null}
            </span>
            {person.id !== me.id ? (
              <span className="hidden text-12 font-medium text-brand group-hover:inline">
                Message
              </span>
            ) : null}
          </button>
        ))}
        {canAdd ? (
          <div className="px-[18px] py-3">
            <PanelButton onClick={() => setAdding(true)}>Add people</PanelButton>
          </div>
        ) : null}
      </div>
    </>
  )
}

/** Everyone who can be messaged, searched by name; the reader is left out. */
function PeoplePicker({
  title,
  exclude = [],
  onBack,
  onPick,
  busy,
  error,
}: {
  title: string
  exclude?: string[]
  onBack: () => void
  onPick: (userId: string) => void
  busy: boolean
  error: string | null
}) {
  const me = useMe()
  const [query, setQuery] = useState('')
  const people = $api.useQuery('get', '/api/core/chat/people', {}, { staleTime: 300_000 })
  const shown = (people.data ?? [])
    .filter((p: Person) => p.id !== me.id && !exclude.includes(p.id))
    .filter((p: Person) => p.name.toLowerCase().includes(query.trim().toLowerCase()))
  return (
    <>
      <ViewHeader onBack={onBack}>
        <span className="text-14 font-semibold text-ink">{title}</span>
      </ViewHeader>
      <div className="shrink-0 px-[18px] pt-3 pb-2">
        <input
          autoFocus
          aria-label="Find a person"
          placeholder="Find a person"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-full rounded-9 border border-line px-3 py-2 text-13 text-ink outline-none placeholder:text-muted-2 focus:border-brand"
        />
        {error ? (
          <p role="alert" className="mt-1.5 text-12 text-danger">
            {error}
          </p>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        {people.isPending ? (
          <p className="px-[18px] py-3 text-12 text-muted">Loading…</p>
        ) : shown.length === 0 ? (
          <p className="px-[18px] py-3 text-12 text-muted">Nobody by that name.</p>
        ) : (
          shown.map((person: Person) => (
            <button
              key={person.id}
              type="button"
              disabled={busy}
              onClick={() => onPick(person.id)}
              className="flex w-full items-center gap-2.5 px-[18px] py-2 text-left hover:bg-row-alt"
            >
              <ChatAvatar id={person.id} initials={person.initials} />
              <span className="min-w-0 flex-1 truncate text-13 font-medium text-ink">
                {person.name}
              </span>
            </button>
          ))
        )}
      </div>
    </>
  )
}

function NewMessage({
  onBack,
  onPick,
  busy,
  error,
}: {
  onBack: () => void
  onPick: (userId: string) => void
  busy: boolean
  error: string | null
}) {
  return (
    <PeoplePicker title="New message" onBack={onBack} onPick={onPick} busy={busy} error={error} />
  )
}

/** A new channel (not drawn): its name and whether it is private. Anyone may make one. */
function NewChannel({ onBack, onMade }: { onBack: () => void; onMade: (id: string) => void }) {
  const client = useQueryClient()
  const [name, setName] = useState('')
  const [secret, setSecret] = useState(false)
  const make = $api.useMutation('post', '/api/core/chat/channels', {
    onSuccess: (channel) => {
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true })
      onMade(channel.id)
    },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) make.mutate({ body: { name, private: secret } })
  }
  return (
    <>
      <ViewHeader onBack={onBack}>
        <span className="text-14 font-semibold text-ink">New channel</span>
      </ViewHeader>
      <form onSubmit={submit} className="flex flex-col gap-3 px-[18px] pt-4 pb-5">
        <label className="flex flex-col gap-1.5">
          <span className="text-12 font-medium text-ink-2">Name</span>
          <span className="flex items-center gap-1.5 rounded-9 border border-line px-3 py-2 focus-within:border-brand">
            <span className="text-14 text-muted">#</span>
            <input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="records-team"
              className="min-w-0 flex-1 text-13 text-ink outline-none placeholder:text-muted-2"
            />
          </span>
          <span className="text-115 text-muted">Lower case, with - or _ between words.</span>
        </label>
        <label className="flex items-start gap-2.5">
          <input
            type="checkbox"
            checked={secret}
            onChange={(event) => setSecret(event.target.checked)}
            className="mt-0.5 accent-brand"
          />
          <span className="text-13 text-ink">
            Private
            <span className="block text-115 text-muted">
              Only the people you add can find it and read it.
            </span>
          </span>
        </label>
        {make.isError ? (
          <p role="alert" className="text-12 text-danger">
            {messageOf(make.error)}
          </p>
        ) : null}
        <div>
          <PanelButton type="submit" disabled={make.isPending || !name.trim()}>
            Create channel
          </PanelButton>
        </div>
      </form>
    </>
  )
}

/** Public channels the person is not in yet. Opening one shows it, with Join under it. */
function Browse({ onBack, onOpen }: { onBack: () => void; onOpen: (id: string) => void }) {
  const list = $api.useQuery('get', '/api/core/chat/browse')
  return (
    <>
      <ViewHeader onBack={onBack}>
        <span className="text-14 font-semibold text-ink">Browse channels</span>
      </ViewHeader>
      <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
        {list.isPending ? (
          <p className="px-[18px] py-3 text-12 text-muted">Loading…</p>
        ) : (list.data ?? []).length === 0 ? (
          <p className="px-[18px] py-3 text-12 text-muted">
            You are in every public channel there is.
          </p>
        ) : (
          (list.data ?? []).map((channel) => (
            <button
              key={channel.id}
              type="button"
              onClick={() => onOpen(channel.id)}
              className="flex w-full items-center gap-2.5 px-[18px] py-[7px] text-left text-13 hover:bg-row-alt"
            >
              <span className="w-[18px] text-center text-14 text-muted">#</span>
              <span className="min-w-0 flex-1 truncate text-ink">{channel.name}</span>
              <span className="text-12 text-muted">{channel.member_count}</span>
            </button>
          ))
        )}
      </div>
    </>
  )
}
