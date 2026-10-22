import type { ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'

import { type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import { SearchIcon } from '../components/icons'
import { CardAction, CardHead, ListCard } from '../components/ListTable'
import { Empty, LoadFailed, Loading } from '../components/States'
import { formatShortInstant } from '../lib/dates'

type InboxRow = Schemas['InboxRowOut']
type Message = Schemas['MailMessageOut']

const LIMIT = 5

/** One row of the Messages group: mail that arrived, or mail sent. */
type MessageEntry = { key: string; received?: InboxRow; sent?: Message }

/** The words found, each lit (041's soft yellow), in the rest of the text. */
export function Highlight({ text, words }: { text: string; words: string }) {
  const needle = words.trim()
  if (!needle) return <>{text}</>
  const parts = text.split(new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'))
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-[2px] bg-amber-tint-2 px-px text-inherit">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  )
}

/**
 * Search (041): the words in the address, found in requests, mail both ways, produced records and
 * agencies; each group a card of a few, with "See all" to the screen that holds the rest. Mail
 * that arrived and mail sent are one "Messages" group, as drawn; "See all" opens All mail.
 */
export function Search() {
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const q = (params.get('q') ?? '').trim()
  const results = $api.useQuery(
    'get',
    '/api/records/search',
    { params: { query: { q, limit: LIMIT } } },
    { enabled: q.length > 0, placeholderData: (previous) => previous },
  )
  const data = results.data
  const encoded = encodeURIComponent(q)
  const total = data
    ? data.requests_total +
      data.received_total +
      data.sent_total +
      data.records_total +
      data.agencies_total
    : 0
  const messages: MessageEntry[] = data
    ? [
        ...data.received.map((row) => ({ key: row.source.id, received: row })),
        ...data.sent.map((message) => ({ key: message.id, sent: message })),
      ]
    : []

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Search" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        {/* 40 px inside its line, content-box as drawn (43 in all). */}
        <label className="box-content flex h-10 items-center gap-[9px] rounded-10 border-[1.5px] border-brand bg-card-white px-3">
          <SearchIcon size={15} width={2} stroke="var(--color-ink)" />
          <input
            key={q}
            type="search"
            aria-label="Search everything"
            placeholder="A tracking number, a subject, a file name, an agency…"
            defaultValue={q}
            autoFocus
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                const next = event.currentTarget.value.trim()
                setParams(next ? { q: next } : {}, { replace: true })
              }
            }}
            className="min-w-0 flex-1 bg-transparent text-135 text-ink outline-none placeholder:text-muted-2 [&::-webkit-search-cancel-button]:hidden"
          />
          {data && q ? (
            <span className="ml-auto text-12 whitespace-nowrap text-muted">
              {total} {total === 1 ? 'result' : 'results'}
            </span>
          ) : null}
        </label>

        {!q ? (
          <Empty title="Type something to search." />
        ) : results.isPending ? (
          <Loading what="Searching" />
        ) : results.isError ? (
          <LoadFailed
            what="the results"
            error={results.error}
            onRetry={() => void results.refetch()}
          />
        ) : !data || total === 0 ? (
          <Empty title={`Nothing matches “${q}”.`} />
        ) : (
          <div
            className={
              results.isFetching ? 'flex flex-col gap-3.5 opacity-60' : 'flex flex-col gap-3.5'
            }
          >
            <Group label="Requests" total={data.requests_total} seeAll={`/docket?q=${encoded}`}>
              {data.requests.map((row) => (
                <ResultRow
                  key={row.id}
                  to={`/requests/${row.id}`}
                  lead={row.tracking_number}
                  right={row.agency_name}
                >
                  <Highlight text={row.subject} words={q} />
                </ResultRow>
              ))}
            </Group>

            <Group
              label="Messages"
              total={data.received_total + data.sent_total}
              seeAll={`/inbox?bucket=all&q=${encoded}`}
            >
              {messages.map((entry) =>
                entry.received ? (
                  <ResultRow
                    key={entry.key}
                    to={`/inbox?bucket=all&message=${entry.received.source.id}`}
                    lead={senderOf(entry.received)}
                    right={formatShortInstant(entry.received.source.captured_at, me.timezone)}
                  >
                    <Highlight text={receivedTitle(entry.received)} words={q} />
                  </ResultRow>
                ) : entry.sent ? (
                  <ResultRow
                    key={entry.key}
                    to={`/requests/${entry.sent.request_id}`}
                    lead={sentTo(entry.sent)}
                    right={formatShortInstant(
                      entry.sent.sent_at ?? entry.sent.created_at,
                      me.timezone,
                    )}
                  >
                    <Highlight text={entry.sent.subject || 'No subject'} words={q} />
                  </ResultRow>
                ) : null,
              )}
            </Group>

            <Group label="Records" total={data.records_total} seeAll={`/records?q=${encoded}`}>
              {data.records.map((record) => (
                <ResultRow
                  key={`${record.attachment.id}-${record.request_id}`}
                  to={`/requests/${record.request_id}`}
                  lead={record.attachment.filename}
                  right={record.agency_name}
                >
                  <Highlight
                    text={
                      record.item_description
                        ? `${record.item_description} · ${record.tracking_number}`
                        : `${record.subject} · ${record.tracking_number}`
                    }
                    words={q}
                  />
                </ResultRow>
              ))}
            </Group>

            <Group label="Agencies" total={data.agencies_total} seeAll={`/agencies?q=${encoded}`}>
              {data.agencies.map((agency) => (
                <ResultRow
                  key={agency.id}
                  to={`/agencies/${agency.id}`}
                  lead={agency.short_name || agency.name}
                  right={agency.jurisdiction}
                >
                  <Highlight
                    text={agency.notes ? `${agency.name} · ${agency.notes}` : agency.name}
                    words={q}
                  />
                </ResultRow>
              ))}
            </Group>
          </div>
        )}
      </div>
    </div>
  )
}

/** What a received message is called: its subject, else who sent it, else its reference. */
function receivedTitle(row: InboxRow): string {
  const meta = row.source.raw_metadata as { subject?: string; from?: string } | null
  return meta?.subject || meta?.from || row.source.external_ref || 'Message'
}

function senderOf(row: InboxRow): string {
  const meta = row.source.raw_metadata as { from_name?: string; from?: string } | null
  return meta?.from_name || meta?.from || row.events[0]?.tracking_number || 'Message'
}

function sentTo(message: Message): string {
  const to = message.recipients.find((r) => r.kind === 'to') ?? message.recipients[0]
  return to?.name || to?.address || message.tracking_number
}

function Group({
  label,
  total,
  seeAll,
  children,
}: {
  label: string
  total: number
  seeAll: string
  children: ReactNode
}) {
  if (total === 0) return null
  return (
    <ListCard aria-label={label}>
      <CardHead
        title={label}
        count={total}
        action={<CardAction to={seeAll}>See all {total}</CardAction>}
      />
      {children}
    </ListCard>
  )
}

function ResultRow({
  to,
  lead,
  right,
  children,
}: {
  to: string
  lead: string
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <Link
      to={to}
      className="flex gap-3.5 border-t border-line-row px-5 py-2.5 text-125 leading-[normal] text-ink hover:bg-surface-row hover:text-ink"
    >
      <span className="w-[150px] shrink-0 truncate text-ink-2">{lead}</span>
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {right ? <span className="whitespace-nowrap text-muted">{right}</span> : null}
    </Link>
  )
}
