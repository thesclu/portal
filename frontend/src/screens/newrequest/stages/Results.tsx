/**
 * After filing (060): "N of M filed" with what failed and what waits on the person beside it,
 * "Retry failed" at the right, and one row per agency: how it files, the result in its colour
 * (Filed · Failed with why · Waiting for you · print and mail · Pending filing), its tracking
 * number as plain text, and the row's one action (Retry, Log mailing, Open), each of which opens
 * that agency in stage 4. "Open campaign" (060) is not drawn: campaigns were dropped.
 */
import type { Schemas } from '../../../api/client'
import { cx } from '../../../components/ui'
import { listName } from './ReviewFile'
import { howShort, resultParts, tally, tallyRest, type Agency } from './stages'

type Draft = Schemas['DraftDetailOut']

/** The columns as 060 draws them, 14 px apart. */
const COLUMNS = '1.3fr 100px 1.5fr 150px 90px'

const ACTION_WORDS = { retry: 'Retry', log: 'Log mailing', open: 'Open' } as const
const TONE = {
  ok: 'text-ok',
  danger: 'font-medium text-danger',
  warn: 'text-warn',
  muted: 'text-muted',
}

export function FilingResults({
  draft,
  agencies,
  failures,
  onOpen,
}: {
  draft: Draft
  agencies: Agency[]
  failures: Record<string, string>
  /** Opens one agency in stage 4. */
  onOpen: (recipientId: string) => void
}) {
  const agencyOf = (id: string) => agencies.find((a) => a.id === id)
  const counts = tally(draft.recipients, failures)
  const rest = tallyRest(counts)
  const rows = draft.recipients.map((recipient) => ({
    recipient,
    agency: agencyOf(recipient.agency_id),
    parts: resultParts(recipient, failures[recipient.id]),
  }))
  const firstFailed = rows.find((row) => row.parts.action === 'retry')
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2 leading-[normal]">
      <div className="flex items-center gap-2.5 text-13">
        <span className="font-medium text-ink" aria-live="polite">
          {counts.filed} of {counts.total} filed
        </span>
        {rest ? <span className="text-muted">{rest}</span> : null}
        {firstFailed ? (
          <span className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={() => onOpen(firstFailed.recipient.id)}
              className="box-border inline-flex h-8 items-center gap-1.5 rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
            >
              Retry failed
            </button>
          </span>
        ) : null}
      </div>
      <section
        aria-label="Filing results"
        className="overflow-hidden rounded-16 bg-card shadow-float"
      >
        <div
          role="row"
          style={{ gridTemplateColumns: COLUMNS }}
          className="grid gap-x-3.5 border-b border-hairline px-9 py-[9px] text-105 font-semibold tracking-label text-muted uppercase"
        >
          <span role="columnheader">Agency</span>
          <span role="columnheader">How</span>
          <span role="columnheader">Result</span>
          <span role="columnheader">Tracking no.</span>
          <span role="columnheader">
            <span className="sr-only">Action</span>
          </span>
        </div>
        {rows.map(({ recipient, agency, parts }, index) => (
          <div
            key={recipient.id}
            role="row"
            style={{ gridTemplateColumns: COLUMNS }}
            className={cx(
              'grid items-center gap-x-3.5 px-9 py-[11px] text-125',
              index < rows.length - 1 && 'border-b border-transparent',
              index % 2 === 1 && 'bg-row-alt',
            )}
          >
            <span className="min-w-0 truncate text-ink">{listName(agency)}</span>
            <span className="min-w-0 truncate text-muted">
              {howShort(recipient.channel, agency)}
            </span>
            <span className="min-w-0 truncate">
              <span className={TONE[parts.tone]}>{parts.head}</span>
              {parts.tail ? <span className="text-muted"> · {parts.tail}</span> : null}
            </span>
            <span className="min-w-0 truncate text-muted">{recipient.tracking_number ?? '—'}</span>
            <span className="min-w-0 truncate text-right">
              {parts.action ? (
                <button
                  type="button"
                  onClick={() => onOpen(recipient.id)}
                  className="text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark"
                >
                  {ACTION_WORDS[parts.action]}
                </button>
              ) : null}
            </span>
          </div>
        ))}
      </section>
    </div>
  )
}
