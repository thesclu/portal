import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { messagesKey, OVERVIEW_KEY, receive, setShowing } from '../../app/chat'
import { useMe } from '../../app/session'
import { CloseIcon, PlusIcon, SendArrowIcon } from '../../components/icons'
import { Menu, type MenuItem } from '../../components/Menu'
import { cx } from '../../components/ui'
import {
  byDay,
  clockTime,
  conversationName,
  insertMention,
  matchPeople,
  memberCount,
  mentionQuery,
  placeholderFor,
  viewingLine,
} from '../../lib/chat'
import { ChatAvatar, FileChip, MessageParts, MoreDots, PanelButton, ViewHeader } from './parts'

type Message = Schemas['ChatMessageOut']
type Channel = Schemas['ChatChannelOut']
type Person = Schemas['ChatPersonOut']
type Seen = Schemas['ChatSeenOut']
type Page = Schemas['ChatPageOut']
type File = Schemas['ChatFileOut']

/**
 * A conversation open in the panel (019 a channel, 020 a direct message): the header (back, the
 * name and its members, or the person with where they are, and ⋯), the messages oldest first
 * under their day lines, newest at the foot, and the composer (attach, @mention, send). An edit
 * reads "edited" after the time and a deletion "<name> deleted this message" in place of the
 * words, in the panel's own style (neither is drawn). Someone reading a public channel they have
 * not joined gets "Join" where the composer would be; an archived channel says so instead.
 */
export function Conversation({
  channelId,
  seen,
  onBack,
  onMembers,
  onLeft,
}: {
  channelId: string
  seen: Seen[]
  onBack: () => void
  onMembers: () => void
  onLeft: () => void
}) {
  const me = useMe()
  const client = useQueryClient()
  const page = $api.useQuery('get', '/api/core/chat/channels/{channel_id}/messages', {
    params: { path: { channel_id: channelId } },
  })
  const detail = $api.useQuery('get', '/api/core/chat/channels/{channel_id}', {
    params: { path: { channel_id: channelId } },
  })
  const channel = page.data?.channel ?? detail.data?.channel
  const messages = useMemo(() => page.data?.messages ?? [], [page.data])
  const last = messages[messages.length - 1]
  const scroller = useRef<HTMLDivElement>(null)
  const [loadingEarlier, setLoadingEarlier] = useState(false)

  useEffect(() => {
    setShowing(channelId)
    return () => setShowing(null)
  }, [channelId])

  // Read to the newest message whenever it changes while the conversation is open.
  const isMember = channel?.is_member ?? false
  useEffect(() => {
    if (!isMember || !last) return
    void api
      .POST('/api/core/chat/channels/{channel_id}/read', {
        params: { path: { channel_id: channelId } },
      })
      .then(() => client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true }))
  }, [channelId, isMember, last?.id, last?.changed_at, client, last])

  // The newest message stays in view, as a chat's does.
  useLayoutEffect(() => {
    const box = scroller.current
    if (box) box.scrollTop = box.scrollHeight
  }, [last?.id])

  async function loadEarlier() {
    const first = messages[0]
    if (!first) return
    setLoadingEarlier(true)
    const { data } = await api.GET('/api/core/chat/channels/{channel_id}/messages', {
      params: { path: { channel_id: channelId }, query: { before: first.created_at } },
    })
    setLoadingEarlier(false)
    if (!data) return
    client.setQueryData<Page>(messagesKey(channelId), (held) =>
      held ? { ...held, more: data.more, messages: [...data.messages, ...held.messages] } : held,
    )
  }

  const join = $api.useMutation('post', '/api/core/chat/channels/{channel_id}/join', {
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: messagesKey(channelId) })
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true })
    },
  })
  const leave = $api.useMutation('post', '/api/core/chat/channels/{channel_id}/leave', {
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true })
      onLeft()
    },
  })
  const archive = $api.useMutation('post', '/api/core/chat/channels/{channel_id}/archive', {
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: messagesKey(channelId) })
      void client.invalidateQueries({ queryKey: OVERVIEW_KEY, exact: true })
    },
  })
  const path = { params: { path: { channel_id: channelId } } }

  if (page.isError) {
    return (
      <>
        <ViewHeader onBack={onBack}>
          <span className="text-14 font-semibold text-ink">Conversation</span>
        </ViewHeader>
        <p className="px-[18px] py-6 text-13 text-muted">
          {messageOf(page.error, 'This conversation could not be opened.')}
        </p>
      </>
    )
  }

  const members = detail.data?.members ?? []
  const items: MenuItem[] = [{ label: 'Members', onPick: onMembers }]
  if (channel?.kind === 'channel' && channel.is_member && !channel.archived) {
    items.push({ label: 'Leave channel', onPick: () => leave.mutate(path) })
  }
  if (channel?.can_archive) {
    items.push({ label: 'Archive channel', danger: true, onPick: () => archive.mutate(path) })
  }
  const menu = (
    <Menu label="Conversation options" items={items} width={180}>
      <span className="inline-flex size-[30px] items-center justify-center rounded-8 hover:bg-hover">
        <MoreDots />
      </span>
    </Menu>
  )

  return (
    <>
      <ViewHeader onBack={onBack} menu={menu}>
        {channel ? <Title channel={channel} seen={seen} /> : null}
      </ViewHeader>
      <div ref={scroller} className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <div className="mt-auto pb-1">
          {page.data?.more ? (
            <div className="flex justify-center py-2">
              <button
                type="button"
                disabled={loadingEarlier}
                onClick={() => void loadEarlier()}
                className="text-12 text-brand hover:text-brand-dark"
              >
                {loadingEarlier ? 'Loading…' : 'Earlier messages'}
              </button>
            </div>
          ) : null}
          {page.isPending ? (
            <p className="px-[18px] py-6 text-center text-12 text-muted">Opening…</p>
          ) : messages.length === 0 ? (
            <p className="px-[18px] py-6 text-center text-12 text-muted">
              No messages yet. Say hello.
            </p>
          ) : (
            byDay(messages, me.timezone).map((day) => (
              <div key={day.key}>
                <div className="flex items-center gap-2.5 px-[18px] py-2 text-11 text-muted">
                  <span className="h-px flex-1 bg-hairline" />
                  {day.label}
                  <span className="h-px flex-1 bg-hairline" />
                </div>
                {day.messages.map((message) => (
                  <MessageRow
                    key={message.id}
                    message={message}
                    timeZone={me.timezone}
                    mentionable={members}
                  />
                ))}
              </div>
            ))
          )}
        </div>
      </div>
      {channel ? (
        channel.archived ? (
          <p className="shrink-0 px-[18px] pt-2.5 pb-4 text-center text-12 text-muted">
            This channel is archived.
          </p>
        ) : !channel.is_member ? (
          <div className="flex shrink-0 flex-col items-center gap-2 px-3.5 pt-2.5 pb-3.5">
            {join.isError ? (
              <p role="alert" className="text-12 text-danger">
                {messageOf(join.error)}
              </p>
            ) : null}
            <PanelButton disabled={join.isPending} onClick={() => join.mutate(path)}>
              Join #{channel.name}
            </PanelButton>
          </div>
        ) : (
          <Composer channel={channel} mentionable={members.filter((p) => p.id !== me.id)} />
        )
      ) : null}
    </>
  )
}

function Title({ channel, seen }: { channel: Channel; seen: Seen[] }) {
  if (channel.kind !== 'dm') {
    return (
      <>
        <span className="text-14 font-semibold text-ink">
          # {channel.name}
          {channel.is_private ? <span className="sr-only"> (private)</span> : null}
        </span>
        <span className="text-12 text-muted">{memberCount(channel.member_count)}</span>
      </>
    )
  }
  const other = channel.people.length === 1 ? channel.people[0] : undefined
  const where = other ? seen.find((p) => p.user_id === other.id) : undefined
  const line = viewingLine(where)
  return (
    <>
      {other ? <ChatAvatar id={other.id} initials={other.initials} state={where?.state} /> : null}
      <div className="ml-0.5 min-w-0">
        <div className="truncate text-14 font-semibold text-ink">{conversationName(channel)}</div>
        {line ? (
          <div className="truncate text-115 text-muted">
            {line.lead}
            {line.label ? (
              <span className="underline decoration-line-control underline-offset-[3px]">
                {line.label}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  )
}

function MessageRow({
  message,
  timeZone,
  mentionable,
}: {
  message: Message
  timeZone: string
  mentionable: Person[]
}) {
  const client = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(message.body ?? '')
  const save = $api.useMutation('patch', '/api/core/chat/messages/{message_id}', {
    onSuccess: (saved) => {
      receive(client, saved, false)
      setEditing(false)
    },
  })
  const remove = $api.useMutation('delete', '/api/core/chat/messages/{message_id}', {
    onSuccess: (saved) => receive(client, saved, false),
  })
  const path = { params: { path: { message_id: message.id } } }
  const author = message.author

  function saveEdit() {
    const picked = mentionable.filter((p) => draft.includes(`@${p.name}`)).map((p) => p.id)
    save.mutate({ ...path, body: { body: draft, mention_ids: picked } })
  }

  return (
    <div className="group flex gap-2.5 px-[18px] py-1.5">
      <ChatAvatar id={author.id} initials={author.initials} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-13 font-semibold text-ink">{author.name}</span>
          <span className="text-11 text-muted">
            {clockTime(message.created_at, timeZone)}
            {message.edited ? ' · edited' : ''}
          </span>
          {!message.deleted && !editing && (message.can_edit || message.can_delete) ? (
            <span className="ml-auto hidden gap-2 group-focus-within:flex group-hover:flex">
              {message.can_edit ? (
                <button
                  type="button"
                  onClick={() => {
                    setDraft(message.body ?? '')
                    setEditing(true)
                  }}
                  className="text-11 text-muted hover:text-ink"
                >
                  Edit
                </button>
              ) : null}
              {message.can_delete ? (
                <button
                  type="button"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(path)}
                  className="text-11 text-muted hover:text-danger"
                >
                  Delete
                </button>
              ) : null}
            </span>
          ) : null}
        </div>
        {message.deleted ? (
          <div className="mt-0.5 text-13 leading-[1.5] text-muted">
            {(message.deleted_by ?? author).name} deleted this message
          </div>
        ) : editing ? (
          <div className="mt-1">
            <textarea
              aria-label="Edit the message"
              value={draft}
              rows={2}
              autoFocus
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  saveEdit()
                }
                if (event.key === 'Escape') {
                  event.stopPropagation()
                  setEditing(false)
                }
              }}
              className="w-full resize-none rounded-8 border border-line px-2 py-1.5 text-13 leading-[1.5] text-ink outline-none focus:border-brand"
            />
            {save.isError ? (
              <p role="alert" className="text-12 text-danger">
                {messageOf(save.error)}
              </p>
            ) : null}
            <div className="mt-1 flex gap-3 text-12">
              <button
                type="button"
                disabled={save.isPending}
                onClick={saveEdit}
                className="font-medium text-brand hover:text-brand-dark"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="text-muted hover:text-ink"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            {message.parts.length > 0 ? (
              <div className="mt-0.5 text-13 leading-[1.5] break-words whitespace-pre-wrap text-ink">
                <MessageParts parts={message.parts} />
              </div>
            ) : null}
            {message.attachments.length > 0 ? (
              <div className="mt-1 flex flex-wrap gap-1.5">
                {message.attachments.map((file) => (
                  <FileChip
                    key={file.id}
                    file={file}
                    href={`/api/core/chat/attachments/${file.id}`}
                  />
                ))}
              </div>
            ) : null}
          </>
        )}
        {remove.isError ? (
          <p role="alert" className="text-12 text-danger">
            {messageOf(remove.error)}
          </p>
        ) : null}
      </div>
    </div>
  )
}

/**
 * The composer (019): + attaches files (uploaded at once, sent with the message, refused past the
 * size Settings allows), "@" offers the people who can read the conversation, Enter sends and
 * Shift+Enter starts a new line, the arrow sends. A request is referred to by pasting its address
 * or typing its tracking number; each reader sees it only if they may.
 */
function Composer({ channel, mentionable }: { channel: Channel; mentionable: Person[] }) {
  const me = useMe()
  const client = useQueryClient()
  const [text, setText] = useState('')
  const [caret, setCaret] = useState(0)
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [choice, setChoice] = useState(0)
  const box = useRef<HTMLTextAreaElement>(null)
  const chooser = useRef<HTMLInputElement>(null)
  const people = $api.useQuery('get', '/api/core/chat/people', {}, { staleTime: 300_000 })
  // Anyone can read a public channel, so anyone can be mentioned in it; elsewhere, its members.
  const candidates =
    channel.kind === 'channel' && !channel.is_private
      ? (people.data ?? []).filter((p) => p.id !== me.id)
      : mentionable
  const query = mentionQuery(text, caret)
  const matches = query === null ? [] : matchPeople(candidates, query)

  const send = $api.useMutation('post', '/api/core/chat/channels/{channel_id}/messages', {
    onSuccess: (message) => {
      receive(client, message, false)
      setText('')
      setFiles([])
      setPicked([])
      setError(null)
    },
    onError: (failure) => setError(messageOf(failure)),
  })

  // The box grows with what is written, up to about five lines.
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 104)}px`
  }, [text])

  function submit() {
    const body = text.trim()
    if ((!body && files.length === 0) || send.isPending || uploading > 0) return
    const mentionIds = picked.filter((id) => {
      const person = candidates.find((p) => p.id === id)
      return person ? body.includes(`@${person.name}`) : false
    })
    send.mutate({
      params: { path: { channel_id: channel.id } },
      body: { body, attachment_ids: files.map((f) => f.id), mention_ids: mentionIds },
    })
  }

  function choose(person: Person) {
    const next = insertMention(text, caret, person.name)
    setText(next.text)
    setCaret(next.caret)
    setPicked((was) => [...new Set([...was, person.id])])
    setChoice(0)
    requestAnimationFrame(() => {
      box.current?.focus()
      box.current?.setSelectionRange(next.caret, next.caret)
    })
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (matches.length > 0) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const step = event.key === 'ArrowDown' ? 1 : -1
        setChoice((at) => (at + step + matches.length) % matches.length)
        return
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault()
        choose(matches[Math.min(choice, matches.length - 1)]!)
        return
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  async function attach(chosen: FileList | null) {
    if (!chosen) return
    setError(null)
    for (const file of Array.from(chosen)) {
      setUploading((n) => n + 1)
      const form = new FormData()
      form.append('file', file)
      const { data, error: failed } = await api.POST(
        '/api/core/chat/channels/{channel_id}/attachments',
        {
          params: { path: { channel_id: channel.id } },
          body: form as never,
          bodySerializer: (body) => body as unknown as FormData,
        },
      )
      setUploading((n) => n - 1)
      if (data) setFiles((was) => [...was, data])
      else setError(messageOf(failed, `${file.name} could not be attached.`))
    }
    if (chooser.current) chooser.current.value = ''
  }

  return (
    <div className="relative shrink-0 px-3.5 pt-2.5 pb-3.5">
      {matches.length > 0 ? (
        <ul
          role="listbox"
          aria-label="People to mention"
          className="absolute right-3.5 bottom-full left-3.5 mb-1 overflow-hidden rounded-10 border border-line bg-card-white py-1 shadow-menu"
        >
          {matches.map((person, index) => (
            <li key={person.id} role="option" aria-selected={index === choice}>
              <button
                type="button"
                onMouseDown={(event) => {
                  event.preventDefault()
                  choose(person)
                }}
                className={cx(
                  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-125 text-ink hover:bg-hover',
                  index === choice && 'bg-hover',
                )}
              >
                <ChatAvatar id={person.id} initials={person.initials} size={24} />
                {person.name}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p role="alert" className="mb-1.5 text-12 text-danger">
          {error}
        </p>
      ) : null}
      {files.length > 0 || uploading > 0 ? (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {files.map((file) => (
            <span
              key={file.id}
              className="inline-flex items-center gap-1 rounded-8 border border-line py-1 pr-1 pl-2 text-12 text-ink"
            >
              <span className="max-w-[220px] truncate">{file.file_name}</span>
              <button
                type="button"
                aria-label={`Remove ${file.file_name}`}
                onClick={() => setFiles((was) => was.filter((f) => f.id !== file.id))}
                className="inline-flex size-4 items-center justify-center rounded-4 hover:bg-hover"
              >
                <CloseIcon size={11} width={2} stroke="var(--color-ink-3)" />
              </button>
            </span>
          ))}
          {uploading > 0 ? <span className="py-1 text-12 text-muted">Uploading…</span> : null}
        </div>
      ) : null}
      <div className="flex items-center gap-2.5 rounded-12 bg-card-white px-3 py-2.5 shadow-field-hover">
        <button
          type="button"
          aria-label="Attach a file"
          onClick={() => chooser.current?.click()}
          className="inline-flex shrink-0 rounded-4 hover:bg-hover"
        >
          <PlusIcon size={16} width={1.9} stroke="var(--color-muted)" />
        </button>
        <input
          ref={chooser}
          type="file"
          multiple
          hidden
          onChange={(event) => void attach(event.target.files)}
        />
        <textarea
          ref={box}
          aria-label={placeholderFor(channel)}
          rows={1}
          value={text}
          placeholder={placeholderFor(channel)}
          onChange={(event) => {
            setText(event.target.value)
            setCaret(event.target.selectionStart)
          }}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart)}
          onKeyDown={onKeyDown}
          className="block min-w-0 flex-1 resize-none bg-transparent text-13 leading-[normal] text-ink outline-none placeholder:text-muted-2"
        />
        <button
          type="button"
          aria-label="Send"
          disabled={send.isPending}
          onClick={submit}
          className="inline-flex shrink-0 rounded-4 hover:bg-hover"
        >
          <SendArrowIcon size={16} width={1.9} stroke="var(--color-muted)" />
        </button>
      </div>
    </div>
  )
}
