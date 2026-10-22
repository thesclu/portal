/**
 * Stage 1's "Not sure which fits? Ask the assistant" (058, P19d): a 400 px panel down the right
 * of the window, over the page. The question as a grey bubble at the right; the answer as the
 * assistant's plain words (the closest master letters and past requests, and why), then a
 * blue-framed proposal: the items it proposes, "Use this draft" (the closest letter, started as
 * any row's Use starts it) and Copy for the items, which a person puts into the letter
 * themselves. The line under it says what the draft would start from and that nothing is filed.
 * "Change item N" and "Widen the dates" (058) need a conversation the stage 1 door does not have
 * and are not drawn. The panel calls `/core/ai/pick`; when no provider is set it answers "off",
 * which no screen draws, so the server's own sentence is shown as the answer.
 */
import { useState } from 'react'

import { api } from '../../api/client'
import { messageOf } from '../../api/query'
import { unwrap } from '../../components/ActionDialog'
import { CloseIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { draftBodyFor, matchKindLabel, type AiPick } from '../../lib/assistant'
import { AskBox } from './parts'

interface Exchange {
  id: number
  question: string
  answer: AiPick | null
  error: string | null
}

export function AskAssistant({
  busy,
  onUse,
  onClose,
}: {
  /** The key of the start under way, as NewRequest tracks it. */
  busy: string | null
  onUse: (key: string, body: Record<string, string>) => void
  onClose: () => void
}) {
  const [thread, setThread] = useState<Exchange[]>([])
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)

  const ask = async () => {
    const asked = question.trim()
    if (!asked || asking) return
    const id = Date.now()
    setThread((t) => [...t, { id, question: asked, answer: null, error: null }])
    setQuestion('')
    setAsking(true)
    try {
      const answer = unwrap(await api.POST('/api/core/ai/pick', { body: { question: asked } }))
      setThread((t) => t.map((x) => (x.id === id ? { ...x, answer } : x)))
    } catch (caught) {
      const error = caught instanceof Error ? caught.message : messageOf(caught)
      setThread((t) => t.map((x) => (x.id === id ? { ...x, error } : x)))
    } finally {
      setAsking(false)
    }
  }
  const copy = async (key: string, text: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(key)
    window.setTimeout(() => setCopied((was) => (was === key ? null : was)), 1500)
  }

  return (
    <aside
      aria-label="Assistant"
      onKeyDown={(event) => {
        if (event.key === 'Escape') onClose()
      }}
      className="fixed top-0 right-0 bottom-0 z-[15] box-border flex w-[400px] flex-col border border-line bg-card-white shadow-menu"
    >
      <div className="flex items-center border-b border-hairline px-5 py-4 leading-[normal]">
        <span className="text-14 font-semibold text-ink">Assistant</span>
        <button
          type="button"
          aria-label="Close the assistant"
          className="ml-auto inline-flex rounded-6 hover:bg-hover"
          onClick={onClose}
        >
          <CloseIcon size={14} width={2.2} stroke="var(--color-muted)" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-[18px]">
        {thread.length === 0 ? (
          <p className="max-w-[94%] text-12 leading-[1.6] text-muted">
            Say what you want to find out, in plain words. The assistant points at the closest
            master letter or past request and may propose items to add; you choose what to use.
          </p>
        ) : null}
        {thread.map((exchange) => (
          <ExchangeView
            key={exchange.id}
            exchange={exchange}
            busy={busy}
            copied={copied}
            onUse={onUse}
            onCopy={copy}
          />
        ))}
        <AskBox
          className="mt-auto"
          placeholder={thread.length ? 'Ask a follow-up…' : 'What do you want to find out?'}
          value={question}
          onChange={setQuestion}
          onSubmit={() => void ask()}
          busy={asking}
          autoFocus
        />
      </div>
    </aside>
  )
}

const WORDS = 'max-w-[94%] text-12 leading-[1.6] text-ink'

function ExchangeView({
  exchange,
  busy,
  copied,
  onUse,
  onCopy,
}: {
  exchange: Exchange
  busy: string | null
  copied: string | null
  onUse: (key: string, body: Record<string, string>) => void
  onCopy: (key: string, text: string) => Promise<void>
}) {
  const answer = exchange.answer
  const problem =
    exchange.error ??
    (answer && answer.status !== 'ok'
      ? answer.message ||
        (answer.status === 'off' ? 'The assistant is off.' : 'The assistant could not answer.')
      : null)
  return (
    <>
      <p className="max-w-[88%] self-end rounded-12 rounded-br-4 bg-hover px-3 py-[9px] text-12 leading-[1.5] whitespace-pre-wrap text-ink">
        {exchange.question}
      </p>
      {!answer && !exchange.error ? (
        <p className={cx(WORDS, 'text-muted')}>Looking through the letters…</p>
      ) : problem ? (
        <p className={cx(WORDS, exchange.error ? 'text-danger' : 'text-ink-2')}>{problem}</p>
      ) : (
        <AnswerView answer={answer!} busy={busy} copied={copied} onUse={onUse} onCopy={onCopy} />
      )}
    </>
  )
}

function AnswerView({
  answer,
  busy,
  copied,
  onUse,
  onCopy,
}: {
  answer: AiPick
  busy: string | null
  copied: string | null
  onUse: (key: string, body: Record<string, string>) => void
  onCopy: (key: string, text: string) => Promise<void>
}) {
  const matches = answer.matches.filter((m) => Boolean(m.id && m.name))
  const closest = matches.find((m) => draftBodyFor(m) !== null) ?? null
  const items = answer.proposed_items.filter((item) => item.trim())
  if (matches.length === 0 && items.length === 0 && !answer.note)
    return (
      <p className={WORDS}>Nothing close enough to point at. Try saying more about the records.</p>
    )
  const useKey = closest ? `pick-${closest.id}` : ''
  const useBody = closest ? draftBodyFor(closest) : null
  return (
    <>
      {matches.map((match, index) => (
        <p key={`${match.id}-${index}`} className={WORDS}>
          {index === 0 ? 'The closest' : 'Also close'}: <em>{match.name}</em>{' '}
          <span className="text-muted">({matchKindLabel(match.kind).toLowerCase()})</span>
          {match.why ? `. ${match.why}` : '.'}
          {index > 0 && draftBodyFor(match) ? (
            <>
              {' '}
              <button
                type="button"
                className="font-medium text-brand hover:text-brand-dark"
                disabled={busy !== null}
                onClick={() => onUse(`pick-${match.id}`, draftBodyFor(match)!)}
              >
                Use this one
              </button>
            </>
          ) : null}
        </p>
      ))}
      {answer.note ? <p className={WORDS}>{answer.note}</p> : null}
      {items.length > 0 || closest ? (
        <div className="overflow-hidden rounded-10 border-[1.5px] border-brand">
          <div className="bg-brand-tint px-3 py-[7px] text-11 leading-[normal] font-semibold tracking-[0.06em] text-brand uppercase">
            {items.length === 0
              ? 'Proposed · Start from'
              : items.length === 1
                ? 'Proposed · 1 item'
                : `Proposed · ${items.length} items`}
          </div>
          <div className="px-3 py-2.5 text-12 leading-[1.6] text-ink">
            {items.length === 0 ? (
              <em>{closest?.name}</em>
            ) : (
              items.map((item, index) => (
                <p key={index} className={cx('whitespace-pre-wrap', index > 0 && 'mt-1.5')}>
                  {item}
                </p>
              ))
            )}
          </div>
          <div className="flex gap-2 px-3 pb-2.5">
            {useBody ? (
              <button
                type="button"
                aria-busy={busy === useKey || undefined}
                disabled={busy !== null}
                onClick={() => onUse(useKey, useBody)}
                className="inline-flex h-7 items-center rounded-pill bg-brand px-[11px] text-115 font-semibold text-white hover:bg-brand-dark disabled:opacity-55"
              >
                Use this draft
              </button>
            ) : null}
            {items.length > 0 ? (
              <button
                type="button"
                onClick={() => void onCopy('items', items.join('\n\n'))}
                className="inline-flex h-7 items-center rounded-pill border border-line-2 px-[11px] text-115 font-medium text-ink-2 hover:bg-page"
              >
                {copied === 'items'
                  ? 'Copied'
                  : items.length === 1
                    ? 'Copy the item'
                    : 'Copy the items'}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
      {closest ? (
        <p className="text-115 leading-[1.5] text-muted">
          Starts from {closest.name} · {items.length > 0 ? 'add the items yourself · ' : ''}nothing
          filed yet
        </p>
      ) : null}
    </>
  )
}
