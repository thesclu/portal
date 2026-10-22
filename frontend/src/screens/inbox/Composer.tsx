/**
 * Writing a message (design 118, 144–150): To, Cc and Bcc as chips, From read only (the request's
 * own address: the person's own is never used), the words, the request's files, saved replies,
 * and Send. The draft is saved as it is written. Send shows the exact message to confirm first
 * (150); confirmed, it is queued for its undo window and the caller shows the countdown (151).
 *
 * Mount it on an existing draft (`messageId`), or have it start one: a new message on a request
 * (`requestId`), or a reply or forward to a message that is on a request (`inReplyToSourceId`).
 *
 * The design's bold, italic and list buttons, the paperclip (a file from this computer) and the
 * "too large to email" chip are not drawn: mail is plain text, a message carries only the
 * request's own files, and no size limit is known before sending (HANDOFF, P20d).
 */
import { useQueryClient } from '@tanstack/react-query'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type Dispatch,
  type KeyboardEvent,
  type ReactNode,
  type SetStateAction,
} from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { needsApproval, useMe } from '../../app/session'
import { unwrap } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { FileIcon, SavedRepliesIcon } from '../../components/icons'
import { LoadFailed, Loading } from '../../components/States'
import { Button, Notice, cx } from '../../components/ui'
import { formatTime } from '../../lib/dates'
import { sendLabel, sendOrSubmit } from '../../lib/outgoing'
import { fileTag } from '../../lib/requestView'
import { fileSize } from '../../lib/vocab'
import {
  parseAddresses,
  recipientsOf,
  shownAddress,
  statusOf,
  toAddress,
  useRefreshMail,
  useSending,
  type Address,
  type Message,
} from './mail'
import { Box } from './parts'

type Attachment = Schemas['AttachmentOut']

export interface DraftStart {
  /** The request the message is on. Not needed for a reply or a forward: the message replied
   *  to names its request. */
  requestId?: string
  /** Reply to (or forward) this inbound message. The draft is prefilled from it. */
  inReplyToSourceId?: string | null
  forward?: boolean
}

export type ComposerStart = { messageId: string } | DraftStart

export interface ComposerCallbacks {
  /** The composer is put away. The draft is kept unless it was discarded. */
  onClose: () => void
  /** Send was confirmed and the API queued it: the message, now `queued`, with its `send_after`. */
  onSent?: (message: Message) => void
  /** The Inbox's pane: the confirm and discard sheets cover it, not the window (145, 150). */
  overlayHost?: HTMLElement | null
  /** "Maya Torres is also writing a reply" (146): drawn above the address rows. */
  notice?: ReactNode
  /** The inbox pane draws its own rule and padding (144); the request page's card does not. */
  frame?: 'pane' | 'card' | 'draft'
  /** The editor has the focus as it opens (146: the reply field expanded in place). */
  autoFocus?: boolean
  /** Bumped by the Drafts pane's bin (118): ask "Discard this draft?" (145). */
  discardAsked?: number
}

export type ComposerProps = ComposerStart & ComposerCallbacks

const messageFrom = (caught: unknown): string =>
  caught instanceof Error ? caught.message : 'Something went wrong. Try again.'

/** Starts a draft and returns it: prefilled from a message when replying or forwarding, empty
 *  on the request otherwise. Throws the API's own sentence when it refuses. */
export async function startDraft(start: DraftStart): Promise<Message> {
  if (start.inReplyToSourceId) {
    return unwrap(
      await api.POST('/api/mail/sources/{source_id}/reply-draft', {
        params: { path: { source_id: start.inReplyToSourceId } },
        body: { forward: Boolean(start.forward) },
      }),
    )
  }
  if (!start.requestId) throw new Error('A new message needs a request to be on.')
  return unwrap(
    await api.POST('/api/mail/messages', {
      body: {
        request_id: start.requestId,
        kind: 'new',
        subject: '',
        body_text: '',
        to: [],
        cc: [],
        bcc: [],
        attachment_ids: [],
        attach_letter: false,
      },
    }),
  )
}

export function Composer(props: ComposerProps) {
  if ('messageId' in props) return <Existing {...props} />
  return <Starter {...props} />
}

function Starter({
  onClose,
  onSent,
  overlayHost,
  notice,
  frame,
  autoFocus,
  discardAsked,
  ...start
}: DraftStart & ComposerCallbacks) {
  const [draft, setDraft] = useState<Message | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = useRefreshMail()
  const started = useRef(false)
  const wanted = useRef(start)
  useEffect(() => {
    // Once per mount, Strict Mode's second pass included: a second call would make a second draft.
    if (started.current) return
    started.current = true
    startDraft(wanted.current).then(
      (made) => {
        setDraft(made)
        void refresh()
      },
      (caught: unknown) => setError(messageFrom(caught)),
    )
  }, [refresh])
  if (error) {
    return (
      <Frame frame={frame}>
        <div className="space-y-2">
          <Notice tone="danger">{error}</Notice>
          <Button onClick={onClose}>Close</Button>
        </div>
      </Frame>
    )
  }
  if (!draft)
    return (
      <Frame frame={frame}>
        <Loading what="Starting the message" />
      </Frame>
    )
  return (
    <DraftForm
      key={draft.id}
      initial={draft}
      onClose={onClose}
      onSent={onSent}
      overlayHost={overlayHost}
      notice={notice}
      frame={frame}
      autoFocus={autoFocus ?? true}
      discardAsked={discardAsked}
    />
  )
}

function Existing({
  messageId,
  onClose,
  onSent,
  overlayHost,
  notice,
  frame,
  autoFocus,
  discardAsked,
}: { messageId: string } & ComposerCallbacks) {
  const message = $api.useQuery('get', '/api/mail/messages/{message_id}', {
    params: { path: { message_id: messageId } },
  })
  if (message.isPending)
    return (
      <Frame frame={frame}>
        <Loading what="Opening the draft" />
      </Frame>
    )
  if (message.isError)
    return (
      <Frame frame={frame}>
        <LoadFailed what="the draft" error={message.error} onRetry={() => void message.refetch()} />
      </Frame>
    )
  if (message.data.status !== 'draft') {
    return (
      <Frame frame={frame}>
        <div className="space-y-2">
          <Notice>
            This message is {statusOf(message.data).label.toLowerCase()}; only a draft can be
            edited.
          </Notice>
          <Button onClick={onClose}>Close</Button>
        </div>
      </Frame>
    )
  }
  return (
    <DraftForm
      key={messageId}
      initial={message.data}
      onClose={onClose}
      onSent={onSent}
      overlayHost={overlayHost}
      notice={notice}
      frame={frame}
      autoFocus={autoFocus ?? false}
      discardAsked={discardAsked}
    />
  )
}

/** The composer's frame: the inbox pane's 14px 22px under a hairline (144), the Drafts pane's
 *  column that fills the pane (118), or nothing extra inside the request page's card. */
function Frame({
  frame = 'card',
  children,
}: {
  frame?: ComposerProps['frame']
  children: ReactNode
}) {
  return (
    <section
      aria-label="Write a message"
      className={cx(
        'relative',
        frame === 'pane' && 'border-t border-hairline px-[22px] py-3.5',
        frame === 'draft' && 'flex flex-1 flex-col px-[22px] pt-3.5 pb-[18px]',
      )}
    >
      {children}
    </section>
  )
}

// ---- the form ------------------------------------------------------------------------------------

function DraftForm({
  initial,
  onClose,
  onSent,
  overlayHost,
  notice,
  frame = 'card',
  autoFocus,
  discardAsked = 0,
}: {
  initial: Message
  onClose: () => void
  onSent?: (message: Message) => void
  overlayHost?: HTMLElement | null
  notice?: ReactNode
  frame?: ComposerProps['frame']
  autoFocus: boolean
  discardAsked?: number
}) {
  const me = useMe()
  const intern = needsApproval(me)
  const client = useQueryClient()
  const sending = useSending()
  const refresh = useRefreshMail()
  const [subject, setSubject] = useState(initial.subject)
  const [body, setBody] = useState(initial.body_text)
  const [to, setTo] = useState<Address[]>(() => recipientsOf(initial, 'to').map(toAddress))
  const [cc, setCc] = useState<Address[]>(() => recipientsOf(initial, 'cc').map(toAddress))
  const [bcc, setBcc] = useState<Address[]>(() => recipientsOf(initial, 'bcc').map(toAddress))
  const [showCc, setShowCc] = useState(() => recipientsOf(initial, 'cc').length > 0)
  const [showBcc, setShowBcc] = useState(() => recipientsOf(initial, 'bcc').length > 0)
  const [files, setFiles] = useState<string[]>(initial.attachment_ids)
  // Every edit bumps `version`; a save records which version it wrote. The debounce below saves
  // whenever the two differ, and Send saves first when they do.
  const [version, setVersion] = useState(0)
  const [savedVersion, setSavedVersion] = useState(0)
  const [savedAt, setSavedAt] = useState<string>(initial.updated_at)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [picker, setPicker] = useState<'replies' | 'files' | null>(null)
  const [chosenSheet, setChosenSheet] = useState<'confirm' | 'discard' | null>(null)
  // The Drafts pane's bin asks by bumping a number; an ask not yet answered shows the sheet.
  const [answered, setAnswered] = useState(discardAsked)
  const sheet = discardAsked > answered ? 'discard' : chosenSheet
  const setSheet = (next: 'confirm' | 'discard' | null) => {
    setChosenSheet(next)
    if (next === null) setAnswered(discardAsked)
  }
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const closed = useRef(false)
  const inFlight = useRef<Promise<unknown>>(Promise.resolve())
  const attachments = $api.useQuery('get', '/api/records/requests/{request_id}/attachments', {
    params: { path: { request_id: initial.request_id } },
  })
  const byId = new Map((attachments.data ?? []).map((file) => [file.id, file]))
  const reply = initial.kind === 'reply'

  const edit =
    <T,>(set: Dispatch<SetStateAction<T>>) =>
    (value: SetStateAction<T>) => {
      set(value)
      setVersion((v) => v + 1)
    }
  const editSubject = edit(setSubject)
  const editBody = edit(setBody)
  const editTo = edit(setTo)
  const editCc = edit(setCc)
  const editBcc = edit(setBcc)
  const editFiles = edit(setFiles)

  const save = useCallback(
    async (at: number): Promise<Message | null> => {
      if (closed.current) return null
      setSaving(true)
      setSaveError(null)
      const previous = inFlight.current
      const attempt = (async () => {
        await previous.catch(() => undefined) // one write at a time, in the order asked
        return unwrap(
          await api.PATCH('/api/mail/messages/{message_id}', {
            params: { path: { message_id: initial.id } },
            body: { subject, body_text: body, to, cc, bcc, attachment_ids: files },
          }),
        )
      })()
      inFlight.current = attempt
      try {
        const saved = await attempt
        setSavedVersion(at)
        setSavedAt(saved.updated_at)
        void client.invalidateQueries({ queryKey: ['get', '/api/mail/messages'] })
        return saved
      } catch (caught) {
        setSaveError(messageFrom(caught))
        return null
      } finally {
        setSaving(false)
      }
    },
    [initial.id, subject, body, to, cc, bcc, files, client],
  )

  // Autosave: a short pause after the last edit, one write at a time.
  useEffect(() => {
    if (version === 0 || version === savedVersion || saving) return
    const timer = window.setTimeout(() => void save(version), 800)
    return () => window.clearTimeout(timer)
  }, [version, savedVersion, saving, save])

  useEffect(() => {
    if (autoFocus) bodyRef.current?.focus()
  }, [autoFocus])

  /** Send: save what is on screen, then show it for a person to confirm (150). An intern's goes
   *  to an approver instead (P20h): nothing leaves, so there is nothing to confirm. */
  async function askToSend() {
    setSendError(null)
    if (version !== savedVersion) {
      const saved = await save(version)
      if (!saved) return
    }
    if (intern) void send()
    else setSheet('confirm')
  }

  async function send() {
    setSendError(null)
    setBusy(true)
    try {
      const outcome = await sendOrSubmit(initial.id, intern)
      closed.current = true
      setSheet(null)
      await refresh()
      if (outcome) onSent?.(outcome)
      else onClose()
    } catch (caught) {
      setSheet(null)
      setSendError(messageFrom(caught))
    } finally {
      setBusy(false)
    }
  }

  async function discard() {
    closed.current = true
    setBusy(true)
    try {
      unwrap(
        await api.DELETE('/api/mail/messages/{message_id}', {
          params: { path: { message_id: initial.id } },
        }),
      )
      await refresh()
      setSheet(null)
      onClose()
    } catch (caught) {
      closed.current = false
      setSheet(null)
      setSendError(messageFrom(caught))
    } finally {
      setBusy(false)
    }
  }

  function insertAtCursor(text: string) {
    const area = bodyRef.current
    const start = area?.selectionStart ?? body.length
    const end = area?.selectionEnd ?? start
    const before = body.slice(0, start)
    const next = before + text + body.slice(end)
    editBody(next)
    setPicker(null)
    window.requestAnimationFrame(() => {
      if (!area) return
      area.focus()
      area.setSelectionRange(before.length + text.length, before.length + text.length)
    })
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    const mod = event.metaKey || event.ctrlKey
    if (mod && event.key === 'Enter') {
      event.preventDefault()
      void askToSend()
    } else if (mod && event.shiftKey && event.key.toLowerCase() === 's') {
      event.preventDefault()
      setPicker((was) => (was === 'replies' ? null : 'replies'))
    } else if (mod && event.key === 'Backspace') {
      event.preventDefault()
      window.setTimeout(() => setSheet('discard'), 0)
    } else if (event.key === 'Escape' && !sheet) {
      event.stopPropagation()
      event.preventDefault()
      // Opened after this key is done: a modal opened while Escape is still being handled is
      // closed again by that same Escape.
      if (picker) setPicker(null)
      else if (body.trim() || subject !== initial.subject)
        window.setTimeout(() => setSheet('discard'), 0)
      else onClose()
    }
  }

  const state = saving
    ? 'Saving…'
    : version !== savedVersion
      ? 'Not saved yet'
      : `Draft saved ${formatTime(savedAt, me.timezone)}`
  const toNames = to.map((a) => a.name || a.address).join(', ') || 'nobody yet'
  const what = reply ? 'reply' : initial.kind === 'forward' ? 'forward' : 'message'

  const sheetBody =
    sheet === 'confirm' ? (
      <ConfirmSend
        from={initial.from_address}
        to={to}
        cc={cc}
        bcc={bcc}
        files={files.map((id) => byId.get(id)?.filename ?? 'A file of this request')}
        body={body}
        title={
          reply
            ? 'Send this reply?'
            : initial.kind === 'forward'
              ? 'Forward this message?'
              : 'Send this message?'
        }
        busy={busy}
        onKeep={() => setSheet(null)}
        onSend={() => void send()}
        testMode={sending.data ? !sending.data.enabled : false}
      />
    ) : sheet === 'discard' ? (
      <DiscardSheet
        words={`The ${what} to ${toNames} will be deleted.`}
        busy={busy}
        onKeep={() => setSheet(null)}
        onDiscard={() => void discard()}
      />
    ) : null

  return (
    <Frame frame={frame}>
      <div onKeyDown={onKeyDown} className={cx(frame === 'draft' && 'flex flex-1 flex-col')}>
        {notice}
        <AddressRow
          label="To"
          value={to}
          onChange={editTo}
          gap={showCc || showBcc ? 'mb-1.5' : 'mb-1.5'}
          trailing={
            <span className="ml-auto inline-flex gap-3 text-ink-2">
              {!showCc ? (
                <button type="button" onClick={() => setShowCc(true)} className="hover:text-ink">
                  Cc
                </button>
              ) : null}
              {!showBcc ? (
                <button type="button" onClick={() => setShowBcc(true)} className="hover:text-ink">
                  Bcc
                </button>
              ) : null}
            </span>
          }
        />
        {showCc ? <AddressRow label="Cc" value={cc} onChange={editCc} gap="mb-[9px]" /> : null}
        {showBcc ? <AddressRow label="Bcc" value={bcc} onChange={editBcc} gap="mb-[9px]" /> : null}
        <div className="mb-2.5 flex items-center gap-2 text-12 text-muted">
          <span className="w-11 shrink-0">From</span>
          <span className="truncate text-ink-2" title="The request's own address">
            {initial.from_address}
          </span>
        </div>
        {!reply ? (
          <label className="mb-2.5 flex items-center gap-2 text-12 text-muted">
            <span className="w-11 shrink-0">Subject</span>
            <input
              className="min-w-0 flex-1 bg-transparent text-12 text-ink outline-none placeholder:text-muted-2"
              placeholder="Subject"
              value={subject}
              onChange={(event) => editSubject(event.target.value)}
            />
          </label>
        ) : null}
        <div
          className={cx(
            'overflow-hidden rounded-16',
            frame === 'draft'
              ? 'flex flex-1 flex-col bg-blue-tint-5 shadow-raised-soft'
              : 'border-[1.5px] border-line-2 bg-card-white focus-within:border-brand focus-within:shadow-ring-focus',
          )}
        >
          <textarea
            ref={bodyRef}
            aria-label="Message"
            rows={frame === 'draft' ? 6 : 4}
            placeholder="Write the message"
            className={cx(
              'block w-full resize-none bg-transparent px-3.5 pt-3 pb-1.5 text-13 leading-[1.6] text-ink outline-none placeholder:text-muted-2',
              frame === 'draft' && 'flex-1',
            )}
            value={body}
            onChange={(event) => editBody(event.target.value)}
          />
          {files.length > 0 ? (
            <ul className="flex flex-wrap gap-2 px-3.5 pb-2.5" aria-label="Attached files">
              {files.map((fileId) => {
                const file = byId.get(fileId)
                return (
                  <li key={fileId}>
                    <AttachedFile
                      name={file?.filename ?? 'A file of this request'}
                      bytes={file?.size_bytes}
                      onRemove={() => editFiles(files.filter((id) => id !== fileId))}
                    />
                  </li>
                )
              })}
            </ul>
          ) : null}
          <div className="flex items-center gap-0.5 border-t border-hairline bg-row-alt px-2 py-1.5">
            <ToolbarButton
              pressed={picker === 'replies'}
              onClick={() => setPicker(picker === 'replies' ? null : 'replies')}
            >
              <SavedRepliesIcon size={15} width={1.8} stroke="var(--color-ink-2)" />
              Saved replies
            </ToolbarButton>
            <ToolbarButton
              pressed={picker === 'files'}
              onClick={() => setPicker(picker === 'files' ? null : 'files')}
            >
              <FileIcon size={15} width={1.8} stroke="var(--color-ink-2)" />
              From request
            </ToolbarButton>
            <span className="mr-2.5 ml-auto min-w-0 truncate text-115 text-muted" role="status">
              {state}
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => void askToSend()}
              className="inline-flex items-center gap-1.5 rounded-pill bg-brand px-3.5 py-1.5 text-12 font-medium text-white hover:bg-brand-dark disabled:opacity-55"
            >
              {sendLabel(intern, initial.kind === 'forward' ? 'Forward' : 'Send')}
            </button>
          </div>
        </div>

        {picker === 'replies' ? (
          <SavedRepliesPicker onPick={insertAtCursor} onClose={() => setPicker(null)} />
        ) : picker === 'files' ? (
          <FilesPicker
            files={attachments.data ?? []}
            pending={attachments.isPending}
            chosen={files}
            tracking={initial.tracking_number}
            onDone={(next) => {
              editFiles(next)
              setPicker(null)
            }}
            onClose={() => setPicker(null)}
          />
        ) : null}

        {saveError ? (
          <div className="mt-2">
            <Notice tone="danger">Couldn’t save the draft. {saveError}</Notice>
          </div>
        ) : null}
        {sendError ? (
          <div className="mt-2">
            <Notice tone="danger">
              {sendError}{' '}
              <Link to={`/inbox?bucket=drafts&message=${initial.id}`} className="font-medium">
                Open it in Drafts
              </Link>
            </Notice>
          </div>
        ) : null}
      </div>
      {sheetBody ? (
        // "Send this reply?" covers the pane (150); "Discard this draft?" the window (145).
        overlayHost && sheet === 'confirm' ? (
          createPortal(<Sheet onClose={() => setSheet(null)}>{sheetBody}</Sheet>, overlayHost)
        ) : (
          <Dialog
            title={sheet === 'confirm' ? 'Send this message?' : 'Discard this draft?'}
            open
            bare
            frameless={sheet === 'confirm'}
            centered
            width={sheet === 'confirm' ? 460 : 400}
            onClose={() => setSheet(null)}
          >
            {sheetBody}
          </Dialog>
        )
      ) : null}
    </Frame>
  )
}

/** A sheet over the reading pane (145, 150): the scrim covers the pane, the card sits centred. */
function Sheet({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <div
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onClose()
      }}
      className="absolute inset-0 z-[5] flex items-center justify-center scrim"
    >
      {children}
    </div>
  )
}

/** "Send this reply?" (150): the exact message, as it will go. Every composer confirms with it,
 *  stage 4's filing email included (P20g). */
export function ConfirmSend({
  from,
  to,
  cc,
  bcc,
  files,
  body,
  title,
  busy,
  onKeep,
  onSend,
  testMode,
}: {
  from: string
  to: Address[]
  cc: Address[]
  bcc: Address[]
  files: string[]
  body: string
  title: string
  busy: boolean
  onKeep: () => void
  onSend: () => void
  testMode: boolean
}) {
  const rows: Array<[string, string]> = [
    ['To', to.map(shownAddress).join(', ') || 'Nobody yet'],
    ...(cc.length ? ([['Cc', cc.map(shownAddress).join(', ')]] as Array<[string, string]>) : []),
    ...(bcc.length ? ([['Bcc', bcc.map(shownAddress).join(', ')]] as Array<[string, string]>) : []),
    ['From', from],
    ...(files.length ? ([['Files', files.join(', ')]] as Array<[string, string]>) : []),
  ]
  return (
    <div
      role="dialog"
      aria-label={title}
      tabIndex={-1}
      ref={(node) => node?.focus()}
      className="w-[460px] max-w-full overflow-hidden rounded-18 bg-card-white shadow-dialog outline-none"
    >
      <div className="px-[22px] pt-[18px] pb-1.5">
        <div className="font-display text-lg font-extrabold">{title}</div>
      </div>
      <div className="px-[22px] pt-2 pb-3.5">
        {rows.map(([label, value]) => (
          <div key={label} className="flex gap-3 py-[5px] text-125">
            <span className="w-[52px] shrink-0 text-muted">{label}</span>
            <span className="min-w-0 text-ink">{value}</span>
          </div>
        ))}
      </div>
      <div className="mx-[22px] mb-4 max-h-56 overflow-y-auto rounded-10 border border-fill-muted bg-surface-soft px-3.5 py-3 text-125 leading-[1.65] whitespace-pre-wrap text-ink-2">
        {body.trim() || '(no text)'}
      </div>
      {testMode ? (
        <p className="mx-[22px] mb-2 text-12 text-muted">
          Test mode: the mail provider accepts it and delivers it to nobody.
        </p>
      ) : null}
      <div className="flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">
        <Button kind="text" pill onClick={onKeep}>
          Keep editing
        </Button>
        <Button kind="primary" pill busy={busy} onClick={onSend}>
          Send
        </Button>
      </div>
    </div>
  )
}

/** "Discard this draft?" (145). */
function DiscardSheet({
  words,
  busy,
  onKeep,
  onDiscard,
}: {
  words: string
  busy: boolean
  onKeep: () => void
  onDiscard: () => void
}) {
  return (
    <div
      aria-label="Discard this draft?"
      tabIndex={-1}
      ref={(node) => node?.focus()}
      className="w-[400px] max-w-full overflow-hidden outline-none"
    >
      <div className="px-[22px] pt-[18px] pb-1.5 font-display text-lg font-extrabold text-ink">
        Discard this draft?
      </div>
      <div className="px-[22px] pt-1.5 pb-4 text-125 leading-[1.6] text-ink-2">{words}</div>
      <div className="flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">
        <Button kind="text" pill onClick={onKeep}>
          Keep editing
        </Button>
        <Button kind="danger" pill busy={busy} onClick={onDiscard}>
          Discard
        </Button>
      </div>
    </div>
  )
}

function ToolbarButton({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cx(
        'inline-flex h-7 items-center gap-1.5 rounded-chip px-[9px] text-12 text-ink-2 hover:bg-hover',
        pressed && 'bg-hover',
      )}
    >
      {children}
    </button>
  )
}

/** A file of the request attached to the message (149): its tag, name and size, removable. */
function AttachedFile({
  name,
  bytes,
  onRemove,
}: {
  name: string
  bytes: number | null | undefined
  onRemove: () => void
}) {
  return (
    <span className="inline-flex items-center gap-[9px] rounded-9 border border-line-chip-2 bg-surface-soft px-[11px] py-[7px] text-115 text-ink">
      <span className="box-border inline-flex h-[26px] w-[22px] items-end justify-center rounded-xs bg-red-tint-2 pb-[3px] text-micro font-bold text-danger">
        {fileTag(name)}
      </span>
      <span className="flex flex-col leading-[1.3]">
        <span>{name}</span>
        <span className="text-105 text-muted">{fileSize(bytes)}</span>
      </span>
      <button
        type="button"
        aria-label={`Remove ${name}`}
        onClick={onRemove}
        className="ml-1 text-12 text-muted-2 hover:text-ink"
      >
        ✕
      </button>
    </span>
  )
}

// ---- addresses -----------------------------------------------------------------------------------

/** A recipient (144): a grey pill with the name and a ✕. */
function Chip({ label, title, onRemove }: { label: string; title?: string; onRemove: () => void }) {
  return (
    <span
      title={title}
      className="inline-flex max-w-full items-center gap-1.5 rounded-pill border border-line bg-line-row py-0.5 pr-1.5 pl-2.5 text-12 text-ink"
    >
      <span className="truncate">{label}</span>
      <button
        type="button"
        aria-label={`Remove ${label}`}
        className="text-11 text-muted-2 hover:text-ink"
        onClick={onRemove}
      >
        ✕
      </button>
    </span>
  )
}

const merge = (current: Address[], added: Address[]): Address[] => {
  const seen = new Set(current.map((a) => a.address.toLowerCase()))
  const next = [...current]
  for (const entry of added) {
    const key = entry.address.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    next.push(entry)
  }
  return next
}

/** One line of recipients (144): the 44 px label, chips, and a box that takes an address on
 *  Enter, a comma or a paste. */
function AddressRow({
  label,
  value,
  onChange,
  trailing,
  gap,
}: {
  label: string
  value: Address[]
  onChange: (next: Address[]) => void
  trailing?: ReactNode
  gap: string
}) {
  const [text, setText] = useState('')
  const [rejected, setRejected] = useState<string[]>([])
  const inputId = `address-${label.toLowerCase()}`

  function take(raw: string) {
    const parsed = parseAddresses(raw)
    if (parsed.addresses.length > 0) onChange(merge(value, parsed.addresses))
    setRejected(parsed.rejected)
    setText(parsed.rejected.join(', '))
  }
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',' || event.key === ';') {
      event.preventDefault()
      if (text.trim()) take(text)
    } else if (event.key === 'Backspace' && !text && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }
  function onPaste(event: ClipboardEvent<HTMLInputElement>) {
    event.preventDefault()
    take(text + event.clipboardData.getData('text'))
  }

  return (
    <div className={gap}>
      <div className="flex items-center gap-2 text-12 text-muted">
        <label htmlFor={inputId} className="w-11 shrink-0">
          {label}
        </label>
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          {value.map((entry) => (
            <Chip
              key={entry.address}
              label={entry.name || entry.address}
              title={entry.address}
              onRemove={() => onChange(value.filter((a) => a !== entry))}
            />
          ))}
          <input
            id={inputId}
            type="text"
            autoComplete="off"
            className="h-[22px] min-w-[8rem] flex-1 bg-transparent text-12 text-ink outline-none placeholder:text-muted-2"
            placeholder={value.length === 0 ? 'name@agency.gov' : ''}
            value={text}
            onChange={(event) => {
              setText(event.target.value)
              if (rejected.length) setRejected([])
            }}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onBlur={() => {
              if (text.trim()) take(text)
            }}
          />
        </div>
        {trailing}
      </div>
      {rejected.length > 0 ? (
        <p role="alert" className="mt-1 pl-[52px] text-12 text-danger">
          Not an address: {rejected.join(', ')}
        </p>
      ) : null}
    </div>
  )
}

// ---- pickers (148, 149) --------------------------------------------------------------------------

const POPOVER =
  'absolute bottom-[62px] left-[22px] z-[3] w-[340px] overflow-hidden rounded-16 bg-card shadow-popover-2'

function PopoverSearch({
  placeholder,
  value,
  onChange,
}: {
  placeholder: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="border-b border-hairline px-3 py-2.5">
      <input
        type="search"
        aria-label={placeholder}
        placeholder={placeholder}
        autoFocus
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-12 bg-card-white px-2.5 py-1.5 text-12 text-ink shadow-field outline-none placeholder:text-muted-2"
      />
    </div>
  )
}

function SavedRepliesPicker({
  onPick,
  onClose,
}: {
  onPick: (body: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [lit, setLit] = useState(0)
  const replies = $api.useQuery('get', '/api/mail/saved-replies', {
    params: { query: { include_archived: false } },
  })
  const needle = q.trim().toLowerCase()
  const shown = (replies.data ?? []).filter(
    (reply) =>
      !needle ||
      reply.name.toLowerCase().includes(needle) ||
      reply.body.toLowerCase().includes(needle),
  )
  return (
    <div
      className={POPOVER}
      role="dialog"
      aria-label="Saved replies"
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation()
          onClose()
        } else if (event.key === 'ArrowDown') {
          event.preventDefault()
          setLit((i) => Math.min(shown.length - 1, i + 1))
        } else if (event.key === 'ArrowUp') {
          event.preventDefault()
          setLit((i) => Math.max(0, i - 1))
        } else if (event.key === 'Enter' && shown[lit]) {
          event.preventDefault()
          onPick(shown[lit].body)
        }
      }}
    >
      <PopoverSearch placeholder="Search saved replies" value={q} onChange={setQ} />
      <div className="px-3.5 pt-2 pb-1 text-105 font-semibold tracking-group text-muted uppercase">
        Saved replies
      </div>
      {replies.isPending ? (
        <p className="px-3.5 py-2 text-12 text-muted">Loading…</p>
      ) : (replies.data ?? []).length === 0 ? (
        <p className="px-3.5 py-2 text-12 text-muted">
          None yet.{' '}
          <Link to="/templates/replies" className="text-brand">
            Add some in Settings
          </Link>
        </p>
      ) : shown.length === 0 ? (
        <p className="px-3.5 py-2 text-12 text-muted">No saved reply matches “{q}”.</p>
      ) : (
        <ul className="max-h-60 overflow-y-auto">
          {shown.map((reply, index) => (
            <li key={reply.id}>
              <button
                type="button"
                onMouseEnter={() => setLit(index)}
                onClick={() => onPick(reply.body)}
                className={cx('block w-full px-3.5 py-2 text-left', index === lit && 'bg-hover')}
              >
                <span className="block text-125 text-ink">{reply.name}</span>
                <span className="mt-px block truncate text-115 text-muted">
                  {reply.body.split('\n')[0]}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="h-1.5" />
    </div>
  )
}

function FilesPicker({
  files,
  pending,
  chosen,
  tracking,
  onDone,
  onClose,
}: {
  files: Attachment[]
  pending: boolean
  chosen: string[]
  tracking: string
  onDone: (next: string[]) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<string[]>(chosen)
  const needle = q.trim().toLowerCase()
  const shown = files.filter((file) => !needle || file.filename.toLowerCase().includes(needle))
  const added = picked.filter((id) => !chosen.includes(id)).length
  return (
    <div
      className={POPOVER}
      role="dialog"
      aria-label={`Files on ${tracking}`}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation()
          onClose()
        }
      }}
    >
      <PopoverSearch placeholder="Search this request’s files" value={q} onChange={setQ} />
      <div className="px-3.5 pt-2 pb-1 text-105 font-semibold tracking-group text-muted uppercase">
        Files on {tracking}
      </div>
      {pending ? (
        <p className="px-3.5 py-2 text-12 text-muted">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="px-3.5 py-2 text-12 text-muted">
          {files.length === 0 ? 'This request has no files yet.' : 'No file matches.'}
        </p>
      ) : (
        <ul className="max-h-60 overflow-y-auto">
          {shown.map((file) => {
            const on = picked.includes(file.id)
            return (
              <li key={file.id}>
                <label
                  className={cx(
                    'flex cursor-pointer items-center gap-2.5 px-3.5 py-2',
                    on && 'bg-blue-tint-4',
                  )}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={on}
                    onChange={() =>
                      setPicked(on ? picked.filter((id) => id !== file.id) : [...picked, file.id])
                    }
                  />
                  <Box checked={on} />
                  <span className="min-w-0 flex-1 truncate text-125 text-ink">{file.filename}</span>
                  <span className="text-11 text-muted">{fileSize(file.size_bytes)}</span>
                </label>
              </li>
            )
          })}
        </ul>
      )}
      <div className="flex justify-end gap-2 border-t border-hairline px-3.5 py-2.5">
        <button type="button" onClick={onClose} className="px-2.5 py-[5px] text-12 text-ink-2">
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onDone(picked)}
          className="rounded-pill bg-brand px-3 py-[5px] text-12 font-medium text-white hover:bg-brand-dark"
        >
          {added > 0 ? `Attach ${added}` : 'Done'}
        </button>
      </div>
    </div>
  )
}
